# 搜索补强 + Claude Code 任务桥接

> **状态**：草案 v0.1
> **日期**：2026-07-05
> **范围**：在 `workspace/extensions/tools/` 下新增 4 个工具（`web_fetch`、`claude_task_create`、`claude_task_status`、`claude_task_continue`），配套 1 个后台任务运行器 + 1 个 summarizer 后台协程，重写 `workspace/persona/AGENTS.md` 和 `TOOLS.md` 中相关段落
> **前置文档**：[2026-07-04-agent-extensibility-design.md](./2026-07-04-agent-extensibility-design.md)、[2026-07-05-agent-filesystem-tools-design.md](./2026-07-05-agent-filesystem-tools-design.md)
> **目标**：让 agent 在语音交互场景下，具备 (a) 深度网页调研能力、(b) 把"笨活"外包给 Claude Code 后台执行并异步汇报的能力

---

## 1. 背景与目标

### 1.1 当前状态

- 已存在 `workspace/extensions/tools/web_search.py`（DuckDuckGo via ddgs），但只能返回"标题 + URL + 摘要"三件套，**无法读取网页正文**
- 已存在 `workspace/extensions/tools/fs/bash.py`，可以执行任意 shell 命令，但**没有组合使用的工作流提示**
- `PIPELINE=qwen-realtime`（千问 Qwen3.5-Omni）原生支持 function calling，工具调用路径已通
- `claude` CLI（2.1.170）已安装在宿主机，支持 `--print`（非交互）和 `--resume`（续接）
- 但 agent 的 system prompt 里 **完全没有** "何时/如何用 claude 调研" 的指引，导致面对"调研竞品 X"这类任务时只会调 web_search 拿到几个链接草草回答

### 1.2 目标

让 agent 在以下三类任务上有**显著高于当前的完成度**：

| 任务类型 | 当前行为 | 目标行为 |
|---|---|---|
| "X 链接里写了什么" | 不知道可以读网页 | web_search → web_fetch → 综合 |
| "帮我调研竞品 X" | 调 web_search 拿到 5 个链接草草回答 | claude_task_create → 用户得到 task_id → 跑完后小语念口语版总结 |
| "那个调研再加一项" | 重新做 | claude_task_continue(task_id, "再加一项...") |

### 1.3 关键约束（用户已确认）

- **集成深度**：B（中等）—— 新建 + 状态查询 + 续接，不暴露 Claude Code 内部 hook/MCP
- **运行模式**：纯异步 + 小语轮询。后台进程跑完写文件即可，小语不下发"任务完成"推送
- **结果压缩**：A（LLM 压缩成口语版总结）。后台任务完成后立即调一次项目自己的 LLM 生成 3-5 句口语版总结写入 `summary.md`，小语读 `summary.md` 给用户
- **范围**：只新增 4 个工具 + 1 个运行器 + 2 份 markdown；不改 `agent_extensions.load_tools` 的发现逻辑
- **平台**：macOS only（与现有项目一致）

### 1.4 范围

**做**：
- `workspace/extensions/tools/web_fetch.py` —— 1 个 `@function_tool` 函数
- `workspace/extensions/tools/claude_task.py` —— 3 个 `@function_tool` 函数
- `workspace/claude_task_runner.py` —— 后台 `asyncio.subprocess` 包装 + summarizer 调度
- `workspace/persona/TOOLS.md` —— 追加 4 个工具的触发词、参数、示例
- `workspace/persona/AGENTS.md` —— 重写"必做"段，新增 claude_task 工作流
- `main.py` —— 在 `entrypoint()` 里 `asyncio.create_task(_summarizer_loop())` 监听完成事件
- 5 个测试文件（详见 §6）
- `.gitignore` —— 追加 `.agent-tasks/`

**不做**（v0.1 明确）：
- 不暴露 Claude Code 内部 hook / MCP / skills 给小语直接编排
- 不做用户级"任务配额"或成本控制
- 不做 WebSocket 推送"任务完成"通知（小语主动轮询）
- 不实现 `web_fetch` 的 JS 渲染（用纯 HTML→MD）
- 不引入第三方 html→md 库以外的依赖（**注**：`markdownify` 单选）
- 不做任务结果的全文搜索（只支持按 task_id 查询单个任务）

---

## 2. 目录布局

