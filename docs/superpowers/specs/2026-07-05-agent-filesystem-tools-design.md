# Agent 文件系统工具集（与 Claude Code 对齐）

> **状态**：草案 v0.1
> **日期**：2026-07-05
> **范围**：为 agent 增加 7 个原子文件操作工具（read/write/edit/glob/grep/bash/notebook_edit），函数名与 Claude Code 完全对齐
> **前置文档**：[2026-07-04-agent-extensibility-design.md](./2026-07-04-agent-extensibility-design.md)（本 spec 是它的"具体工具集"实施）

---

## 1. 背景与目标

### 1.1 当前状态

项目已经建立了 `workspace/extensions/tools/<name>.py` 的自动加载机制（`agent_extensions.load_tools` glob `*.py` 并调每个文件的 `register()`）。`current_time.py` 是目前唯一的工具样例。

**框架扩展点（livekit-agents）的能力矩阵**里 function tools 已经被支持，但**项目里没有任何文件操作类工具**——agent 不能读、写、列、搜、改用户磁盘上的文件。这与 Claude Code 的能力差距明显。

### 1.2 目标

把 agent 的能力从"问答 + 单一时间工具"扩展到"问答 + 时间 + **完整文件操作 + bash 执行**"。

| 工具 | Claude Code 同名 | 用途 |
|---|---|---|
| `read_file` | `Read` | 读文本文件 |
| `write_file` | `Write` | 写文本文件 |
| `edit_file` | `Edit` | 基于 old_string/new_string 的精确替换 |
| `glob_files` | `Glob` | 按 glob 模式列文件 |
| `grep_files` | `Grep` | 按 regex 搜文件内容 |
| `bash` | `Bash` | 执行 shell 命令 |
| `notebook_edit` | `NotebookEdit` | 读写 Jupyter notebook |

### 1.3 关键约束（用户已确认）

- **操作范围**：宿主机任意路径（沙箱外）。`workspace/sandbox/` 不强制。
- **PIPELINE 兼容**：**只支持 realtime**（pipeline 模式尚未跑通，按 CLAUDE.md 历史）
- **安全机制**：v0.1 不做任何限制；仅记录详细日志 + 危险操作 WARNING + 敏感路径 WARNING。**v0.2 必须补路径白名单 + 命令白名单**，否则不能上生产。
- **工具调用路径**：现有 `current_time` 在 realtime 模式下能否被调起来**尚未验证**——本 spec 把"验证文件系统工具 baseline"列为 Phase 0 前置步骤，**必须先于**所有工具的实现。
- **skill vs 工具**：本 spec 只交付原子工具。复合场景（"代码审查"、"重构某个 feature"）用 skill 包装，由后续 spec 单独定义。
- **测试**：单元测试 + E2E 烟雾测试。

### 1.4 范围

**做**：

- 在 `workspace/extensions/tools/fs/` 下放 7 个工具文件
- 把 `agent_extensions.load_tools` 的 glob 从单层 `*.py` 升级到递归 `**/*.py`（让 `tools/fs/<name>.py` 这种子目录结构也能被加载）
- 7 个工具的单元测试 + E2E 烟雾测试
- 一份"工具使用元说明"追加到 `workspace/persona/TOOLS.md`

**不做**（v0.1 明确）：

- v0.2 的路径白名单、命令白名单、用户审批（spec 第 9 节列迁移路径）
- pipeline 模式的适配（待 pipeline 跑通后另开 spec）
- 工具并发 / 流式 read / 大文件断点续传
- 把 `extensions/tools/` 改成独立 Python 包
- MCP filesystem server 方案（备选方案，**仅当** Phase 0 baseline 失败时启动评估）

---

## 2. 目录布局

