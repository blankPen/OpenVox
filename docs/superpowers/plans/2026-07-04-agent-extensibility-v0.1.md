# Agent Extensibility v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LiveKit × Volcengine worker 里落地 spec `2026-07-04-agent-extensibility-design.md` 的 5 个模块 + 1 个 tool + 1 个 skill + 1 个 memory 读通的端到端最小可跑切片。

**Architecture:** 5 个 Python 模块（`workspace/agent_*.py`）各管一个目录；`main.py` 加 `build_agent()` 组装；persona/skill/tool 走自动扫描零注册；memory 用 markdown 文件。

**Tech Stack:** Python 3.11+, `livekit-agents` 1.5.x, `volcengine` vendored 插件, pytest 7+.

## File Structure

**新增（11 个 Python 文件 + 5 个 markdown + .gitignore 改动）：**

| 路径 | 职责 |
|---|---|
| `workspace/__init__.py` | 标记 workspace/ 为 import 根（空文件）|
| `workspace/agent_persona.py` | `load_persona()` — 读 SOUL/AGENTS/TOOLS.md |
| `workspace/agent_skills.py` | `scan_skills()` + `make_load_skill_tool()` |
| `workspace/agent_extensions.py` | `load_tools()` + `load_mcp_servers()` |
| `workspace/agent_memory.py` | `MemoryStore`（v0.1 只读：load_user_prompt）|
| `workspace/persona/SOUL.md` | starter 内容 |
| `workspace/persona/AGENTS.md` | starter 内容 |
| `workspace/persona/TOOLS.md` | starter 内容 |
| `workspace/skills/weather/SKILL.md` | starter skill（被 worked example 引用）|
| `workspace/skills/weather/scripts/.gitkeep` | 保留空目录 |
| `workspace/extensions/tools/__init__.py` | 标记（loader 跳过）|
| `workspace/extensions/tools/current_time.py` | 第一个 tool |
| `workspace/extensions/mcp/.gitkeep` | 保留空目录（v0.1 不启用 MCP）|
| `workspace/users/.gitkeep` | 保留空目录 |
| `workspace/sandbox/.gitkeep` | 保留空目录 |
| `tests/test_agent_persona.py` | persona 模块测试 |
| `tests/test_agent_skills.py` | skills registry 测试 |
| `tests/test_agent_extensions.py` | extensions loader 测试 |
| `tests/test_agent_memory.py` | memory store 测试 |
| `tests/test_build_agent.py` | build_agent() 集成测试 |
| `tests/conftest.py` | pytest fixture：WORKSPACE_ROOT |

**修改（1 个）：**
- `main.py` — 加 `WORKSPACE_ROOT` 常量、`sys.path` 注入、`build_agent()` 工厂、`on_enter` 注入 memory 段
- `.gitignore` — 加 `workspace/users/*/`（保留 `.gitkeep`）、`workspace/sandbox/*/`（保留 `.gitkeep`）、`workspace/extensions/mcp/*.json`（v0.1 没真 MCP 配置）

**显式不做（v0.1 范围外，留给后续 plan）：**
- `MemoryStore` 写路径（`append_today` / `commit_today_to_memory`）
- `on_exit` 摘要逻辑
- bash tool / MCP 真接入
- `tests/e2e_generate_reply.py` 修复

## Global Constraints

- Python 3.11+（`.venv` 已有）
- `livekit-agents` 1.5.x（`pip show` 显示已装 1.5.x，CLAUDE.md 已纠正 1.2.9 旧说法）
- 火山引擎插件走 `plugins/livekit-plugins-volcengine/`（`--no-deps` editable 安装，pin 在 1.5.4）
- pytest 在 `.venv/bin/pytest`
- 已有 `tests/e2e_generate_reply.py` 是坏的，本 plan **不碰**它
- 日志格式保持 main.py 现有格式（`%(asctime)s.%(msecs)03d | %(levelname)-5s | %(name)s | %(message)s`）
- 所有 Python 文件用 `from __future__ import annotations` + 类型注解
- 不引入新第三方依赖（用 stdlib + livekit-agents + volcengine 已有）
- 失败立即 fail-fast：`raise` 往上抛，worker 启动失败比静默错误好

---

## Task 1: Bootstrap workspace/ + 测试基础设施

**Files:**
- Create: `workspace/__init__.py`（空）
- Create: `workspace/persona/SOUL.md`（占位 1 行）
- Create: `workspace/persona/AGENTS.md`（占位 1 行）
- Create: `workspace/persona/TOOLS.md`（占位 1 行）
- Create: `workspace/skills/weather/scripts/.gitkeep`（空）
- Create: `workspace/extensions/tools/__init__.py`（空）
- Create: `workspace/extensions/mcp/.gitkeep`（空）
- Create: `workspace/users/.gitkeep`（空）
- Create: `workspace/sandbox/.gitkeep`（空）
- Create: `tests/conftest.py`
- Create: `pyproject.toml`（pytest 配置 + 项目元数据）
- Modify: `.gitignore`

**Interfaces:**
- 无（bootstrap 任务）

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p workspace/persona workspace/skills/weather/scripts \
         workspace/extensions/tools workspace/extensions/mcp \
         workspace/users workspace/sandbox tests
```

- [ ] **Step 2: 创建所有占位文件**

`workspace/__init__.py`、`workspace/extensions/tools/__init__.py` 留空。

`workspace/persona/SOUL.md`：
```markdown
# SOUL（agent 人格）

