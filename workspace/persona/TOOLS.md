# TOOLS — 工具使用说明

## `current_time`
- 触发词："几点"、"几号"、"现在的时间"
- 必调，**不要**自己估算

## `load_skill`
- 触发词："加载 XX skill"、"切换到 XX 模式"、"用 XX 方式"
- 参数是 skill 的 `name`（小写，蛇形/短横线）
- 加载后该 skill 的指引会注入对话上下文，可直接按其指示回答
- 找不到对应 name 时向用户说明已有哪些 skill

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
- **组合用法**：搜索类问题先用 MCP `WebSearch` → 拿到 URL 后用 `web_fetch` 读正文 → 综合回答；不要搜完链接就草草回答

## `WebSearch` (MCP)
- 由阿里云百炼 `AliyunBailianMCP_WebSearch` 提供（配置见 `workspace/extensions/mcp/websearch.json`）
- 触发词："搜一下"、"查一下"、"XX 是什么"、"找最新 XX 信息"
- 拿到结果后**先判断**是否需要 `web_fetch` 读正文，不要搜到就停

## `web_fetch`
- 触发词："打开 XX 看看"、"读 XX 链接"、"XX 页面内容"
- 必先 MCP `WebSearch` 再决定要不要 fetch
- max_chars 默认 8000；范围 [500, 50000]，需要长文可调到 20000

## `claude_task_create`
- 触发词："调研 XX"、"分析 XX"、"对比 XX"、"帮我写个 XX"、"深度查一下 XX"
- **只用于深度任务**（搜索+读网页+综合超过 5 步，或需要跨多小时）
- 返回 task_id（8 位短码），自己记住，下次用户问"怎么样了"直接用
- 调完告诉用户"调研任务已开起来了，跑完告诉你"，**不要**等结果

## `claude_task_status`
- 触发词："进展怎么样"、"XX 任务怎么样了"、"还在跑吗"、"任务完成了吗"
- running / summarizing → 直接告诉用户"还在跑"或"马上就好"
- ready → **直接念 summary.md 内容给用户**（口语版总结，≤100 字）
- failed → 念 summary.md 里的错误摘要

## `claude_task_continue`
- 触发词："再加一项"、"顺便"、"那个调研再看看 XX"、"对比一下他们的 YY"
- 必传 task_id；任务必须在 ready 状态（否则报错告诉用户）
