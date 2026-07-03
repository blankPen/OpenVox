# LiveKit × Volcengine Agent 扩展性架构设计

> **状态**：草案 v0.1
> **日期**：2026-07-04
> **范围**：仅输出架构文档，不动 Python 实现

---

## 1. 背景与目标

### 1.1 当前状态

`/Users/pz/workspace/livekit` 是对接火山引擎（Volcengine）语音服务的 LiveKit Agents worker。`main.py` 中 `VolcengineAgent` 当前只使用了 `Agent` 基类的 `instructions` 和 `on_enter` 两个能力，约发挥出 `livekit-agents` 框架全部能力的 1/10。

框架实际上已经支持 function tools、MCP、生命周期 hooks、ChatContext 动态注入、userdata、多 agent 编排等扩展点（详见 `docs/agent-capabilities-extension.md` 已有的 10 项扩展点清单），但本项目一个都没接。

### 1.2 重构目标

建立一个**"想给 agent 加 X 能力时该往哪里动手"**的清晰目录与加载机制，使以下三类新增能力可以**零注册代码**完成：

1. **Tool 扩展** — bash、自定义 Python 函数、MCP server
2. **Memory 系统** — 跨会话的 per-user 长期记忆（Markdown 多层）
3. **Persona / Workspace** — 可读可改的角色与工作沙箱配置

### 1.3 关键约束

- **v0.1 不动实现**：本 spec 只画架构、定义接口、列扩展清单
- **方案选择**：约定优于配置（自动扫描），与 Claude Code 风格一脉相承
- **既有能力保留**：`main.py` 的 log 去重 patch、`_build_session` 双模式（realtime / pipeline）、`prewarm_fnc`、vendored 火山引擎插件全部不动
- **范围**：本 spec 不涉及多租户、RBAC、向量库、远程 MCP

---

## 2. 目录布局

```
/Users/pz/workspace/livekit/
├── main.py                     # 现有：worker 入口、log patch、session 工厂
├── workspace/                  # 新增：agent 资源根（git 跟踪）
│   ├── persona/                # agent 级 prompt 注入（worker 启动时全量读）
│   │   ├── SOUL.md             # 人格、口吻、价值观
│   │   ├── AGENTS.md           # 行为规则、边界、禁忌
│   │   └── TOOLS.md            # 工具使用元说明（给 agent 自己看）
│   ├── skills/                 # Claude Code 风格 SKILL 包
│   │   └── <name>/
│   │       ├── SKILL.md        # 必填：frontmatter(name,description) + body
│   │       └── scripts/        # 可选：被 bash 工具调用的脚本
│   ├── extensions/             # 全局工具与 MCP
│   │   ├── tools/              # Python @function_tool 实现（glob import）
│   │   │   ├── __init__.py
│   │   │   ├── bash.py
│   │   │   ├── memory_recall.py
│   │   │   └── ...
│   │   └── mcp/                # MCP server 配置（每个一个 .json）
│   │       └── <name>.json     # {command, args, env?, transport:"stdio"}
│   ├── users/                  # agent 级 cache，按 user_id 切分
│   │   └── <participant_identity>/
│   │       ├── User.md         # 用户画像（启动时全量读）
│   │       ├── MEMORY.md       # 长期精选事实（启动时全量读）
│   │       └── memory/
│   │           ├── 2026-07-04.md   # 一天一文件，多 session 合并
│   │           ├── 2026-07-03.md
│   │           └── ...
│   └── sandbox/                # bash tool 工作根（gitignore）
│       └── .gitkeep
└── ...（其余文件不动）
```

### 2.1 关键约定

- **`workspace/` 是资源/数据根，不是 Python 包**：`persona.py`、`loaders.py` 也放进去，但用 `sys.path.insert(0, workspace)` 注入
- **每个目录由唯一模块拥有**：`persona.py` 不读 `skills/`，`memory_store.py` 不读 `tools/`
- **加载顺序固定**：`persona → skills registry → tools → mcp → memory`，任一失败立即 fail-fast
- **`.gitignore` 加 `workspace/users/`、`workspace/sandbox/`** — 运行时数据不进 git；`workspace/persona/`、`workspace/skills/`、`workspace/extensions/` 跟踪