你叫"小语"，是一个友好的中文语音助手。语气简洁自然，不用表情符号或 Markdown。
```

`workspace/persona/AGENTS.md`：
```markdown
# AGENTS（行为规则）

- 回答简洁，2-3 句话为主
- 不主动给长列表或大段代码
- 涉及工具调用时先用工具再回答
```

`workspace/persona/TOOLS.md`：
```markdown
# TOOLS（工具使用说明）

- `current_time` — 查当前时间，需要时调用
- `load_skill` — 加载名为 `<name>` 的 skill，调它的 body 注入对话上下文
```

各 `.gitkeep` 文件为空。

- [ ] **Step 3: 写 `pyproject.toml`（pytest 配置）**

`pyproject.toml`（项目根，整个文件替换；如果已有则只补 `[tool.pytest.ini_options]`）：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_functions = ["test_*"]
addopts = "-v --tb=short"
```

- [ ] **Step 4: 写 `tests/conftest.py`**

```python
"""Shared pytest fixtures for the agent extensibility test suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """提供一个空 workspace 根，预先把项目 workspace/ 拷过来。

    测试可以往 tmp_path 下写任何东西，不污染真实目录。
    """
    # 把项目 workspace 拷一份做基础（含空目录 + 已有的 persona/skill 占位）
    import shutil
    if WORKSPACE_ROOT.exists():
        shutil.copytree(WORKSPACE_ROOT, tmp_path, dirs_exist_ok=True)
    else:
        tmp_path.mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _inject_workspace_path():
    """每个测试自动把 workspace/ 加到 sys.path，让 agent_* 模块可 import。"""
    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    yield
```

- [ ] **Step 5: 更新 `.gitignore`**

追加：

```
# Agent extensibility runtime data
workspace/users/*
!workspace/users/.gitkeep
workspace/sandbox/*
!workspace/sandbox/.gitkeep
workspace/extensions/mcp/*.json
!workspace/extensions/mcp/.gitkeep
workspace/skills/*/scripts/*
!workspace/skills/*/scripts/.gitkeep
```

- [ ] **Step 6: 写第一个 sanity test**

`tests/test_workspace_layout.py`：

```python
"""Sanity test: workspace/ 目录结构存在。"""
from pathlib import Path


def test_workspace_directories_exist():
    root = Path(__file__).parent.parent / "workspace"
    for sub in ["persona", "skills", "extensions/tools", "extensions/mcp", "users", "sandbox"]:
        assert (root / sub).is_dir(), f"missing workspace/{sub}"


def test_persona_files_exist():
    root = Path(__file__).parent.parent / "workspace" / "persona"
    for f in ["SOUL.md", "AGENTS.md", "TOOLS.md"]:
        assert (root / f).is_file(), f"missing persona/{f}"
```

- [ ] **Step 7: 跑测试确认通过**

```bash
cd /Users/pz/workspace/openvox
source .venv/bin/activate
pytest tests/test_workspace_layout.py -v
```

Expected: `2 passed`

- [ ] **Step 8: 提交**

```bash
git add workspace/ tests/conftest.py tests/test_workspace_layout.py pyproject.toml .gitignore
git commit -m "feat(workspace): bootstrap workspace/ layout + pytest infra"
```

---

## Task 2: agent_persona 模块

**Files:**
- Create: `workspace/agent_persona.py`
- Create: `tests/test_agent_persona.py`

**Interfaces:**
- 无上游
- Produces: `load_persona(workspace_root: Path) -> Persona`，`Persona` 含 `soul: str`、`agents: str`、`tools_guide: str`、`combined: str`

- [ ] **Step 1: 写失败测试**

`tests/test_agent_persona.py`：

```python
"""Tests for workspace/agent_persona.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_persona import Persona, load_persona


def test_load_persona_returns_dataclass(workspace_root: Path):
    p = load_persona(workspace_root)
    assert isinstance(p, Persona)
    assert isinstance(p.soul, str)
    assert isinstance(p.agents, str)
    assert isinstance(p.tools_guide, str)
    assert isinstance(p.combined, str)


def test_load_persona_combined_contains_all_three(workspace_root: Path):
    p = load_persona(workspace_root)
    assert p.soul in p.combined
    assert p.agents in p.combined
    assert p.tools_guide in p.combined


def test_load_persona_missing_file_raises(tmp_path: Path):
    # tmp_path 没有 persona 目录 → 应该 fail-fast
    with pytest.raises(FileNotFoundError):
        load_persona(tmp_path)


def test_load_persona_partial_files_raises(tmp_path: Path):
    # 只建 SOUL.md → 缺另外两个 → 失败
    (tmp_path / "persona").mkdir()
    (tmp_path / "persona" / "SOUL.md").write_text("soul", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_persona(tmp_path)
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
pytest tests/test_agent_persona.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent_persona'`

- [ ] **Step 3: 实现 `agent_persona.py`**

`workspace/agent_persona.py`：

```python
"""Read agent persona (SOUL/AGENTS/TOOLS) from workspace/persona/."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Persona:
    soul: str
    agents: str
    tools_guide: str
    combined: str


def load_persona(workspace_root: Path) -> Persona:
    """Read workspace/persona/{SOUL,AGENTS,TOOLS}.md and return a Persona.

    Raises FileNotFoundError if any of the three files is missing.
    """
    persona_dir = workspace_root / "persona"
    soul = (persona_dir / "SOUL.md").read_text(encoding="utf-8")
    agents = (persona_dir / "AGENTS.md").read_text(encoding="utf-8")
    tools_guide = (persona_dir / "TOOLS.md").read_text(encoding="utf-8")
    combined = "\n\n".join([soul, agents, tools_guide])
    return Persona(soul=soul, agents=agents, tools_guide=tools_guide, combined=combined)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_agent_persona.py -v
```