```
/Users/pz/workspace/livekit/
├── workspace/
│   ├── extensions/
│   │   ├── tools/
│   │   │   ├── __init__.py             # 空
│   │   │   ├── current_time.py         # 现有：保留
│   │   │   └── fs/                     # 新增：文件系统工具子分类
│   │   │       ├── __init__.py         # 空（防止 import 触发 register）
│   │   │       ├── _sensitive.py       # 共享：敏感路径正则 + 检测函数
│   │   │       ├── read_file.py
│   │   │       ├── write_file.py
│   │   │       ├── edit_file.py
│   │   │       ├── glob_files.py
│   │   │       ├── grep_files.py
│   │   │       ├── bash.py
│   │   │       └── notebook_edit.py
│   │   └── mcp/                         # 现有
│   └── persona/
│       └── TOOLS.md                     # 追加：fs 工具使用元说明
└── tests/
    └── fs_tools/                        # 新增：单元测试
        ├── __init__.py
        ├── conftest.py                  # 共享 fixture（tmp_workspace 等）
        ├── test_read_file.py
        ├── test_write_file.py
        ├── test_edit_file.py
        ├── test_glob_files.py
        ├── test_grep_files.py
        ├── test_bash.py
        └── test_notebook_edit.py
```

**E2E 测试位置**：`tests/e2e_fs_tools.py`（与现有 `tests/e2e_generate_reply.py` 同级，仿其脚手架；后者因路径过期已被标记为不可用，可作为脚手架参考重写）。

---

## 3. 模块接口契约

### 3.1 `tools/fs/_sensitive.py`（共享）

```python
import re
from pathlib import Path

SENSITIVE_PATTERNS = [
    re.compile(r"^/etc(/|$)"),
    re.compile(r"^/usr(/|$)"),
    re.compile(r"^/var(/|$)"),
    re.compile(r"^/private/etc(/|$)"),  # macOS
    re.compile(rf"^{re.escape(str(Path.home()))}/\.ssh(/|$)"),
    re.compile(rf"^{re.escape(str(Path.home()))}/\.aws(/|$)"),
]

def is_sensitive(path: str) -> bool:
    """绝对路径命中任意敏感模式 → True。"""
    return any(p.match(path) for p in SENSITIVE_PATTERNS)
```

### 3.2 工具文件统一模板

每个工具遵循 `current_time.py` 的协议：`@function_tool()` 装饰 + 中文 docstring + `register() -> list`。

```python
# workspace/extensions/tools/fs/read_file.py
from pathlib import Path
from livekit.agents import function_tool

from ._sensitive import is_sensitive

logger = logging.getLogger("volcengine-agent")


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
        if is_sensitive(str(p)):
            logger.warning("[fs] SENSITIVE_PATH read_file(path=%r)", path)
        # ... 实际读文件 + 截断逻辑
        return content
    except Exception as e:
        logger.warning("[fs] read_file ERROR path=%r err=%r", path, e)
        return f"[ERROR] {e}"


def register() -> list:
    return [read_file]
```

### 3.3 7 个工具的签名与返回契约

| 工具 | 签名 | 错误返回示例 |
|---|---|---|
| `read_file` | `read_file(path: str, start_line: int = 0, end_line: int = 0) -> str` | `[ERROR] /x/y 不存在` / `[ERROR] 无权读取 /x/y` / `[ERROR] /x/y 是目录，请用 glob_files` |
| `write_file` | `write_file(path: str, content: str, mode: str = "overwrite") -> str` | `[ERROR] 父目录 /x 不存在` / `[ERROR] mode 必须是 overwrite 或 append` |
| `edit_file` | `edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str` | `[ERROR] 在 /x/y 中找不到 "foo"` / `[ERROR] "foo" 出现 3 次，请加更多上下文或设 replace_all=true` |
| `glob_files` | `glob_files(pattern: str, path: str = ".") -> str` | `[ERROR] path /x 不存在` |
| `grep_files` | `grep_files(pattern: str, path: str = ".", include: str = "", max_results: int = 100) -> str` | `[ERROR] pattern 不是合法 regex: ...` |
| `bash` | `bash(cmd: str, cwd: str = "", timeout: int = 30) -> str` | `[TIMEOUT] 30s 内未完成，已 kill` / `[EXIT N] <stdout+stderr>` |
| `notebook_edit` | `notebook_read(path: str) -> str` / `notebook_edit(path: str, cell_id: str, new_source: str) -> str` | `[ERROR] 不是合法的 .ipynb JSON` / `[ERROR] cell_id X 不存在` |

