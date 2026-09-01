#!/usr/bin/env bash
# 开发迭代用: 交叉编译并更新包含 librkclaw_native.so 的 gateway wheel。
#
# 用法:
#   export DEVICE=your-adb-serial
#   ./scripts/dev_deploy.sh
#
# 可选环境变量:
#   DEVICE      adb 设备序列号 (必填)
#   BOARD_ROOT  板端部署根, 默认 /userdata/RKClawServer
#   CROSS_COMPILE / NATIVE_BUILD_DIR / NATIVE_INSTALL_DIR 可覆盖交叉编译配置

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

: "${DEVICE:?需要设置 DEVICE=adb 设备序列号}"
BOARD_ROOT="${BOARD_ROOT:-/userdata/RKClawServer}"

adb_sh()   { adb -s "$DEVICE" shell "$@"; }
adb_push() { adb -s "$DEVICE" push "$@"; }

# ---------- 1. 打包 ----------
echo "[dev] (1/5) 构建 gateway wheel"
"$ROOT_DIR/scripts/package.sh"

WHEEL="$(ls -t "$ROOT_DIR"/dist/rk_claw_server-*.whl | head -1)"
WHEEL_NAME="$(basename "$WHEEL")"

# ---------- 2. 推送 wheel ----------
echo "[dev] (2/5) 推送 $WHEEL_NAME"
adb_push "$WHEEL" "$BOARD_ROOT/packages/"

# ---------- 3. native library 已打入 wheel ----------
echo "[dev] (3/5) librkclaw_native.so 已包含在 gateway wheel"

# ---------- 4. 在已有 venv 中重装 ----------
echo "[dev] (4/5) 重装 gateway wheel"
adb_sh "$BOARD_ROOT/venv/bin/pip install --no-deps --force-reinstall \
  $BOARD_ROOT/packages/$WHEEL_NAME"

# ---------- 5. 前台启动提示 ----------
echo "[dev] (5/5) 更新完成，未启动后台服务"

echo "[dev] 完成。"
echo "  前台启动: adb -s $DEVICE shell 'cd $BOARD_ROOT && PYTHONUNBUFFERED=1 venv/bin/python -m gateway'"
