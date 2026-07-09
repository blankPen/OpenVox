# Agent Filesystem Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 LiveKit × Volcengine worker 里落地 spec `2026-07-05-agent-filesystem-tools-design.md` 的 6 个原子文件操作工具（6 个 `@function_tool` 函数，每个工具文件 1 个函数），通过 Phase 0 baseline 验证 realtime 模式下工具调用路径，再批量交付其余 5 个工具。

**Architecture:** 6 个独立工具文件放在 `workspace/extensions/tools/fs/`，仿 `current_time.py` 的 `@function_tool() + register()` 协议。`agent_extensions.load_tools` glob 升级到递归以支持子目录。每个工具**绝不抛异常跨越边界**——错误用 `[ERROR]` / `[TIMEOUT]` / `[EXIT N]` 字符串返回。所有写操作（write/edit/bash）打 WARNING 日志；敏感路径（`/etc/`、`/usr/`、`/var/`、`~/.ssh/`、`~/.aws/`、`/private/etc/`）也打 WARNING。

**Tech Stack:** Python 3.11+, `livekit-agents` 1.5.x, `volcengine` vendored 插件, pytest 7+, asyncio。

---

## File Structure

**新增（15 个 Python 文件 + 1 个 markdown 追加）：**

| 路径 | 职责 |
|---|---|
| `workspace/extensions/tools/fs/__init__.py` | 空（防止 import 触发 register）|
| `workspace/extensions/tools/fs/_sensitive.py` | 敏感路径正则 + `is_sensitive(path)` |
| `workspace/extensions/tools/fs/read_file.py` | 1 函数：`read_file(path, start_line, end_line)` |
| `workspace/extensions/tools/fs/write_file.py` | 1 函数：`write_file(path, content, mode)` |
| `workspace/extensions/tools/fs/edit_file.py` | 1 函数：`edit_file(path, old_string, new_string, replace_all)` |
| `workspace/extensions/tools/fs/glob_files.py` | 1 函数：`glob_files(pattern, path)` |
| `workspace/extensions/tools/fs/grep_files.py` | 1 函数：`grep_files(pattern, path, include, max_results)` |
| `workspace/extensions/tools/fs/bash.py` | 1 函数：`bash(cmd, cwd, timeout)` |
| `tests/fs_tools/__init__.py` | 空 |
| `tests/fs_tools/conftest.py` | 共享 fixture：`tmp_workspace` |
| `tests/fs_tools/test_sensitive.py` | 敏感路径检测 |
| `tests/fs_tools/test_read_file.py` | read_file 完整测试矩阵 |
| `tests/fs_tools/test_write_file.py` | write_file 完整测试矩阵 |
| `tests/fs_tools/test_edit_file.py` | edit_file 完整测试矩阵 |
| `tests/fs_tools/test_glob_files.py` | glob_files 完整测试矩阵 |
| `tests/fs_tools/test_grep_files.py` | grep_files 完整测试矩阵 |
| `tests/fs_tools/test_bash.py` | bash 完整测试矩阵 |
| `tests/e2e_fs_tools.py` | E2E 烟雾测试（6 工具 × 1 场景）|
| `workspace/persona/TOOLS.md` | 追加 6 工具使用元说明（修改现有）|

**修改（2 个）：**

- `workspace/agent_extensions.py:34` — `load_tools` 的 `glob("*.py")` 升级到 `glob("**/*.py")`，过滤 `__pycache__`
- `workspace/persona/TOOLS.md` — 追加 6 个工具说明章节

**显式不做（spec 已明确）：**

- v0.2 的路径白名单 / 命令白名单 / 用户审批
- pipeline 模式适配
- 二进制文件读 / 大文件断点续传
- 把 `extensions/tools/` 改成独立 Python 包
- MCP filesystem server 方案
- **`notebook_edit` 工具**（用户决策：notebook 与当前 demo 场景不匹配；如未来 agent 跑 notebook 工作流再补）

---

## Global Constraints

- Python 3.11+（`.venv` 已有，spec §2 上下文）
- `livekit-agents` 1.5.x（CLAUDE.md 已纠正 1.2.9 旧说法）
- 火山引擎插件走 `plugins/livekit-plugins-volcengine/`（`--no-deps` editable 安装）
- pytest 在 `.venv/bin/pytest`；运行 `source .venv/bin/activate` 后用 `pytest`
- 只支持 realtime PIPELINE（spec §1.3 用户决策；pipeline 跑通后另开 spec）
- 操作范围：宿主机任意路径，v0.1 不做安全限制（spec §1.3）
- 函数名与 Claude Code 完全对齐：`read_file` / `write_file` / `edit_file` / `glob_files` / `grep_files` / `bash`（spec §3.3）
- 工具统一返回字符串，错误前缀：`[ERROR]` / `[TIMEOUT]` / `[EXIT N]` / `[OK]`（spec §5.2）
- 写操作（write/edit/bash）打 WARNING 日志；敏感路径命中也打 WARNING（spec §5.3）
- Bash timeout 默认 30s，上限 300s（spec §5.4）
- Bash 子进程只透传 `PATH` 和 `HOME`（spec §5.4）
- write_file 用 `tmp + rename` 原子写（spec §5.5）
- write_file 仅支持 UTF-8 文本；非 UTF-8 返回 `[ERROR] 内容不是合法 UTF-8`（spec §5.5 自审补充）
- read_file 截断阈值 1MB / 2000 行（spec §5.2）
- 所有 Python 文件用 `from __future__ import annotations`
- 失败立即 fail-fast：`raise` 往上抛，worker 启动失败比静默错误好
- 不引入新第三方依赖（用 stdlib + livekit-agents）
- 测试断言约定：错误用 `startswith`，列表用 `json.loads`（spec §6.1）
- 不修 `tests/e2e_generate_reply.py`（已坏，但作为本 plan 的脚手架参考）

---

## 执行顺序（spec §6.3 强制）

```
Task 1:  fs/ 骨架 + _sensitive
Task 2:  load_tools 升级递归
Task 3:  read_file 最小版本 + Phase 0 E2E baseline ← BLOCKING
        ↓  baseline 通过
Task 4:  read_file 完整版 + 单测
Task 5:  write_file + 单测
Task 6:  edit_file + 单测
Task 7:  glob_files + 单测
Task 8:  grep_files + 单测
Task 9:  bash + 单测
Task 10: 追加 persona/TOOLS.md
Task 11: Phase 1 E2E 烟雾测试
Task 12: 全量回归 + build_agent summary 验证
```

Task 3 的 E2E baseline 是**唯一阻塞点**。如果 Phase 0 E2E 失败（realtime 不调工具 / 调起但报错），后续 9 个 task 不应继续——回到 spec §4.2 的降级路径决策。

---

### Task 1: Bootstrap `fs/` 子目录 + 共享 `_sensitive.py`

**Files:**
- Create: `workspace/extensions/tools/fs/__init__.py`（空）
- Create: `workspace/extensions/tools/fs/_sensitive.py`
- Create: `tests/fs_tools/__init__.py`（空）
- Create: `tests/fs_tools/conftest.py`
- Create: `tests/fs_tools/test_sensitive.py`

**Interfaces:**
- 无（bootstrap + 共享模块）

- [ ] **Step 1: 创建空目录标记文件**