**返回值字符串格式约定**：
- 成功：`"OK: ..."` 或内容直接返回（read 类工具）
- 成功列表：JSON 字符串数组（glob/grep 返回 `["a.txt", "b.txt"]`）
- 失败：`"[ERROR] <描述>"` 或 `"[TIMEOUT] <描述>"` 或 `"[EXIT N] <stdout+stderr>"`

### 3.4 `agent_extensions.load_tools` 改动

```python
# 旧
py_files = [p for p in sorted(tools_dir.glob("*.py")) if not p.name.startswith("_")]

# 新
py_files = [
    p for p in sorted(tools_dir.glob("**/*.py"))
    if not p.name.startswith("_")
    and "__pycache__" not in p.parts
]
```

**目的**：让 `tools/fs/<name>.py` 这种子目录结构也能被自动加载。`__init__.py` 因 `_` 前缀被过滤。`__pycache__` 显式排除。

**零 `main.py` 改动**：`build_agent` 调 `load_tools(workspace_root / "extensions" / "tools")` 已经指向 `tools/` 根，递归后会自动包含 `tools/fs/`。`build_agent` 的 summary 日志会自动列出新工具名。

---

## 4. 关键数据流

### 4.1 工具调用链路

```
User: "帮我读 /Users/pz/.../main.py 看看里面 import 了哪些 livekit 模块"
  │
  ▼
LiveKit 客户端（语音/文本）→ LiveKit Server → Worker
  │
  ▼
vendor RealtimeSession → 火山引擎 realtime 模型（豆包端到端）
  │
  │  模型生成 tool_call: {name: "read_file", args: {path: "/Users/pz/.../main.py"}}
  ▼
LiveKit AgentServer.function_call() 调度
  │
  ▼
workspace/extensions/tools/fs/read_file.py:read_file()
  │  - Path.expanduser().resolve() 解析绝对路径
  │  - is_sensitive() 检查（命中 → WARNING 日志）
  │  - p.read_text() 读文件
  │  - logger.info("[fs] read_file(path=%r) → %dc", path, len(content))
  ▼
返回 content 字符串
  │
  ▼
LiveKit 把 tool_result 喂回 realtime 模型
  │
  ▼
模型继续生成回答 → TTS → 用户听到"main.py 里 import 了 livekit.agents、livekit.plugins.volcengine..."
```

### 4.2 Phase 0 — baseline 验证（**必须先于所有实现**）

**目的**：验证 realtime 模式下 `@function_tool` 工具能否被火山引擎 realtime 模型调起来。

**步骤**：

```
1. 实现 tools/fs/read_file.py 一个最小版本（仅 path 参数，无 start/end，无敏感路径检查）
2. 启动 worker（realtime 模式）
3. 准备测试文件：
   echo "hello world" > /tmp/fs_baseline_test.txt
4. lk dispatch create --agent-name openz --room fs-baseline
5. 客户端语音："帮我读 /tmp/fs_baseline_test.txt 的内容"
6. 验证回复包含 "hello world"
```

**三种结果与处理**：

| 结果 | 含义 | 处理 |
|---|---|---|
| 工具被调起来，返回 "hello world" | realtime 模式工具路径走通 | ✅ 进 Phase 1（实现剩余 6 个工具） |
| 模型直接瞎答（"这个文件里有 hello world" 但实际没读） | realtime 模型不读 tool schema | ⚠️ 整个 spec 降级：要么改 pipeline only，要么走"侧路 hook"让用户用 DataChannel 文本触发工具 |
| 工具报错 / 模型不调用 | LiveKit 工具注册失败 或 vendor RealtimeSession 透传问题 | 🔧 排查 `agent_extensions.load_tools` 日志、vendor RealtimeSession 内部透传逻辑 |

