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
# 注意：livekit-agents 1.2.x 的 cli/_run.py 模块用 `from .log import setup_logging`
# （局部导入），所以必须同时 monkey-patch 模块层和 _run 模块层才能生效。
# livekit-agents 1.5.x 把 cli/_run.py 合并进了 cli/cli.py，_run 子模块已不存在，
# 这里 try/except 兼容两种版本。
from livekit.agents.cli import log as _cli_log  # noqa: E402

_cli_log.setup_logging = lambda *args, **kwargs: None  # type: ignore[assignment]
try:
    # 1.2.x: cli/_run.py 是独立子模块，需要第二次 patch
    from livekit.agents.cli import _run as _cli_run  # noqa: E402

    _cli_run.setup_logging = lambda *args, **kwargs: None  # type: ignore[assignment]
except ImportError:
    # 1.5.x: cli/_run.py 已合并进 cli/cli.py，setup_logging 只剩一个入口
    pass

# vendored livekit-plugins-volcengine 的 utils.to_fnc_ctx() 调用
# ``ToolContext(fnc_ctx).parse_function_tools("openai")``，但这个方法是
# livekit-agents 1.5.x 才加的；当前 venv 装的是 1.2.9（CLAUDE.md 提到的
# 1.5.x 是 intent，但 venv 实际未升级）。如果不打这个 shim，pipeline 模式
# 的 LLM._run() 会在 to_fnc_ctx 处抛 AttributeError，整个 STT→LLM→TTS 链路
# 永远走不到 TTS。
#
# shim 用 inspect.signature() 反推每个 @function_tool 的参数类型，组装成
# OpenAI 的 ChatCompletionToolParam 格式。仅支持 "openai"（volcengine.LLM
# 是 OpenAI 兼容的，唯一调用方）。
import inspect as _inspect  # noqa: E402

from livekit.agents.llm import ToolContext as _ToolContext  # noqa: E402


def _patched_parse_function_tools(self, fmt: str) -> list:
    """1.5.x 的 ToolContext.parse_function_tools 的 1.2.9 兼容实现。

    仅实现 volcengine.LLM 实际调用的 ``fmt="openai"`` 分支：把
    ``self.function_tools`` 转成 ``[{"type": "function", "function": {name, description, parameters}}]``。
    """
    if fmt != "openai":
        # volcengine.LLM 只用 "openai"；其他格式直接返回空，触发框架的
        # "tool 不支持" 兜底（避免 AttributeError 把整个 LLM 调用搞挂）。
        return []

    result = []
    for _name, tool in self.function_tools.items():
        info = getattr(tool, "__livekit_tool_info", None)
        if info is None:
            continue
        parameters = {"type": "object", "properties": {}, "required": []}
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
                parameters = {"type": "object", "properties": properties, "required": required}
        except (TypeError, ValueError):
            # C 函数 / inspect 失败 → 保留默认空 schema（OpenAI 仍可接受）
            pass
        result.append({
            "type": "function",
            "function": {
                "name": info.name,
                "description": info.description or "",
                "parameters": parameters,
            },
        })
    return result


# 已经在 1.5.x 上时不需要这个 shim（已经原生有 parse_function_tools）
if not hasattr(_ToolContext, "parse_function_tools"):
    _ToolContext.parse_function_tools = _patched_parse_function_tools  # type: ignore[attr-defined]

# livekit-agents 1.2.9 的 ``function_arguments_to_pydantic_model`` 把带默认值
# 的参数（如 ``read_file(path: str, start_line: int = 0)``）错误地标记为必填，
# 因为它把 ``FieldInfo.default`` 设到 Pydantic FieldInfo 实例上，但 Pydantic v2
# 的 ``create_model`` 不会从 ``FieldInfo.default`` 读默认值。结果：LLM 按 schema
# 只传 ``path``，框架的 prepare_function_arguments 在 Pydantic validation 时
# 抛 ValidationError → 工具调用被拒 → LLM 重试 → 失败，最终回退为 ``[]`` 文本。
# shim 用 ``(type, default_or_ellipsis)`` tuple 语法重写 model 构造，匹配
# Pydantic v2 create_model 的实际约定。
import inspect as _inspect_p  # noqa: E402
from pydantic import create_model as _create_model  # noqa: E402
from pydantic.fields import Field as _PField  # noqa: E402
from pydantic_core import PydanticUndefined as _PUndef  # noqa: E402
from livekit.agents.llm import utils as _llm_utils  # noqa: E402

