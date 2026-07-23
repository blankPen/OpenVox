from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import json
import os
import time
import weakref
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import aiohttp
import numpy as np
from livekit import rtc
from livekit.agents import llm, utils
from livekit.agents.types import (
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    APIConnectOptions,
    NotGivenOr,
)

from .log import logger

# ---------------------------------------------------------------------------
# Qwen Omni Realtime API — 配置
# ---------------------------------------------------------------------------

# 输入/输出音频规格（千问固定值，不可修改）
QWEN_INPUT_SAMPLE_RATE = 16000   # 16kHz, 单声道, 16-bit PCM
QWEN_OUTPUT_SAMPLE_RATE = 24000  # 24kHz, 单声道, 16-bit PCM


@dataclass
class _RealtimeOptions:
    api_key: str
    model: str
    voice: str
    instructions: str
    output_modalities: list[str]
    turn_detection_type: str  # "semantic_vad" or "server_vad"
    opening: str | None
    conn_options: APIConnectOptions

    @property
    def ws_url(self) -> str:
        return f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={self.model}"

    def get_ws_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
        }


# ---------------------------------------------------------------------------
# 内部数据结构
# ---------------------------------------------------------------------------


@dataclass
class _MessageGeneration:
    message_id: str
    text_ch: utils.aio.Chan[str]
    audio_ch: utils.aio.Chan[rtc.AudioFrame]
    modalities: asyncio.Future[list[Literal["text", "audio"]]] | None = None


@dataclass
class _ResponseGeneration:
    message_ch: utils.aio.Chan[llm.MessageGeneration]
    function_ch: utils.aio.Chan[llm.FunctionCall]
    messages: dict[str, _MessageGeneration]
    _done_fut: asyncio.Future[None]
    _created_timestamp: float
    _first_token_timestamp: float | None = None
    # 本轮累积的 function_call_output items（用于 update_chat_ctx 检测新结果）
    _pending_fnc_outputs: list[llm.FunctionCallOutput] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RealtimeModel
# ---------------------------------------------------------------------------


class RealtimeModel(llm.RealtimeModel):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "qwen3.5-omni-plus-realtime",
        voice: str = "Tina",
        instructions: str = (
            "你是一个友好的中文语音助手。请用简洁、自然的口吻回答用户的问题，"
            "避免使用表情符号、Markdown 或特殊符号。"
        ),
        opening: str | None = None,
        turn_detection_type: Literal["semantic_vad", "server_vad"] = "semantic_vad",
        output_modalities: NotGivenOr[list[str]] = NOT_GIVEN,
        http_session: aiohttp.ClientSession | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> None:
        output_modalities = (
            output_modalities if utils.is_given(output_modalities) else ["text", "audio"]
        )
        super().__init__(
            capabilities=llm.RealtimeCapabilities(
                message_truncation=True,
                turn_detection=True,
                user_transcription=True,
                auto_tool_reply_generation=False,
                audio_output=("audio" in output_modalities),
                manual_function_calls=True,
            )
        )
        api_key = api_key or os.environ.get("DASHSCOPE_API_KEY")
        if api_key is None:
            raise ValueError("DASHSCOPE_API_KEY is required")

        self._opts = _RealtimeOptions(
            api_key=api_key,
            model=model,
            voice=voice,
            instructions=instructions,
            output_modalities=output_modalities,
            turn_detection_type=turn_detection_type,
            opening=opening,
            conn_options=conn_options,
        )
        self._http_session = http_session
        self._sessions = weakref.WeakSet[RealtimeSession]()

        logger.info(f"[Qwen] model={model} voice={voice}")
        logger.info(f"[Qwen] turn_detection={turn_detection_type}")
        logger.info(f"[Qwen] output_modalities={output_modalities}")
        logger.info(f"[Qwen] opening={opening!r}")

    def update_options(
        self,
        *,
        max_session_duration: NotGivenOr[float | None] = NOT_GIVEN,
    ) -> None:
        pass

    def _ensure_http_session(self) -> aiohttp.ClientSession:
        if not self._http_session:
            self._http_session = utils.http_context.http_session()
        return self._http_session

    def session(self) -> RealtimeSession:
        sess = RealtimeSession(self)
        self._sessions.add(sess)
        return sess

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# RealtimeSession
# ---------------------------------------------------------------------------