**Phase 0 通过门槛**：read_file 最小版本跑通 + 单元测试 + E2E 烟雾测试。后续 Phase 1 才开始批量做其他 6 个工具。

### 4.3 危险操作日志样例

worker 日志会同时出现：

```
12:34:56.789 | INFO  | volcengine-agent | [fs] read_file(path='/Users/pz/.../main.py') → 1234c
12:35:01.234 | WARN  | volcengine-agent | [fs] WRITE_OP write_file(path='/tmp/x.txt', mode='overwrite', size=42c)
12:35:02.345 | WARN  | volcengine-agent | [fs] BASH_OP bash(cmd='pip install requests', timeout=30)
12:35:05.678 | WARN  | volcengine-agent | [fs] SENSITIVE_PATH read_file(path='/etc/hosts')
```

demo 时这一段日志能让操作员一眼看到 agent 在做什么。

---

## 5. 错误处理与日志约定

### 5.1 核心原则

**绝不抛异常跨越工具边界**。所有错误统一返回 `[ERROR] <描述>` 字符串，让 LiveKit 把错误喂回模型自己处理。这样：
- 不会因为工具内部 bug 中断 LiveKit session
- 模型能根据错误信息自主决策（重试 / 改路径 / 放弃）

### 5.2 工具级错误处理契约

| 工具 | 错误场景 | 返回格式 |
|---|---|---|
| `read_file` | 文件不存在 / 权限拒绝 / 是目录 | `[ERROR] 路径 /x/y 不存在` / `[ERROR] 无权读取 /x/y` / `[ERROR] /x/y 是目录，请用 glob_files` |
| `read_file` | 文件 > 1MB | 截断到前 2000 行 + 末尾追加 `[TRUNCATED] 文件共 N 行，已截断至前 2000 行` |
| `write_file` | 父目录不存在 / 权限拒绝 / mode 非法 | `[ERROR] 父目录 /x 不存在` / `[ERROR] 无权写入 /x/y` / `[ERROR] mode 必须是 overwrite 或 append` |
| `edit_file` | old_string 不存在 / 出现多次（未指定 replace_all） | `[ERROR] 在 /x/y 中找不到 "..."` / `[ERROR] "..." 出现 3 次，请加更多上下文或设 replace_all=true` |
| `edit_file` | new_string == old_string | `[OK] 内容未变化` |
| `glob_files` | 无匹配 | `[]`（空 JSON 数组字符串） |
| `grep_files` | 无匹配 / pattern 不是合法 regex | `[]` / `[ERROR] pattern 不是合法 regex: ...` |
| `bash` | 超时 / 非零退出码 | `[TIMEOUT] 30s 内未完成，已 kill` / `[EXIT N] <stdout+stderr>` |
| `notebook_*` | JSON 解析失败 / cell_id 不存在 | `[ERROR] ...` |

### 5.3 日志分级

复用 `logger = logging.getLogger("volcengine-agent")`，按操作危险度分级：

```python
# 普通读取 — INFO
logger.info("[fs] read_file(path=%r, start=%d, end=%d) → %dc", path, start, end, len(result))

# 危险操作（write / edit / bash）— WARNING，让 demo 时一眼能看到
logger.warning("[fs] WRITE_OP write_file(path=%r, mode=%r, size=%dc)", path, mode, len(content))
logger.warning("[fs] EDIT_OP edit_file(path=%r, old_len=%d, new_len=%d, replace_all=%s)",
               path, len(old_string), len(new_string), replace_all)
logger.warning("[fs] BASH_OP bash(cmd=%r, cwd=%r, timeout=%ds)", cmd[:200], cwd, timeout)

# 敏感路径（任何操作）— WARNING
logger.warning("[fs] SENSITIVE_PATH read_file(path=%r) 命中敏感路径", path)
```

