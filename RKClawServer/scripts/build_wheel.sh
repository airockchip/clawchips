#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p dist
rm -rf build

if python -m build --version >/dev/null 2>&1; then
  python -m build --wheel --outdir dist
else
  python -m pip wheel . --wheel-dir dist --no-deps --no-build-isolation
fi