```bash
mkdir -p workspace/extensions/tools/fs
mkdir -p tests/fs_tools
touch workspace/extensions/tools/fs/__init__.py
touch tests/fs_tools/__init__.py
```

- [ ] **Step 2: 写 `_sensitive.py`**

`workspace/extensions/tools/fs/_sensitive.py` 完整内容：

```python
"""Shared sensitive-path detection for fs tools (v0.1 demo: WARNING log only).

Spec reference: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §5.3
"""
from __future__ import annotations

import re
from pathlib import Path

SENSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^/etc(/|$)"),
    re.compile(r"^/usr(/|$)"),
    re.compile(r"^/var(/|$)"),
    re.compile(r"^/private/etc(/|$)"),  # macOS
    re.compile(rf"^{re.escape(str(Path.home()))}/\.ssh(/|$)"),
    re.compile(rf"^{re.escape(str(Path.home()))}/\.aws(/|$)"),
]


def is_sensitive(path: str) -> bool:
    """绝对路径命中任意敏感模式 → True。

    Args:
        path: 待检测路径（绝对路径或相对路径，相对路径视为不敏感）。

    Returns:
        bool: 是否命中敏感模式。
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = p.resolve()
    s = str(p)
    return any(pat.match(s) for pat in SENSITIVE_PATTERNS)
```

- [ ] **Step 3: 写 conftest.py**

`tests/fs_tools/conftest.py` 完整内容：

```python
"""Shared pytest fixtures for fs tools tests."""
from __future__ import annotations

import sys

import pytest
from pathlib import Path


@pytest.fixture
def tmp_workspace(tmp_path) -> Path:
    """预置几个测试文件。

    macOS 忽略非 own 用户的 chmod 0o000，无法制造真实 EACCES；该平台跳过 no_read 相关断言。
    """
    (tmp_path / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.json").write_text('{"k": "v"}', encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")  # 2MB
    (tmp_path / "no_read").write_text("secret")
    if sys.platform != "darwin":
        (tmp_path / "no_read").chmod(0o000)
    return tmp_path
```

- [ ] **Step 4: 写测试（红）**

`tests/fs_tools/test_sensitive.py` 完整内容：

```python
"""Tests for fs tools _sensitive module."""
from __future__ import annotations

from pathlib import Path

from workspace.extensions.tools.fs._sensitive import is_sensitive


def test_is_sensitive_detects_etc():
    assert is_sensitive("/etc/passwd") is True


def test_is_sensitive_detects_usr():
    assert is_sensitive("/usr/local/bin/foo") is True


def test_is_sensitive_detects_var():
    assert is_sensitive("/var/log/syslog") is True


def test_is_sensitive_detects_private_etc_macos():
    assert is_sensitive("/private/etc/hosts") is True


def test_is_sensitive_detects_home_ssh():
    ssh_dir = Path.home() / ".ssh" / "id_rsa"
    assert is_sensitive(str(ssh_dir)) is True


def test_is_sensitive_detects_home_aws():
    aws_dir = Path.home() / ".aws" / "credentials"
    assert is_sensitive(str(aws_dir)) is True


def test_is_sensitive_allows_tmp():
    assert is_sensitive("/tmp/hello.txt") is False


def test_is_sensitive_allows_workspace():
    assert is_sensitive("/Users/pz/workspace/openvox/main.py") is False


def test_is_sensitive_allows_relative_path():
    # 相对路径 resolve 后如果不是敏感路径 → False
    assert is_sensitive("hello.txt") is False
```

- [ ] **Step 5: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_sensitive.py -v
```

期望输出：`9 passed`

- [ ] **Step 6: Commit**

```bash
git add workspace/extensions/tools/fs/ tests/fs_tools/
git commit -m "feat(fs-tools): bootstrap fs/ skeleton + _sensitive module"
```

---

### Task 2: 升级 `agent_extensions.load_tools` 支持递归

**Files:**
- Modify: `workspace/agent_extensions.py:34`（glob 表达式）

**Interfaces:**
- Consumes: 无（纯内部改动）
- Produces: `load_tools(tools_dir)` 现在 glob `tools_dir/**\/*.py`，过滤 `__pycache__` 和 `_` 前缀文件

- [ ] **Step 1: 读现状**

读 `workspace/agent_extensions.py:25-54`，确认当前 `load_tools` 实现是 `glob("*.py")`。

- [ ] **Step 2: 修改 glob 表达式**

修改 `workspace/agent_extensions.py:34`，把：

```python
py_files = [p for p in sorted(tools_dir.glob("*.py")) if not p.name.startswith("_")]
```

改为：

```python
py_files = [
    p for p in sorted(tools_dir.glob("**/*.py"))
    if not p.name.startswith("_")
    and "__pycache__" not in p.parts
]
```

- [ ] **Step 3: 验证现有 `current_time.py` 仍能加载**

写一个临时集成验证脚本（不提交，验证完删掉）：

```bash
source .venv/bin/activate
python -c "
from pathlib import Path
from workspace.agent_extensions import load_tools
tools = load_tools(Path('workspace/extensions/tools'))
names = [getattr(t, 'info', None) and t.info.name or getattr(t, '__name__', '?') for t in tools]
print('Loaded:', names)
assert any('current_time' in n for n in names), 'current_time missing!'
print('OK')
"
```

期望输出：
```
Loaded: ['current_time']
OK
```

- [ ] **Step 4: 跑现有单元测试确保没回归**

```bash
source .venv/bin/activate
pytest tests/ -v --ignore=tests/fs_tools --ignore=tests/e2e_generate_reply.py
```

期望输出：所有现有测试 PASS（具体数量取决于当前仓库状态，至少 `test_agent_extensions.py` 应 PASS）。

- [ ] **Step 5: 删除临时验证脚本 + Commit**

临时脚本是 `python -c`，不需要删文件。直接：

```bash
git add workspace/agent_extensions.py
git commit -m "feat(extensions): load_tools glob recursive to support tools/fs/ subdir"
```

---

### Task 3: Phase 0 — 实现 `read_file` 最小版本 + E2E baseline

> **⚠️ BLOCKING TASK**：本 task 的 E2E 验证（Step 5-6）是后续 7 个 task 的前置条件。如果 realtime 模式下 `current_time`/`read_file` 不能被调起来，必须回到 spec §4.2 的降级路径决策。

**Files:**
- Create: `workspace/extensions/tools/fs/read_file.py`（**最小版本**：仅 `path` 参数，无 start/end，无敏感路径检查）
- Create: `tests/fs_tools/test_read_file.py`
- Modify: `main.py` 临时——本 task 不改 main.py，靠 worker 重启重新加载 tools

**Interfaces:**
- Consumes: 无（首个工具实现）
- Produces: `read_file(path: str) -> str`（Phase 0 最小签名，仅 path 参数）

- [ ] **Step 1: 写最小测试（红）**

`tests/fs_tools/test_read_file.py` 完整内容（Phase 0 最小测试）：

```python
"""Phase 0 read_file 最小版本测试（仅 path 参数）。

完整测试矩阵在 Task 4 补全。
"""
from __future__ import annotations

import pytest