Expected: `4 passed`

- [ ] **Step 5: 提交**

```bash
git add workspace/agent_persona.py tests/test_agent_persona.py
git commit -m "feat(persona): add load_persona() with fail-fast on missing files"
```

---

## Task 3: agent_skills 注册表

**Files:**
- Create: `workspace/agent_skills.py`
- Create: `tests/test_agent_skills.py`

**Interfaces:**
- 无上游
- Produces: `scan_skills(skills_root: Path) -> dict[str, SkillDef]`，`SkillDef(name, description, body, scripts_dir)`；`make_load_skill_tool(registry, session_provider) -> Callable`

- [ ] **Step 1: 写失败测试**

`tests/test_agent_skills.py`：

```python
"""Tests for workspace/agent_skills.py."""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agent_skills import SkillDef, make_load_skill_tool, scan_skills


def _write_skill(root: Path, name: str, body: str = "skill body", description: str | None = None) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = description if description is not None else f"Description for {name}"
    content = dedent(f"""\
        ---
        name: {name}
        description: {desc}
        ---
        {body}
    """)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_scan_skills_finds_skill(workspace_root: Path):
    skills = scan_skills(workspace_root / "skills")
    # workspace_root fixture 已拷过来 starter skill(s)；至少应该有 weather
    assert "weather" in skills
    w = skills["weather"]
    assert isinstance(w, SkillDef)
    assert w.name == "weather"
    assert "weather" in w.description.lower()
    assert "skill body" in w.body or len(w.body) > 0


def test_scan_skills_duplicate_name_raises(workspace_root: Path):
    _write_skill(workspace_root / "skills", "weather", body="dup", description="dup")
    with pytest.raises(ValueError, match="duplicate"):
        scan_skills(workspace_root / "skills")


def test_scan_skills_missing_description_raises(workspace_root: Path):
    skill_dir = workspace_root / "skills" / "broken"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: broken\n---\nbody\n", encoding="utf-8")
    with pytest.raises(ValueError, match="description"):
        scan_skills(workspace_root / "skills")


def test_make_load_skill_tool_injects_into_chat_ctx(workspace_root: Path):
    _write_skill(workspace_root / "skills", "alpha", body="ALPHA_BODY", description="alpha desc")
    registry = scan_skills(workspace_root / "skills")
    captured: list[list[dict]] = []

    class FakeSession:
        def update_chat_ctx(self, messages: list[dict]) -> None:
            captured.append(messages)

    def session_provider():
        return FakeSession()

    tool = make_load_skill_tool(registry, session_provider)
    # tool 是 async 函数，但调用本身不需要 await 因为只走同步路径到 update_chat_ctx
    import asyncio
    asyncio.run(tool("alpha"))
    assert len(captured) == 1
    assert "ALPHA_BODY" in captured[0][0]["content"]


def test_make_load_skill_tool_unknown_name_returns_error(workspace_root: Path):
    registry = scan_skills(workspace_root / "skills")
    def session_provider(): raise AssertionError("should not be called")
    tool = make_load_skill_tool(registry, session_provider)
    import asyncio
    result = asyncio.run(tool("nope"))
    assert "找不到" in result or "not found" in result.lower()
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
pytest tests/test_agent_skills.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent_skills'`

- [ ] **Step 3: 实现 `agent_skills.py`**

`workspace/agent_skills.py`：

```python
"""Scan workspace/skills/ for SKILL.md files and build a load_skill() tool."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class SkillDef:
    name: str
    description: str
    body: str
    scripts_dir: Path | None


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _parse_skill_md(path: Path) -> SkillDef:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter (---...---)")
    fm, body = m.group(1), m.group(2)
    name: str | None = None
    description: str | None = None
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
    if not name:
        raise ValueError(f"{path}: frontmatter missing 'name'")
    if not description:
        raise ValueError(f"{path}: frontmatter missing 'description'")
    scripts_dir = path.parent / "scripts"
    return SkillDef(name=name, description=description, body=body.strip(),
                    scripts_dir=scripts_dir if scripts_dir.is_dir() else None)


def scan_skills(skills_root: Path) -> dict[str, SkillDef]:
    """Glob skills_root/*/SKILL.md and return {name: SkillDef}.

    Raises ValueError on duplicate name or malformed SKILL.md.
    """
    registry: dict[str, SkillDef] = {}
    if not skills_root.is_dir():
        return registry
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        skill = _parse_skill_md(skill_md)
        if skill.name in registry:
            raise ValueError(f"duplicate skill name: {skill.name!r}")
        registry[skill.name] = skill
    return registry


def make_load_skill_tool(
    registry: dict[str, SkillDef],
    session_provider: Callable[[], object],
) -> Callable:
    """Return an async function load_skill(name: str) -> str suitable as a tool.

    When called, injects the skill's body into the session's chat context
    as a system message.
    """
    async def load_skill(name: str) -> str:
        skill = registry.get(name)
        if skill is None:
            return f"找不到 skill {name!r}，可用：{', '.join(sorted(registry)) or '(无)'}"
        session = session_provider()
        session.update_chat_ctx(messages=[{"role": "system", "content": skill.body}])
        return f"已加载 skill {name!r}，可使用其指引。"

    load_skill.__name__ = "load_skill"
    load_skill.__doc__ = (
        "加载名为 <name> 的 skill。加载后该 skill 的指引会注入对话上下文。"
        "调用前先用 list_skills 看可用名字。"
    )
    return load_skill
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_agent_skills.py -v
```