---

## 3. 模块接口契约

### 3.1 `agent_persona`（位于 `workspace/agent_persona.py`）

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Persona:
    soul: str           # SOUL.md 全文
    agents: str         # AGENTS.md 全文
    tools_guide: str    # TOOLS.md 全文
    combined: str       # 三段拼好的 system prompt 段 A

def load_persona(workspace_root: Path) -> Persona:
    """读 workspace/persona/{SOUL,AGENTS,TOOLS}.md，拼成 Persona。
    任一文件不存在 → raise FileNotFoundError（fail-fast）。
    """
```

### 3.2 `agent_skills`（位于 `workspace/agent_skills.py`）

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class SkillDef:
    name: str
    description: str    # 来自 frontmatter，给 LLM 看
    body: str           # SKILL.md 正文
    scripts_dir: Path | None

def scan_skills(skills_root: Path) -> dict[str, SkillDef]:
    """扫 skills/<name>/SKILL.md，解析 frontmatter(name, description) + body。
    返回 {name: SkillDef}，name 重复 → raise。
    """

def make_load_skill_tool(
    registry: dict[str, SkillDef],
    session_provider: Callable[[], AgentSession],
) -> Callable:
    """返回一个 @function_tool 装饰后的函数 load_skill(name) -> str：
       - registry.get(name) 拿到 SkillDef
       - session = session_provider()
       - session.update_chat_ctx(messages=[
           {"role": "system", "content": skill.body}
         ])
       - return f"已加载 skill {name}，相关指引已注入对话上下文"
    """
```

### 3.3 `agent_extensions`（位于 `workspace/agent_extensions.py`）

```python
from pathlib import Path
from livekit.agents.llm import Tool
from mcp import StdioServerParams

def load_tools(tools_dir: Path) -> list[Tool]:
    """glob tools/*.py（跳过下划线开头和 __init__.py），对每个文件：
       - importlib.import_module(f"agent_tools.{stem}")
       - 调用 module.register() → list[Tool]  # 收集该文件导出的 tool
       - 累加所有 list 返回
    任意文件无 register 函数 → raise AttributeError（fail-fast）。
    任意 register() 抛异常 → 透传，worker 启动失败。
    """

def load_mcp_servers(mcp_dir: Path) -> list[StdioServerParams]:
    """读 mcp/*.json → [StdioServerParams, ...]
    任意文件缺 'command' 字段 → raise。
    v0.1 只支持 stdio 传输；JSON 含 'transport' 字段且非 'stdio' → 跳过并打 WARNING。
    """
```

#### Tool 编写约定

```python
# workspace/extensions/tools/example.py
from typing import Annotated
from livekit.agents import function_tool


@function_tool()
async def example_tool(arg: Annotated[str, "参数说明"]) -> str:
    """工具描述（docstring → LLM 看到的 schema 描述）。"""
    return "result"


def register() -> list:
    """返回本文件提供的 tool 列表。loader 会收集所有 register() 的返回值。"""
    return [example_tool]
```

### 3.4 `agent_memory`（位于 `workspace/agent_memory.py`）

```python
from pathlib import Path
from datetime import date

class MemoryStore:
    def __init__(self, user_root: Path):
        """user_root = workspace/users/<participant_identity>/
        目录不存在时自动创建（含 User.md/MEMORY.md/memory/）。
        """

    def load_user_prompt(self) -> str:
        """读 User.md + MEMORY.md，拼接为 system prompt 段 B+C。
        文件不存在时返回空串。
        """

    def load_today_prompt(self, today: date | None = None) -> str:
        """读 memory/<today>.md；不存在时创建空文件并返回空串。"""

    def append_today(self, fact: str, *, source: str, ts: datetime) -> None:
        """追加一条到 memory/<today>.md：
        '- [<ISO ts>] [<source>] <fact>'
        """

    def commit_today_to_memory(self, summary: str) -> None:
        """on_exit 调：将 LLM 摘要精选的事实追加到 MEMORY.md。"""
```

#### user_id 来源

