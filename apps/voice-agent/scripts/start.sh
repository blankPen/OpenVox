#!/bin/bash
# scripts/start.sh — start/fg/stop/status 兼容 shim
#
# 委派给统一 openvox CLI（openvox_cli.py）；本脚本不再实现任何 provider /
# 进程生命周期逻辑，也不再假设本机独占 LiveKit/agent 端口——避免影响共享开发
# 机上其他用户的进程。
#
# 用法：
#   ./scripts/start.sh         # openvox start --yes
#   ./scripts/start.sh fg      # openvox start --yes（前台）
#   ./scripts/start.sh stop    # openvox stop
#   ./scripts/start.sh status  # openvox status
#
# 注意：
# - `start` / `fg` 不假设本机独占 LiveKit IPC 端口（8081 之类）；如果端口
#   被无关进程占用，那是用户/LiveKit 自己的问题，由 openvox_cli 报错。
# - `stop` 只停止受管的 `agentd`，不会去碰本机其它 LiveKit / worker 进程。
# - 没有 LIVEKIT 进程的机器上跑 `status` 是合法的：openvox_cli.py status
#   只读 supervisor pidfile，不会去探测端口。

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

# 真正的进程生命周期交给统一 CLI。本脚本不再做任何基于端口的进程管理。
run_openvox() {
    exec "$PY" "$REPO_ROOT/openvox_cli.py" "$@"
}

ACTION="${1:-start}"

case "$ACTION" in
    start)
        run_openvox start --yes
        ;;

    fg)
        # 前台（实时日志）；同一份 worker 由 CLI 拉起；脚本不去抢别人的端口。
        run_openvox start --yes
        ;;

    stop)
        run_openvox stop
        ;;

    status)
        run_openvox status
        ;;

    *)
        echo "用法：$0 [start|fg|stop|status]"
        echo ""
        echo "  start   - 启动 worker（受管；不会碰本机其它 LiveKit 进程）"
        echo "  fg      - 前台启动（实时日志）"
        echo "  stop    - 停止受管的 agentd"
        echo "  status  - 查看 provider 状态"
        exit 1
        ;;
esac
