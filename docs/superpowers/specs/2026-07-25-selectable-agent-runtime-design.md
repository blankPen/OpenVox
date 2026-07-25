# OpenVox 可选 Agent Runtime 设计规格

## 目标

将 OpenVox 当前硬编码的 Hermes LLM 管线改为可选择的全局 runtime provider，并由统一的 `openvox` CLI 管理配置与进程生命周期。

首期目标：

- 保留 Hermes 作为外部 OpenAI-compatible provider。
- 将 agentd 纳入 OpenVox monorepo，保留 Node/TypeScript 实现，由 OpenVox CLI 自动启动。
- 通过 agentd 的 Claude provider 接入 Claude CLI。
- Codex 与 OpenClaw 只登记为 `planned` 占位，不在首期启动或声称可用。
- 保持 LiveKit STT、TTS、AgentSession、DataChannel 和现有 Agent 行为不变。
- 所有新增/变更行为按 TDD 实施：每个生产行为先有一个可观察的失败测试。

## 约束与决策

1. OpenVox 的统一入口采用 Python CLI，优先使用标准库，避免新增运行时依赖。
2. agentd 保留为 Node 20+ 子项目，路径为 `apps/agentd`；不将 TypeScript 逻辑重写为 Python。
3. `openvox init` 选择全局默认 provider，不支持首期 room/session 动态覆盖。
4. `llm.provider` 的 active 值只有 `hermes` 与 `agentd`；`codex`、`openclaw` 只出现在 provider catalog 中，状态为 `planned`。
5. Hermes 不迁入 OpenVox。CLI 通过 `hermes` 命令和 HTTP API 检测、引导或在明确授权时配置/启动它。
6. agentd 和 Hermes 均通过 OpenAI-compatible Chat Completions 接口供 LiveKit 的 `openai.LLM` 使用；不在 OpenVox 引入 Anthropic SDK。
7. 旧配置没有 `llm.provider` 时按 Hermes 解释，以保持向后兼容。

## 配置契约

继续使用 `~/.openvox/config.json`。新增结构如下（示例值仅作说明）：

```json
{
  "llm": { "provider": "agentd" },
  "agentd": {
    "api_base": "http://127.0.0.1:8787/v1",
    "api_key": "",
    "model": "agentd/claude",
    "host": "127.0.0.1",
    "port": 8787,
    "startup_timeout_seconds": 20
  },
  "hermes": {
    "cli": "hermes",
    "api_base": "http://127.0.0.1:8642/v1",
    "api_key": "",
    "model": "hermes-agent",
    "auto_start": false,
    "auto_configure": false,
    "startup_timeout_seconds": 20
  }
}
```

配置写入必须使用临时文件和原子替换，并将包含密钥的文件权限设为 `0600`。CLI 输出和异常不得打印完整密钥。

## Provider readiness

### Hermes

检测顺序：

1. `shutil.which()` 查找配置的 CLI，并执行 `hermes --version`。
2. 对配置的 API host 请求 `GET /health`。
3. 健康后携带 Bearer token 请求 `GET /v1/models`。
4. HTTP 失败时执行只读的 `hermes gateway status`，区分 CLI 缺失、服务未启动和服务配置异常。
5. 交互模式提供启动/配置/手动说明选项；`--yes` 或显式 `auto_start` 才允许执行 `hermes gateway start`。
6. `openvox hermes setup` 通过 Hermes 官方 `config set` 写入 api_server 配置，先备份、后启动/重启并轮询健康；失败时恢复备份。

普通 `openvox start` 默认不修改 Hermes 配置文件。外部 Hermes endpoint 仍由 Hermes 自己维护。

### agentd/Claude

1. 校验 Node.js 20+、agentd 构建产物和 `claude` CLI。
2. 生成 OpenVox 管理的 agentd 配置文件，传入 `--config` 或 `AGENTD_CONFIG`。
3. 启动 `node apps/agentd/dist/index.js`，等待 `/health`，再确认 `agentd/claude` 出现在 `/v1/models`。
4. 启动 LiveKit worker；worker 使用 LLM factory 读取 `agentd.*`。
5. `stop` 只终止本次 CLI 启动的子进程，不误杀用户已有的 agentd/Hermes 进程。

### planned provider

Codex/OpenClaw 的 catalog 项包含 id、label、状态、未来协议说明和错误提示；若写入 active 配置，CLI 以非零退出码拒绝并说明“尚未实现”，不启动 stub。

## CLI 设计

- `openvox init [--provider hermes|agentd] [--config PATH]`：交互向导或 flags；密钥不回显。
- `openvox start [--yes] [--config PATH]`：校验 provider readiness，按需启动 agentd，启动 LiveKit worker。
- `openvox stop [--config PATH]`：按 pid/state 文件优雅停止本次启动的进程。
- `openvox status [--json]`：显示配置 provider、CLI/API readiness、受管进程状态和 planned provider。
- `openvox doctor hermes`：只读诊断。
- `openvox hermes setup [--yes]`：备份并调用 Hermes 官方 config CLI 完成 API server 配置。

进程状态和日志放在 `~/.openvox/runtime/`，避免依赖固定端口杀进程。启动失败时必须清理本次已启动的子进程并返回可行动的错误。

## Python worker 适配

新增独立的 provider factory/配置解析模块：

- `hermes` → 当前 `hermes.api_base/api_key/model`。
- `agentd` → `agentd.api_base/api_key/model`。
- planned provider → 明确的配置错误。

`main.py` 继续只调用 factory，并保留现有 Hermes stream compatibility patch，直到两条路径都有回归测试证明可以安全移除。STT/TTS 构造和会话回调不变。

## 测试策略（TDD）

每个行为先写测试并确认测试因缺失实现而失败，再写最小实现：

- 配置：provider 默认、schema、旧配置兼容、planned 拒绝、原子写入/权限/脱敏。
- Hermes：CLI 缺失、版本失败、health 成功/失败、models 鉴权、gateway 启动提示、`--yes` 自动启动、setup 备份/恢复。
- agentd：Node/构建缺失、配置投影、健康等待、子进程退出、只停止 owned process。
- LLM factory：Hermes/agentd 参数映射与 planned 错误。
- CLI：init/start/stop/status/doctor 的退出码和输出。
- 现有 LiveKit 回归：`AgentSession` 装配、Hermes compatibility patch、STT/TTS 和 greeting 行为。
- 真实 Hermes/Claude 冒烟测试仅在显式环境变量开启时运行，不在默认单测中修改用户服务。

## 非目标

- 本次不实现 Codex/OpenClaw 的真实协议/provider。
- 本次不重写 agentd 为 Python。
- 本次不增加 room/session 级 provider 选择。
- 本次不迁移 Hermes 源码或替换 Hermes 的服务管理器。
- 本次不改变 Flutter 客户端协议或 LiveKit dispatch 名称。