```
/Users/pz/workspace/livekit/
├── workspace/
│   ├── extensions/
│   │   └── tools/
│   │       ├── current_time.py            # 现有
│   │       ├── web_search.py              # 现有
│   │       ├── web_fetch.py               # 新增
│   │       ├── claude_task.py             # 新增（3 个 @function_tool）
│   │       └── fs/                        # 现有
│   ├── claude_task_runner.py              # 新增：后台进程 + summarizer
│   └── persona/
│       ├── AGENTS.md                      # 重写"必做"段
│       └── TOOLS.md                       # 追加 4 个工具
├── main.py                                # 改 entrypoint()
└── tests/
    ├── test_web_fetch.py                  # 新增
    ├── test_claude_task.py                # 新增（mock subprocess）
    ├── test_claude_task_runner.py         # 新增（mock 进程 + 验证状态机）
    ├── test_e2e_claude_task.py            # 新增（端到端：派单→调研→查询）
    └── test_prompt_compliance.py          # 新增（可选：验证 prompt→tool 路由）
```

运行时数据落点：

```
/Users/pz/workspace/livekit/.agent-tasks/<task_id>/
  ├── task.json     # {id, prompt, status, started_at, finished_at, exit_code}
  ├── output.md     # claude --print 完整 stdout
  └── summary.md    # summarizer 写的口语版（status=ready 才存在）
```

`.agent-tasks/` 加入 `.gitignore`。

---

## 3. 新工具接口

### 3.1 `web_fetch`

```python
# workspace/extensions/tools/web_fetch.py
@function_tool()
async def web_fetch(url: str, max_chars: int = 8000) -> str:
    """抓取网页并转为 Markdown 文本。

    Args:
        url: 完整 URL（http/https）。
        max_chars: 返回的最大字符数，默认 8000；范围 [500, 50000]。

    Returns:
        Markdown 文本；截断时附 "[TRUNCATED at N/M chars]" 标记。
        抓取失败返回 "[ERROR] <reason>"。
    """
```

实现要点：
- `httpx.AsyncClient` GET，超时 15s，`follow_redirects=True`，UA 设为常见浏览器
- 用 `markdownify` 把 HTML 转 Markdown（已评估：比 html2text 更稳，pip 可装，单一依赖）
- 截断策略：保留 head 80% + tail 20%（避免只看到开头错过结尾）
- 非 2xx、超时、非 text/html 都返回 `[ERROR] ...`
- 大小 > 5MB 的响应直接拒绝，避免内存爆

### 3.2 `claude_task_create`

```python
@function_tool()
async def claude_task_create(prompt: str) -> str:
    """启动一个后台 Claude Code 调研任务。

    适用于：调研、深度分析、多步操作、跨多小时工作。
    任务在后台独立进程运行，完成后会自动生成口语版总结。

    Args:
        prompt: 完整的调研/任务描述。

    Returns:
        "task_id=<8位短码> status=running" —— 立即返回
        "[ERROR] claude CLI 未安装" —— 若 CLI 缺失
    """
```

实现要点：
- 生成短码：`uuid.uuid4().hex[:8]`
- 任务目录：`/Users/pz/workspace/livekit/.agent-tasks/<task_id>/`
- 写 `task.json`（status=created → 立即 running）
- `asyncio.create_task(_run_claude_subprocess(task_id, prompt))` 后立即返回（**不 await**）
- 不做任何超时设置

### 3.3 `claude_task_status`

```python
@function_tool()
async def claude_task_status(task_id: str) -> str:
    """查询后台任务状态。

    Args:
        task_id: claude_task_create 返回的 8 位短码。

    Returns:
        - "status=running" —— 还在跑
        - "status=summarizing" —— 跑完了但总结还在生成
        - "status=ready\n<summary.md 全文>" —— 已完成，**返回 summary.md 全文**（由 summarizer 控制在 ≤100 字，小语可直接念给用户）
        - "status=failed\n<summary.md 前 500 字>" —— 失败，**返回 summary.md 前 500 字**（失败时 summary.md 写 stderr 前 500 字）
        - "[ERROR] task <task_id> not found" —— 短码无效
    """
```

### 3.4 `claude_task_continue`

```python
@function_tool()
async def claude_task_continue(task_id: str, prompt: str) -> str:
    """在已有任务上追加指令（语义上等价于 Claude Code 的 --resume）。

    Args:
        task_id: 已存在的任务短码。
        prompt: 追加的指令。

    Returns:
        "task_id=<task_id> status=running continue_seq=N" —— 立即返回
        "[ERROR] task not found" / "[ERROR] task not in ready state" —— 错误
    """
```