**`user_id` 取 `ctx.room.remote_participants.values()[0].identity`**（LiveKit participant identity，由 `lk token create --identity` 设置，跨房间稳定）。若同时有多个远端参与者，**v0.1 取第一个并 warn**（"多个用户同房间"是 v0.2+ 议题）。

### 3.5 `agent_core`（`main.py` 改造点）

`main.py` 的 `_build_session()` 不动（继续负责 STT/LLM/TTS 工厂）。**新增** `build_agent(workspace_root) -> Agent`，仅在 `entrypoint()` 内调用：

```python
def build_agent(workspace_root: Path) -> Agent:
    persona = load_persona(workspace_root)                          # 段 A
    skills = scan_skills(workspace_root / "skills")
    mcp_servers = load_mcp_servers(workspace_root / "extensions" / "mcp")
    tools = load_tools(workspace_root / "extensions" / "tools")
    load_skill = make_load_skill_tool(skills, session_provider=...)
    return Agent(
        instructions=persona.combined,
        tools=tools + [load_skill],
        mcp_servers=mcp_servers,
    )
```

**memory 段 B+C+D 的注入点决策**：`on_enter` 内用 `self.update_chat_ctx` 追加三条 system message。**不**在 `build_agent` 阶段拼——因为 `build_agent` 不知道 user_id。

**prewarm 决策**：`_prewarm(proc)` 保持现有行为，只调 `_build_session()` 预热 STT/LLM/TTS 连接。**不**调 `build_agent`——prewarm 时没 user_id，构造出来的 agent 也不能复用（每个 room 的 agent 都按各自 user 注入 memory）。

---

## 4. 关键数据流

### 4.1 Worker 启动

```
python main.py start
  └─ WorkerOptions(prewarm_fnc=_prewarm)
       └─ _prewarm(proc) → _build_session()      # 预热 STT/LLM/TTS 连接
            （不调 build_agent —— prewarm 时无 user_id）
```

### 4.2 用户进房（JobContext 派单）

```
entrypoint(ctx)
  ├─ 等待 ctx.room.remote_participants 非空
  ├─ user_id = ctx.room.remote_participants.values()[0].identity
  ├─ session = _build_session()    # 火山引擎 STT/LLM/TTS
  ├─ agent = build_agent(WORKSPACE_ROOT)            # 不带 user_id
  └─ await session.start(agent, ctx.room, RoomInputOptions(...))
       └─ agent.on_enter():
            ├─ memory = MemoryStore(WORKSPACE_ROOT / "users" / user_id)
            ├─ self.update_chat_ctx(messages=[
            │     {"role": "system", "content": memory.load_user_prompt()},
            │     {"role": "system", "content": memory.load_today_prompt()},
            │ ])
            └─ logger.info(f"[Agent] 进入房间 user={user_id}")
```

### 4.3 工具调用（以 `bash` 为例）

```
User 语音 → RealtimeModel → tool_call("bash", {"cmd": "ls"})
  ↓
livekit-agents 框架 → extensions/tools/bash.py::register 注册的 bash 函数
  ├─ cmd 含 '..' 或绝对路径？→ return "拒绝：路径越界"
  ├─ 命令不在白名单 {ls, cat, grep, find, head, tail, wc, ...}？→ return "拒绝：未授权命令"
  └─ subprocess.run(cmd, cwd=workspace/sandbox/, shell=True, timeout=5)
       └─ return stdout（截断 4KB）
  ↓
RealtimeModel 把结果喂回 LLM → TTS → 用户
```

### 4.4 `load_skill` 调用

```
User: "load weather skill"
  ↓
RealtimeModel → tool_call("load_skill", {"name": "weather"})
  ↓
agent_skills.make_load_skill_tool 返回的函数
  ├─ skill = registry.get("weather")   # 不存在 → return "找不到 skill"
  ├─ session.update_chat_ctx(messages=[{"role": "system", "content": skill.body}])
  └─ return f"已加载 skill {name}，可使用其指引"
  ↓
LLM 看到新 system prompt → 按 SKILL.md 指引回复
```

### 4.5 用户退房

