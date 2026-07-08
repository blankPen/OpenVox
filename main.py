"""LiveKit Agent 与 Volcengine（火山引擎）语音服务集成的入口。

本模块支持两种运行模式，由环境变量 ``PIPELINE`` 选择：

* ``"realtime"`` — 端到端语音模型，只需填写 ``VOLCENGINE_REALTIME_*``
  相关凭据。
* ``"pipeline"`` — 分离的 STT + LLM + TTS 模型 ，需要分别填写
  三套凭据并额外提供 ``VOLCENGINE_LLM_API_KEY``。

程序启动时会从 ``.env`` 加载环境变量，方便本地开发和部署。
"""

from __future__ import annotations
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.agents.voice.room_io import RoomInputOptions, TextInputEvent
from livekit.plugins import volcengine, openai

# 从 .env 文件中加载环境变量，覆盖当前进程环境。仅在开发和本地运行时使用。
load_dotenv()

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

logger = logging.getLogger("volcengine-agent")

# 当前仅支持 PIPELINE="pipeline"：
#   火山引擎 STT/TTS + 指向 Hermes api_server 的 openai.LLM
# 历史的多 PIPELINE 分支已合并清理，仅保留 pipeline 一种。
PIPELINE = os.environ.get("PIPELINE", "pipeline")


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
    指向 Hermes api_server 的 ``openai.LLM``。Hermes gateway 自带
    OpenAI 兼容 HTTP server（端口 8642），不需要本地桥接服务。

    历史的多 PIPELINE 分支已合并清理；要扩展新管线请直接在
    ``if PIPELINE != "pipeline"`` 处加 ValueError 之外的入口。
    """
    if PIPELINE != "pipeline":
        raise ValueError(
            f"Unsupported PIPELINE={PIPELINE!r}; only 'pipeline' is supported"
        )

    # 火山引擎 STT + 指向 Hermes api_server 的 OpenAI 兼容 LLM + 火山 TTS
    return AgentSession(
        stt=volcengine.STT(
            app_id=os.environ["VOLCENGINE_STT_APP_ID"],
            access_token=os.environ["VOLCENGINE_STT_ACCESS_TOKEN"],
        ),
        llm=openai.LLM(
            base_url=os.environ["BRIDGE_BASE_URL"],
            api_key=os.environ["BRIDGE_API_KEY"],
            extra_headers={
                "X-LiveKit-Room": os.environ["LIVEKIT_ROOM_NAME"],
                "X-LiveKit-User": os.environ.get("_OPENCZ_USER_ID", ""),
            },
        ),
        tts=volcengine.TTS(
            app_id=os.environ["VOLCENGINE_TTS_APP_ID"],
            access_token=os.environ["VOLCENGINE_TTS_ACCESS_TOKEN"],
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
    os.environ["_OPENCZ_USER_ID"] = user_id
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
            # 如果需要部署多个不同 pipeline 的 worker，可通过环境变量
            # AGENT_NAME 覆盖该默认值。
            agent_name=os.environ.get("AGENT_NAME", "volcengine-agent"),
        )
    )