实现要点：
- 只允许在 `status=ready` 状态上续接（避免与正在跑的进程撞）
- 把新 prompt append 到 `task.json.continuations: [...]` 数组（首条 `prompt` 字段保留为初始 prompt，后续续接 push 到 `continuations`）
- 把现有 `output.md` 和 `summary.md` 备份到 `<task_id>/archive/v<continue_seq>/`（**N = 当前 continue_seq**，续接前是 1，续接一次后变 2，备份当前内容到 `archive/v1/`，**避免被覆盖**）
- 启动新一轮 `claude --print` 并把 N+1 写回 `task.json`

---

## 4. Claude Code 任务运行器（核心）

### 4.1 进程模型

```
claude_task_create(prompt)
  ↓
asyncio.create_task(_run_claude_task(task_id, prompt, seq=1))
  ↓
subprocess exec:
  claude --print <prompt>
         --add-dir /Users/pz/workspace/livekit
         --append-system-prompt "你是中文助手，结果用中文输出"
         --output-format text
         --dangerously-skip-permissions
  ↓
stdout (UTF-8) → .agent-tasks/<task_id>/output.md
stderr → .agent-tasks/<task_id>/stderr.log
exit_code → task.json
  ↓
process.on_exit 回调
  ↓
若 exit_code == 0：summarizer (LLM call) → summary.md → status=ready
若 exit_code != 0：status=failed，summary.md = stderr 前 500 字
```

**关于 `--dangerously-skip-permissions`**：v0.1 demo 用法。生产化时必须改用 `--allowedTools` 白名单（详见 §7）。

### 4.2 状态机

```
created → running ──→ summarizing ──→ ready
                    │              ↘
                    │               failed (LLM 总结失败，降级为 output.md 前 500 字)
                    │
                    └─→ failed (后台进程非零退出，跳过 summarizing，summary.md = stderr 前 500 字)
```

`task.json.status` 字段取值为：`created` | `running` | `summarizing` | `ready` | `failed`

### 4.3 Summarizer 实现

> **注意**：summarizer 必须**独立构造** `volcengine.LLM`，**不能**借用当前 LiveKit session 里的 qwen-realtime 模型。原因：(a) qwen 是语音实时模型，未必走 OpenAI 兼容 chat 接口；(b) summarizer 在后台协程跑，跟 session 解耦。summarizer 用项目 `.env` 里的 `VOLCENGINE_LLM_API_KEY`。

```python
# workspace/claude_task_runner.py
async def _summarize(task_id: str, full_output: str) -> str:
    """用 volcengine.LLM 把长输出压成 3-5 句口语版。"""
    prompt = f"""请把以下 Claude Code 调研结果压缩成 3-5 句中文口语版总结，
    用于语音助手告诉用户。不超过 100 字。保留关键结论和数据。
    不要 markdown、不要 emoji、不要项目符号。

    原始输出:
    {full_output[:6000]}
    """
    llm = volcengine.LLM(
        model="doubao-1-5-pro-32k-250115",
        api_key=os.environ["VOLCENGINE_LLM_API_KEY"],
    )
    stream = llm.chat(messages=[{"role": "user", "content": prompt}])
    chunks: list[str] = []
    async for c in stream:
        if c.delta.content:
            chunks.append(c.delta.content)
    return "".join(chunks).strip()
```

降级策略：summarizer LLM 调用失败时，直接用 `output.md[:500]` 写入 `summary.md`。

### 4.4 错误处理

| 场景 | 处理 |
|---|---|
| `claude` CLI 不存在 | `claude_task_create` 立即返回 `[ERROR] claude CLI 未安装` |
| 后台进程非零退出 | exit_code 写入 task.json；status=failed；summary.md = stderr 前 500 字 |
| 后台进程 OOM / kill -9 | 同上 |
| Summarizer LLM 调用失败 | 降级：output.md 前 500 字写入 summary.md，status=ready |
| `claude_task_continue` 引用不存在 task_id | `[ERROR] task not found` |
| `claude_task_continue` 在 running/summarizing 状态调用 | `[ERROR] task not in ready state, current=<status>` |
| `web_fetch` 抓取失败（404 / 超时 / 非 HTML） | `[ERROR] <reason>`，不抛异常 |
| `.agent-tasks/<task_id>/` 写失败（权限） | 返回 `[ERROR] cannot create task dir`，进程不启动 |

---

## 5. AGENTS.md / TOOLS.md 重写要点

### 5.1 `AGENTS.md` 新增段落

