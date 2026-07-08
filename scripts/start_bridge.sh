#!/bin/bash
# scripts/start_bridge.sh — 后台启动 / 停止 / 查询 Hermes bridge server (8765)
#
# 用法：
#   ./scripts/start_bridge.sh         # 后台启动，日志到 /tmp/bridge.log
#   ./scripts/start_bridge.sh fg      # 前台启动（实时日志）
#   ./scripts/start_bridge.sh stop    # 停止 bridge
#   ./scripts/start_bridge.sh status  # 查看状态 + 最近日志
#   ./scripts/start_bridge.sh restart # 重启

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# python 探测
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PY="$REPO_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "ERROR: 找不到 python（请激活 venv 或安装 python3）"
    exit 1
fi

# 加载 .env（让 BRIDGE_* / HERMES_API_* 注入到 bridge_server）
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

BRIDGE_PORT="${BRIDGE_PORT:-8765}"
LOG="${BRIDGE_LOG:-/tmp/bridge.log}"

stop_bridge() {
    pids=$(lsof -ti:"$BRIDGE_PORT" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        echo "杀掉卡在 $BRIDGE_PORT 的 bridge: $pids"
        echo "$pids" | xargs kill -9 2>/dev/null || true
        sleep 1
    fi
}

case "${1:-start}" in
    start)
        stop_bridge
        echo "后台启动 bridge（端口 $BRIDGE_PORT，日志 $LOG）..."
        # 后台跑，日志写文件；不阻塞终端
        nohup "$PY" scripts/bridge_server.py >> "$LOG" 2>&1 &
        disown || true
        BRIDGE_PID=$!
        echo "bridge pid: $BRIDGE_PID"
        sleep 2
        if curl -sf "http://127.0.0.1:$BRIDGE_PORT/health" >/dev/null 2>&1; then
            echo "✅ bridge 起来了"
            echo ""
            echo "查日志：tail -f $LOG"
            echo "停 bridge：./scripts/start_bridge.sh stop"
            echo "查状态：./scripts/start_bridge.sh status"
        else
            echo "❌ bridge 没起来，看日志：$LOG"
            tail -30 "$LOG"
            exit 1
        fi
        ;;

    fg)
        stop_bridge
        exec "$PY" scripts/bridge_server.py
        ;;

    stop)
        stop_bridge
        echo "✅ bridge 已停"
        ;;

    restart)
        "$0" stop
        "$0" start
        ;;

    status)
        if curl -sf "http://127.0.0.1:$BRIDGE_PORT/health" >/dev/null 2>&1; then
            pid=$(lsof -ti:"$BRIDGE_PORT" 2>/dev/null | head -1)
            echo "✅ bridge 在跑（pid $pid，端口 $BRIDGE_PORT）"
            echo "日志：$LOG"
            echo ""
            echo "最近 15 行："
            tail -15 "$LOG"
        else
            echo "❌ bridge 没在跑"
            echo "启动：./scripts/start_bridge.sh"
        fi
        ;;

    *)
        echo "用法：$0 [start|fg|stop|restart|status]"
        exit 1
        ;;
esac
