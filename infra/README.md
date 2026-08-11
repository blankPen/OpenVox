# infra

> OpenVox 的本地 LiveKit Server 部署文件。当前只有一个 `docker-compose.yml` 起一个 dev-mode 的 LiveKit 容器。

承接 [INSTALLATION.md § 3.4 LiveKit Server](../INSTALLATION.md)；本文件讲 `infra/` 的**具体内容、端口、与 `task dev:infra` 的等价关系**，以及 dev vs 生产的边界。

---

## 1. 目录速查

```
infra/
├── README.md             # 本文件
└── docker-compose.yml    # 单服务（livekit）docker-compose，dev 模式
```

极简 —— 整个目录就一个 compose 文件。

---

## 2. 服务清单

### `livekit`（唯一服务）

```yaml
# infra/docker-compose.yml
services:
  livekit:
    image: livekit/livekit-server:latest
    container_name: openvox-livekit
    command: --dev
    ports:
      - "7880:7880"        # HTTP signaling + API
      - "7881:7881"        # TCP fallback
      - "7882:7882"        # WebRTC media (TCP)
      - "7882:7882/udp"    # WebRTC media (UDP)
    restart: unless-stopped
```

| 字段 | 值 | 说明 |
|---|---|---|
| 镜像 | `livekit/livekit-server:latest` | 始终拉最新；CI 用 `tag` 锁定 |
| 容器名 | `openvox-livekit` | 方便 `docker logs openvox-livekit` |
| 启动参数 | `--dev` | dev 模式（关鉴权、宽松限流、内置 devkey/secret）；**生产勿用** |
| 重启策略 | `unless-stopped` | 崩溃自动重启；`docker compose stop` 才会真正停 |

---

## 3. 端口映射

| 主机端口 | 容器端口 | 协议 | 用途 |
|---|---|---|---|
| 7880 | 7880 | TCP | HTTP signaling + LiveKit HTTP API |
| 7881 | 7881 | TCP | TCP fallback（弱网环境） |
| 7882 | 7882 | TCP + UDP | WebRTC 媒体（音视频帧） |

`firewall` / 路由器放行时，7880 + 7882/UDP 是最少必要集。7881 可选。

> **7882 必须放 UDP**。LiveKit 默认走 UDP 传媒体；如果被防火墙挡住，客户端能进房但听不到 / 看不到对方。

---

## 4. 启停命令

### 直接 docker compose

```bash
# 起
(cd infra && docker compose up -d)

# 看状态
docker ps | grep openvox-livekit
docker logs -f openvox-livekit    # 跟日志

# 健康检查（端口可达 = up）
curl -sf http://localhost:7880 >/dev/null && echo "LiveKit up"

# 停（保留数据卷，如果有）
(cd infra && docker compose down)

# 停 + 删数据卷（完全清空）
(cd infra && docker compose down -v)
```

### 通过 Taskfile（等价）

```bash
task dev:infra           # = docker compose up -d
task dev:infra-down      # = docker compose down
```

> Taskfile 是薄 wrapper，详见 [tooling/README.md § 2 Taskfile vs scripts/ 的分工](../tooling/README.md)。

### 通过顶层脚本（环境 bootstrap）

`./scripts/install.sh`（无 `--no-livekit` 时）会自动跑 `docker compose up -d`。详见 [INSTALLATION.md § 2 一键安装](../INSTALLATION.md)。

---

## 5. Dev vs 生产

| 维度 | dev（当前 compose） | 生产（**不在本仓库范围**） |
|---|---|---|
| 启动参数 | `--dev` | 无 `--dev`；配置由 `livekit.yaml` 驱动 |
| 鉴权 | 关 | 启用；签 token 用 `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` |
| API key | 内置 `devkey` / `secret` | 强密钥（≥ 32 字节随机），存 Secret Manager |
| TURN | 关 | 启用 STUN/TURN（NAT 穿透） |
| 录制 | 关 | 按需启用 S3 / GCS / Azure Blob |
| 水平扩展 | 单实例 | 多实例 + 外部 Redis / 单点 etcd |
| 镜像 tag | `latest`（漂移风险） | 锁版本（如 `v1.8.0`） |

生产部署指引见 LiveKit 官方文档：

- 自建：<https://docs.livekit.io/home/self-hosting/>
- LiveKit Cloud：<https://docs.livekit.io/home/cloud/>

---

## 6. 已知坑

| 症状 | 原因 | 处理 |
|---|---|---|
| `docker compose up -d` 报 `bind: address already in use` | 7880 / 7881 / 7882 被占用 | `lsof -ti:7880 \| xargs kill -9`（macOS / Linux）/ Windows：`netstat -ano \| findstr :7880` + `taskkill /PID <pid> /F` |
| 容器 up 但客户端连不上 | 防火墙挡了 7882/UDP | 路由器放行 UDP 7882 |
| `flutter run` 报 `LiveKit not reachable` | 容器没起 / 端口没暴露 | `docker ps` 确认；`curl http://localhost:7880` 验证 |
| `docker logs openvox-livekit` 报 `address already in use` | 旧容器没干净退出 | `docker rm -f openvox-livekit && cd infra && docker compose up -d` |
| `latest` 镜像漂移导致 API 变化 | 没锁 tag | 编辑 `docker-compose.yml` 把 `latest` 改成具体 tag（如 `v1.8.0`） |

---

## 7. 修改 compose 的注意事项

- **不要直接改 `image: latest`**：CI / 生产锁版本；改之前先讨论 release 计划
- **不要把 `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` 写进 compose** —— 走 env_file 或 docker secret
- **端口冲突** 是改 compose 最常见的副作用；改之前 `lsof -i:7880-7882` 看占用
- **`command: --dev`** 是 dev 模式；生产前必须删掉，改用 `livekit.yaml` 配置文件挂载
- **CI** 里用 `docker compose -f infra/docker-compose.yml up -d` 起 LiveKit（见 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) 中相关 step）

---

## 8. 下一步

- 装环境 → [INSTALLATION.md](../INSTALLATION.md)
- 跑起来 → [USAGE.md § 4.A 本地端到端调试](../USAGE.md)
- 改代码 → [CONTRIBUTING.md](../CONTRIBUTING.md)
- 系统设计 → [ARCHITECTURE.md § 4.2 LiveKit Server](../ARCHITECTURE.md)
- 顶层编排 → [tooling/README.md](../tooling/README.md)
- 生产部署 → LiveKit 官方文档（不在本仓库）
