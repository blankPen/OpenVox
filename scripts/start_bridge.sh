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

# 配置入口：~/.openz/config.json（与 start.sh 一致；OPENZ_CONFIG 可覆盖）
CONFIG_PATH="${OPENZ_CONFIG:-$HOME/.openz/config.json}"
CONFIG_PATH="${CONFIG_PATH/#\~/$HOME}"
if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: config not found: $CONFIG_PATH"
    echo "       bridge_server.py 不再读本地 .env；请创建 ~/.openz/config.json。"
    exit 1
fi
if ! "$PY" -c "import json,sys; json.load(open(sys.argv[1]))" "$CONFIG_PATH" 2>/dev/null; then
    echo "ERROR: config 解析失败: $CONFIG_PATH"
    exit 1
fi

# 端口从 config 读（与 bridge_server.py 内部一致）。BRIDGE_PORT shell 变量
# 仍可覆盖，方便临时换端口调试。
BRIDGE_PORT_FROM_CONFIG=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['bridge_server']['port'])" "$CONFIG_PATH")
BRIDGE_PORT="${BRIDGE_PORT:-$BRIDGE_PORT_FROM_CONFIG}"
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
