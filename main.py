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
from livekit.plugins import volcengine

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
# 注意：_run.py 用的是 `from .log import setup_logging`（局部导入），所以必须
# 同时 monkey-patch 模块层和 _run 模块层才能生效。
from livekit.agents.cli import log as _cli_log  # noqa: E402
from livekit.agents.cli import _run as _cli_run  # noqa: E402

_cli_log.setup_logging = lambda *args, **kwargs: None  # type: ignore[assignment]
_cli_run.setup_logging = lambda *args, **kwargs: None  # type: ignore[assignment]

# 阻止子进程的 root logger 同时走 IPC 和 stdout（造成每条日志重复输出）。
# LiveKit fork 出子进程后，proc_client.initialize_logger() 会把 root logger
# 设为 NOTSET 并 addHandler(LogQueueHandler) — 但**不**移除我们从主进程继承的
# StreamHandler。子进程就会通过 stdout 输出一次，再通过 IPC 发回主进程输出一次。
# 修复：monkey-patch 让 initialize_logger 先移除 root logger 的所有 StreamHandler
# 再加 IPC handler；这样日志只在主进程输出一次。
import logging as _logging  # noqa: E402
from livekit.agents.ipc import proc_client as _proc_client  # noqa: E402

_orig_init_logger = _proc_client._ProcClient.initialize_logger


def _patched_init_logger(self) -> None:  # type: ignore[no-untyped-def]
    # 移除从主进程继承的所有 StreamHandler（保留其他 handler 类型）
    root_logger = _logging.getLogger()
    for h in list(root_logger.handlers):
        if isinstance(h, _logging.StreamHandler):
            root_logger.removeHandler(h)
    _orig_init_logger(self)


_proc_client._ProcClient.initialize_logger = _patched_init_logger  # type: ignore[assignment]

logger = logging.getLogger("volcengine-agent")

# ---------------------------------------------------------------------------
# Agent extensibility 资源根
# ---------------------------------------------------------------------------
# workspace/ 是 agent 的"家目录"：persona/skills/extensions/users/sandbox 都放这里。
# 把 workspace/ 加到 sys.path 顶，让 agent_persona / agent_skills / agent_extensions
# / agent_memory 这些模块可以直接 import。
import sys as _sys
from pathlib import Path as _Path

WORKSPACE_ROOT = _Path(__file__).parent / "workspace"
if str(WORKSPACE_ROOT) not in _sys.path:
    _sys.path.insert(0, str(WORKSPACE_ROOT))

# load_skill() 工具需要拿到当前 session 才能调 update_chat_ctx。
# 用模块级 holder 共享：build_agent() 写入 closure 读，on_enter() 写入 holder。
# （v0.1 简化实现；v0.2 改成把 session_provider 注入到 agent 实例属性）
_session_holder: list[AgentSession | None] = [None]

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

    def __init__(
        self,
        *,
        instructions: str | None = None,
        tools: list | None = None,
        mcp_servers: list | None = None,
    ) -> None:
        super().__init__(
            instructions=instructions or (
                "你是一个友好的中文语音助手，名字叫小语。"
                "请用简洁、自然的口吻回答用户的问题，"
                "避免使用表情符号、Markdown 或特殊符号。"
            ),
            tools=tools or [],
            mcp_servers=mcp_servers or [],
        )

    async def on_enter(self) -> None:
        # 把 self.session 暴露给 build_agent() 的 session_provider
        _session_holder[0] = self.session

        # 不在此调用 self.session.generate_reply(...)：vendor 插件的
        # RealtimeSession.generate_reply 是占位实现（vendor/.../realtime.py:824），
        # 5 秒后必抛 RealtimeError，会让框架关闭 session，导致客户端被踢。
        # 进房主动打招呼改由 RealtimeModel 的 opening= 参数在 vendor 的
        # _run_ws 启动路径里主动发 hello_request（vendor/.../realtime.py:457）。
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
    # opening= 让 vendor 插件在 ws 连接就绪后自动发一句 hello_request，
    # 实现"进入房间主动打招呼"的效果；这条路径是 vendor 内部完整实现的
    # （vendor/.../realtime.py:457-487），而 vendor 的 generate_reply 是
    # 未完成的占位实现（vendor/.../realtime.py:824），必须避开。
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
            opening="你好啊，今天过得怎么样？",
            enable_volc_websearch=_bool_env("VOLCENGINE_ENABLE_WEBSEARCH", False),
            volc_websearch_api_key=os.environ.get("VOLCENGINE_WEBSEARCH_API_KEY") or None,
            volc_websearch_no_result_message="我再想想怎么回答你。",
        ),
    )