```
ctx.room.on("disconnected") → session.aclose() → agent.on_exit()
  ├─ summary = await _summarize_chat(self.session)   # 调同一个 LLM
  ├─ memory.commit_today_to_memory(summary)          # 追加到 MEMORY.md
  └─ logger.info(f"[Memory] user={user_id} 长期记忆已更新")
```

---

## 5. 扩展清单（"想加 X 怎么动手"）

| 想加什么 | 改哪里 | 需不需要碰 `main.py` |
|---|---|---|
| 1 个新 tool | `workspace/extensions/tools/<name>.py` 导出 `register() -> list` | 否 |
| 1 个新 MCP server | `workspace/extensions/mcp/<name>.json` | 否 |
| 1 个新 skill | `workspace/skills/<name>/SKILL.md` | 否 |
| 改 agent 人格 | `workspace/persona/SOUL.md` | 否 |
| 改 agent 行为规则 | `workspace/persona/AGENTS.md` | 否 |
| 改 agent 工具元说明 | `workspace/persona/TOOLS.md` | 否 |
| 改 bash 白名单 | `workspace/extensions/tools/bash.py` 内一个 set 字面量 | 否 |
| 改工作沙箱路径 | `_build_session()` 旁边一个常量；或环境变量 `WORKSPACE_SANDBOX` | 1 行 |
| 迁移某用户记忆到别处 | `mv workspace/users/<uid>/ ~/<新位置>/`，然后改 `MemoryStore.__init__` 接受 path override | 实施时再定 |

---

## 6. 走通 4 层的示例：加一个 `current_time` tool

假设你要让 agent 能回答"现在几点了"。

### 第 1 层：目录

新增文件 `workspace/extensions/tools/current_time.py`：

```python
from datetime import datetime
from livekit.agents import function_tool


@function_tool()
async def current_time() -> str:
    """获取当前的日期和时间。

    Returns:
        形如 "现在是 2026-07-04 14:32:10" 的字符串。
    """
    return datetime.now().strftime("现在是 %Y-%m-%d %H:%M:%S")


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [current_time]
```

### 第 2 层：自动加载

`agent_extensions.load_tools(tools_dir)` 启动时 glob 到 `current_time.py`，import + 调 `register()` 收集 tool 列表。LLM 之后的 tool schema 里就多了 `current_time`。

### 第 3 层：LLM 视角

`<system prompt>` 段 A 包含 `TOOLS.md`（由 agent 自己维护的工具使用说明）。LLM 看到 tool 列表里有 `current_time`，且 description 是"获取当前的日期和时间"，用户问"几点了"时自动调用。

### 第 4 层：用户视角

```
User: "现在几点了？"
Agent: "现在是 2026-07-04 14:32:10。"
```

**零 `main.py` 改动**。重启 worker 即生效。

---

## 7. 关键决策与默认

| 决策 | 默认 | 理由 | 备选 |
|---|---|---|---|
| 扩展发现 | 自动扫描（glob import）| 零注册代码，加一个文件就能用 | manifest 文件（v0.2 再说）|
| persona 文件格式 | Markdown | 易编辑、可手改、支持长文本 | YAML（更结构化但不支持长文本）|
| 长期记忆后端 | Markdown 文件 | 可 git 跟踪、可手改、可 grep | SQLite（v0.2 上向量库时再考虑）|
| memory 写入时机 | 工具调用实时 append + on_exit 摘要 commit | 平衡实时性和性能 | 全部 on_exit（丢细节）|
| memory 检索 | 启动时全量读 User.md + MEMORY.md + 当日文件 | 简单，v0.1 用户量小够用 | 向量检索（v0.2+）|
| bash tool 沙箱 | `workspace/sandbox/` + 路径白名单 + 命令白名单 | 最小安全边界 | 容器隔离（成本高，v0.2+）|
| bash 默认命令白名单 | `ls, cat, grep, find, head, tail, wc, pwd, echo, date` | 满足日常查询 | 实施时按需扩 |
| MCP 传输 | stdio only | 简单，零网络配置 | HTTP/SSE（v0.2）|
| `load_skill` 实现 | 一个全局 `@function_tool` | LLM 主动触发，对用户透明 | session 启动时全量加载（prompt 太长）|
| tool 注册接口 | `def register() -> list[Tool]` | 与 livekit-agents 的 `Agent(tools=[...])` 风格一致 | 装饰器自动注册（侵入性大）|
| user_id 来源 | `participant.identity` | 跨房间稳定 | `room.name`（错的，已纠正）|
| daily memory 文件粒度 | 一天一文件，session 作为段 | 一天多次会话自然合并 | 一会话一文件（v0.2 再切）|
| 多用户同房间 | v0.1 取第一个 + warn | 简单 | session 切换（v0.2+）|