Expected: `5 passed`

- [ ] **Step 5: 提交**

```bash
git add workspace/agent_skills.py tests/test_agent_skills.py
git commit -m "feat(skills): add scan_skills() + make_load_skill_tool()"
```

---

## Task 4: agent_extensions 加载器

**Files:**
- Create: `workspace/agent_extensions.py`
- Create: `tests/test_agent_extensions.py`

**Interfaces:**
- 无上游
- Produces: `load_tools(tools_dir: Path) -> list[Any]`（每个 tool 文件导出 `register() -> list[Tool]`），`load_mcp_servers(mcp_dir: Path) -> list[StdioServerParams]`

- [ ] **Step 1: 写失败测试**

`tests/test_agent_extensions.py`：

```python
"""Tests for workspace/agent_extensions.py."""
from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from agent_extensions import load_mcp_servers, load_tools


def test_load_tools_collects_register_calls(workspace_root: Path):
    tools_dir = workspace_root / "extensions" / "tools"
    (tools_dir / "fake_a.py").write_text(dedent("""\
        async def fake_a() -> str:
            '''Tool A.'''
            return 'a'
        def register():
            return [fake_a]
    """), encoding="utf-8")
    (tools_dir / "fake_b.py").write_text(dedent("""\
        async def fake_b() -> str:
            '''Tool B.'''
            return 'b'
        def register():
            return [fake_b]
    """), encoding="utf-8")
    tools = load_tools(tools_dir)
    names = sorted(t.__name__ if callable(t) else t.__class__.__name__ for t in tools)
    # function_tool 包装后属性可能不是 __name__，宽松断言：至少有 2 个 tool
    assert len(tools) == 2


def test_load_tools_skips_dunder_and_underscore(workspace_root: Path):
    tools_dir = workspace_root / "extensions" / "tools"
    (tools_dir / "_private.py").write_text("def register(): return []", encoding="utf-8")
    tools = load_tools(tools_dir)
    assert tools == []  # _private.py 跳过


def test_load_tools_missing_register_raises(workspace_root: Path):
    tools_dir = workspace_root / "extensions" / "tools"
    (tools_dir / "broken.py").write_text("x = 1", encoding="utf-8")
    with pytest.raises(AttributeError, match="register"):
        load_tools(tools_dir)


def test_load_mcp_servers_reads_json(workspace_root: Path):
    mcp_dir = workspace_root / "extensions" / "mcp"
    (mcp_dir / "git.json").write_text(json.dumps({
        "command": "uvx", "args": ["mcp-server-git"]
    }), encoding="utf-8")
    servers = load_mcp_servers(mcp_dir)
    assert len(servers) == 1


def test_load_mcp_servers_missing_command_raises(workspace_root: Path):
    mcp_dir = workspace_root / "extensions" / "mcp"
    (mcp_dir / "bad.json").write_text(json.dumps({"args": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="command"):
        load_mcp_servers(mcp_dir)
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
pytest tests/test_agent_extensions.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent_extensions'`

- [ ] **Step 3: 实现 `agent_extensions.py`**

`workspace/agent_extensions.py`：

```python
"""Load tools and MCP servers from workspace/extensions/."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


def load_tools(tools_dir: Path) -> list[Any]:
    """Glob tools_dir/*.py, import each, call module.register() -> list.

    Files starting with `_` (incl. __init__.py) are skipped.
    Raises AttributeError if a file has no register() function.
    """
    if not tools_dir.is_dir():
        return []
    tools: list[Any] = []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"_agent_tool_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load spec for {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not hasattr(module, "register"):
            raise AttributeError(f"{path} missing register() function")
        tools.extend(module.register())
    return tools


def load_mcp_servers(mcp_dir: Path) -> list[Any]:
    """Read mcp_dir/*.json → list[StdioServerParams].

    v0.1: only stdio transport supported. Non-stdio entries are skipped
    with a warning.
    """
    if not mcp_dir.is_dir():
        return []
    from mcp import StdioServerParams
    servers: list[Any] = []
    for path in sorted(mcp_dir.glob("*.json")):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        if "command" not in cfg:
            raise ValueError(f"{path}: missing 'command' field")
        if cfg.get("transport", "stdio") != "stdio":
            import warnings
            warnings.warn(f"{path}: non-stdio transport not supported in v0.1, skipping")
            continue
        servers.append(StdioServerParams(
            command=cfg["command"],
            args=cfg.get("args", []),
            env=cfg.get("env"),
        ))
    return servers
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_agent_extensions.py -v
```

Expected: `5 passed`

- [ ] **Step 5: 提交**

```bash
git add workspace/agent_extensions.py tests/test_agent_extensions.py
git commit -m "feat(extensions): add load_tools() and load_mcp_servers()"
```

---

## Task 5: agent_memory 读路径

**Files:**
- Create: `workspace/agent_memory.py`
- Create: `tests/test_agent_memory.py`

**Interfaces:**
- 无上游
- Produces: `MemoryStore(user_root)`，方法 `load_user_prompt() -> str`（v0.1 范围）

- [ ] **Step 1: 写失败测试**

`tests/test_agent_memory.py`：