```markdown
## 必做（新增）
- 用户说"调研/分析/对比/写个 XX" 类深度任务 → 先调 `claude_task_create`
- 用户说"怎么样了/进度/进展" → 调 `claude_task_status(task_id)`
- 用户在原任务上加新要求 → 调 `claude_task_continue(task_id, prompt)`
- `web_search` 拿到 URL 后需要看正文 → 调 `web_fetch(url)`

## 不做（新增）
- 不要同时启动超过 3 个 `claude_task`（防止后台进程雪崩）
- 不要把 `claude_task` 的完整 `output.md` 念给用户（用 `summary.md`）
- 不要用 `claude_task` 做 1 步能完成的事（查时间/读小文件/简单搜索）
- 拿到 task_id 后自己记住，不要每次都问用户
```

### 5.2 `TOOLS.md` 新增段落

```markdown
## `web_fetch`
- 触发词："打开 XX 看看"、"读 XX 链接"、"XX 页面内容"
- 必先 `web_search` 再决定要不要 fetch
- max_chars 默认 8000，需要长文可调到 20000

## `claude_task_create`
- 触发词："调研 XX"、"分析 XX"、"对比 XX"、"帮我写个 XX"
- 返回 task_id（8 位短码），自己记住，下次用户问"怎么样了"直接用

## `claude_task_status`
- 触发词："进展怎么样"、"XX 任务怎么样了"、"还在跑吗"
- ready 状态返回口语版总结（直接念给用户）

## `claude_task_continue`
- 触发词："再加一项"、"顺便"、"那个调研再看看 XX"
- 必传 task_id，且任务必须在 ready 状态
```

---

## 6. 测试

| 测试文件 | 方式 | 验证点 |
|---|---|---|
| `tests/test_web_fetch.py` | 真实抓 `https://httpbin.org/html` | Markdown 转换正确、截断标记存在、超时返回 `[ERROR]` |
| `tests/test_claude_task.py` | mock subprocess | 3 个工具的 happy path + 各种 `[ERROR]` 分支 |
| `tests/test_claude_task_runner.py` | mock 进程 + 注入固定 output.md | 状态机流转、summarizer 降级、stderr 写入 |
| `tests/test_e2e_claude_task.py` | 真实派单：`派单 → "调研 LiveKit Agents 1.6 新特性" → claude_task_create → 等 ready → claude_task_status 返回口语版` | 端到端不超过 90s |
| `tests/test_prompt_compliance.py` | 用 10 条 fixture prompt（"调研 X"、"对比 X"、"X 链接里写了什么"等），断言至少 7 条调对了工具 | 工具路由准确率 ≥ 70% |

可选：跑 `tests/test_e2e_realtime.py` 验证 baseline 不回归。

---

## 7. 风险与边界

- **延迟**：summarizer 多一次 LLM 调用，~3-5s 延迟。`claude_task_status` 看到 `status=summarizing` 时返回 "马上就好，再等一下"
- **并发**：默认 worker 单 session，但 claude_task 后台进程独立，10 个并发没问题
- **磁盘**：`.agent-tasks/<id>/output.md` 可能很大（几十 MB），加 cleanup task：>7 天的自动删（**v0.2 实现，v0.1 仅记录 TODO**）
- **安全**：`--dangerously-skip-permissions` 仅 demo 用；生产应 `--allowedTools` 白名单（**v0.2 TODO**）
- **跨平台**：只测 macOS（与现有项目一致）
- **依赖**：`httpx`、`markdownify` 需加入 `pyproject.toml`（v0.1 不加锁版本，宽松 pin `>=0.7,<1.0`）

---

## 8. 实施清单（按顺序）

1. `pyproject.toml` 加 `httpx`、`markdownify` 依赖 → `pip install`
2. 写 `workspace/extensions/tools/web_fetch.py` → 单元测试通过
3. 写 `workspace/claude_task_runner.py`（含 summarizer）→ 单元测试通过
4. 写 `workspace/extensions/tools/claude_task.py`（3 个工具）→ 单元测试通过
5. 改 `main.py` —— `entrypoint()` 起 `_summarizer_loop()` 后台协程
6. 重写 `workspace/persona/AGENTS.md` + `TOOLS.md`
7. 改 `.gitignore` —— 追加 `.agent-tasks/`
8. 跑 `tests/test_e2e_realtime.py` baseline 验证不回归
9. 跑 `tests/test_e2e_claude_task.py` 端到端验证
10. 更新 `CLAUDE.md` "常用命令" 表格，添加 `claude_task_*` 相关说明

---

## 9. v0.2 迁移路径（不在本 spec 范围）

- 路径白名单 + 命令白名单
- `--allowedTools` 收紧 Claude Code 权限
- `.agent-tasks/` 自动 cleanup（>7 天删）
- summarizer 缓存（同一 output.md 不重复总结）
- task 列表查询（按时间/状态过滤）
- 多 worker 共享任务池（Redis 或 sqlite 替换文件存储）