class RealtimeSession(
    llm.RealtimeSession[
        Literal["qwen_server_event_received", "qwen_client_event_queued"]
    ]
):
    def __init__(self, realtime_model: RealtimeModel) -> None:
        super().__init__(realtime_model)
        self._realtime_model: RealtimeModel = realtime_model
        self._opts = realtime_model._opts
        self._tools = llm.ToolContext.empty()
        self._msg_ch = utils.aio.Chan[rtc.AudioFrame]()
        self._input_resampler: rtc.AudioResampler | None = None

        self._instructions: str | None = self._opts.instructions
        self._main_atask = asyncio.create_task(
            self._main_task(), name="QwenRealtimeSession._main_task"
        )

        self._current_generation: _ResponseGeneration | None = None
        self._current_item: _MessageGeneration | None = None
        self._remote_chat_ctx = llm.remote_chat_context.RemoteChatContext()
        self._is_opening = False
        self._first_text_chunk = True
        self._text_buffer = ""  # 累积 response.audio_transcript.delta

        self._update_chat_ctx_lock = asyncio.Lock()
        self._update_fnc_ctx_lock = asyncio.Lock()

        # 已发送到千问服务端的 item IDs（避免重复发送 conversation.item.create）
        self._sent_item_ids: set[str] = set()

        # AudioByteStream: 把 16kHz PCM 拆成 ~100ms 的 chunks
        self._bstream = utils.audio.AudioByteStream(
            QWEN_INPUT_SAMPLE_RATE,
            1,
            samples_per_channel=QWEN_INPUT_SAMPLE_RATE // 10,
        )
        self._pushed_duration_s: float = 0.0

    # ------------------------------------------------------------------
    # 音频输入
    # ------------------------------------------------------------------

    def send_event(self, event: rtc.AudioFrame) -> None:
        with contextlib.suppress(utils.aio.channel.ChanClosed):
            self._msg_ch.send_nowait(event)

    def push_audio(self, frame: rtc.AudioFrame) -> None:
        for f in self._resample_input_audio(frame):
            data = f.data.tobytes()
            for nf in self._bstream.write(data):
                self.send_event(nf)
                self._pushed_duration_s += nf.duration

    def push_video(self, frame: rtc.VideoFrame) -> None:
        pass  # 千问支持视频，但当前只做音频

    def commit_audio(self) -> None:
        if self._pushed_duration_s > 0.1:
            self._pushed_duration_s = 0

    def clear_audio(self) -> None:
        self._pushed_duration_s = 0

    # ------------------------------------------------------------------
    # 主任务
    # ------------------------------------------------------------------

    @utils.log_exceptions(logger=logger)
    async def _main_task(self) -> None:
        logger.info("[Qwen] start realtime main task")
        ws_conn = await self._create_ws_conn()
        self._ws_conn = ws_conn

        try:
            await self._run_ws(ws_conn)
        except Exception as e:
            logger.error("[Qwen] realtime main task error", exc_info=e)
            self._emit_error(e, recoverable=False)
            raise e
        logger.info("[Qwen] realtime main task break")

    async def _create_ws_conn(self) -> aiohttp.ClientWebSocketResponse:
        headers = self._realtime_model._opts.get_ws_headers()
        url = self._realtime_model._opts.ws_url
        logger.info(f"[Qwen] connecting to {url}")
        return await asyncio.wait_for(
            self._realtime_model._ensure_http_session().ws_connect(
                url=url,
                headers=headers,
                heartbeat=30.0,
            ),
            self._realtime_model._opts.conn_options.timeout,
        )

    async def _run_ws(self, ws_conn: aiohttp.ClientWebSocketResponse) -> None:
        closing = False

        # --- 事件: session.created ---
        session_created_msg = await ws_conn.receive()
        if session_created_msg.type == aiohttp.WSMsgType.TEXT:
            evt = json.loads(session_created_msg.data)
            if evt.get("type") == "session.created":
                session_id = evt.get("session", {}).get("id", "unknown")
                logger.info(f"[Qwen] session created: {session_id}")
                self._session_id = session_id
            else:
                logger.warning(
                    f"[Qwen] expected session.created, got {evt.get('type')}"
                )
        else:
            logger.error(
                f"[Qwen] unexpected first message type: {session_created_msg.type}"
            )

        # --- 发送 session.update ---
        await self._send_session_update(ws_conn)

        # 等待 session.updated 确认
        session_updated_msg = await ws_conn.receive()
        if session_updated_msg.type == aiohttp.WSMsgType.TEXT:
            evt = json.loads(session_updated_msg.data)
            if evt.get("type") == "session.updated":
                logger.info("[Qwen] session updated successfully")
            else:
                logger.warning(
                    f"[Qwen] expected session.updated, got {evt.get('type')}"
                )

        # --- 开场白 ---
        if self._realtime_model._opts.opening is not None:
            self._is_opening = True
            logger.info(
                f"[Qwen] sending opening: {self._realtime_model._opts.opening!r}"
            )

            # 创建 output channels
            self._current_generation = _ResponseGeneration(
                message_ch=utils.aio.Chan(),
                function_ch=utils.aio.Chan(),
                messages={},
                _created_timestamp=time.time(),
                _done_fut=asyncio.Future(),
            )

            generation_ev = llm.GenerationCreatedEvent(
                message_stream=self._current_generation.message_ch,
                function_stream=self._current_generation.function_ch,
                user_initiated=False,
            )
            self.emit("generation_created", generation_ev)
            item_id = utils.shortuuid()
            modalities_fut: asyncio.Future[list[Literal["text", "audio"]]] = (
                asyncio.Future()
            )
            self._current_item = _MessageGeneration(
                message_id=item_id,
                text_ch=utils.aio.Chan(),
                audio_ch=utils.aio.Chan(),
                modalities=modalities_fut,
            )
            if not self._realtime_model.capabilities.audio_output:
                self._current_item.audio_ch.close()
                self._current_item.modalities.set_result(["text"])  # type: ignore[union-attr]
            else:
                self._current_item.modalities.set_result(["audio", "text"])  # type: ignore[union-attr]

            with contextlib.suppress(utils.aio.channel.ChanClosed):
                self._current_generation.message_ch.send_nowait(
                    llm.MessageGeneration(
                        message_id=item_id,
                        text_stream=self._current_item.text_ch,
                        audio_stream=self._current_item.audio_ch,
                        modalities=self._current_item.modalities,
                    )
                )

            # 触发千问生成开场白（用 instructions 参数引导模型打招呼）
            await self._send_json(ws_conn, {
                "type": "response.create",
                "response": {
                    "instructions": (
                        f"请用以下开场白向用户打招呼："
                        f"{self._realtime_model._opts.opening}"
                    ),
                },
            })

        # --- 并行收发 ---
        @utils.log_exceptions(logger=logger)
        async def _send_task() -> None:
            nonlocal closing
            async for frame in self._msg_ch:
                try:
                    audio_b64 = base64.b64encode(frame.data.tobytes()).decode("ascii")
                    await self._send_json(ws_conn, {
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64,
                    })
                    self.emit(
                        "qwen_client_event_queued",
                        {"type": "input_audio_buffer.append"},
                    )
                except Exception:
                    logger.error("[Qwen] send task error", exc_info=True)
                    break
            closing = True
            await ws_conn.close()

        @utils.log_exceptions(logger=logger)
        async def _recv_task() -> None:
            async for msg in ws_conn:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.info("[Qwen] WebSocket closed by server")
                        break
                    continue

                try:
                    evt = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.warning(f"[Qwen] invalid JSON: {msg.data[:200]}")
                    continue

                evt_type = evt.get("type", "")
                self.emit("qwen_server_event_received", evt)

                try:
                    await self._handle_server_event(evt, evt_type, ws_conn)
                except Exception:
                    logger.error(
                        f"[Qwen] error handling event {evt_type}", exc_info=True
                    )

        tasks = [
            asyncio.create_task(_recv_task(), name="_recv_task"),
            asyncio.create_task(_send_task(), name="_send_task"),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            await utils.aio.cancel_and_wait(*tasks)
            await ws_conn.close()

    # ------------------------------------------------------------------
    # 服务端事件处理
    # ------------------------------------------------------------------

    async def _handle_server_event(
        self,
        evt: dict,
        evt_type: str,
        ws_conn: aiohttp.ClientWebSocketResponse,
    ) -> None:
        if evt_type == "input_audio_buffer.speech_started":
            logger.info("[Qwen] 🎤 检测到用户开始说话")
            self.emit("input_speech_started", llm.InputSpeechStartedEvent())

        elif evt_type == "input_audio_buffer.speech_stopped":
            logger.info("[Qwen] 🛑 用户停止说话，等待模型回复")
            self.emit(
                "input_speech_stopped",
                llm.InputSpeechStoppedEvent(user_transcription_enabled=False),
            )
            self._first_text_chunk = True
            self._text_buffer = ""

        elif evt_type == "conversation.item.input_audio_transcription.completed":
            transcript = evt.get("transcript", "")
            item_id = evt.get("item_id", utils.shortuuid())
            logger.info(f"[Qwen] 📝 识别到用户说: {transcript!r}")
            self.emit(
                "input_audio_transcription_completed",
                llm.InputTranscriptionCompleted(
                    item_id=item_id,
                    transcript=transcript,
                    is_final=True,
                ),
            )

            # 确保 _current_generation 已创建（供后续事件写入）
            if self._current_generation is None:
                self._current_generation = _ResponseGeneration(
                    message_ch=utils.aio.Chan(),
                    function_ch=utils.aio.Chan(),
                    messages={},
                    _created_timestamp=time.time(),
                    _done_fut=asyncio.Future(),
                )

                generation_ev = llm.GenerationCreatedEvent(
                    message_stream=self._current_generation.message_ch,
                    function_stream=self._current_generation.function_ch,
                    user_initiated=False,
                )
                self.emit("generation_created", generation_ev)
                item_id = utils.shortuuid()
                modalities_fut: asyncio.Future[list[Literal["text", "audio"]]] = (
                    asyncio.Future()
                )
                self._current_item = _MessageGeneration(
                    message_id=item_id,
                    text_ch=utils.aio.Chan(),
                    audio_ch=utils.aio.Chan(),
                    modalities=modalities_fut,
                )
                if not self._realtime_model.capabilities.audio_output:
                    self._current_item.audio_ch.close()
                    self._current_item.modalities.set_result(["text"])  # type: ignore[union-attr]
                else:
                    self._current_item.modalities.set_result(["audio", "text"])  # type: ignore[union-attr]

                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_generation.message_ch.send_nowait(
                        llm.MessageGeneration(
                            message_id=item_id,
                            text_stream=self._current_item.text_ch,
                            audio_stream=self._current_item.audio_ch,
                            modalities=self._current_item.modalities,
                        )
                    )

        elif evt_type == "response.created":
            logger.info("[Qwen] 🤖 模型开始生成回复...")

        elif evt_type == "response.audio_transcript.delta":
            delta = evt.get("delta", "")
            self._text_buffer += delta
            if self._current_item is not None:
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_item.text_ch.send_nowait(delta)

        elif evt_type == "response.audio.delta":
            delta_b64 = evt.get("delta", "")
            if delta_b64 and self._current_item is not None:
                if self._first_text_chunk:
                    logger.info("[Qwen] 🔊 开始合成语音")
                    self._first_text_chunk = False
                try:
                    audio_bytes = base64.b64decode(delta_b64)
                    # Qwen 输出: 24kHz, 16-bit PCM, mono
                    audio_frame = rtc.AudioFrame(
                        data=audio_bytes,
                        sample_rate=QWEN_OUTPUT_SAMPLE_RATE,
                        num_channels=1,
                        samples_per_channel=len(audio_bytes) // 2,
                    )
                    # 重采样到 48kHz 供 LiveKit 使用
                    for f in self._resample_output_audio(audio_frame):
                        with contextlib.suppress(utils.aio.channel.ChanClosed):
                            self._current_item.audio_ch.send_nowait(f)
                except Exception:
                    logger.error(
                        "[Qwen] audio decode/resample error", exc_info=True
                    )

        elif evt_type == "response.audio_transcript.done":
            transcript = evt.get("transcript", "")
            if transcript:
                logger.info(f"[Qwen] 💬 Agent 完整回复: {transcript!r}")
                self._text_buffer = ""

        elif evt_type == "response.audio.done":
            logger.info("[Qwen] ✅ 语音合成结束")
            if self._current_item is not None:
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_item.audio_ch.close()

        elif evt_type == "response.function_call_arguments.done":
            # 千问原生 function calling
            fnc_name = evt.get("name", "")
            fnc_call_id = evt.get("call_id", "")
            fnc_arguments = evt.get("arguments", "{}")
            logger.info(
                f"[Qwen] 🔧 模型请求调用工具: {fnc_name}({fnc_arguments!r})"
            )
            if self._current_generation is not None:
                fnc_call = llm.FunctionCall(
                    call_id=fnc_call_id,
                    name=fnc_name,
                    arguments=fnc_arguments,
                )
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_generation.function_ch.send_nowait(fnc_call)

        elif evt_type == "response.function_call_arguments.delta":
            # 流式返回参数时打印进度（调试用）
            delta = evt.get("delta", "")
            logger.info(f"[Qwen] 🔧 工具参数 delta: {delta!r}")

        elif evt_type == "response.output_item.done":
            # 检查是否是 function_call 类型的 item
            item = evt.get("item", {})
            if item.get("type") == "function_call":
                fnc_name = item.get("name", "")
                fnc_call_id = item.get("call_id", "")
                fnc_arguments = item.get("arguments", "{}")
                # 避免重复发送（function_call_arguments.done 已经发过）
                if fnc_call_id:
                    logger.info(
                        f"[Qwen] 🔧 function_call item done: {fnc_name}"
                    )

        elif evt_type == "response.done":
            logger.info("[Qwen] ✅ 模型响应结束")
            response = evt.get("response", {})
            usage = response.get("usage", {})
            if usage:
                logger.info(
                    f"[Qwen] token usage: in={usage.get('input_tokens', 0)} "
                    f"out={usage.get('output_tokens', 0)} "
                    f"total={usage.get('total_tokens', 0)}"
                )

            # 检查 response.output 中是否有 function_call
            output_items = response.get("output", [])
            has_function_call = any(
                item.get("type") == "function_call" for item in output_items
            )

            # 兜底：streaming 路径未发送的 function_call 补发
            if has_function_call:
                for item in output_items:
                    if item.get("type") == "function_call":
                        fnc_call_id = item.get("call_id", "")
                        fnc_name = item.get("name", "")
                        fnc_arguments = item.get("arguments", "{}")
                        if fnc_call_id:
                            logger.info(
                                f"[Qwen] 🔧 response.done function_call: "
                                f"{fnc_name}({fnc_arguments!r})"
                            )

            # 关闭 text/audio channels（响应结束，不再产生文本/音频）
            # 注意：无论是否有 function_call，都必须关闭 audio_ch！
            # 框架的 _audio_forwarding_task 会阻塞等待 audio_ch，
            # 不关闭会导致 _read_messages → wait_if_not_interrupted 永远不返回。
            if self._current_item is not None:
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_item.text_ch.close()
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_item.audio_ch.close()

            # 关键：有 function_call 时不能立即关闭 function_ch！
            # 框架的 _read_fnc_stream (tee consumer 1) 先读 function call，
            # 然后阻塞等待更多数据；perform_tool_executions (tee consumer 2)
            # 要等 _read_fnc_stream 退出后才开始消费。
            # 延迟关闭让 consumer 1 能收到 EOF 退出，同时 consumer 2 的
            # 缓冲区已有 function call 副本可读。
            if self._current_generation is not None:
                with contextlib.suppress(utils.aio.channel.ChanClosed):
                    self._current_generation.message_ch.close()
                if not has_function_call:
                    with contextlib.suppress(utils.aio.channel.ChanClosed):
                        self._current_generation.function_ch.close()
                else:
                    # 延迟 500ms 关闭，给 tee 两个 consumer 都足够的时间
                    gen_to_close = self._current_generation
                    loop = asyncio.get_event_loop()
                    loop.create_task(self._delayed_close_function_ch(gen_to_close))

            if not has_function_call:
                self._current_generation = None
                self._current_item = None
                self._is_opening = False

        elif evt_type == "error":
            error_info = evt.get("error", {})
            logger.error(
                f"[Qwen] server error: {error_info.get('message', str(evt))}"
            )

    # ------------------------------------------------------------------
    # 发送 JSON 消息
    # ------------------------------------------------------------------

    async def _send_json(
        self, ws_conn: aiohttp.ClientWebSocketResponse, payload: dict
    ) -> None:
        await ws_conn.send_str(json.dumps(payload, ensure_ascii=False))

    async def _send_session_update(
        self, ws_conn: aiohttp.ClientWebSocketResponse
    ) -> None:
        session_config: dict = {
            "modalities": self._realtime_model._opts.output_modalities,
            "voice": self._realtime_model._opts.voice,
            "instructions": self._instructions or "",
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": {
                "type": self._realtime_model._opts.turn_detection_type,
                "threshold": 0.5,
                "silence_duration_ms": 800,
            },
            "enable_input_audio_transcription": True,
            "input_audio_transcription_model": "qwen3-asr-flash-realtime",
        }
        # 如果有 tools，一起发
        tools = self._tools_to_qwen()
        if tools:
            session_config["tools"] = tools

        logger.info(f"[Qwen] sending session.update (tools={len(tools)})")
        await self._send_json(ws_conn, {
            "type": "session.update",
            "session": session_config,
        })

    # ------------------------------------------------------------------
    # Tools 转换
    # ------------------------------------------------------------------

    def _tools_to_qwen(self) -> list[dict]:
        """将 LiveKit ToolContext 转为千问的 tools 格式（OpenAI 兼容 JSON Schema）。"""
        result = []
        for _name, tool in self._tools.function_tools.items():
            info = getattr(tool, "__livekit_tool_info", None)
            if info is None:
                continue
            func_def: dict = {
                "type": "function",
                "function": {
                    "name": info.name,
                    "description": info.description or "",
                },
            }
            # 尝试从 signature 推断 parameters schema
            import inspect as _inspect
            try:
                sig = _inspect.signature(tool)
                properties = {}
                required = []
                for pname, param in sig.parameters.items():
                    ann = param.annotation
                    if ann is _inspect.Parameter.empty or ann is str:
                        ptype = "string"
                    elif ann is int:
                        ptype = "integer"
                    elif ann is float:
                        ptype = "number"
                    elif ann is bool:
                        ptype = "boolean"
                    elif ann is list:
                        ptype = "array"
                    elif ann is dict:
                        ptype = "object"
                    else:
                        ptype = "string"
                    properties[pname] = {"type": ptype}
                    if param.default is _inspect.Parameter.empty:
                        required.append(pname)
                if properties:
                    func_def["function"]["parameters"] = {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
            except (TypeError, ValueError):
                pass
            result.append(func_def)
        return result

    # ------------------------------------------------------------------
    # 抽象方法实现
    # ------------------------------------------------------------------

    def update_options(
        self,
        *,
        tool_choice: NotGivenOr[llm.ToolChoice | None] = NOT_GIVEN,
        voice: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        pass

    async def update_tools(self, tools: list[llm.Tool]) -> None:
        """收到框架的新 tools 列表后，更新本地并发送 session.update 到千问服务端。"""
        self._tools = llm.ToolContext(tools)

        if getattr(self, "_ws_conn", None) is None or self._ws_conn.closed:
            logger.warning("[Qwen] update_tools called but ws not ready, queued")
            return

        logger.info(f"[Qwen] update_tools: sending {len(tools)} tools")
        session_config: dict = {
            "modalities": self._realtime_model._opts.output_modalities,
            "voice": self._realtime_model._opts.voice,
            "instructions": self._instructions or "",
            "input_audio_format": "pcm",
            "output_audio_format": "pcm",
            "turn_detection": {
                "type": self._realtime_model._opts.turn_detection_type,
                "threshold": 0.5,
                "silence_duration_ms": 800,
            },
            "tools": self._tools_to_qwen(),
        }
        await self._send_json(self._ws_conn, {
            "type": "session.update",
            "session": session_config,
        })

    async def update_chat_ctx(self, chat_ctx: llm.ChatContext) -> None:
        """同步 chat_ctx 到千问服务端。

        发送新的 user message 和 function_call_output items 到千问服务端，
        确保服务端有完整的对话上下文。
        """
        async with self._update_chat_ctx_lock:
            if getattr(self, "_ws_conn", None) is None or self._ws_conn.closed:
                logger.warning(
                    "[Qwen] update_chat_ctx called but ws not ready"
                )
                return

            logger.info(
                f"[Qwen] update_chat_ctx: {len(chat_ctx.items)} items, "
                f"{len(self._sent_item_ids)} already sent"
            )

            new_items = 0
            for item in chat_ctx.items:
                if item.id in self._sent_item_ids:
                    continue
                logger.info(f"[Qwen] update_chat_ctx: new item type={item.type}")

                if item.type == "message" and getattr(item, "role", None) == "user":
                    # 发送用户消息到千问服务端
                    content = item.content
                    if isinstance(content, list):
                        parts = []
                        for c in content:
                            if isinstance(c, str):
                                parts.append(c)
                            else:
                                txt = getattr(c, "text", None)
                                if txt:
                                    parts.append(txt)
                        text = "".join(parts)
                    elif isinstance(content, str):
                        text = content
                    else:
                        text = str(content)

                    await self._send_json(self._ws_conn, {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": text}],
                        },
                    })
                    logger.info(f"[Qwen] sent user message: {text[:100]!r}")
                    self._sent_item_ids.add(item.id)
                    new_items += 1

                elif item.type == "function_call_output":
                    await self._send_json(self._ws_conn, {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": item.call_id,
                            "output": item.output,
                        },
                    })
                    logger.info(
                        f"[Qwen] sent function_call_output: "
                        f"call_id={item.call_id} output={item.output[:100]!r}"
                    )
                    self._sent_item_ids.add(item.id)
                    new_items += 1

            if new_items > 0:
                logger.info(
                    f"[Qwen] update_chat_ctx: synced {new_items} new items"
                )

    async def update_instructions(self, instructions: str) -> None:
        self._instructions = instructions

    def generate_reply(
        self,
        *,
        instructions: NotGivenOr[str] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        tools: NotGivenOr[list[llm.Tool]] = NOT_GIVEN,
    ) -> asyncio.Future[llm.GenerationCreatedEvent]:
        """触发千问服务端生成回复（文本输入或 function calling 后续回复）。"""
        fut = asyncio.Future[llm.GenerationCreatedEvent]()

        if getattr(self, "_ws_conn", None) is None or self._ws_conn.closed:
            fut.set_exception(
                llm.RealtimeError(
                    "QwenRealtimeSession.generate_reply: ws not ready"
                )
            )
            return fut

        # 有新的 instructions 时更新
        if utils.is_given(instructions):
            self._instructions = instructions

        # 关闭上一轮 generation（可能来自 function call 后被保留的旧 generation）
        if self._current_generation is not None:
            logger.info("[Qwen] generate_reply: closing previous generation")
            with contextlib.suppress(utils.aio.channel.ChanClosed):
                self._current_generation.function_ch.close()
            with contextlib.suppress(utils.aio.channel.ChanClosed):
                self._current_generation.message_ch.close()

        # 创建 generation + item
        self._text_buffer = ""
        self._current_generation = _ResponseGeneration(
            message_ch=utils.aio.Chan(),
            function_ch=utils.aio.Chan(),
            messages={},
            _created_timestamp=time.time(),
            _done_fut=asyncio.Future(),
        )

        generation_ev = llm.GenerationCreatedEvent(
            message_stream=self._current_generation.message_ch,
            function_stream=self._current_generation.function_ch,
            user_initiated=True,
        )

        item_id = utils.shortuuid()
        modalities_fut: asyncio.Future[list[Literal["text", "audio"]]] = (
            asyncio.Future()
        )
        self._current_item = _MessageGeneration(
            message_id=item_id,
            text_ch=utils.aio.Chan(),
            audio_ch=utils.aio.Chan(),
            modalities=modalities_fut,
        )
        if not self._realtime_model.capabilities.audio_output:
            self._current_item.audio_ch.close()
            self._current_item.modalities.set_result(["text"])  # type: ignore[union-attr]
        else:
            self._current_item.modalities.set_result(["audio", "text"])  # type: ignore[union-attr]

        with contextlib.suppress(utils.aio.channel.ChanClosed):
            self._current_generation.message_ch.send_nowait(
                llm.MessageGeneration(
                    message_id=item_id,
                    text_stream=self._current_item.text_ch,
                    audio_stream=self._current_item.audio_ch,
                    modalities=self._current_item.modalities,
                )
            )

        fut.set_result(generation_ev)

        # 异步触发千问 response.create
        ws_conn = self._ws_conn
        loop = asyncio.get_event_loop()
        loop.create_task(self._do_response_create(ws_conn, instructions))

        return fut

    async def _delayed_close_function_ch(
        self, gen: _ResponseGeneration
    ) -> None:
        """延迟关闭 function_ch，确保 tee 的两个 consumer 都有时间读取。"""
        await asyncio.sleep(0.5)
        with contextlib.suppress(utils.aio.channel.ChanClosed):
            gen.function_ch.close()
        logger.info("[Qwen] delayed close: function_ch closed")

    async def _do_response_create(
        self,
        ws_conn: aiohttp.ClientWebSocketResponse,
        instructions: NotGivenOr[str],
    ) -> None:
        try:
            msg: dict = {"type": "response.create"}
            if utils.is_given(instructions):
                msg["response"] = {"instructions": instructions}
            logger.info("[Qwen] sending response.create")
            await self._send_json(ws_conn, msg)
        except Exception:
            logger.error("[Qwen] response.create failed", exc_info=True)

    def interrupt(self) -> None:
        """取消正在进行的响应（打断）。"""
        if getattr(self, "_ws_conn", None) is not None and not self._ws_conn.closed:
            logger.info("[Qwen] sending response.cancel (interrupt)")
            loop = asyncio.get_event_loop()
            loop.create_task(
                self._send_json(self._ws_conn, {"type": "response.cancel"})
            )

        # 关闭当前输出 channels
        if self._current_item is not None:
            with contextlib.suppress(utils.aio.channel.ChanClosed):
                self._current_item.audio_ch.close()
            with contextlib.suppress(utils.aio.channel.ChanClosed):
                self._current_item.text_ch.close()

    def truncate(
        self,
        *,
        message_id: str,
        modalities: list[Literal["text", "audio"]],
        audio_end_ms: int,
        audio_transcript: NotGivenOr[str] = NOT_GIVEN,
    ) -> None:
        if "audio" in modalities:
            pass  # 千问未暴露远端音频截断
        elif utils.is_given(audio_transcript):
            chat_ctx = self.chat_ctx.copy()
            if (idx := chat_ctx.index_by_id(message_id)) is not None:
                new_item = copy.copy(chat_ctx.items[idx])
                assert new_item.type == "message"
                new_item.content = [audio_transcript]
                chat_ctx.items[idx] = new_item

    async def aclose(self) -> None:
        self._msg_ch.close()
        await self._main_atask

    # ------------------------------------------------------------------
    # 音频工具
    # ------------------------------------------------------------------

    def _resample_input_audio(self, frame: rtc.AudioFrame) -> Iterator[rtc.AudioFrame]:
        """将输入音频重采样到 16kHz mono（千问输入格式）。"""
        if self._input_resampler is not None:
            if frame.sample_rate != self._input_resampler._input_rate:
                self._input_resampler = None

        if self._input_resampler is None and (
            frame.sample_rate != QWEN_INPUT_SAMPLE_RATE
            or frame.num_channels != 1
        ):
            self._input_resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=QWEN_INPUT_SAMPLE_RATE,
                num_channels=1,
            )

        if self._input_resampler:
            yield from self._input_resampler.push(frame)
        else:
            yield frame

    def _resample_output_audio(
        self, frame: rtc.AudioFrame
    ) -> Iterator[rtc.AudioFrame]:
        """将千问输出（24kHz）重采样到 48kHz 供 LiveKit 使用。"""
        if not hasattr(self, "_output_resampler"):
            self._output_resampler: rtc.AudioResampler | None = None

        if self._output_resampler is not None:
            if frame.sample_rate != self._output_resampler._input_rate:
                self._output_resampler = None

        if self._output_resampler is None and frame.sample_rate != 48000:
            self._output_resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate,
                output_rate=48000,
                num_channels=1,
            )

        if self._output_resampler:
            yield from self._output_resampler.push(frame)
        else:
            yield frame

    def _emit_error(self, error: Exception, recoverable: bool) -> None:
        self.emit(
            "error",
            llm.RealtimeModelError(
                timestamp=time.time(),
                label=self._realtime_model._label,
                error=error,
                recoverable=recoverable,
            ),
        )

    # ------------------------------------------------------------------
    # Context properties
    # ------------------------------------------------------------------

    @property
    def chat_ctx(self) -> llm.ChatContext:
        return self._remote_chat_ctx.to_chat_ctx()

    @property
    def tools(self) -> llm.ToolContext:
        return self._tools.copy()