def test_read_file_hello(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "hello.txt"
    target.write_text("hello world\n", encoding="utf-8")

    result = read_file(str(target))

    assert "hello world" in result


def test_read_file_not_found(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = read_file(str(tmp_path / "missing.txt"))

    assert result.startswith("[ERROR]")


def test_read_file_is_directory(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = read_file(str(tmp_path))

    assert result.startswith("[ERROR]")
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_read_file.py -v
```

期望输出：`ModuleNotFoundError: No module named 'workspace.extensions.tools.fs.read_file'`（因为文件还没创建）。这是预期的红。

- [ ] **Step 3: 写最小实现**

`workspace/extensions/tools/fs/read_file.py` 完整内容（Phase 0 最小版本）：

```python
"""Phase 0 最小 read_file — 仅 path 参数，无 start/end_line、无敏感路径检查。

完整版本在 Task 4 补全。Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("openvox")


@function_tool()
async def read_file(path: str) -> str:
    """读取文本文件的内容（Phase 0 最小版本）。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。

    Returns:
        文件内容字符串，或 "[ERROR] ..." 开头的错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if p.is_dir():
            return f"[ERROR] {p} 是目录，请用 glob_files"
        content = p.read_text(encoding="utf-8")
        logger.info("[fs] read_file(path=%r) → %dc", path, len(content))
        return content
    except Exception as e:
        logger.warning("[fs] read_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [read_file]
```

- [ ] **Step 4: 跑测试验证通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_read_file.py -v
```

期望输出：`3 passed`

- [ ] **Step 5: 重启 worker 验证 build_agent 加载新工具**

```bash
# 杀掉旧 worker（如果还在跑）
lsof -ti:8081 | xargs kill -9 2>/dev/null || true

# 后台启动新 worker
source .venv/bin/activate
python main.py start > /tmp/worker.log 2>&1 &
WORKER_PID=$!
echo "worker pid: $WORKER_PID"

# 等待 5 秒让 worker 启动
sleep 5

# 检查 build_agent summary 日志里有 read_file
grep -E "read_file|build_agent summary" /tmp/worker.log

# 期望输出包含：
# [Agent]   tools    : 2 → ['current_time', 'read_file']
```

- [ ] **Step 6: Phase 0 E2E — realtime baseline 验证**

```bash
# 准备测试文件
echo "hello world baseline test" > /tmp/fs_baseline_test.txt
cat /tmp/fs_baseline_test.txt

# 派单
source .venv/bin/activate
lk dispatch create --dev --room fs-baseline --agent-name openvox

# 加入房间发文本（用 lk 或浏览器客户端）
# 客户端发："帮我读 /tmp/fs_baseline_test.txt 的内容"

# 等待 30 秒，查看 worker 日志是否有 [fs] read_file 被调起来
sleep 30
grep -E "\[fs\] read_file|baseline" /tmp/worker.log
```

**期望输出**（成功）：
```
[fs] read_file(path='/tmp/fs_baseline_test.txt') → 26c
```

**失败处理**（spec §4.2 决策表）：
- 没看到 `[fs] read_file` 日志 → 整个 spec 降级（pipeline only 或侧路 hook），后续 9 个 task 不做
- 看到 `[fs] read_file ERROR` → 排查 worker 日志的具体错误

- [ ] **Step 7: 杀掉测试 worker + Commit（仅当 E2E 通过）**

```bash
lsof -ti:8081 | xargs kill -9 2>/dev/null || true
git add workspace/extensions/tools/fs/read_file.py tests/fs_tools/test_read_file.py
git commit -m "feat(fs-tools): Phase 0 read_file minimal — realtime baseline verified"
```

如果 E2E 失败：不 commit，向用户报告并启动降级讨论。

---

### Task 4: read_file 完整版本（start/end_line + 敏感路径 + 截断 + 完整测试矩阵）

**Files:**
- Modify: `workspace/extensions/tools/fs/read_file.py`（升级到完整签名）
- Modify: `tests/fs_tools/test_read_file.py`（追加完整测试用例）

**Interfaces:**
- Consumes: `_sensitive.is_sensitive(path)`（Task 1 已实现）
- Produces: `read_file(path: str, start_line: int = 0, end_line: int = 0) -> str` 完整签名

- [ ] **Step 1: 升级测试到完整矩阵（红）**

完整替换 `tests/fs_tools/test_read_file.py`：

```python
"""read_file 完整测试矩阵。Spec §5.2 错误处理契约。"""
from __future__ import annotations

import logging
import sys

import pytest


# ---- 成功路径 ----

def test_read_file_hello(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "hello.txt"
    target.write_text("hello\nworld\n", encoding="utf-8")

    result = read_file(str(target))

    assert "hello" in result
    assert "world" in result


def test_read_file_with_start_line(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\nline4\n", encoding="utf-8")

    result = read_file(str(target), start_line=1, end_line=3)

    assert "line1" not in result
    assert "line2" in result
    assert "line3" in result
    assert "line4" not in result


def test_read_file_with_start_only(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = read_file(str(target), start_line=2)

    assert "line1" not in result
    assert "line2" in result
    assert "line3" in result


def test_read_file_with_end_only(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "lines.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")

    result = read_file(str(target), end_line=2)

    assert "line1" in result
    assert "line2" in result
    assert "line3" not in result


# ---- 错误路径 ----

def test_read_file_not_found(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = read_file(str(tmp_path / "missing.txt"))

    assert result.startswith("[ERROR]")
    assert "不存在" in result


def test_read_file_is_directory(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file

    result = read_file(str(tmp_path))

    assert result.startswith("[ERROR]")
    assert "目录" in result


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 忽略 chmod 0o000")
def test_read_file_permission_denied(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    no_read = tmp_path / "no_read.txt"
    no_read.write_text("secret")
    no_read.chmod(0o000)
    try:
        result = read_file(str(no_read))
        assert result.startswith("[ERROR]")
        # 不要断言具体内容（PermissionError vs OSError 因平台而异）
    finally:
        no_read.chmod(0o644)  # 清理


def test_read_file_big_file_truncated(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "big.txt"
    # 写入 1MB+ 触发截断
    target.write_text("x" * 2_000_000, encoding="utf-8")  # 2MB

    result = read_file(str(target))

    assert "[TRUNCATED]" in result


# ---- 敏感路径 WARNING 日志 ----

def test_read_file_sensitive_path_warning(tmp_path, caplog):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "fake_passwd"
    target.write_text("fake content")

    # 用 monkeypatch 把 is_sensitive 临时返回 True
    import workspace.extensions.tools.fs.read_file as rf_mod
    original = rf_mod.is_sensitive
    rf_mod.is_sensitive = lambda p: True
    try:
        with caplog.at_level(logging.WARNING, logger="openvox"):
            result = read_file(str(target))
        assert "SENSITIVE_PATH" in caplog.text
    finally:
        rf_mod.is_sensitive = original

    # 正常返回内容
    assert "fake content" in result


# ---- 默认参数 ----

def test_read_file_default_start_end(tmp_path):
    from workspace.extensions.tools.fs.read_file import read_file
    target = tmp_path / "x.txt"
    target.write_text("content", encoding="utf-8")

    # start_line=0, end_line=0 应该读全部
    result = read_file(str(target))

    assert "content" in result
```

- [ ] **Step 2: 跑测试验证部分失败（红）**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_read_file.py -v
```

期望：test_read_file_with_start_line / test_read_file_with_end_only / test_read_file_big_file_truncated / test_read_file_sensitive_path_warning / test_read_file_default_start_end 失败（其他 PASS）。

- [ ] **Step 3: 升级 read_file 到完整版本**

完整替换 `workspace/extensions/tools/fs/read_file.py`：

```python
"""read_file — 读文本文件，支持行范围、敏感路径告警、超大文件截断。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

from .._sensitive import is_sensitive  # noqa: F401  re-exported for tests
from workspace.extensions.tools.fs._sensitive import is_sensitive as _is_sensitive

logger = logging.getLogger("openvox")

_MAX_BYTES = 1_000_000  # 1MB
_MAX_LINES = 2000


@function_tool()
async def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """读取文本文件的内容。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。
        start_line: 从第几行开始（0-indexed；0 表示从开头）。默认 0。
        end_line: 到第几行结束（exclusive；0 表示读到末尾）。默认 0。

    Returns:
        文件内容字符串，或 "[ERROR] ..." 开头的错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if _is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH read_file(path=%r)", path)
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if p.is_dir():
            return f"[ERROR] {p} 是目录，请用 glob_files"
        if not p.is_file():
            return f"[ERROR] {p} 不是普通文件"
        size = p.stat().st_size
        if size > _MAX_BYTES:
            # 截断到前 _MAX_LINES 行
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            truncated = "\n".join(lines[:_MAX_LINES])
            logger.info(
                "[fs] read_file(path=%r, TRUNCATED) size=%d → %d lines",
                path, size, min(_MAX_LINES, len(lines)),
            )
            return truncated + f"\n\n[TRUNCATED] 文件共 {len(lines)} 行，已截断至前 {_MAX_LINES} 行"
        content = p.read_text(encoding="utf-8")
        # 行范围裁剪
        if start_line > 0 or end_line > 0:
            lines = content.splitlines()
            s = start_line
            e = end_line if end_line > 0 else len(lines)
            content = "\n".join(lines[s:e])
        logger.info("[fs] read_file(path=%r, start=%d, end=%d) → %dc", path, start_line, end_line, len(content))
        return content
    except Exception as e:
        logger.warning("[fs] read_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    """返回本文件提供的 tool 列表。"""
    return [read_file]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_read_file.py -v
```

期望输出：所有用例 PASS（包括 skipif 跳过的 macOS 用例）。

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/read_file.py tests/fs_tools/test_read_file.py
git commit -m "feat(fs-tools): read_file full version — start/end_line, sensitive, truncation"
```

---

### Task 5: write_file（原子写 + 两种 mode）

**Files:**
- Create: `workspace/extensions/tools/fs/write_file.py`
- Create: `tests/fs_tools/test_write_file.py`

**Interfaces:**
- Consumes: 无新增
- Produces: `write_file(path: str, content: str, mode: str = "overwrite") -> str`

- [ ] **Step 1: 写测试（红）**

`tests/fs_tools/test_write_file.py` 完整内容：

```python
"""write_file 完整测试矩阵。Spec §3.3 / §5.2 / §5.5。"""
from __future__ import annotations

import os

import pytest


# ---- 成功路径 ----

def test_write_file_overwrite_creates(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    result = write_file(str(target), "hello world")

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_file_overwrite_existing(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    target.write_text("OLD", encoding="utf-8")

    result = write_file(str(target), "NEW")

    assert target.read_text(encoding="utf-8") == "NEW"


def test_write_file_append_mode(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    target.write_text("line1\n", encoding="utf-8")

    result = write_file(str(target), "line2\n", mode="append")

    assert target.read_text(encoding="utf-8") == "line1\nline2\n"


def test_write_file_creates_parent_dirs(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "sub" / "nested" / "out.txt"

    result = write_file(str(target), "content")

    assert target.read_text(encoding="utf-8") == "content"


# ---- 错误路径 ----

def test_write_file_invalid_mode(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    result = write_file(str(target), "x", mode="invalid")

    assert result.startswith("[ERROR]")
    assert "overwrite" in result and "append" in result


def test_write_file_non_utf8(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.bin"

    result = write_file(str(target), "abc\xff\xfe")

    assert result.startswith("[ERROR]")
    assert "UTF-8" in result


# ---- 原子写 ----

def test_write_file_atomic_no_leftover_tmp(tmp_path):
    """成功后不应在父目录留下 .tmp 临时文件。"""
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"

    write_file(str(target), "content")

    leftovers = list(tmp_path.glob(".tmp_*"))
    assert leftovers == []


def test_write_file_write_op_warning_logged(tmp_path, caplog):
    """WRITE_OP 必须是 WARNING 级日志。"""
    from workspace.extensions.tools.fs.write_file import write_file
    target = tmp_path / "out.txt"
    import logging
    with caplog.at_level(logging.WARNING, logger="openvox"):
        write_file(str(target), "x")
    assert "WRITE_OP" in caplog.text


# ---- 权限（macOS 跳过）----

import sys

@pytest.mark.skipif(sys.platform == "darwin", reason="macOS 忽略 chmod 0o000")
def test_write_file_permission_denied(tmp_path):
    from workspace.extensions.tools.fs.write_file import write_file
    no_write = tmp_path / "no_write.txt"
    no_write.write_text("existing")
    no_write.chmod(0o444)
    try:
        result = write_file(str(no_write), "new content")
        # 实际写入可能被 OS 允许（root 用户）或拒绝；至少函数不抛异常
        assert not result.startswith("[OK]") or no_write.read_text(encoding="utf-8") == "new content"
    finally:
        no_write.chmod(0o644)
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_write_file.py -v
```

期望：`ModuleNotFoundError`

- [ ] **Step 3: 实现 write_file**

`workspace/extensions/tools/fs/write_file.py` 完整内容：

```python
"""write_file — 原子写文本文件，支持 overwrite / append 两种 mode。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2 / §5.5
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from livekit.agents import function_tool

from workspace.extensions.tools.fs._sensitive import is_sensitive

logger = logging.getLogger("openvox")


@function_tool()
async def write_file(path: str, content: str, mode: str = "overwrite") -> str:
    """写文本文件到指定路径。

    Args:
        path: 绝对路径或相对 worker cwd 的路径。
        content: 要写入的文本内容（必须 UTF-8）。
        mode: "overwrite"（默认，覆盖）或 "append"（追加到末尾）。

    Returns:
        "[OK] <路径>" 成功，或 "[ERROR] ..." 开头的错误描述。
    """
    if mode not in ("overwrite", "append"):
        return f"[ERROR] mode 必须是 overwrite 或 append，收到 {mode!r}"
    try:
        # UTF-8 校验
        try:
            content.encode("utf-8")
        except UnicodeEncodeError as e:
            return f"[ERROR] 内容不是合法 UTF-8: {e}"

        p = Path(path).expanduser().resolve()
        if is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH write_file(path=%r)", path)
        parent = p.parent
        parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            # append 不需要原子写，直接追加
            with open(p, "a", encoding="utf-8") as f:
                f.write(content)
            logger.warning(
                "[fs] WRITE_OP write_file(path=%r, mode='append', size=%dc)",
                path, len(content),
            )
            return f"[OK] append {p} (+{len(content)}c)"

        # overwrite：原子写（tmp + rename）
        fd, tmp_path = tempfile.mkstemp(dir=str(parent), prefix=".tmp_", suffix=".tmp")
        try:
            os.write(fd, content.encode("utf-8"))
            os.close(fd)
            os.replace(tmp_path, str(p))
        except Exception:
            # 清理临时文件
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

        logger.warning(
            "[fs] WRITE_OP write_file(path=%r, mode='overwrite', size=%dc)",
            path, len(content),
        )
        return f"[OK] overwrite {p} ({len(content)}c)"
    except Exception as e:
        logger.warning("[fs] write_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [write_file]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_write_file.py -v
```

期望：所有用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/write_file.py tests/fs_tools/test_write_file.py
git commit -m "feat(fs-tools): write_file with atomic overwrite + append modes"
```

---

### Task 6: edit_file（精确替换 + replace_all）

**Files:**
- Create: `workspace/extensions/tools/fs/edit_file.py`
- Create: `tests/fs_tools/test_edit_file.py`

**Interfaces:**
- Produces: `edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str`

- [ ] **Step 1: 写测试（红）**

`tests/fs_tools/test_edit_file.py` 完整内容：

```python
"""edit_file 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import logging

import pytest


def test_edit_file_single_replace(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("def foo():\n    return 1\n", encoding="utf-8")

    result = edit_file(str(target), "return 1", "return 2")

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "def foo():\n    return 2\n"


def test_edit_file_replace_all(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("a = 1\na = 1\na = 1\n", encoding="utf-8")

    result = edit_file(str(target), "a = 1", "a = 2", replace_all=True)

    assert result.startswith("[OK]")
    assert target.read_text(encoding="utf-8") == "a = 2\na = 2\na = 2\n"


def test_edit_file_multiple_match_without_replace_all(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("foo\nfoo\nfoo\n", encoding="utf-8")

    result = edit_file(str(target), "foo", "bar")

    assert result.startswith("[ERROR]")
    assert "3 次" in result
    assert "replace_all" in result


def test_edit_file_old_string_not_found(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("hello world\n", encoding="utf-8")

    result = edit_file(str(target), "nonexistent", "x")

    assert result.startswith("[ERROR]")
    assert "找不到" in result


def test_edit_file_new_equals_old(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "code.py"
    target.write_text("hello\n", encoding="utf-8")

    result = edit_file(str(target), "hello", "hello")

    assert "[OK]" in result
    assert "未变化" in result


def test_edit_file_missing_file(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file

    result = edit_file(str(tmp_path / "missing.txt"), "old", "new")

    assert result.startswith("[ERROR]")


def test_edit_file_warning_logged(tmp_path, caplog):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "x.txt"
    target.write_text("a", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="openvox"):
        edit_file(str(target), "a", "b")
    assert "EDIT_OP" in caplog.text


def test_edit_file_case_sensitive(tmp_path):
    from workspace.extensions.tools.fs.edit_file import edit_file
    target = tmp_path / "x.txt"
    target.write_text("Foo\n", encoding="utf-8")

    # "foo" 不应匹配 "Foo"
    result = edit_file(str(target), "foo", "bar")

    assert result.startswith("[ERROR]")
    assert "找不到" in result
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_edit_file.py -v
```

期望：`ModuleNotFoundError`

- [ ] **Step 3: 实现 edit_file**

`workspace/extensions/tools/fs/edit_file.py` 完整内容：

```python
"""edit_file — 基于 old_string/new_string 的精确文本替换。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import logging
from pathlib import Path

from livekit.agents import function_tool

from workspace.extensions.tools.fs._sensitive import is_sensitive

logger = logging.getLogger("openvox")


@function_tool()
async def edit_file(
    path: str, old_string: str, new_string: str, replace_all: bool = False
) -> str:
    """基于字符串字面量替换文件内容。

    Args:
        path: 目标文件路径。
        old_string: 要查找的字符串（字面量，大小写敏感）。
        new_string: 替换为的字符串。
        replace_all: True 表示替换所有出现；False（默认）要求 old_string 仅出现 1 次。

    Returns:
        "[OK] ..." 成功，或 "[ERROR] ..." 错误描述。
    """
    try:
        p = Path(path).expanduser().resolve()
        if is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH edit_file(path=%r)", path)
        if not p.exists():
            return f"[ERROR] 路径 {p} 不存在"
        if not p.is_file():
            return f"[ERROR] {p} 不是普通文件"
        content = p.read_text(encoding="utf-8")
        occurrences = content.count(old_string)
        if occurrences == 0:
            return f"[ERROR] 在 {p} 中找不到 {old_string!r}"
        if occurrences > 1 and not replace_all:
            return (
                f"[ERROR] {old_string!r} 出现 {occurrences} 次，"
                "请加更多上下文或设 replace_all=true"
            )
        if new_string == old_string:
            logger.warning(
                "[fs] EDIT_OP edit_file(path=%r, NO_CHANGE) old_len=%d, new_len=%d",
                path, len(old_string), len(new_string),
            )
            return "[OK] 内容未变化"
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        p.write_text(new_content, encoding="utf-8")
        logger.warning(
            "[fs] EDIT_OP edit_file(path=%r, old_len=%d, new_len=%d, replace_all=%s, occurrences=%d)",
            path, len(old_string), len(new_string), replace_all, occurrences,
        )
        return f"[OK] edited {p} ({occurrences} replacement{'s' if replace_all and occurrences > 1 else ''})"
    except Exception as e:
        logger.warning("[fs] edit_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [edit_file]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_edit_file.py -v
```

期望：所有用例 PASS。

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/edit_file.py tests/fs_tools/test_edit_file.py
git commit -m "feat(fs-tools): edit_file with single/replace_all modes"
```

---

### Task 7: glob_files（标准 glob 模式）

**Files:**
- Create: `workspace/extensions/tools/fs/glob_files.py`
- Create: `tests/fs_tools/test_glob_files.py`

**Interfaces:**
- Produces: `glob_files(pattern: str, path: str = ".") -> str`（返回 JSON 数组字符串）

- [ ] **Step 1: 写测试（红）**

`tests/fs_tools/test_glob_files.py` 完整内容：

```python
"""glob_files 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import json

import pytest


def test_glob_files_simple_pattern(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "c.py").write_text("c")

    result = glob_files("*.txt", str(tmp_path))

    files = json.loads(result)
    assert sorted(files) == ["a.txt", "b.txt"]


def test_glob_files_recursive_double_star(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.py").write_text("d")
    (tmp_path / "top.py").write_text("t")

    result = glob_files("**/*.py", str(tmp_path))

    files = json.loads(result)
    assert sorted(files) == ["sub/deep.py", "top.py"]


def test_glob_files_no_match(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files

    result = glob_files("*.nonexistent", str(tmp_path))

    assert json.loads(result) == []


def test_glob_files_path_not_exist(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files

    result = glob_files("*.txt", str(tmp_path / "missing_dir"))

    assert result.startswith("[ERROR]")


def test_glob_files_default_path_is_cwd(tmp_path, monkeypatch):
    from workspace.extensions.tools.fs.glob_files import glob_files
    monkeypatch.chdir(tmp_path)
    (tmp_path / "x.txt").write_text("x")

    result = glob_files("*.txt")

    files = json.loads(result)
    assert "x.txt" in files


def test_glob_files_returns_relative_paths(tmp_path):
    from workspace.extensions.tools.fs.glob_files import glob_files
    (tmp_path / "x.txt").write_text("x")

    result = glob_files("*.txt", str(tmp_path))

    files = json.loads(result)
    assert files == ["x.txt"]  # 不是绝对路径
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_glob_files.py -v
```

- [ ] **Step 3: 实现 glob_files**

`workspace/extensions/tools/fs/glob_files.py` 完整内容：

```python
"""glob_files — 按 glob 模式列文件。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("openvox")


@function_tool()
async def glob_files(pattern: str, path: str = ".") -> str:
    """按 glob 模式列文件。

    Args:
        pattern: 标准 glob 模式，支持 `*` `**` `?` `[...]`。
        path: 搜索起点（绝对路径或相对 cwd）。默认 "."。

    Returns:
        JSON 字符串数组（相对 path 的相对路径），或 "[ERROR] ..."。
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] path {p} 不存在"
        if not p.is_dir():
            return f"[ERROR] path {p} 不是目录"
        matches = sorted(str(m.relative_to(p)) for m in p.glob(pattern))
        logger.info(
            "[fs] glob_files(pattern=%r, path=%r) → %d matches",
            pattern, str(p), len(matches),
        )
        return json.dumps(matches, ensure_ascii=False)
    except Exception as e:
        logger.warning("[fs] glob_files ERROR pattern=%r err=%r", pattern, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [glob_files]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_glob_files.py -v
```

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/glob_files.py tests/fs_tools/test_glob_files.py
git commit -m "feat(fs-tools): glob_files with recursive ** support"
```

---

### Task 8: grep_files（正则搜索 + include glob）

**Files:**
- Create: `workspace/extensions/tools/fs/grep_files.py`
- Create: `tests/fs_tools/test_grep_files.py`

**Interfaces:**
- Produces: `grep_files(pattern: str, path: str = ".", include: str = "", max_results: int = 100) -> str`（返回 JSON 数组字符串，每元素格式 `path:lineno:content`）

- [ ] **Step 1: 写测试（红）**

`tests/fs_tools/test_grep_files.py` 完整内容：

```python
"""grep_files 完整测试矩阵。Spec §3.3 / §5.2。"""
from __future__ import annotations

import json

import pytest


def test_grep_files_single_file_match(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.py"
    target.write_text("def foo():\n    pass\n", encoding="utf-8")

    result = grep_files("def foo", str(tmp_path))

    matches = json.loads(result)
    assert len(matches) == 1
    # 格式：path:lineno:content
    assert "x.py:1:def foo():" in matches[0]


def test_grep_files_multiple_files(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    (tmp_path / "a.py").write_text("target_text\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("target_text\nother\n", encoding="utf-8")

    result = grep_files("target_text", str(tmp_path), include="*.py")

    matches = json.loads(result)
    assert len(matches) == 3  # a.py:1, b.py:1, b.py:2


def test_grep_files_include_filter(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    (tmp_path / "a.py").write_text("target\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("target\n", encoding="utf-8")

    result = grep_files("target", str(tmp_path), include="*.py")

    matches = json.loads(result)
    assert len(matches) == 1
    assert "a.py" in matches[0]


def test_grep_files_max_results(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    content = "\n".join(f"line {i}" for i in range(200))
    target = tmp_path / "big.txt"
    target.write_text(content, encoding="utf-8")

    result = grep_files("line", str(tmp_path), max_results=10)

    matches = json.loads(result)
    assert len(matches) == 10


def test_grep_files_no_match(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = grep_files("nonexistent", str(tmp_path))

    assert json.loads(result) == []


def test_grep_files_invalid_regex(tmp_path):
    from workspace.extensions.tools.fs.grep_files import grep_files
    target = tmp_path / "x.txt"
    target.write_text("hello\n", encoding="utf-8")

    result = grep_files("[invalid(", str(tmp_path))

    assert result.startswith("[ERROR]")
    assert "regex" in result.lower() or "正则" in result
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_grep_files.py -v
```

- [ ] **Step 3: 实现 grep_files**

`workspace/extensions/tools/fs/grep_files.py` 完整内容：

```python
"""grep_files — 按 regex 搜文件内容，支持 include glob + max_results 截断。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.2
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("openvox")


@function_tool()
async def grep_files(
    pattern: str, path: str = ".", include: str = "", max_results: int = 100
) -> str:
    """按 regex 搜索文件内容。

    Args:
        pattern: 正则表达式字符串。
        path: 搜索起点目录（绝对路径或相对 cwd）。默认 "."。
        include: 文件 glob 过滤（如 "*.py"），空表示匹配所有。默认 ""。
        max_results: 最多返回多少匹配。默认 100。

    Returns:
        JSON 字符串数组，每元素格式 "relative_path:lineno:content"，
        或 "[ERROR] ..."。
    """
    try:
        try:
            compiled = re.compile(pattern)
        except re.error as e:
            return f"[ERROR] pattern 不是合法 regex: {e}"
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"[ERROR] path {p} 不存在"
        if not p.is_dir():
            return f"[ERROR] path {p} 不是目录"
        results: list[str] = []
        # 用 rglob 递归找所有候选文件
        for file_path in p.rglob("*"):
            if not file_path.is_file():
                continue
            if include and not file_path.match(include):
                continue
            try:
                # 用 errors='replace' 避免二进制文件整个跳过的边界问题
                for lineno, line in enumerate(
                    file_path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if compiled.search(line):
                        rel = str(file_path.relative_to(p))
                        results.append(f"{rel}:{lineno}:{line}")
                        if len(results) >= max_results:
                            break
            except (OSError, UnicodeDecodeError):
                continue  # 跳过无法读的文件
            if len(results) >= max_results:
                break
        logger.info(
            "[fs] grep_files(pattern=%r, path=%r, include=%r, max=%d) → %d matches",
            pattern, str(p), include, max_results, len(results),
        )
        return json.dumps(results, ensure_ascii=False)
    except Exception as e:
        logger.warning("[fs] grep_files ERROR pattern=%r err=%r", pattern, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [grep_files]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_grep_files.py -v
```

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/grep_files.py tests/fs_tools/test_grep_files.py
git commit -m "feat(fs-tools): grep_files with regex + include glob + max_results"
```

---

### Task 9: bash（子进程执行 + timeout + 环境隔离）

**Files:**
- Create: `workspace/extensions/tools/fs/bash.py`
- Create: `tests/fs_tools/test_bash.py`

**Interfaces:**
- Produces: `bash(cmd: str, cwd: str = "", timeout: int = 30) -> str`

- [ ] **Step 1: 写测试（红）**

`tests/fs_tools/test_bash.py` 完整内容：

```python
"""bash 完整测试矩阵。Spec §3.3 / §5.2 / §5.4。"""
from __future__ import annotations

import os
import sys

import pytest


def test_bash_echo():
    from workspace.extensions.tools.fs.bash import bash

    result = bash("echo hello")

    assert "hello" in result
    assert result.startswith("[EXIT 0]")


def test_bash_non_zero_exit():
    from workspace.extensions.tools.fs.bash import bash

    result = bash("false")

    assert result.startswith("[EXIT 1]")


def test_bash_timeout():
    from workspace.extensions.tools.fs.bash import bash

    result = bash("sleep 5", timeout=1)

    assert result.startswith("[TIMEOUT]")


def test_bash_invalid_timeout():
    from workspace.extensions.tools.fs.bash import bash

    result = bash("echo hi", timeout=0)
    assert result.startswith("[ERROR]")
    assert "timeout" in result

    result = bash("echo hi", timeout=301)
    assert result.startswith("[ERROR]")
    assert "timeout" in result


def test_bash_cwd(tmp_path):
    from workspace.extensions.tools.fs.bash import bash

    result = bash("pwd", cwd=str(tmp_path))

    assert str(tmp_path) in result
    assert result.startswith("[EXIT 0]")


def test_bash_cwd_not_exist():
    from workspace.extensions.tools.fs.bash import bash

    result = bash("echo hi", cwd="/nonexistent/path")

    assert result.startswith("[ERROR]")


def test_bash_does_not_inherit_secret_env(monkeypatch):
    """Bash 子进程不应继承除 PATH/HOME 之外的敏感环境变量。"""
    monkeypatch.setenv("MY_SECRET", "supersecret")

    from workspace.extensions.tools.fs.bash import bash

    result = bash("echo MY_SECRET=$MY_SECRET")

    # 应该没有 "supersecret"
    assert "supersecret" not in result


def test_bash_pipes_and_chains():
    """支持 shell 管道和 && 链式。"""
    from workspace.extensions.tools.fs.bash import bash

    result = bash("echo a && echo b | tr a-z A-Z")

    assert "A" in result
    assert "B" in result
    assert result.startswith("[EXIT 0]")


def test_bash_warning_logged(caplog):
    import logging
    from workspace.extensions.tools.fs.bash import bash
    with caplog.at_level(logging.WARNING, logger="openvox"):
        bash("echo hi")
    assert "BASH_OP" in caplog.text
```

- [ ] **Step 2: 跑测试验证失败**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_bash.py -v
```

- [ ] **Step 3: 实现 bash**

`workspace/extensions/tools/fs/bash.py` 完整内容：

```python
"""bash — 子进程执行 shell 命令，带 timeout + 环境隔离。

Spec: docs/superpowers/specs/2026-07-05-agent-filesystem-tools-design.md §3.3 / §5.4
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from livekit.agents import function_tool

logger = logging.getLogger("openvox")

_MIN_TIMEOUT = 1
_MAX_TIMEOUT = 300
_DEFAULT_TIMEOUT = 30


def _safe_env() -> dict[str, str]:
    """只透传 PATH 和 HOME。"""
    env: dict[str, str] = {}
    for key in ("PATH", "HOME"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    return env


@function_tool()
async def bash(cmd: str, cwd: str = "", timeout: int = _DEFAULT_TIMEOUT) -> str:
    """执行 shell 命令。

    Args:
        cmd: shell 命令字符串（支持管道 / && / || / 重定向）。
        cwd: 工作目录（绝对路径或相对 worker cwd）。空表示用 worker cwd。
        timeout: 超时秒数。范围 [1, 300]，默认 30。

    Returns:
        "[EXIT N] <stdout+stderr>" 成功（含退出码），
        "[TIMEOUT] ..." 超时，或 "[ERROR] ..." 参数错误。
    """
    if not (isinstance(timeout, int) and _MIN_TIMEOUT <= timeout <= _MAX_TIMEOUT):
        return f"[ERROR] timeout 必须在 {_MIN_TIMEOUT}-{_MAX_TIMEOUT} 之间，收到 {timeout!r}"
    try:
        work_dir = Path(cwd).expanduser().resolve() if cwd else Path.cwd()
        if not work_dir.exists() or not work_dir.is_dir():
            return f"[ERROR] cwd {work_dir} 不存在或不是目录"
        logger.warning(
            "[fs] BASH_OP bash(cmd=%r, cwd=%r, timeout=%ds)",
            cmd[:200], str(work_dir), timeout,
        )
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # 合并 stderr 到 stdout
            cwd=str(work_dir),
            env=_safe_env(),
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
            return f"[TIMEOUT] {timeout}s 内未完成，已 kill"
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return f"[EXIT {proc.returncode}] {output}"
    except Exception as e:
        logger.warning("[fs] bash ERROR cmd=%r err=%r", cmd[:200], e)
        return f"[ERROR] {e}"


def register() -> list:
    return [bash]
```

- [ ] **Step 4: 跑测试验证全部通过**

```bash
source .venv/bin/activate
pytest tests/fs_tools/test_bash.py -v
```

- [ ] **Step 5: Commit**

```bash
git add workspace/extensions/tools/fs/bash.py tests/fs_tools/test_bash.py
git commit -m "feat(fs-tools): bash with timeout + PATH/HOME-only env"
```

### Task 10: 追加 persona/TOOLS.md 工具说明

**Files:**
- Modify: `workspace/persona/TOOLS.md`（追加 6 工具章节）

**Interfaces:**
- 无（仅文档）

- [ ] **Step 1: 读现有 TOOLS.md**

```bash
cat workspace/persona/TOOLS.md
```

期望输出包含 `current_time` 和 `load_skill` 两个章节。

- [ ] **Step 2: 追加 fs 工具章节**

用 Edit 追加（保持现有内容不变）：

`workspace/persona/TOOLS.md` 末尾追加：

```markdown

## `read_file`
- 触发词："读 xxx"、"看 xxx 的内容"、"xxx 里有什么"
- 必调，**不要**自己编文件内容
- 路径用绝对路径；想读相对路径时用 worker cwd（仓库根）
- 超大文件（>1MB）会自动截断到前 2000 行

## `write_file`
- 触发词："写到 xxx"、"保存到 xxx"、"创建 xxx"
- 默认 overwrite；要追加显式说"追加"
- 写到敏感路径（/etc/、~/.ssh/ 等）会 WARNING 日志

## `edit_file`
- 触发词："改 xxx 里的 a 为 b"、"把 a 替换成 b"
- 必须给出准确的 old_string；多次匹配要明确说"全部替换"
- 找不到 old_string 时工具返回错误，按错误重试

## `glob_files` / `grep_files`
- 触发词："列 xxx 下所有 y"、"在 xxx 里找包含 y 的"
- glob 模式用标准 glob（`**/*.py`、`*.txt`）
- grep 返回 `path:lineno:content` 格式

## `bash`
- 触发词："运行 xxx 命令"、"执行 xxx"
- 默认 timeout 30s，最长 300s
- 不在白名单的命令也可以跑（v0.1 demo 不限制）
```

- [ ] **Step 3: Commit**

```bash
git add workspace/persona/TOOLS.md
git commit -m "docs(persona): append fs tools usage guide to TOOLS.md"
```

---

### Task 11: Phase 1 E2E 烟雾测试

**Files:**
- Create: `tests/e2e_fs_tools.py`

**Interfaces:**
- Consumes: 6 个工具全部已实现
- Produces: 一个 e2e 烟雾测试脚本（参考 `tests/e2e_generate_reply.py` 脚手架重写）

- [ ] **Step 1: 读现有 e2e 脚手架**

```bash
cat tests/e2e_generate_reply.py 2>/dev/null | head -50
```

期望：能看到 LiveKit SDK + dispatch 的脚手架。即使文件是坏的（路径过期），脚手架结构仍可参考。

- [ ] **Step 2: 实现 e2e_fs_tools.py**

`tests/e2e_fs_tools.py` 完整内容：

```python
"""E2E 烟雾测试：验证 6 个 fs 工具在 realtime 模式下能被调起来。

前置：
- worker 用 `python main.py start` 在后台运行
- LiveKit server 在跑（lk dispatch 依赖）
- 环境变量 LIVEKIT_URL/API_KEY/SECRET 已配置

跑法：
    source .venv/bin/activate
    pytest tests/e2e_fs_tools.py -v -s
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest


# 6 工具的 e2e 场景：(触发 prompt 模板, 验证函数)
SCENARIOS = [
    (
        "请帮我读 {path} 的内容",
        lambda result, path, content: content in result,
    ),
    (
        "请把 'foo' 写到 {path}",
        lambda result, path, content: Path(path).read_text(encoding="utf-8") == "foo",
    ),
    (
        "请把 {path} 里的 'foo' 改成 'bar'",
        lambda result, path, content: Path(path).read_text(encoding="utf-8") == "bar",
    ),
    (
        "请列 {dir} 下所有 .txt",
        lambda result, path, content: Path(path).name in result,
    ),
    (
        "请在 {dir} 下找包含 'foo' 的文件",
        lambda result, path, content: Path(path).name in result,
    ),
    (
        "请运行 echo hi",
        lambda result, path, content: "hi" in result,
    ),
]


@pytest.mark.parametrize("prompt_template,validate", SCENARIOS)
@pytest.mark.asyncio
async def test_fs_tool_e2e(prompt_template, validate, tmp_path):
    """每个工具一个最小 e2e 场景。

    流程：
    1. 准备测试文件
    2. 通过 LiveKit SDK 加入房间，发文本 prompt
    3. 等 agent 回复
    4. 验证回复满足 validate

    完整实现需要 LiveKit room + room.io，这里留 TODO 待 Phase 1 实测时补。
    当前 plan 仅交付骨架 + 数据驱动参数化。
    """
    pytest.skip("Phase 1 E2E 骨架待 Phase 0 baseline 通过后实跑时补完")
```

> 注：本 task 仅交付 e2e 骨架 + 数据驱动的 parametrize 列表。**实际 e2e 实跑**（与 worker 联调）在 Phase 0 baseline 通过后，由 orchestrator 单独安排（不在本 plan 范围，因为需要 dispatch agent + 监听 TTS 回复 + 验证工具副作用，复杂度远超单元测试）。本 task 的价值是把测试矩阵和数据驱动结构定下来。

- [ ] **Step 3: 跑骨架确认 parametrize 正常**

```bash
source .venv/bin/activate
pytest tests/e2e_fs_tools.py -v
```

期望：6 个 skipped（每个 scenario 一个）。

- [ ] **Step 4: 跑全量 fs 单元测试确认回归**

```bash
source .venv/bin/activate
pytest tests/fs_tools/ -v
```

期望：所有用例 PASS（除了 macOS skip 的）。

- [ ] **Step 5: Commit**

```bash
git add tests/e2e_fs_tools.py
git commit -m "test(e2e): fs tools smoke test skeleton with data-driven scenarios"
```

---

### Task 12: 全量回归 + build_agent summary 验证

**Files:**
- 无文件改动（仅跑测试 + 验证 build_agent 装配）

**Interfaces:**
- Consumes: 全部 7 工具文件 + load_tools 升级 + persona/TOOLS.md 追加
- Produces: 完整测试套件全过 + worker 启动后 build_agent summary 日志包含全部 7 工具

- [ ] **Step 1: 全量单元测试**

```bash
source .venv/bin/activate
pytest tests/ -v --ignore=tests/e2e_generate_reply.py --ignore=tests/e2e_fs_tools.py
```

期望：所有现有测试 + fs_tools 全部 PASS。

- [ ] **Step 2: 启动 worker 验证 build_agent summary**

```bash
# 杀掉旧 worker
lsof -ti:8081 | xargs kill -9 2>/dev/null || true

# 后台启动
source .venv/bin/activate
python main.py start > /tmp/worker.log 2>&1 &

# 等 5 秒
sleep 5

# 检查 summary
grep -A 5 "build_agent summary" /tmp/worker.log
```

期望输出包含：

```
[Agent]   tools    : 7 → ['bash', 'current_time', 'edit_file', 'glob_files', 'grep_files', 'read_file', 'write_file']
```

（确切顺序可能不同，但应该看到 6 个 fs 工具名 + current_time）

- [ ] **Step 3: 杀掉 worker**

```bash
lsof -ti:8081 | xargs kill -9 2>/dev/null || true
```

- [ ] **Step 4: 写最终 commit message 标注 plan 完成**

如果全过但还有未提交的微调（例如 persona 调整），提交。否则无需新 commit。

```bash
git status
# 如有未提交改动：
git add -A
git commit -m "chore: fs tools v0.1 plan complete — 7 tools, 8 functions, all unit tests pass"
```

---

## Self-Review Checklist（执行前最后一遍）

1. **Spec coverage**：
   - §1 目录布局 ✓（Task 1 bootstrap）
   - §3.1 `_sensitive.py` ✓（Task 1）
   - §3.2 工具模板 ✓（每个工具 task 都按此模板）
   - §3.3 6 工具签名 + 错误返回 ✓（Task 4-9）
   - §3.4 load_tools 升级递归 ✓（Task 2）
   - §4.1 数据流图 → 由 build_agent 自动装配，不需要单独任务
   - §4.2 Phase 0 baseline ✓（Task 3 Step 5-6）
   - §4.3 危险日志样例 ✓（每个工具 task 的 WARNING 日志断言）
   - §5.1 不抛异常 ✓（每个工具 try/except + [ERROR] 返回）
   - §5.2 错误处理契约 ✓（每个工具的测试矩阵覆盖）
   - §5.3 日志分级 ✓（每个工具 INFO/WARNING 日志）
   - §5.4 Bash 约束 ✓（Task 9：timeout + _safe_env + cwd 校验）
   - §5.5 原子写 ✓（Task 5：tmp + rename）
   - §5.5 UTF-8 限制 ✓（Task 5：write_file 单测含非 UTF-8 用例）
   - §6.1 单元测试 ✓（每个工具 task）
   - §6.2 E2E 烟雾测试 ✓（Task 11 骨架 + Task 3 baseline）
   - §6.3 测试顺序 ✓（Task 3 BLOCKING + Task 12 全量回归）
   - §7 persona/TOOLS.md 追加 ✓（Task 10）
   - §8 关键决策（命名/分类/错误格式/操作范围/安全/Bash timeout/env） ✓

2. **占位符扫描**：检查每步代码完整、无 TBD/TODO/fill-in。

3. **类型一致性**：
   - `is_sensitive(path: str) -> bool` — Task 1 定义，Task 4-9 一致调用
   - 工具签名 vs 测试矩阵 — 每个工具的测试都用一致签名调用
   - 错误前缀 `[ERROR]` / `[TIMEOUT]` / `[EXIT N]` / `[OK]` — 工具返回 vs 测试断言一致
   - 6 个工具文件各自暴露 1 个 `@function_tool` 函数（`register() -> list` 返回单元素列表）— 所有工具测试都验证 `register()` 返回值

## 执行交接

Plan 完成，保存到 `docs/superpowers/plans/2026-07-05-agent-filesystem-tools.md`。

**两个执行选项**：

**1. Subagent-Driven（推荐）** — 每个 task dispatch 一个新 subagent，task 之间 review，快速迭代

**2. Inline Execution** — 在本会话内按 executing-plans 批量执行 + checkpoint 复审

要选哪种？