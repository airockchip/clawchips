#!/usr/bin/env bash
# 在 PC 上交叉编译 native library，并构建包含该库的 gateway platform wheel。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OUT_DIR="${OUT_DIR:-dist}"
BUILD_NATIVE="${BUILD_NATIVE:-1}"
NATIVE_BUILD_DIR="${NATIVE_BUILD_DIR:-$ROOT_DIR/build/native-aarch64}"
NATIVE_INSTALL_DIR="${NATIVE_INSTALL_DIR:-$ROOT_DIR/dist/native-aarch64}"
PACKAGED_NATIVE_DIR="$ROOT_DIR/gateway/_native"
PACKAGED_NATIVE_LIB="$PACKAGED_NATIVE_DIR/librkclaw_native.so"

mkdir -p "$OUT_DIR"

if [ "$BUILD_NATIVE" = "1" ]; then
  echo "[package] cross compiling librkclaw_native.so"
  BUILD_DIR="$NATIVE_BUILD_DIR" \
  INSTALL_DIR="$NATIVE_INSTALL_DIR" \
    "$ROOT_DIR/scripts/build_native.sh"
  NATIVE_LIBRARY="$NATIVE_INSTALL_DIR/lib/librkclaw_native.so"
else
  NATIVE_LIBRARY="${NATIVE_LIBRARY:-$NATIVE_INSTALL_DIR/lib/librkclaw_native.so}"
  echo "[package] using prebuilt native library: $NATIVE_LIBRARY"
fi

[ -f "$NATIVE_LIBRARY" ] || {
  echo "[package] native library not found: $NATIVE_LIBRARY" >&2
  exit 1
}

NATIVE_FILE_INFO="$(file -b "$NATIVE_LIBRARY")"
if [ -z "${WHEEL_PLATFORM:-}" ]; then
  case "$NATIVE_FILE_INFO" in
    *aarch64*|*ARM64*) WHEEL_PLATFORM="linux_aarch64" ;;
    *x86-64*|*x86_64*) WHEEL_PLATFORM="linux_x86_64" ;;
    *)
      echo "[package] cannot infer wheel platform from: $NATIVE_FILE_INFO" >&2
      echo "[package] set WHEEL_PLATFORM explicitly (for example linux_aarch64)" >&2
      exit 1
      ;;
  esac
fi

mkdir -p "$PACKAGED_NATIVE_DIR"
install -m 0755 "$NATIVE_LIBRARY" "$PACKAGED_NATIVE_LIB"
cleanup() {
  rm -f "$PACKAGED_NATIVE_LIB"
}
trap cleanup EXIT

echo "[package] native library: $NATIVE_FILE_INFO"
echo "[package] wheel platform: $WHEEL_PLATFORM"

# 优先用 python -m build，缺则回退到 pip wheel --no-build-isolation
if python3 -m build --version >/dev/null 2>&1; then
  RKCLAW_WHEEL_PLATFORM="$WHEEL_PLATFORM" \
    python3 -m build --wheel --outdir "$OUT_DIR"
else
  RKCLAW_WHEEL_PLATFORM="$WHEEL_PLATFORM" \
    python3 -m pip wheel . --no-deps --no-build-isolation --wheel-dir "$OUT_DIR"
fi

# 找出刚刚生成的 wheel，方便后续 deploy 脚本引用
WHEEL="$(ls -t "$OUT_DIR"/rk_claw_server-*.whl | head -1)"
python3 -c 'import sys, zipfile; name="gateway/_native/librkclaw_native.so"; archive=zipfile.ZipFile(sys.argv[1]); assert name in archive.namelist(), f"{name} missing from {sys.argv[1]}"' "$WHEEL"
echo "[package] built: $WHEEL"
