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
