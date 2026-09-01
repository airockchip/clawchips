#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "build_native_sampling.sh is deprecated; building librkclaw_native.so" >&2
exec "${ROOT_DIR}/scripts/build_native.sh" "$@"
