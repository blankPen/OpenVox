#!/bin/bash
# run_tests.sh — 在主目录运行 fs-tools 测试
#
# 用法：
#   ./scripts/run_tests.sh              # 默认 unit：单元测试（不需 LiveKit）
#   ./scripts/run_tests.sh unit         # 单元测试
#   ./scripts/run_tests.sh e2e          # 只跑 e2e
#   ./scripts/run_tests.sh full         # 跑全部
#
# 前置：
# - .env 文件存在（含 LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET / AGENT_NAME）
# - .venv 已创建（路径：<repo_root>/.venv）

# 切到主目录（脚本所在目录的父级）
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# 探测 python：优先 .venv/bin/python，回退到 PATH 里的 python3 / python
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

MODE="${1:-unit}"

# 加载 .env
if [ -f .env ]; then
    set -a
    source .env
    set +a
else
    echo "ERROR: .env not found in $REPO_ROOT"
    exit 1
fi

echo "=== fs-tools 测试 (mode: $MODE) ==="
echo "repo: $REPO_ROOT"
echo "py:   $PY"
echo ""

case "$MODE" in
    unit)
        echo "[unit] 跑单元测试（不需 LiveKit server）"
        "$PY" -m pytest tests/ \
            --ignore=tests/e2e_generate_reply.py \
            --ignore=tests/test_e2e_fs_tools.py \
            -v
        ;;

    e2e)
        echo "[e2e] 跑 e2e 烟雾测试（需要 LiveKit server，autouse fixture 会自动启动 worker）"
        "$PY" -m pytest tests/test_e2e_fs_tools.py -v -s
        ;;

    full)
        echo "[1/2] 单元测试"
        "$PY" -m pytest tests/ \
            --ignore=tests/e2e_generate_reply.py \
            --ignore=tests/test_e2e_fs_tools.py \
            -v
        echo ""
        echo "[2/2] e2e 烟雾测试"
        "$PY" -m pytest tests/test_e2e_fs_tools.py -v -s
        ;;

    *)
        echo "用法：$0 [unit|e2e|full]"
        echo ""
        echo "  unit  - 单元测试（82 passed），不需 LiveKit"
        echo "  e2e   - e2e 烟雾测试（6 xfailed），需要 LiveKit + worker"
        echo "  full  - 全部"
        exit 1
        ;;
esac
