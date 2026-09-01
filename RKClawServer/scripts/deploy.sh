#!/usr/bin/env bash
# 通过 adb 把 gateway 部署到 RK1820/RK1828 开发板。
# 对应 README 第 3 ~ 8 节。
#
# 用法 (环境变量配置):
#   export DEVICE=your-adb-serial
#   export TOOLKIT_LITE_WHEEL=/path/to/rknn3_toolkit_lite-1.0.5b2-cp311-cp311-linux_aarch64.whl
#   export MODEL_DIR=/path/to/AgentModel-V3.1-4B-RKNN   # 可选
#   ./scripts/deploy.sh
#
# 或一次性前置:
#   DEVICE=... TOOLKIT_LITE_WHEEL=... ./scripts/deploy.sh
#
# 可选环境变量:
#   DEVICE              adb 设备序列号 (必填)
#   TOOLKIT_LITE_WHEEL  主机上 rknn3 toolkit lite wheel 路径 (必填)
#   MODEL_DIR           主机上模型目录, 不传则跳过模型推送 (可选)
#   BOARD_ROOT          板端部署根目录, 默认 /userdata/RKClawServer
#   BOARD_MODEL_DIR     板端模型目录, 默认 /userdata/AgentModel-V3.1-4B-RKNN
#   SKIP_MODEL          =1 强制跳过模型推送
#   ENABLE_SERVICE      =1 安装并启用 systemd 服务 (默认关闭，使用前台启动)
#   RESTART_SERVICE     =1 重启已存在的 systemd 服务

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# ---------- 参数检查 ----------
: "${DEVICE:?需要设置 DEVICE=adb 设备序列号}"
: "${TOOLKIT_LITE_WHEEL:?需要设置 TOOLKIT_LITE_WHEEL=主机上 rknn3 toolkit lite wheel 路径}"
[ -f "$TOOLKIT_LITE_WHEEL" ] || { echo "[deploy] 找不到 toolkit lite wheel: $TOOLKIT_LITE_WHEEL" >&2; exit 1; }

BOARD_ROOT="${BOARD_ROOT:-/userdata/RKClawServer}"
BOARD_MODEL_DIR="${BOARD_MODEL_DIR:-/userdata/AgentModel-V3.1-4B-RKNN}"
SKIP_MODEL="${SKIP_MODEL:-0}"
ENABLE_SERVICE="${ENABLE_SERVICE:-0}"
RESTART_SERVICE="${RESTART_SERVICE:-0}"

WHEEL="$(ls -t "$ROOT_DIR"/dist/rk_claw_server-*.whl 2>/dev/null | head -1 || true)"
if [ -z "$WHEEL" ]; then
  echo "[deploy] 本地未找到 gateway wheel, 先执行 ./scripts/package.sh" >&2
  exit 1
fi
python3 -c 'import sys, zipfile; name="gateway/_native/librkclaw_native.so"; archive=zipfile.ZipFile(sys.argv[1]); assert name in archive.namelist(), f"{name} missing from {sys.argv[1]}; rebuild with scripts/package.sh"' "$WHEEL"

echo "[deploy] DEVICE            = $DEVICE"
echo "[deploy] BOARD_ROOT        = $BOARD_ROOT"
echo "[deploy] BOARD_MODEL_DIR   = $BOARD_MODEL_DIR"
echo "[deploy] gateway wheel     = $WHEEL"
echo "[deploy] toolkit lite wheel= $TOOLKIT_LITE_WHEEL"

# ---------- helper ----------
adb_sh() { adb -s "$DEVICE" shell "$@"; }
adb_push() { adb -s "$DEVICE" push "$@"; }

# ---------- 1. 创建板端目录 ----------
echo "[deploy] (1/7) 创建板端目录"
adb_sh "mkdir -p \
  $BOARD_ROOT/packages \
  $BOARD_ROOT/logs"

# ---------- 2. 上传 wheel / toolkit lite / 配置 ----------
echo "[deploy] (2/7) 上传 python 包与配置"
adb_push "$WHEEL" "$BOARD_ROOT/packages/"
adb_push "$TOOLKIT_LITE_WHEEL" "$BOARD_ROOT/packages/"
adb_push "$ROOT_DIR/gateway.toml" "$BOARD_ROOT/gateway.toml"

# ---------- 3. 上传模型 ----------
if [ "$SKIP_MODEL" = "1" ]; then
  echo "[deploy] (3/7) 跳过模型推送"
elif [ -n "${MODEL_DIR:-}" ] && [ -d "$MODEL_DIR" ]; then
  echo "[deploy] (3/7) 上传模型 $MODEL_DIR -> $BOARD_MODEL_DIR"
  adb_sh "rm -rf $BOARD_MODEL_DIR && mkdir -p $BOARD_MODEL_DIR"
  adb_push "$MODEL_DIR/." "$BOARD_MODEL_DIR/"
else
  echo "[deploy] (3/7) 未设置 MODEL_DIR, 跳过模型推送"
fi

# ---------- 4. native library 已打入 gateway wheel ----------
echo "[deploy] (4/7) librkclaw_native.so 已包含在 gateway wheel"

# ---------- 5. 创建 venv 并安装 ----------
echo "[deploy] (5/7) 创建 venv 并安装 wheel"
TOOLKIT_WHEEL_NAME="$(basename "$TOOLKIT_LITE_WHEEL")"
GATEWAY_WHEEL_NAME="$(basename "$WHEEL")"
adb_sh "
  python3 -m venv --system-site-packages $BOARD_ROOT/venv &&
  $BOARD_ROOT/venv/bin/pip install --no-deps \
    $BOARD_ROOT/packages/$TOOLKIT_WHEEL_NAME &&
  $BOARD_ROOT/venv/bin/pip install --no-deps --force-reinstall \
    $BOARD_ROOT/packages/$GATEWAY_WHEEL_NAME
"
adb_sh "
  $BOARD_ROOT/venv/bin/python -c 'import gateway, rknn3lite; print(\"imports ok\")'
"

# ---------- 6. 确认 rknn3.service ----------
echo "[deploy] (6/7) 检查 rknn3.service"
adb_sh "systemctl is-active rknn3.service || echo 'WARN: rknn3.service not active'"

# ---------- 7. systemd 服务 ----------
if [ "$ENABLE_SERVICE" = "1" ] && [ -f "$ROOT_DIR/rkclaw-server.service" ]; then
  echo "[deploy] (7/7) 安装 systemd 服务"
  adb_push "$ROOT_DIR/rkclaw-server.service" /etc/systemd/system/
  adb_sh "systemctl daemon-reload"
  if adb_sh "systemctl is-enabled rkclaw-server.service >/dev/null 2>&1"; then
    if [ "$RESTART_SERVICE" = "1" ]; then
      adb_sh "systemctl restart rkclaw-server.service"
    fi
  else
    adb_sh "systemctl enable --now rkclaw-server.service"
  fi
  adb_sh "systemctl status rkclaw-server.service --no-pager" || true
else
  echo "[deploy] (7/7) 跳过 systemd 服务安装 (前台启动: cd $BOARD_ROOT && venv/bin/python -m gateway)"
fi

echo "[deploy] 完成。"
echo "  日志:    adb -s $DEVICE shell 'journalctl -u rkclaw-server.service -f'"
echo "  健康检查: adb -s $DEVICE shell 'curl -sS http://127.0.0.1:8081/readyz'"
