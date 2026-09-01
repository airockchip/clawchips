#!/usr/bin/env bash
# Build a source-reduced Cython wheel in a target-architecture Docker container.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/arm64}"
DEFAULT_PYTHON_IMAGE="docker.m.daocloud.io/library/python:3.11-slim-bookworm"
PYTHON_IMAGE="${PYTHON_IMAGE:-$DEFAULT_PYTHON_IMAGE}"
CYTHON_SPEC="${CYTHON_SPEC:-Cython>=3.0,<4}"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/dist/cython}"
BUILD_NATIVE="${BUILD_NATIVE:-auto}"
STRIP_RELEASE="${STRIP_RELEASE:-1}"

case "$TARGET_PLATFORM" in
  linux/arm64)
    WHEEL_PLATFORM="linux_aarch64"
    DEFAULT_NATIVE_LIBRARY="$ROOT_DIR/dist/native-aarch64/lib/librkclaw_native.so"
    ;;
  linux/amd64)
    WHEEL_PLATFORM="linux_x86_64"
    DEFAULT_NATIVE_LIBRARY="$ROOT_DIR/dist/native/lib/librkclaw_native.so"
    ;;
  *)
    echo "[cython-release] unsupported TARGET_PLATFORM: $TARGET_PLATFORM" >&2
    echo "[cython-release] supported values: linux/arm64, linux/amd64" >&2
    exit 2
    ;;
esac

NATIVE_LIBRARY="${NATIVE_LIBRARY:-$DEFAULT_NATIVE_LIBRARY}"
case "$BUILD_NATIVE" in
  auto|0|1) ;;
  *)
    echo "[cython-release] BUILD_NATIVE must be auto, 0, or 1" >&2
    exit 2
    ;;
esac

if [[ "$BUILD_NATIVE" = "1" || ( "$BUILD_NATIVE" = "auto" && ! -f "$NATIVE_LIBRARY" ) ]]; then
  echo "[cython-release] building librkclaw_native.so for $TARGET_PLATFORM"
  if [[ "$TARGET_PLATFORM" = "linux/arm64" ]]; then
    NATIVE_BUILD_MODE=cross \
      BUILD_DIR="$ROOT_DIR/build/native-aarch64" \
      INSTALL_DIR="$ROOT_DIR/dist/native-aarch64" \
      "$ROOT_DIR/scripts/build_native.sh"
  else
    NATIVE_BUILD_MODE=native \
      BUILD_DIR="$ROOT_DIR/build/native" \
      INSTALL_DIR="$ROOT_DIR/dist/native" \
      "$ROOT_DIR/scripts/build_native.sh"
  fi
fi

if [[ ! -f "$NATIVE_LIBRARY" ]]; then
  echo "[cython-release] native library not found: $NATIVE_LIBRARY" >&2
  echo "[cython-release] set NATIVE_LIBRARY or use BUILD_NATIVE=1" >&2
  exit 1
fi

NATIVE_INFO="$(file -b "$NATIVE_LIBRARY")"
case "$TARGET_PLATFORM:$NATIVE_INFO" in
  linux/arm64:*aarch64*|linux/arm64:*ARM64*) ;;
  linux/amd64:*x86-64*|linux/amd64:*x86_64*) ;;
  *)
    echo "[cython-release] native library architecture mismatch" >&2
    echo "[cython-release] target: $TARGET_PLATFORM" >&2
    echo "[cython-release] file:   $NATIVE_INFO" >&2
    exit 1
    ;;
esac

command -v docker >/dev/null 2>&1 || {
  echo "[cython-release] docker is required" >&2
  exit 1
}
docker buildx version >/dev/null 2>&1 || {
  echo "[cython-release] docker buildx is required" >&2
  exit 1
}

BUILDER_ARGS=()
if [[ -n "${BUILDER:-}" ]]; then
  BUILDER_ARGS=(--builder "$BUILDER")
fi

BUILD_CONTEXT="$(mktemp -d "${TMPDIR:-/tmp}/rkclaw-cython-release.XXXXXX")"
cleanup() {
  if [[ -n "${BUILD_CONTEXT:-}" && -d "$BUILD_CONTEXT" ]]; then
    rm -rf -- "$BUILD_CONTEXT"
  fi
}
trap cleanup EXIT

cp "$ROOT_DIR/pyproject.toml" "$ROOT_DIR/setup.py" "$ROOT_DIR/README.md" "$BUILD_CONTEXT/"
cp -a "$ROOT_DIR/gateway" "$BUILD_CONTEXT/gateway"
mkdir -p "$BUILD_CONTEXT/gateway/_native"
install -m 0755 "$NATIVE_LIBRARY" "$BUILD_CONTEXT/gateway/_native/librkclaw_native.so"
cp "$ROOT_DIR/docker/Dockerfile.cython-release" "$BUILD_CONTEXT/Dockerfile"

mkdir -p "$OUT_DIR"
echo "[cython-release] target platform: $TARGET_PLATFORM"
echo "[cython-release] Python image:   $PYTHON_IMAGE"
echo "[cython-release] native library: $NATIVE_INFO"
echo "[cython-release] output:         $OUT_DIR"

docker buildx build \
  "${BUILDER_ARGS[@]}" \
  --platform "$TARGET_PLATFORM" \
  --build-arg "PYTHON_IMAGE=$PYTHON_IMAGE" \
  --build-arg "CYTHON_SPEC=$CYTHON_SPEC" \
  --build-arg "WHEEL_PLATFORM=$WHEEL_PLATFORM" \
  --build-arg "STRIP_RELEASE=$STRIP_RELEASE" \
  --target artifact \
  --output "type=local,dest=$OUT_DIR" \
  --progress "${BUILDX_PROGRESS:-plain}" \
  "$BUILD_CONTEXT"

WHEEL="$(find "$OUT_DIR" -maxdepth 1 -type f \
  -name "rk_claw_server-*-cp3*-cp3*-$WHEEL_PLATFORM.whl" \
  -printf '%T@ %p\n' | sort -nr | head -1 | cut -d' ' -f2-)"
if [[ -z "$WHEEL" || ! -f "$WHEEL" ]]; then
  echo "[cython-release] expected Cython wheel was not produced" >&2
  exit 1
fi

echo "[cython-release] built: $WHEEL"
sha256sum "$WHEEL"