if not hasattr(_llm_utils, "_PATCHED_FAPM"):
    _orig_fapm = _llm_utils.function_arguments_to_pydantic_model  # type: ignore[attr-defined]

    def _patched_fapm(func):  # type: ignore[no-untyped-def]
        """1.2.9 → 1.5.x 兼容：default 参数生成的 Pydantic field 也标记为 optional。"""
        from docstring_parser import parse_from_object  # noqa: PLC0415

        fnc_names = func.__name__.split("_")
        fnc_name = "".join(x.capitalize() for x in fnc_names)
        model_name = fnc_name + "Args"
        docstring = parse_from_object(func)
        param_docs = {p.arg_name: p.description for p in docstring.params}
        signature = _inspect_p.signature(func)

        fields: dict = {}
        for param_name, param in signature.parameters.items():
            # 用 param.annotation 而非 typing.get_type_hints（避免 _inspect_p 的 stub 问题）
            type_hint = param.annotation
            if type_hint is _inspect_p.Parameter.empty:
                continue
            if _llm_utils.is_context_type(type_hint):  # type: ignore[attr-defined]
                continue
            default_value = param.default if param.default is not param.empty else _inspect_p.Parameter.empty
            fi = _PField()
            if default_value is not _inspect_p.Parameter.empty and fi.default is _PUndef:
                fi.default = default_value
            if fi.description is None:
                fi.description = param_docs.get(param_name, None)
            # 关键：把 default 直接作为 tuple 第二项，而不是放在 FieldInfo.default。
            # Pydantic v2 create_model 只认这种形式。
            if default_value is not _inspect_p.Parameter.empty:
                fields[param_name] = (type_hint, default_value)
            else:
                fields[param_name] = (type_hint, ...)
        return _create_model(model_name, **fields)

    _llm_utils.function_arguments_to_pydantic_model = _patched_fapm  # type: ignore[assignment]
    _llm_utils._PATCHED_FAPM = True  # type: ignore[attr-defined]

# 把 volcengine.LLM 每轮 assistant 文本作为 [LLM-TEXT] 标记打日志，
# 方便 E2E 测试和事后排查用 grep 抓关键词。volcengine 插件默认 INFO 级别只
# 打 llm start / llm first response / llm end 这种事件，不打实际文本。
# patch 思路：wrap LLMStream._parse_choice 抓 delta.content，run 结束后
# 一次性把累积文本打到 volcengine-agent logger。
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
            logger.info(f"[LLM-TEXT] {full_text}")


_VolcLLMStream._run = _patched_llm_run  # type: ignore[assignment]

# 阻止子进程的 root logger 同时走 IPC 和 stdout（造成每条日志重复输出）。
# LiveKit fork 出子进程后，proc_client.initialize_logger() 会把 root logger
# 设为 NOTSET 并 addHandler(LogQueueHandler) — 但**不**移除我们从主进程继承的
# StreamHandler。子进程就会通过 stdout 输出一次，再通过 IPC 发回主进程输出一次。
# 修复：monkey-patch 让 initialize_logger 先移除 root logger 的所有 StreamHandler
# 再加 IPC handler；这样日志只在主进程输出一次。
import logging as _logging  # noqa: E402
from livekit.agents.ipc import proc_client as _proc_client  # noqa: E402

# livekit-agents 1.5+ 移除了 _ProcClient.initialize_logger（logger 初始化逻辑
# 已经合并到 cli.setup_logging 路径）。这段 patch 是 1.2.9 时代的兼容代码，1.5+
# 不需要再处理。
if hasattr(_proc_client._ProcClient, "initialize_logger"):
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

        # 主动打招呼的策略：
        # - realtime 模式不在此调用 generate_reply(...)，因为 vendor 的
        #   RealtimeSession.generate_reply 是占位实现
        #   （vendor/.../realtime.py:824），5 秒后必抛 RealtimeError。
        #   realtime 改由 RealtimeModel(opening=) 在 vendor WebSocket 层
        #   主动发 hello_request（vendor/.../realtime.py:457）。
        # - pipeline 模式是标准 chat-mode，generate_reply() 会触发 LLM 出
        #   一句开场白并经 TTS 合成广播，让客户端进房就能听到招呼声。
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