# ---------------------------------------------------------------------------
# Worker 入口
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agent 工厂 — 从 workspace/ 装配 VolcengineAgent
# ---------------------------------------------------------------------------


def build_agent(workspace_root: _Path) -> Agent:
    """Assemble a VolcengineAgent from the 4 workspace modules.

    Called per-dispatch in entrypoint() with the real workspace root.
    Order: persona → skills registry → mcp servers → tools → load_skill.
    """
    from agent_persona import load_persona
    from agent_skills import scan_skills, make_load_skill_tool
    from agent_extensions import load_tools, load_mcp_servers

    persona = load_persona(workspace_root)
    skills_registry = scan_skills(workspace_root / "skills")
    mcp_servers = load_mcp_servers(workspace_root / "extensions" / "mcp")
    tools = load_tools(workspace_root / "extensions" / "tools")

    # load_skill 需要 session → 用模块级 _session_holder 跟 on_enter 共享
    def session_provider() -> AgentSession:
        assert _session_holder[0] is not None, "load_skill called before session started"
        return _session_holder[0]

    load_skill = make_load_skill_tool(skills_registry, session_provider)
    tools.append(load_skill)

    logger.info(
        f"[Agent] build_agent: tools={len(tools)}, skills={len(skills_registry)}, "
        f"mcp_servers={len(mcp_servers)}"
    )
    return VolcengineAgent(
        instructions=persona.combined,
        tools=tools,
        mcp_servers=mcp_servers,
    )


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker 启动后由调度器调用的主入口函数。"""
    logger.info(f"[Worker] 收到任务，正在加入房间: {ctx.room.name} (pipeline={PIPELINE})")

    session = _build_session()
    # 注意：room_input_options 必须传给 session.start()，不是 AgentSession.__init__()
    # livekit-agents 1.2.9 的 __init__ 不接受此参数；1.5+ 才移到 __init__

    await session.start(
        agent=build_agent(WORKSPACE_ROOT),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            text_input_cb=_custom_text_input_cb,
        ),
    )

    # Connect 后 remote_participants 才可见
    await ctx.connect()
    logger.info(f"[Worker] 已连接到房间: {ctx.room.name}")

    # 等远端参与者加入（fake_alice / 真客户端 / 模拟客户端）
    import asyncio
    deadline = asyncio.get_event_loop().time() + 15
    while not ctx.room.remote_participants:
        if asyncio.get_event_loop().time() > deadline:
            logger.warning("[Worker] 15s 内无远端参与者")
            return
        await asyncio.sleep(0.1)
    first = next(iter(ctx.room.remote_participants.values()))
    user_id = first.identity
    os.environ["_OPENCZ_USER_ID"] = user_id
    logger.info(f"[Worker] user_id={user_id}")

    # 注入 per-user 长期记忆（connect + 拿到 user_id 之后）
    from agent_memory import MemoryStore
    memory = MemoryStore(WORKSPACE_ROOT / "users" / user_id)
    recall = memory.load_user_prompt()
    if recall:
        try:
            session.current_agent.update_chat_ctx(messages=[
                {"role": "system", "content": recall}
            ])
            logger.info(f"[Memory] 注入 user={user_id} ({len(recall)} chars)")
        except Exception as e:
            logger.warning(f"[Memory] 注入失败: {e}")


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