### 5.4 Bash 工具的额外约束

- `timeout` 默认 30s，最大 300s（超出返回 `[ERROR] timeout 必须在 1-300 之间`）
- 命令字符串前 200 字符打 WARNING 日志（完整命令在日志里要小心，**v0.2 加密/tokenize 处理**）
- 子进程不继承当前 shell 的环境变量，只透传 `PATH` 和 `HOME`（防 `eval` 类的副作用）
- `cwd` 默认 worker 进程 cwd；指定时必须存在且是目录

### 5.5 原子写

`write_file` 用 `tmp + rename`：

```python
import os, tempfile
fd, tmp_path = tempfile.mkstemp(dir=parent_dir, prefix=".tmp_", suffix=".tmp")
os.write(fd, content.encode("utf-8"))
os.close(fd)
os.replace(tmp_path, target)  # 原子 rename
```

避免写到一半崩溃留半截文件。

---

## 6. 测试策略

### 6.1 单元测试（pytest）

**位置**：`tests/fs_tools/`，每个工具一个测试文件。

**共享 fixture**（`tests/fs_tools/conftest.py`）：

```python
import pytest
from pathlib import Path

@pytest.fixture
def tmp_workspace(tmp_path) -> Path:
    """预置几个测试文件"""
    (tmp_path / "hello.txt").write_text("hello\nworld\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "data.json").write_text('{"k": "v"}', encoding="utf-8")
    (tmp_path / "big.txt").write_text("x" * 2_000_000, encoding="utf-8")  # 2MB
    (tmp_path / "no_read").write_text("secret")
    (tmp_path / "no_read").chmod(0o000)  # 无读权限
    return tmp_path
```

**每个工具的测试矩阵**：

| 工具 | 必测用例 |
|---|---|
| `read_file` | 正常读 / 文件不存在 / 权限拒绝 / 是目录 / 超大文件截断 / start_line+end_line 窗口 / 敏感路径 WARNING |
| `write_file` | 正常覆盖写 / append 模式 / 父目录不存在 / 权限拒绝 / 原子性（写到一半崩溃不留半截）/ overwrite 覆盖已存在文件 |
| `edit_file` | 单次替换 / replace_all / old_string 不存在 / 多次匹配未指定 replace_all / new == old 返回 `[OK]` / 大小写敏感 |
| `glob_files` | 匹配多个 / 不匹配 / `**/*.py` 递归 / 转义字符 / 路径不存在 |
| `grep_files` | 单文件匹配 / 跨文件匹配 / 行号格式 `path:lineno:content` / include glob / max_results 截断 / 无匹配 / 非法 regex |
| `bash` | `echo` 正常 / 非零退出 / 超时（用 `sleep 5` + timeout=1）/ cwd 切换 / 不继承敏感 env（`MY_SECRET=xxx` 应拿不到）|
| `notebook_edit` | 读 v4 notebook / 替换 cell / cell_id 不存在 / 损坏 JSON |

**断言约定**：

```python
# 错误用 startswith
assert result.startswith("[ERROR]")
assert result.startswith("[TIMEOUT]")

# 成功用 contains
assert "hello" in result

# 列表用 JSON 解析
import json
assert json.loads(result) == ["a.txt", "b.txt"]

# 不用 == "..." 这种脆断言
```

### 6.2 E2E 烟雾测试

**位置**：`tests/e2e_fs_tools.py`，仿 `tests/e2e_generate_reply.py` 的脚手架重写。

**Phase 0 E2E**（**必须先于单元测试全量跑**）：

```
1. worker 用 python main.py dev 起在后台
2. lk dispatch create --agent-name openz --room fs-baseline
3. 通过 LiveKit SDK 加入房间
4. 准备 echo "hello world" > /tmp/fs_baseline_test.txt
5. 发送 TTS 文本"读 /tmp/fs_baseline_test.txt 的内容"
6. STT 收到的回复必须**包含** "hello world"
7. 若模型**没**调工具直接回答 → 整个 spec 降级
```