```python
"""Tests for workspace/agent_memory.py (v0.1 read path only)."""
from __future__ import annotations

from pathlib import Path

from agent_memory import MemoryStore


def test_init_creates_directory_structure(tmp_path: Path):
    user_root = tmp_path / "alice"
    MemoryStore(user_root)
    assert user_root.is_dir()
    assert (user_root / "User.md").is_file()  # 空文件
    assert (user_root / "MEMORY.md").is_file()  # 空文件
    assert (user_root / "memory").is_dir()


def test_load_user_prompt_reads_both_files(tmp_path: Path):
    user_root = tmp_path / "alice"
    store = MemoryStore(user_root)
    (user_root / "User.md").write_text("# USER\nname: alice\n", encoding="utf-8")
    (user_root / "MEMORY.md").write_text("# MEMORY\nlikes coffee\n", encoding="utf-8")
    out = store.load_user_prompt()
    assert "alice" in out
    assert "coffee" in out
    # 两段之间有分隔
    assert "\n\n" in out


def test_load_user_prompt_empty_when_no_content(tmp_path: Path):
    user_root = tmp_path / "bob"
    store = MemoryStore(user_root)
    assert store.load_user_prompt() == ""


def test_load_user_prompt_only_user_file(tmp_path: Path):
    user_root = tmp_path / "carol"
    store = MemoryStore(user_root)
    (user_root / "User.md").write_text("only user", encoding="utf-8")
    out = store.load_user_prompt()
    assert "only user" in out


def test_load_user_prompt_only_memory_file(tmp_path: Path):
    user_root = tmp_path / "dave"
    store = MemoryStore(user_root)
    (user_root / "MEMORY.md").write_text("only memory", encoding="utf-8")
    out = store.load_user_prompt()
    assert "only memory" in out
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
pytest tests/test_agent_memory.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent_memory'`

- [ ] **Step 3: 实现 `agent_memory.py`**

`workspace/agent_memory.py`：