---

## 8. 非目标（v0.1 明确不做）

| 不做 | 原因 |
|---|---|
| 写 Python 实现代码 | 用户要求 v0.1 只输出架构 |
| 改 `main.py` 现有逻辑 | 重构未启动前不动线上代码 |
| 替换 `VolcengineAgent` 类 | 留给实施阶段 |
| 实现任何具体 tool / skill / memory 存储 | 同上 |
| 引入向量库 / embedding | v0.1 全文 + LLM 摘要足够 |
| 远程 MCP（HTTP/SSE）| v0.1 stdio only |
| 多租户、RBAC、审计日志 | 单实例个人项目，YAGNI |
| 改造 vendored 火山引擎插件 | 那是上游仓库议题 |
| 把 `workspace/users/` 迁出项目目录到 `~/.openz/` | 用户最终拍板"先放项目目录"，v0.2 再考虑 |

---

## 9. 未来迁移路径

| 触发条件 | 迁移内容 |
|---|---|
| 多项目复用同一 agent | `workspace/` 整体 → `~/.openz/<agent-name>/`；`MemoryStore.__init__` 接受 base path 参数 |
| 同一用户出现在多 agent | `users/<uid>/` 提到 `~/.openz/users/<uid>/`（跨 agent 共享）|
| memory 体积膨胀 | 引入向量库 + 摘要，MEMORY.md 退化为索引 |
| skill 出现越权风险 | skill frontmatter 加 `requires_tools: [bash, ...]`，`load_skill` 前置检查 |
| bash tool 需要更强隔离 | 沙箱从 `subprocess` 切到容器（docker-in-docker 或 firecracker）|

---

## 10. 待决问题（实施阶段回答）

| 问题 | 何时决定 |
|---|---|
| bash 白名单具体内容 | 实现 bash tool 时 |
| MEMORY.md 上限多大、溢出怎么办 | 出现 LLM context 装不下时 |
| 何时把 `workspace/users/` 迁到 `~/.openz/` | 多项目/备份需求出现时 |
| on_exit 摘要用哪个 LLM（realtime 还是另起）| 实施时 |
| skill 是否需要权限声明 | 出现越权风险时 |
| 多用户同房间的实际语义 | 真有第二用户接入时 |

---

## 附录 A：与 openclaw 的关系

本设计的 memory/prompt 注入层借鉴了 [OpenClaw](https://github.com/openclaw/openclaw) 的工作区模型：

| OpenClaw 文件 | 本项目 | 差异 |
|---|---|---|
| `SOUL.md` | `persona/SOUL.md` | 一致 |
| `AGENTS.md` | `persona/AGENTS.md` | 一致 |
| `TOOLS.md` | `persona/TOOLS.md` | 一致 |
| `MEMORY.md`（agent 级）| `users/<uid>/MEMORY.md` | 我们 per-user |
| `memory/YYYY-MM-DD.md`（agent 级目录）| `users/<uid>/memory/YYYY-MM-DD.md` | 我们 per-user + session 作为段 |
| — | `users/<uid>/User.md` | 我们加的（per-user 画像）|
| `skills/<name>/SKILL.md` | 一致 | 一致 |

差异来源于 OpenClaw 是单用户系统，本项目是 per-user 跨房间会话模型。

## 附录 B：与 `docs/agent-capabilities-extension.md` 的关系

该文档（已存在）列出了 `livekit-agents` 框架的 10 个扩展点并给了 Function Tools demo。本 spec 在它**之上**做"组织层"——目录布局、加载机制、配置 schema、记忆后端。两者不冲突，本 spec 是它的实施侧。