**Phase 1 E2E**（7 个工具各一个最小场景）：

| 工具 | 触发 prompt | 验证 |
|---|---|---|
| `read_file` | "读 x.txt 的内容" | 回复包含文件内容 |
| `write_file` | "把 'foo' 写到 /tmp/x.txt" | `/tmp/x.txt` 文件已生成，内容为 `foo` |
| `edit_file` | "把 x.txt 里的 foo 改成 bar" | 文件内容已从 `foo` 改为 `bar` |
| `glob_files` | "列 /tmp 下所有 .txt" | 回复列举正确 |
| `grep_files` | "在 /tmp 下找包含 foo 的文件" | 回复列举正确 |
| `bash` | "运行 echo hi" | 回复包含 "hi" |
| `notebook_edit` | 可选 | 单独跑 |

**E2E 通过门槛**：7 工具全部 PASS + worker 日志可见所有 `[fs]` 操作。

### 6.3 测试顺序（强制）

```
Phase 0 baseline E2E (read_file 1 个)
   ↓  通过
Phase 1 单元测试 (7 工具全覆盖)
   ↓  全过
Phase 1 E2E 烟雾测试 (7 工具)
```

任一阶段失败都阻塞下一阶段，**不允许跳阶段**。

---

## 7. 工具使用元说明（追加到 persona/TOOLS.md）

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

---

## 8. 关键决策与默认

| 决策 | 默认 | 理由 | 备选 |
|---|---|---|---|
| 工具命名 | 与 Claude Code 完全对齐（`read_file` / `write_file` / ...） | 跨项目心智模型一致 | 加 `fs_` 前缀（避免和 MCP 重名，但增加心智负担） |
| 工具分类目录 | `tools/fs/<name>.py`（tools 下的子分类）| 后续可能加 `tools/web/`、`tools/db/` | 平铺到 `tools/`（无法分类） |
| 错误返回格式 | `[ERROR] / [TIMEOUT] / [EXIT N]` 前缀 | LLM 能解析 + 不会被误认为正常输出 | 抛异常（断 session）|
| 操作范围 | 宿主机任意路径 | 用户决策 | workspace 限定（功能受限） |
| 安全机制 | v0.1 无限制 + 详细日志 | 用户决策（demo 阶段） | 路径白名单（v0.2）|
| bash 命令白名单 | v0.1 不设 | 用户决策 | 白名单（v0.2）|
| bash timeout | 30s 默认，300s 上限 | 防失控 | 用户每次指定 |
| bash 环境变量 | 只透传 PATH 和 HOME | 防 `eval` 类副作用 | 全继承（危险）|
| read_file 截断 | 1MB / 2000 行 | 防 prompt 爆炸 | 报错（不够灵活）|
| edit_file 原子性 | 不要求原子性（编辑是小操作） | 简化 | tmp + rename（成本不必要）|
| glob/grep 输出格式 | JSON 数组字符串 | LLM 友好 | 多行文本（解析困难）|
| Phase 0 验证 | 用 `read_file` 一个最小工具 | 直接验证 fs 路径，不用 current_time 旁证 | 用 current_time（间接）|
| PIPELINE 兼容 | 只支持 realtime | 用户决策（pipeline 还没跑通） | 两个都支持（需验证）|

---

## 9. 未来迁移路径（v0.2+）