```python
"""Per-user memory store backed by markdown files (read path in v0.1)."""
from __future__ import annotations

from pathlib import Path


class MemoryStore:
    """Read/write per-user memory under <user_root>/{User.md, MEMORY.md, memory/}.

    v0.1 only implements the read path. Write methods land in v0.2.
    """

    _USER_FILE = "User.md"
    _MEMORY_FILE = "MEMORY.md"
    _DAILY_DIR = "memory"

    def __init__(self, user_root: Path):
        self._root = user_root
        self._root.mkdir(parents=True, exist_ok=True)
        (self._root / self._USER_FILE).touch(exist_ok=True)
        (self._root / self._MEMORY_FILE).touch(exist_ok=True)
        (self._root / self._DAILY_DIR).mkdir(exist_ok=True)

    def load_user_prompt(self) -> str:
        """Concatenate User.md and MEMORY.md (both optional) for system prompt injection."""
        parts: list[str] = []
        for name in (self._USER_FILE, self._MEMORY_FILE):
            text = (self._root / name).read_text(encoding="utf-8").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)
```

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_agent_memory.py -v
```

Expected: `5 passed`

- [ ] **Step 5: 提交**

```bash
git add workspace/agent_memory.py tests/test_agent_memory.py
git commit -m "feat(memory): add MemoryStore with read path (User.md + MEMORY.md)"
```

---

## Task 6: build_agent() 集成 + main.py hookup

**Files:**
- Modify: `main.py`（新增 `WORKSPACE_ROOT` 常量、`sys.path` 注入、`build_agent()` 工厂、`on_enter` 注入 memory 段）
- Create: `tests/test_build_agent.py`

**Interfaces:**
- Consumes: `load_persona()` (Task 2)、`scan_skills()` / `make_load_skill_tool()` (Task 3)、`load_tools()` / `load_mcp_servers()` (Task 4)
- Produces: `build_agent(workspace_root: Path) -> Agent`

- [ ] **Step 1: 写失败测试**

`tests/test_build_agent.py`：

```python
"""Integration test for build_agent() in main.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _import_main():
    """Load main.py as a module from the project root."""
    spec = importlib.util.spec_from_file_location(
        "main", Path(__file__).parent.parent / "main.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_agent_returns_agent_instance(workspace_root: Path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["main"])
    main = _import_main()
    agent = main.build_agent(workspace_root)
    # Agent 类有 instructions / tools 属性
    assert hasattr(agent, "instructions")
    assert hasattr(agent, "tools")
    # instructions 包含 SOUL 内容
    assert "小语" in agent.instructions
    # tools 至少包含 load_skill（不一定有 current_time，取决于 workspace fixture）
    tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
    assert "load_skill" in tool_names


def test_build_agent_loads_current_time_when_present(workspace_root: Path, monkeypatch):
    # fixture 已经拷了 current_time.py 过来？task 6 时还没拷；自己写一份
    tools_dir = workspace_root / "extensions" / "tools"
    (tools_dir / "current_time.py").write_text(
        "from livekit.agents import function_tool\n"
        "@function_tool()\n"
        "async def current_time() -> str:\n"
        "    '''Get current time.'''\n"
        "    return 'now'\n"
        "def register():\n"
        "    return [current_time]\n",
        encoding="utf-8",
    )
    main = _import_main()
    agent = main.build_agent(workspace_root)
    tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
    assert "current_time" in tool_names
```

- [ ] **Step 2: 跑测试确认 fail**

```bash
pytest tests/test_build_agent.py -v
```

Expected: `AttributeError: module 'main' has no attribute 'build_agent'`

- [ ] **Step 3: 修改 `main.py`**

在 `main.py` 顶部（紧跟 `import` 之后、`logger = ...` 之前）插入：

```python
import sys
from pathlib import Path as _Path

WORKSPACE_ROOT = _Path(__file__).parent / "workspace"
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))
```

然后在文件末尾（`if __name__ == "__main__":` 之前）新增 `build_agent()` 和改造 `VolcengineAgent`：

```python
def build_agent(workspace_root: _Path) -> Agent:
    """Assemble a VolcengineAgent from the 5 workspace modules.

    Called per-dispatch in entrypoint() with the real workspace root.
    """
    from agent_persona import load_persona
    from agent_skills import scan_skills, make_load_skill_tool
    from agent_extensions import load_tools, load_mcp_servers

    persona = load_persona(workspace_root)
    skills_registry = scan_skills(workspace_root / "skills")
    mcp_servers = load_mcp_servers(workspace_root / "extensions" / "mcp")
    tools = load_tools(workspace_root / "extensions" / "tools")

    # load_skill 需要 session → 用模块级 _session_holder 跟 on_enter 共享
    # （on_enter 时 self.session 还没绑定，所以用 closure 延迟取）
    def session_provider() -> AgentSession:
        assert _session_holder[0] is not None, "load_skill called before session started"
        return _session_holder[0]

    load_skill = make_load_skill_tool(skills_registry, session_provider)
    tools.append(load_skill)

    return VolcengineAgent(
        instructions=persona.combined,
        tools=tools,
        mcp_servers=mcp_servers,
    )
```

替换 `VolcengineAgent.__init__` 接受 `instructions` / `tools` / `mcp_servers`：

```python
class VolcengineAgent(Agent):
    """基于 Volcengine 模型的中文语音助手。"""

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
```

替换 `on_enter` 注入 memory 段：

```python
    async def on_enter(self) -> None:
        # 把 self.session 暴露给 build_agent() 的 session_provider
        _session_holder[0] = self.session

        # 注入 per-user 长期记忆
        from agent_memory import MemoryStore
        # user_id 来自 ctx，由 entrypoint 在调用 build_agent 后通过环境变量传递
        user_id = os.environ.get("_OPENAUZ_USER_ID", "")
        if user_id:
            memory = MemoryStore(WORKSPACE_ROOT / "users" / user_id)
            recall = memory.load_user_prompt()
            if recall:
                self.update_chat_ctx(messages=[
                    {"role": "system", "content": recall}
                ])
                logger.info(f"[Memory] 注入 user={user_id} 长期记忆 ({len(recall)} chars)")

        logger.info("[Agent] 小语进入房间，等待与用户交互")
```

文件顶部 `_session_holder`：

```python
_session_holder: list[AgentSession | None] = [None]
```

最后改 `entrypoint`：

```python
async def entrypoint(ctx: JobContext) -> None:
    """LiveKit worker 启动后由调度器调用的主入口函数。"""
    logger.info(f"[Worker] 收到任务，正在加入房间: {ctx.room.name} (pipeline={PIPELINE})")

    session = _build_session()
    # 等远端参与者加入
    import asyncio
    while not ctx.room.remote_participants:
        await asyncio.sleep(0.1)
    user_id = next(iter(ctx.room.remote_participants.values())).identity
    os.environ["_OPENAUZ_USER_ID"] = user_id
    logger.info(f"[Worker] user_id={user_id}")

    await session.start(
        agent=build_agent(WORKSPACE_ROOT),
        room=ctx.room,
        room_input_options=RoomInputOptions(
            text_input_cb=_custom_text_input_cb,
        ),
    )

    await ctx.connect()
    logger.info(f"[Worker] 已连接到房间: {ctx.room.name}")
```

> **注**：上面 `_session_holder` 走 module-level 全局变量是简化做法。生产代码应该把 `session_provider` 闭包注入到 `VolcengineAgent` 实例属性。v0.1 这样能跑通，v0.2 再重构。

- [ ] **Step 4: 跑测试确认通过**

```bash
pytest tests/test_build_agent.py -v
```

Expected: `2 passed`

- [ ] **Step 5: 跑全套测试确认无回归**

```bash
pytest tests/ -v
```

Expected: `2 + 4 + 5 + 5 + 5 + 2 = 23 passed`

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_build_agent.py
git commit -m "feat(core): add build_agent() factory + memory injection in on_enter"
```

---

## Task 7: starter persona 内容（升级占位）

**Files:**
- Modify: `workspace/persona/SOUL.md`
- Modify: `workspace/persona/AGENTS.md`
- Modify: `workspace/persona/TOOLS.md`

**Interfaces:**
- 无（纯内容）

- [ ] **Step 1: 写 SOUL.md 完整版**

`workspace/persona/SOUL.md`（替换 Task 1 的占位）：

```markdown
# SOUL — 小语

你是"小语"，一个中文语音助手。

## 性格
- 友好、耐心、不急躁
- 自嘲幽默但不轻浮
- 偶尔主动关心用户状态（疲惫、心情），但不过度

## 语气
- 自然口语，像跟朋友聊天
- 短句优先，单条回复不超过 3 句话
- 不堆叠礼貌词（"您好，请问有什么可以帮您" → "在的，怎么了？")

## 边界
- 不假装是人类
- 不主动给长列表 / 大段代码
- 不使用 emoji 和 Markdown 格式
```

- [ ] **Step 2: 写 AGENTS.md 完整版**

`workspace/persona/AGENTS.md`：

```markdown
# AGENTS — 行为规则

## 必做
- 涉及工具能力时**先调工具**再回答，不要凭印象答
- 听到"现在几点了" / "今天几号" → 调 `current_time`
- 听到"加载 XX skill" / "用 XX 模式" → 调 `load_skill`
- 不确定时反问，不编

## 不做
- 不连续追问超过 2 个澄清问题
- 不复述用户问题再回答
- 不主动给学习建议 / 鸡汤
- 不读出文件路径 / 配置项
```

- [ ] **Step 3: 写 TOOLS.md 完整版**

`workspace/persona/TOOLS.md`：

```markdown
# TOOLS — 工具使用说明

## `current_time`
- 触发词："几点"、"几号"、"现在的时间"
- 必调，**不要**自己估算

## `load_skill`
- 触发词："加载 XX skill"、"切换到 XX 模式"、"用 XX 方式"
- 参数是 skill 的 `name`（小写，蛇形/短横线）
- 加载后该 skill 的指引会注入对话上下文，可直接按其指示回答
- 找不到对应 name 时向用户说明已有哪些 skill
```

- [ ] **Step 4: 跑测试确认无回归**

```bash
pytest tests/ -v
```

Expected: `23 passed`（无回归）

- [ ] **Step 5: 提交**

```bash
git add workspace/persona/
git commit -m "feat(persona): upgrade SOUL/AGENTS/TOOLS to starter content"
```

---

## Task 8: 第一个 tool — current_time

**Files:**
- Create: `workspace/extensions/tools/current_time.py`

**Interfaces:**
- Consumes: `register() -> list[Tool]` 约定（Task 4）
- Produces: 名为 `current_time` 的 @function_tool

- [ ] **Step 1: 写 tool**

`workspace/extensions/tools/current_time.py`：

```python
"""current_time tool — 告诉用户现在的日期时间。"""
from __future__ import annotations

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

- [ ] **Step 2: 跑测试确认 tool 被自动加载**

`tests/test_build_agent.py` 已覆盖；只需重跑确认：

```bash
pytest tests/test_build_agent.py -v
```

Expected: `2 passed`（第二个测试 `test_build_agent_loads_current_time_when_present` 现在能通过实际 workspace，**可能**需要把它移出"自建 current_time"分支，让 fixture 直接带上）

> **小修**：把 `test_build_agent_loads_current_time_when_present` 改为依赖 fixture（既然 Task 1 已拷 workspace，Task 8 又写了 current_time.py，fixture 应已包含）。如果 fixture 还没自动包含（拷的是 git 跟踪前的旧 workspace），就在 conftest 加 `current_time.py` 的 stub。
>
> 简化做法：把 `test_build_agent_loads_current_time_when_present` 改为**期望**它存在；如果不存在，**断言失败**而不是"自己写"。

修改 `tests/test_build_agent.py` 第二个测试为：

```python
def test_build_agent_loads_current_time(workspace_root: Path, monkeypatch):
    main = _import_main()
    agent = main.build_agent(workspace_root)
    tool_names = [t.__name__ for t in agent.tools if hasattr(t, "__name__")]
    # Task 8 写入了 current_time.py → 应该被自动加载
    assert "current_time" in tool_names, f"expected current_time, got {tool_names}"
```

- [ ] **Step 3: 跑测试确认通过**

```bash
pytest tests/test_build_agent.py::test_build_agent_loads_current_time -v
```

Expected: `PASSED`

- [ ] **Step 4: 跑全套测试**

```bash
pytest tests/ -v
```

Expected: `24 passed`（新增 1 个 case）

- [ ] **Step 5: 提交**

```bash
git add workspace/extensions/tools/current_time.py tests/test_build_agent.py
git commit -m "feat(tools): add current_time tool (first @function_tool)"
```

---

## Task 9: 第一个 skill — weather

**Files:**
- Create: `workspace/skills/weather/SKILL.md`

**Interfaces:**
- Consumes: SKILL.md frontmatter 约定（Task 3）
- Produces: 名为 `weather` 的 SkillDef

- [ ] **Step 1: 写 SKILL.md**

`workspace/skills/weather/SKILL.md`：

```markdown
---
name: weather
description: 天气查询模式。告诉用户"支持北京/上海/广州/深圳四城，其他城市暂不支持"。仅在用户问天气时进入。
---

# 天气模式

当用户问某城市天气时：

1. 确认城市是否在白名单：北京、上海、广州、深圳
2. 在的话用 `get_weather` 工具查（如果加载了），没有就告诉用户暂不支持
3. 回答时给出：天气、温度、是否需要带伞/防晒的简短提醒

不在白名单时直接说"目前只支持北上广深，其他城市还在接入中"。

不要在用户没问天气时主动聊天气。
```

- [ ] **Step 2: 跑测试确认 skill 被注册**

```bash
pytest tests/test_agent_skills.py -v
```

Expected: `5 passed`（`test_scan_skills_finds_skill` 现在能扫到 weather）

- [ ] **Step 3: 跑全套测试**

```bash
pytest tests/ -v
```

Expected: `24 passed`

- [ ] **Step 4: 提交**

```bash
git add workspace/skills/weather/SKILL.md
git commit -m "feat(skills): add weather skill (first SKILL package)"
```

---

## Task 10: 端到端 smoke test

**Files:**
- Create: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: 上面所有 9 个任务产出的模块
- Produces: 验证 "workspace 准备好 → build_agent 组装 → skill 加载 → tool 注册 → memory 读路径" 全链路无错

- [ ] **Step 1: 写 smoke test**

`tests/test_end_to_end.py`：

```python
"""End-to-end smoke test: verify all 5 modules + content wire up correctly.

