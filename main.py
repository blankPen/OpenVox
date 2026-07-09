"""LiveKit Agent 与 Volcengine（火山引擎）语音服务集成的入口。

当前唯一支持的运行模式是 PIPELINE=pipeline：火山引擎 STT + LLM（经本地
bridge 打到 Hermes api_server）+ 火山引擎 TTS。

配置从 ``~/.openvox/config.json`` 读取（schema 见 config.py 模块头注释）。路径
可通过 OPENVOX_CONFIG 环境变量覆盖，主要供测试使用。模块导入即读一次。
"""

from __future__ import annotations
import asyncio
import logging
import os
import sys

from config import get_config

# ───────── Hermes 兼容补丁 ─────────
# livekit-plugins-openai 的 inference/llm.py:432 写的是
#   for choice in chunk.choices:
# 火山引擎 Hermes 网关在 stream_options.include_usage=True 时发的 usage-only
# chunk 形如 {"choices": null, "usage": {...}}，触发 TypeError 把每次 LLM 调用
# 尾巴炸掉。临时在 openai SDK 入口包一层流过滤器把这种块丢掉；上游修好后整段删。
import openai as _openai_sdk
from openai.resources.chat.completions import AsyncCompletions as _AsyncCompletions

_orig_create = _AsyncCompletions.create


class _FilterNoneChoices:
    """透传 stream，但跳过 chunk.choices 为 None 的帧（Hermes usage-only 块）。"""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __aiter__(self) -> "_FilterNoneChoices":
        return self

    async def __anext__(self):
        async for chunk in self._inner:
            if chunk.choices is not None:
                return chunk
        raise StopAsyncIteration

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()

    async def __aenter__(self) -> "_FilterNoneChoices":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()


async def _safe_create(self, **kwargs):
    inner = await _orig_create(self, **kwargs)
    if kwargs.get("stream"):
        return _FilterNoneChoices(inner)
    return inner


_AsyncCompletions.create = _safe_create
# ───────── 补丁结束 ─────────

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents.voice.room_io import RoomInputOptions, TextInputEvent
from livekit.plugins import volcengine, openai

_cfg = get_config()

# 配置日志输出到 stdout，便于在控制台观察完整对话过程。
# 日志格式：时间 | 级别 | logger 名 | 消息
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d | %(levelname)-5s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
    force=True,  # 覆盖之前可能设置的 handler
)
# 静音过于冗长的第三方 logger（按需调整）
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
logging.getLogger("livekit_api").setLevel(logging.WARNING)

# 阻止 LiveKit 的 cli.log.setup_logging 在 start/dev 命令中再次添加 JSON handler，
# 否则每条日志会打印两次（一次我们的格式，一次 JSON）。
# 我们已经手动设置了 logging.basicConfig，所以让 setup_logging 什么都不做。
from livekit.agents.cli import log as _cli_log  # noqa: E402

_cli_log.setup_logging = lambda *args, **kwargs: None  # type: ignore[assignment]

# pipeline 模式中文语音日志：STT 最终识别结果 → [用户语音] 标签。
#
# STT 的 _process_stream_event 在 FINAL_TRANSCRIPT 时把识别文本塞在
# logger.info(..., extra={"text": text}) 里，但 logging.basicConfig 的
# %(message)s 不会展开 extra 字段，导致控制台看不到用户说了什么。
# patch 思路：wrap _process_stream_event，原方法跑完后若为最终结果就记日志。
# LLM 文本日志：openai.LLM 走标准 OpenAI 流式 chunk，控制台可直接看见，
# 不再需要自定义 patch。
from livekit.plugins.volcengine.stt import SpeechStream as _VolcSTTSpeechStream, parse_response as _stt_parse_response  # noqa: E402

_orig_stt_process = _VolcSTTSpeechStream._process_stream_event


def _patched_stt_process(self, data: dict) -> None:
    _orig_stt_process(self, data)
    # 从已解析的响应中提取最终识别文本（仅在 definite=True 时才是最终结果）
    try:
        payload = _stt_parse_response(data).get("payload_msg", {})
        result = payload.get("result", None)
        if result is None:
            return
        text = result.get("text", "")
        utterances = result.get("utterances", [])
        if text and utterances and utterances[0].get("definite", False):
            logger.info(f"[用户语音] {text}")
    except Exception:
        pass  # 日志不应该影响主流程


_VolcSTTSpeechStream._process_stream_event = _patched_stt_process  # type: ignore[assignment]

logger = logging.getLogger("openvox-agent")

# 当前唯一支持的 PIPELINE 值是 "pipeline"。读 config 而非环境变量；多分支
# 历史已合并清理，要扩展新管线直接在 _build_session 入口加 ValueError 之外的分支。
PIPELINE: str = _cfg.require("pipeline")


# ---------------------------------------------------------------------------
# Agent 定义
# ---------------------------------------------------------------------------


class VolcengineAgent(Agent):
    """基于 Volcengine 模型的中文语音助手。

    该代理使用中文指令初始化，尽量以简洁自然的方式回答用户问题。
    """

    def __init__(
        self,
        *,
        instructions: str | None = None,
    ) -> None:
        super().__init__(
            instructions=instructions or (
                "你是一个友好的中文语音助手，名字叫小语。"
                "请用简洁、自然的口吻回答用户的问题，"
                "避免使用表情符号、Markdown 或特殊符号。"
            ),
        )

    async def on_enter(self) -> None:
        # 主动打招呼的策略：当前仅 pipeline 模式 — generate_reply() 触发 LLM 出
        # 一句开场白并经 TTS 合成广播，让客户端进房就能听到招呼声。
        if PIPELINE == "pipeline":
            logger.info("[Agent] (pipeline) 主动打招呼")
            await self.session.generate_reply()
        else:
            logger.info("[Agent] 小语进入房间，等待与用户交互")


