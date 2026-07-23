# Room 命名 / Agent 命名规则

> 跨端一致的房间 + agent 命名约定。**任何一端硬编码字符串前先看这里。**

## Agent 名称

```
agent_name = "openvox"
```

- LiveKit dispatch 时用：`lk dispatch create --agent-name openvox`
- LiveKit worker 注册时用（见 `apps/voice-agent/main.py`）
- Flutter 客户端连 Session 时填 `roomOptions: { agentName: "openvox" }`

## Room 名称（约定，未钉死具体值）

| 用途 | Room 名 | 谁创建 |
|---|---|---|
| 本地开发 / e2e 测试 | `dev-{user}-{yyyyMMdd}` 例：`dev-pz-20260723` | 客户端 |
| 生产（按租户） | `voice-{tenant_id}-{uuid}` 例：`voice-t_acme-9a3f` | 派单服务 |
| 演示 | `demo` | 客户端 |

## 命名规则

```
room_name = [namespace-]{subject}-{short_id}
```

- `namespace` 用 dev / prod / demo，便于按环境过滤
- `subject` 描述语义（用户 id / 租户 id / 演示名）
- `short_id` 时间戳或 uuid 末 4 位避免冲突
- **全部小写**，LiveKit Room 名称按小写比较

## 不允许的形式

- ❌ 纯数字房间名（容易和 token subject 错位）
- ❌ 含 `:` `@` `/` 的房间名（URL/路径解析时会断）
- ❌ 含空格或中文的房间名（web 客户端 join 失败）

## 派单（dispatch）方式

后端 worker 启动后默认注册到 `agent_name = "openvox"`。派单方式：

```bash
# 派到指定房间
lk dispatch create --agent-name openvox --room dev-pz-20260723
```

客户端无需自己 dispatch，只需 `room.connect(url, token)`，LiveKit 自动把 agent 拉进同一房间。
