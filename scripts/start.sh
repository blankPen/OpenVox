#!/bin/bash
# scripts/start.sh — 启动 / 停止 / 查询 LiveKit Agent Worker
#
# 用法：
#   ./scripts/start.sh         # 后台启动，日志到 /tmp/livekit-worker.log
#   ./scripts/start.sh fg      # 前台启动（看实时日志）
#   ./scripts/start.sh stop    # 停止 worker
#   ./scripts/start.sh status  # 查看状态 + 最近日志

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# python 探测（无硬编码绝对路径）
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
elif command -v python >/dev/null 2>&1; then
    PY="python"
else
    echo "ERROR: 找不到 python（请激活 venv 或安装 python3）"
    exit 1
fi

# 配置入口：~/.openvox/config.json（路径可用 OPENVOX_CONFIG 环境变量覆盖）。
# main.py 在 import 时就读，缺关键 key 会 ConfigError 早抛。这里只做
# 一次性的存在性 / 语法检查，让启动脚本能先报而不是要等 worker 起来后
# 在日志里才看到 import error。
CONFIG_PATH="${OPENVOX_CONFIG:-$HOME/.openvox/config.json}"
CONFIG_PATH="${CONFIG_PATH/#\~/$HOME}"  # 展开开头的 ~
if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: config not found: $CONFIG_PATH"
    echo "       main.py 不再读本地 .env；请创建 ~/.openvox/config.json（schema 见 config.py）。"
    exit 1
fi
if ! "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$CONFIG_PATH" 2>/dev/null; then
    echo "ERROR: config 解析失败: $CONFIG_PATH"
    exit 1
fi

# 从 config 抽 AGENT_NAME 仅用于展示（main.py 自己会读）
AGENT_NAME=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['livekit']['agent_name'])" "$CONFIG_PATH" 2>/dev/null || echo "<unknown>")

# 把 LIVEKIT_* 导出到环境变量 — livekit-agents 的 worker.run() 走
# os.environ['LIVEKIT_URL'] / ['LIVEKIT_API_KEY'] / ['LIVEKIT_API_SECRET']
# 这条路径，main.py 自己用 config 读，但 LiveKit SDK 内部仍然期望 env。
# 不破坏 main.py 的「配置走 config」原则，只在启动器这一层做适配。
LIVEKIT_URL=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['livekit']['url'])" "$CONFIG_PATH")
LIVEKIT_API_KEY=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['livekit']['api_key'])" "$CONFIG_PATH")
LIVEKIT_API_SECRET=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['livekit']['api_secret'])" "$CONFIG_PATH")
export LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET

ACTION="${1:-start}"
WORKER_PORT="${WORKER_PORT:-8081}"
LOG="${WORKER_LOG:-/tmp/livekit-worker.log}"

stop_worker() {
    pids=$(lsof -ti:$WORKER_PORT 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "杀掉卡在 $WORKER_PORT 的 worker: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

case "$ACTION" in
    start)
        stop_worker
        echo "后台启动 worker..."
        "$PY" main.py start > "$LOG" 2>&1 &
        echo "worker pid: $!"
        sleep 5
        if lsof -i:$WORKER_PORT >/dev/null 2>&1; then
            echo "✅ worker 在跑（日志：$LOG）"
            echo "AGENT_NAME=$AGENT_NAME"
            echo ""
            echo "派单："
            echo "  lk dispatch create --room demo --agent-name $AGENT_NAME"
            echo ""
            echo "查日志：tail -f $LOG"
            echo "停 worker：./scripts/start.sh stop"
        else
            echo "❌ worker 没起来，看日志：$LOG"
            tail -30 "$LOG"
            exit 1
        fi
        ;;

    fg)
        stop_worker
        exec "$PY" main.py start
        ;;

    stop)
        stop_worker
        echo "✅ worker 已停"
        ;;

    status)
        if lsof -i:$WORKER_PORT >/dev/null 2>&1; then
            pid=$(lsof -ti:$WORKER_PORT | head -1)
            echo "✅ worker 在跑（pid $pid，端口 $WORKER_PORT）"
            echo "日志：$LOG"
            echo ""
            echo "最近 15 行："
            tail -15 "$LOG"
        else
            echo "❌ worker 没在跑"
            echo "启动：./scripts/start.sh"
        fi
        ;;

    *)
        echo "用法：$0 [start|fg|stop|status]"
        echo ""
        echo "  start   - 后台启动（默认）"
        echo "  fg      - 前台启动（实时日志）"
        echo "  stop    - 停止 worker"
        echo "  status  - 查看状态 + 最近日志"
        exit 1
        ;;
esac
