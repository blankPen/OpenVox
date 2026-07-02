"""LiveKit Agent 与 Volcengine（火山引擎）语音服务集成的入口。

本模块支持两种运行模式，由环境变量 ``PIPELINE`` 选择：

* ``"realtime"`` — 端到端语音模型，只需填写 ``VOLCENGINE_REALTIME_*``
  相关凭据。
* ``"pipeline"`` — 分离的 STT + LLM + TTS 模型 ，需要分别填写
  三套凭据并额外提供 ``VOLCENGINE_LLM_API_KEY``。

程序启动时会从 ``.env`` 加载环境变量，方便本地开发和部署。
"""

from __future__ import annotations
``
import asyncio
import logging
import os

from dotenv import load_dotenv

from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import volcengine

# 从 .env 文件中加载环境变量，覆盖当前进程环境。仅在开发和本地运行时使用。
load_dotenv()

logger = logging.getLogger("volcengine-agent")

# 默认使用 realtime 模式，可通过 PIPELINE=pipeline 切换到分离 STT/LLM/TTS 模式。
PIPELINE = os.environ.get("PIPELINE", "realtime")  # "realtime" 或 "pipeline"


def _bool_env(name: str, default: bool) -> bool:
    """将环境变量解析为布尔值。

    支持的真值字符串包括："1"、"true"、"yes"、"on"。
    其它值会被视为 False。如果变量未设置，则返回默认值。
    """
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Agent 定义
# ---------------------------------------------------------------------------


class VolcengineAgent(Agent):
    """基于 Volcengine 模型的中文语音助手。

    该代理使用中文指令初始化，尽量以简洁自然的方式回答用户问题。
    """

    def __init__(self) -> None:
        super().__init__(
            instructions=(
                "你是一个友好的中文语音助手，名字叫小语。"
                "请用简洁、自然的口吻回答用户的问题，"
                "避免使用表情符号、Markdown 或特殊符号。"
            )
        )

    async def on_enter(self) -> None:
        # 当机器人进入房间时，生成一句简短自我介绍。
        self.session.generate_reply(
            instructions="用一句话向用户问好并介绍自己。"
        )


# ---------------------------------------------------------------------------
# 会话构建工厂 — 根据 PIPELINE 选择 Realtime 或 分离 STT/LLM/TTS
# ---------------------------------------------------------------------------


def _prewarm(proc) -> AgentSession:
    """Worker 启动时的预热入口。

    LiveKit 会将监督进程对象传入该函数，但我们不需要使用它。
    只需在 worker 启动时提前构建模型会话，避免第一个房间出现冷启动延迟。
    """
    return _build_session()


def _build_session() -> AgentSession:
    """根据当前 PIPELINE 配置构建 AgentSession。"""
    if PIPELINE == "pipeline":
        # 方案 B：将语音识别、语言模型、语音合成分离成独立组件。
        return AgentSession(
            stt=volcengine.STT(
                app_id=os.environ["VOLCENGINE_STT_APP_ID"],
                access_token=os.environ["VOLCENGINE_STT_ACCESS_TOKEN"],
            ),
            llm=volcengine.LLM(
                model="doubao-1-5-pro-32k-250115",
                api_key=os.environ["VOLCENGINE_LLM_API_KEY"],
            ),
            tts=volcengine.TTS(
                app_id=os.environ["VOLCENGINE_TTS_APP_ID"],
                access_token=os.environ["VOLCENGINE_TTS_ACCESS_TOKEN"],
            ),
        )

    # 方案 A：使用 Volcengine Realtime 端到端语音模型。
    # RealtimeModel 需要 bot_name 来标识语音代理的角色。
    #
    # 可选的联网搜索功能依赖独立的 Volcengine AI 联网搜索产品，
    # 需要在控制台中激活并把 API Key 填入 VOLCENGINE_WEBSEARCH_API_KEY。
    # 如果不启用或未提供该 Key，agent 会回退到离线训练知识。
    return AgentSession(
        llm=volcengine.RealtimeModel(
            app_id=os.environ["VOLCENGINE_REALTIME_APP_ID"],
            access_token=os.environ["VOLCENGINE_REALTIME_ACCESS_TOKEN"],
            bot_name="小语",
            model="O",
            enable_volc_websearch=_bool_env("VOLCENGINE_ENABLE_WEBSEARCH", False),
            volc_websearch_api_key=os.environ.get("VOLCENGINE_WEBSEARCH_API_KEY") or None,
            volc_websearch_no_result_message="我再想想怎么回答你。",
        ),
    )


# ---------------------------------------------------------------------------
# Worker 入口
# ---------------------------------------------------------------------------


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker 启动后由调度器调用的主入口函数。"""
    logger.info(f"Joining room {ctx.room.name} (pipeline={PIPELINE})")

    session = _build_session()
    await session.start(agent=VolcengineAgent(), room=ctx.room)

    # 直到房间断开连接之前，保持会话运行。
    await ctx.connect()


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