| 触发条件 | 迁移内容 |
|---|---|
| 工具出现越权风险 | `tools/fs/_sensitive.py` 加 `is_allowed(path)` 函数（路径白名单），每个工具入口前置检查；非法路径返回 `[ERROR] 路径不在白名单` |
| bash 出现危险命令执行 | `tools/fs/bash.py` 加 `ALLOWED_COMMANDS` 白名单 + 命令参数过滤 |
| 写操作需要用户审批 | LiveKit DataChannel 上加审批 UI；工具入口先返回 `[NEEDS_APPROVAL] ...`，LiveKit 调 `sess.generate_reply()` 反问用户，用户回"是"后再执行 |
| 需要审计日志 | 把 `[fs] WRITE_OP` / `[fs] BASH_OP` 落到结构化日志文件（含 user_id、操作时间、参数）|
| 多个并发用户 | per-user 沙箱目录；`workspace/users/<uid>/sandbox/` 自动创建 |
| pipeline 跑通 | 验证 pipeline 模式下的工具调用路径；通过后 spec 加 §10 双模式支持 |
| 大文件需求 | read_file 加 `start_byte` / `end_byte` 支持真正的二进制文件 / 大文件断点续传 |
| MCP filesystem server 出现需求 | 评估 `mcp-server-filesystem`；如果延迟/权限比自写工具好，可以替代部分工具 |

---

## 10. 待决问题（实施阶段回答）

| 问题 | 何时决定 |
|---|---|
| `notebook_edit` v0.1 是否必做？ | Phase 0 通过后、Phase 1 启动前确认 |
| `read_file` 是否需要支持 `encoding` 参数？ | 实施时看是否碰到非 UTF-8 文件 |
| `glob_files` 是否需要排除 `.git/`？ | 实施时确认（默认应当排除，扫仓库会爆）|
| `edit_file` 是否需要支持正则 old_string？ | 实施时看需求（默认仅字面量替换）|
| `bash` 是否支持 `&&` / `\|` 管道？ | 是（subprocess 用 `shell=True` 默认支持）|
| 大文件读截断阈值是 1MB 还是别的？ | 实施时按 prompt 大小实测调整 |
| `workspace/sandbox/` 还要不要保留？ | 本 spec 用不到；如果未来加 per-user 沙箱再启用 |

---

## 附录 A：与 `2026-07-04-agent-extensibility-design.md` 的关系

那份 spec 画了整体的扩展性架构（persona/skills/memory/tools/mcp），本 spec 是它的**具体工具集实施**。本 spec 沿用那份 spec 的所有约定：

- `tools/<name>.py` 的 `register()` 协议
- `load_tools` 的 glob import 机制
- `build_agent` 的装配顺序
- 日志格式 `[xxx] ...`

唯一的接口差异：本 spec 把 `load_tools` 的 glob 从单层升级到递归，以支持 `tools/fs/` 子目录。这是对那份 spec 的**最小补充**，不影响其他约定。

## 附录 B：与 Claude Code 工具的差异

| 维度 | Claude Code | 本项目 |
|---|---|---|
| 工具命名 | 大写驼峰（`Read` / `Write`）| 蛇形（`read_file` / `write_file`）—— Python PEP 8 强制 |
| 工具发现 | `tools/` 平铺 | `tools/fs/` 子分类（未来还有 `tools/web/` 等）|
| 安全模型 | 用户级 + 工作目录限定 | v0.1 任意路径 + 详细日志；v0.2 补白名单 |
| Bash 白名单 | 默认有白名单 | v0.1 无白名单（用户决策）|
| Read 截断 | 默认 2000 行 | 同（保持一致）|
| Edit 反馈 | diff 预览 | v0.1 仅返回 `OK` / `ERROR`；v0.2 加 diff |
| NotebookEdit | 完整实现 | v0.1 可选；只做 `notebook_read` + `notebook_edit` 两个最小操作 |
| WebFetch / WebSearch | 完整 | 不在本 spec（v0.2 另开）|
| Task / TodoWrite | 完整 | 不在本 spec（v0.2 另开）|

差异的核心原因：本项目是**语音 agent + 火山引擎 realtime**，Claude Code 是**CLI agent + Anthropic LLM**。底层能力相似（都能调工具），但 UX 形态、prompt 注入点、模型对接路径完全不同。