# ---------------------------------------------------------------------------
# 自定义回调：覆盖 LiveKit 框架默认的文本输入回调，加中文日志
# ---------------------------------------------------------------------------


def _custom_text_input_cb(sess: AgentSession, ev: TextInputEvent) -> None:
    """客户端通过 DataChannel (TOPIC_CHAT) 发送文本消息时被调用。

    LiveKit 默认实现是 sess.interrupt() + sess.generate_reply(user_input=ev.text)，
    这里保留同样的语义，仅加上中文日志便于在控制台观察对话过程。
    """
    logger.info(f"[文本] 收到客户端消息: {ev.text!r}")
    sess.interrupt()
    sess.generate_reply(user_input=ev.text)
    logger.info("[文本] 已将消息发送给 agent 触发回复")


# ---------------------------------------------------------------------------
# 会话构建工厂 — 唯一支持的 PIPELINE="pipeline"
# ---------------------------------------------------------------------------


def _prewarm(proc) -> AgentSession:
    """Worker 启动时的预热入口。

    LiveKit 会将监督进程对象传入该函数，但我们不需要使用它。
    只需在 worker 启动时提前构建模型会话，避免第一个房间出现冷启动延迟。
    """
    return _build_session()


def _build_session() -> AgentSession:
    """构建 ``AgentSession``。

    当前唯一支持的 PIPELINE 是 ``"pipeline"`` —— 火山引擎 STT/TTS 配
    ``openai.LLM``。LLM 通过 ``bridge.base_url``（默认 :8765）打到
    ``scripts/bridge_server.py``，bridge 再透传到 Hermes gateway 自带的
    OpenAI 兼容 api_server（:8642，``hermes.api_base``）。
    """
    if PIPELINE != "pipeline":
        raise ValueError(
            f"Unsupported PIPELINE={PIPELINE!r}; only 'pipeline' is supported"
        )

    # _OPENVOX_USER_ID 是 entrypoint() 在远端参与者 join 时写进 os.environ 的
    # 运行时状态，不是 config；build_session() 在 worker 启动早期就可能被
    # 调一次（_prewarm），此时 user_id 为空，符合预期。
    user_id = os.environ.get("_OPENVOX_USER_ID", "")

    return AgentSession(
        stt=volcengine.STT(
            app_id=_cfg.require("volcengine.stt.app_id"),
            access_token=_cfg.require("volcengine.stt.access_token"),
        ),
        llm=openai.LLM(
            model=_cfg.require("bridge.model"),
            base_url=_cfg.require("bridge.base_url"),
            api_key=_cfg.require("bridge.api_key"),
            extra_headers={
                "X-LiveKit-Room": _cfg.require("bridge.livekit_room_name"),
                "X-LiveKit-User": user_id,
            },
        ),
        tts=volcengine.TTS(
            app_id=_cfg.require("volcengine.tts.app_id"),
            access_token=_cfg.require("volcengine.tts.access_token"),
        ),
    )


# ---------------------------------------------------------------------------
# Worker 入口
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker 启动后由调度器调用的主入口函数。"""
    logger.info(f"[Worker] 收到任务，正在加入房间: {ctx.room.name} (pipeline={PIPELINE})")

    session = _build_session()
    # room_input_options 传给 session.start()（livekit-agents 1.5+ 也可在 __init__ 中传入）

    await session.start(
        agent=VolcengineAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            text_input_cb=_custom_text_input_cb,
        ),
    )

    # Connect 后 remote_participants 才可见
    # 用事件而不是轮询，避免 worker process 重启时卡 15s
    user_id_future: asyncio.Future[str] = asyncio.get_event_loop().create_future()

    @ctx.room.on("participant_connected")
    def _on_participant_connected(participant):
        if participant.identity == ctx.room.local_participant.identity:
            return  # skip self
        if not user_id_future.done():
            user_id_future.set_result(participant.identity)
            logger.info(f"[Worker] participant_connected event: identity={participant.identity}")

    await ctx.connect()
    logger.info(f"[Worker] 已连接到房间: {ctx.room.name}")

    # 关键：participant_connected 事件**不会**对 agent join 之前已在房里的
    # 远端触发（典型场景：test 先 dispatch 再 connect，agent 后 join），
    # 所以 ctx.connect() 之后立刻看 remote_participants。
    if ctx.room.remote_participants:
        first = next(iter(ctx.room.remote_participants.values()))
        if not user_id_future.done():
            user_id_future.set_result(first.identity)
            logger.info(
                f"[Worker] participant already present: identity={first.identity} "
                f"(skipping wait_for event)"
            )

    # 等远端参与者加入（带超时但不卡事件循环）
    try:
        user_id = await asyncio.wait_for(user_id_future, timeout=20.0)
    except asyncio.TimeoutError:
        logger.warning("[Worker] 20s 内无远端参与者")
        return
    # _OPENVOX_USER_ID 是 session 级运行时状态而非 config——后续 LLM 调用会
    # 通过 os.environ["_OPENVOX_USER_ID"] 读到。再次 build_session() 才能拿到。
    os.environ["_OPENVOX_USER_ID"] = user_id
    logger.info(f"[Worker] user_id={user_id}")


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # 在 worker 启动时预热一次模型，避免第一个房间承担冷启动开销。
            # LiveKit 会把监督进程对象作为参数传入 prewarm_fnc，
            # 这里仅返回构建好的会话对象即可。
            prewarm_fnc=_prewarm,
            # 明确指定 agent_name，方便 LiveKit Dispatch API 定向本 worker。
            agent_name=_cfg.require("livekit.agent_name"),
        )
    )