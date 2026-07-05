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

# 加载 .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "ERROR: .env not found in $REPO_ROOT"
    exit 1
fi

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