Doesn't require LiveKit or Volcengine — exercises everything offline.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _import_main():
    spec = importlib.util.spec_from_file_location(
        "main", Path(__file__).parent.parent / "main.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_end_to_end_wiring(workspace_root: Path, monkeypatch):
    """Build an agent and verify it has: persona, tools, skills, mcp slot."""
    main = _import_main()

    # Step 1: persona loads
    from agent_persona import load_persona
    p = load_persona(workspace_root)
    assert "小语" in p.combined
    assert "current_time" in p.tools_guide  # 工具说明里有 current_time

    # Step 2: skills register
    from agent_skills import scan_skills
    skills = scan_skills(workspace_root / "skills")
    assert "weather" in skills
    assert "天气" in skills["weather"].body  # weather skill 提到天气

    # Step 3: extensions load
    from agent_extensions import load_tools
    tools = load_tools(workspace_root / "extensions" / "tools")
    assert any(getattr(t, "__name__", "") == "current_time" for t in tools)

    # Step 4: build_agent
    agent = main.build_agent(workspace_root)
    tool_names = [getattr(t, "__name__", "") for t in agent.tools]
    assert "current_time" in tool_names
    assert "load_skill" in tool_names  # load_skill 是 build_agent 注入的

    # Step 5: memory read path
    from agent_memory import MemoryStore
    # 模拟一个用户的 user_root（写一份 User.md）
    user_root = workspace_root / "users" / "alice"
    user_root.mkdir(parents=True, exist_ok=True)
    (user_root / "User.md").write_text("name: alice\n住在北京\n", encoding="utf-8")
    store = MemoryStore(user_root)
    prompt = store.load_user_prompt()
    assert "alice" in prompt
    assert "北京" in prompt


def test_agent_instructions_contain_all_layers(workspace_root: Path, monkeypatch):
    """build_agent 输出的 instructions 应包含 persona + （on_enter 注入的 memory）。

    注：on_enter 的 memory 注入需要 self.session，smoke test 不跑 livekit，
    只验证 build_agent 阶段的内容。memory 注入由集成测试覆盖。
    """
    main = _import_main()
    agent = main.build_agent(workspace_root)
    # 段 A — persona
    assert "小语" in agent.instructions
    assert "current_time" in agent.instructions
    # 不应包含"默认 fallback"那条 hardcoded 文案
    assert "名字叫小语" in agent.instructions or "小语" in agent.instructions
```

- [ ] **Step 2: 跑 smoke test**

```bash
pytest tests/test_end_to_end.py -v
```

Expected: `2 passed`

- [ ] **Step 3: 跑全套测试**

```bash
pytest tests/ -v
```

Expected: `26 passed`（之前 24 + 2 个新 smoke test）

- [ ] **Step 4: 手动 sanity check — import main 不报错**

```bash
cd /Users/pz/workspace/openvox
source .venv/bin/activate
python -c "import sys; sys.path.insert(0, 'workspace'); import main; print('OK', main.WORKSPACE_ROOT)"
```

Expected: `OK /Users/pz/workspace/openvox/workspace`

- [ ] **Step 5: 提交**

```bash
git add tests/test_end_to_end.py
git commit -m "test(e2e): add end-to-end smoke test for full wiring"
```

---

## 验证清单（plan 完成后跑一遍）

```bash
cd /Users/pz/workspace/openvox
source .venv/bin/activate

# 1. 全套测试
pytest tests/ -v
# 期望：26 passed

# 2. import sanity
python -c "import main; print(main.WORKSPACE_ROOT)"
# 期望：/Users/pz/workspace/openvox/workspace

# 3. 模块独立 import
python -c "import sys; sys.path.insert(0, 'workspace'); from agent_persona import load_persona; from agent_skills import scan_skills; from agent_extensions import load_tools; from agent_memory import MemoryStore; print('all 4 modules importable')"
# 期望：all 4 modules importable

# 4. 启动 worker（不需要 dispatch）
python main.py dev
# 期望：worker 启动，注册到 LiveKit server，没崩
```

---

## 附录 A：明确不做（留给后续 plan）

| 项 | 原因 | 触发 plan |
|---|---|---|
| `MemoryStore` 写路径（`append_today` / `commit_today_to_memory`）| v0.1 范围控制 | v0.2：memory 读写闭环 |
| `on_exit` 摘要逻辑 | 依赖写路径 | v0.2 |
| `bash` tool | 安全模型需要更多设计 | v0.2：tool 安全 + 沙箱 |
| MCP server 真接入 | 需要真实 MCP server | v0.2：MCP 集成 |
| `tests/e2e_generate_reply.py` 修复 | 与本 plan 无关 | 单独任务 |
| `_session_holder` 全局变量改闭包 | 简化实现 | v0.2：refactor session injection |
| 多用户同房间 | v0.1 取第一个 + warn | v0.3：multi-user |
| memory 迁移到 `~/.openvox/` | 用户决定"先放项目目录" | v0.3：data 迁移 |

## 附录 B：依赖关系图

```
Task 1 (bootstrap)
  ├─→ Task 2 (persona)
  ├─→ Task 3 (skills)
  ├─→ Task 4 (extensions)
  ├─→ Task 5 (memory)
  └─→ Tasks 2/3/4/5 全部完成
        └─→ Task 6 (build_agent + main.py)
              ├─→ Task 7 (persona 内容)
              ├─→ Task 8 (current_time tool)
              └─→ Task 9 (weather skill)
                    └─→ Task 10 (smoke test)
```

任意 Task 失败都可以独立回滚到上一个稳定 commit，10 个 commit 各自可独立 review。
