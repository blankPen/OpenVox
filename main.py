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
from livekit.plugins import qwen, volcengine

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

# pipeline 模式中文语音日志（用户说了什么 + AI 回复了什么）：
# - STT 最终识别结果 → [用户语音] 标签（本节 patch）
# - LLM 每轮 assistant 文本 → [AI回复] 标签（下方 LLMStream patch）
#
# STT 的 _process_stream_event 在 FINAL_TRANSCRIPT 时把识别文本塞在
# logger.info(..., extra={"text": text}) 里，但 logging.basicConfig 的
# %(message)s 不会展开 extra 字段，导致控制台看不到用户说了什么。
# patch 思路：wrap _process_stream_event，原方法跑完后若为最终结果就记日志。
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

# volcengine 插件默认 INFO 级别只打 llm start / llm first response / llm end
# 这种事件，不打实际文本。patch 思路：wrap LLMStream._parse_choice 抓
# delta.content，run 结束后一次性把累积文本打到 volcengine-agent logger。
from livekit.plugins.volcengine.llm import LLMStream as _VolcLLMStream  # noqa: E402

_orig_llm_run = _VolcLLMStream._run


async def _patched_llm_run(self) -> None:  # type: ignore[no-untyped-def]
    _orig_parse = self._parse_choice
    text_parts: list[str] = []

    def _wrapped_parse(chunk_id, choice):
        delta = getattr(choice, "delta", None)
        if delta is not None:
            content = getattr(delta, "content", None)
            # 只累积字符串 content。LLM 有时会把 delta.content 设为 []（空 list）
            # 而非 None，过滤掉以免 "".join() 产生字面 "[]"。
            if isinstance(content, str) and content:
                text_parts.append(content)
        return _orig_parse(chunk_id, choice)

    self._parse_choice = _wrapped_parse  # type: ignore[method-assign]
    try:
        await _orig_llm_run(self)
    finally:
        self._parse_choice = _orig_parse  # type: ignore[method-assign]
        full_text = "".join(text_parts).strip()
        if full_text:
            logger.info(f"[AI回复] {full_text}")


_VolcLLMStream._run = _patched_llm_run  # type: ignore[assignment]

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

# 默认使用 realtime 模式，可通过 PIPELINE 切换：
#   "realtime"        → Volcengine 端到端语音
#   "pipeline"        → Volcengine STT/LLM/TTS 分离
#   "qwen-realtime"   → 千问 Qwen3.5-Omni 端到端语音（原生 function calling）
PIPELINE = os.environ.get("PIPELINE", "realtime")


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

        # 主动打招呼的策略：
        # - realtime / qwen-realtime 模式不在此调用 generate_reply(...)，
        #   因为 realtime 模型有自己的 opening 机制（volcengine 通过 hello_request，
        #   千问通过 response.create 触发开场白）。
        # - pipeline 模式是标准 chat-mode，generate_reply() 会触发 LLM 出
        #   一句开场白并经 TTS 合成广播，让客户端进房就能听到招呼声。
        if PIPELINE == "pipeline":
            logger.info("[Agent] (pipeline) 主动打招呼")
            await self.session.generate_reply()
        elif PIPELINE == "qwen-realtime":
            logger.info("[Agent] 小语(Qwen)进入房间，等待与用户交互")
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
        # Volcengine STT + LLM + TTS 分离管线
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

    if PIPELINE == "qwen-realtime":
        # Qwen3.5-Omni 端到端实时语音 — 原生支持 function calling。
        # semantic_vad 可过滤无意义语音，与 function calling 配合更自然。
        model = os.environ.get("QWEN_MODEL", "qwen3.5-omni-plus-realtime")
        voice = os.environ.get("QWEN_VOICE", "Tina")
        opening = os.environ.get("QWEN_OPENING") or "你好啊，今天过得怎么样？"
        logger.info(f"[Qwen] building session: model={model} voice={voice}")
        return AgentSession(
            llm=qwen.RealtimeModel(
                model=model,
                voice=voice,
                opening=opening,
                turn_detection_type="semantic_vad",
            ),
        )

    # Volcengine Realtime 端到端语音 — 不支持 function calling。
    # opening= 让 vendor 插件在 ws 连接就绪后自动发一句 hello_request，
    # 实现"进入房间主动打招呼"的效果。
    # 可选的联网搜索功能依赖独立的 Volcengine AI 联网搜索产品，
    # 需要在控制台中激活并把 API Key 填入 VOLCENGINE_WEBSEARCH_API_KEY。
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

    # 摘要：列清楚每类资源各加载了什么
    from agent_extensions import _tool_name  # type: ignore[attr-defined]
    tool_names = [_tool_name(t) for t in tools]
    skill_names = sorted(skills_registry)
    mcp_names = [getattr(s, "command", "?") for s in mcp_servers]
    logger.info("=" * 60)
    logger.info(f"[Agent] build_agent summary for {workspace_root}:")
    logger.info(f"[Agent]   persona  : {len(persona.combined)}c system prompt")
    logger.info(f"[Agent]   skills   : {len(skills_registry)} → {skill_names}")
    logger.info(f"[Agent]   mcp      : {len(mcp_servers)} → {mcp_names}")
    logger.info(f"[Agent]   tools    : {len(tools)} → {tool_names}")
    logger.info("=" * 60)
    return VolcengineAgent(
        instructions=persona.combined,
        tools=tools,
        mcp_servers=mcp_servers,
    )


async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker 启动后由调度器调用的主入口函数。"""
    logger.info(f"[Worker] 收到任务，正在加入房间: {ctx.room.name} (pipeline={PIPELINE})")

    session = _build_session()
    # room_input_options 传给 session.start()（livekit-agents 1.5+ 也可在 __init__ 中传入）

    await session.start(
        agent=build_agent(WORKSPACE_ROOT),
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

    # 注入 per-user 长期记忆（connect + 拿到 user_id 之后）
    from agent_memory import MemoryStore
    user_dir = WORKSPACE_ROOT / "users" / user_id
    memory = MemoryStore(user_dir)
    recall = memory.load_user_prompt()
    if recall:
        try:
            session.current_agent.update_chat_ctx(messages=[
                {"role": "system", "content": recall}
            ])
            # 摘要在 build_agent 那行已经打了，这里只标 [Memory]
            logger.info(
                f"[Memory] injected user={user_id} {len(recall)}c into chat ctx "
                f"(from {user_dir})"
            )
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


