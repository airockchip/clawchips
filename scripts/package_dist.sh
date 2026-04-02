#!/usr/bin/env bash
# Assemble a minimal OpenClaw plugin bundle under dist/clawchips/:
#   - clawchips-plugin: tsc → dist/ (no src/, scripts/, tsconfig in zip)
#   - dashboard: vite build → dashboard/dist/ only (no dashboard/src, node_modules in zip)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PLUGIN_SRC_DIR="$ROOT_DIR/clawchips-plugin"
DASHBOARD_SRC_DIR="$PLUGIN_SRC_DIR/dashboard"
DASHBOARD_BUILD_DIR="$DASHBOARD_SRC_DIR/dist"
TARGET_DIR="$DIST_DIR/clawchips"

SKIP_PLUGIN_BUILD=0
SKIP_DASHBOARD_BUILD=0
SOURCE_MAP=0

usage() {
  cat <<'EOF'
Usage:
  bash scripts/package_dist.sh [options]

Options:
  --skip-plugin-build       Use existing clawchips-plugin/dist (do not run tsc).
  --skip-dashboard-build    Use existing clawchips-plugin/dashboard/dist (do not run vite).
  --source-map              Emit source maps for debugging (tsc: .js.map + inlineSources;
                            vite: build --sourcemap). Slightly larger bundle.
  -h, --help                Show this help.

Environment:
  CLAWCHIPS_FORCE_NPM_INSTALL=1   Always run npm ci / npm install (ignore cached node_modules).

Outputs:
  dist/clawchips/        unpacked bundle (runtime files only)
  dist/clawchips.zip
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-plugin-build) SKIP_PLUGIN_BUILD=1; shift ;;
    --skip-dashboard-build) SKIP_DASHBOARD_BUILD=1; shift ;;
    --source-map) SOURCE_MAP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if ! command -v zip >/dev/null 2>&1; then
  echo "Error: 'zip' is required but not installed." >&2
  exit 1
fi

force_npm_install() {
  [[ "${CLAWCHIPS_FORCE_NPM_INSTALL:-}" == "1" ]]
}

install_plugin_deps() {
  if [[ ! -f "$PLUGIN_SRC_DIR/package.json" ]]; then
    echo "Error: missing $PLUGIN_SRC_DIR/package.json" >&2
    exit 1
  fi
  if ! force_npm_install && [[ -x "$PLUGIN_SRC_DIR/node_modules/.bin/tsc" ]]; then
    echo "[clawchips-plugin] node_modules OK — skip npm install (set CLAWCHIPS_FORCE_NPM_INSTALL=1 to reinstall)."
    return 0
  fi
  echo "[clawchips-plugin] npm install..."
  (
    cd "$PLUGIN_SRC_DIR"
    if [[ -f package-lock.json ]]; then
      npm ci
    else
      npm install
    fi
  )
}

install_dashboard_deps() {
  if ! force_npm_install && [[ -x "$DASHBOARD_SRC_DIR/node_modules/.bin/vite" ]]; then
    echo "[dashboard] node_modules OK — skip npm install (set CLAWCHIPS_FORCE_NPM_INSTALL=1 to reinstall)."
    return 0
  fi
  echo "[dashboard] npm install..."
  (
    cd "$DASHBOARD_SRC_DIR"
    if [[ -f package-lock.json ]]; then
      npm ci
    else
      npm install
    fi
  )
}

build_plugin_ts() {
  if [[ "$SKIP_PLUGIN_BUILD" -eq 1 ]]; then
    echo "[clawchips-plugin] skip compile (--skip-plugin-build)."
    return 0
  fi
  install_plugin_deps
  if [[ "$SOURCE_MAP" -eq 1 ]]; then
    echo "[clawchips-plugin] tsc compile (with source maps)..."
    (
      cd "$PLUGIN_SRC_DIR"
      npx tsc -p tsconfig.json --sourceMap --inlineSources
    )
  else
    echo "[clawchips-plugin] tsc compile..."
    (cd "$PLUGIN_SRC_DIR" && npm run build)
  fi
}

build_dashboard() {
  if [[ "$SKIP_DASHBOARD_BUILD" -eq 1 ]]; then
    echo "[dashboard] skip build (--skip-dashboard-build)."
    return 0
  fi
  if [[ ! -f "$DASHBOARD_SRC_DIR/package.json" ]]; then
    echo "Warning: no dashboard — skipping dashboard build." >&2
    return 0
  fi
  install_dashboard_deps
  if [[ "$SOURCE_MAP" -eq 1 ]]; then
    echo "[dashboard] vite build (with source maps)..."
    (cd "$DASHBOARD_SRC_DIR" && npx vite build --sourcemap)
  else
    echo "[dashboard] vite build..."
    (cd "$DASHBOARD_SRC_DIR" && npm run build)
  fi
}

require_built_plugin() {
  local entry="$PLUGIN_SRC_DIR/dist/index.js"
  if [[ ! -f "$entry" ]]; then
    echo "Error: missing $entry — run: (cd clawchips-plugin && npm run build)" >&2
    exit 1
  fi
}

require_built_dashboard() {
  local index="$DASHBOARD_BUILD_DIR/index.html"
  if [[ ! -f "$index" ]]; then
    echo "Error: missing $index — run: (cd clawchips-plugin/dashboard && npm run build)" >&2
    exit 1
  fi
}

# --- assemble: only runtime paths (mirrors plugin loading: dist/index.js + dashboard/dist) ---
assemble_bundle() {
  rm -rf "$DIST_DIR"
  mkdir -p "$TARGET_DIR"

  cp -a "$PLUGIN_SRC_DIR/dist" "$TARGET_DIR/dist"
  mkdir -p "$TARGET_DIR/dashboard"
  cp -a "$DASHBOARD_BUILD_DIR" "$TARGET_DIR/dashboard/dist"

  cp -a "$PLUGIN_SRC_DIR/package.json" "$TARGET_DIR/package.json"
  cp -a "$PLUGIN_SRC_DIR/openclaw.plugin.json" "$TARGET_DIR/openclaw.plugin.json"
  cp -a "$PLUGIN_SRC_DIR/openclaw.security.json" "$TARGET_DIR/openclaw.security.json"
  cp -a "$PLUGIN_SRC_DIR/README.md" "$TARGET_DIR/README.md"
  cp -a "$PLUGIN_SRC_DIR/clawchips_default.yaml" "$TARGET_DIR/clawchips_default.yaml"
  mkdir -p "$TARGET_DIR/scripts"
  cp -a "$PLUGIN_SRC_DIR/scripts/setup.mjs" "$TARGET_DIR/scripts/setup.mjs"

  find "$TARGET_DIR" -depth -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
  find "$TARGET_DIR" -type f -name ".DS_Store" -delete 2>/dev/null || true

  if [[ "$SOURCE_MAP" -eq 1 ]]; then
    cat > "$TARGET_DIR/BUNDLE_CONTENTS.txt" <<'EOF'
ClawChips OpenClaw plugin bundle (minimal runtime layout)

- dist/                 — tsc output + *.js.map (inlineSources) for TS debugging
- dashboard/dist/       — vite output + source maps for browser debugging
- package.json, openclaw.*.json, README.md, clawchips_default.yaml, scripts/setup.mjs
EOF
  else
    cat > "$TARGET_DIR/BUNDLE_CONTENTS.txt" <<'EOF'
ClawChips OpenClaw plugin bundle (minimal runtime layout)

- dist/                 — tsc output (entry: dist/index.js); excludes src/, scripts/, tsconfig
- dashboard/dist/       — vite output only; excludes dashboard/src, node_modules, config
- package.json, openclaw.*.json, README.md, clawchips_default.yaml, scripts/setup.mjs
EOF
  fi
}

zip_bundle() {
  (
    cd "$DIST_DIR"
    zip -qr "clawchips.zip" "clawchips"
  )
}

# --- main ---
if [[ "$SOURCE_MAP" -eq 1 ]]; then
  echo "[pack] Source maps enabled (--source-map)."
  if [[ "$SKIP_PLUGIN_BUILD" -eq 1 ]] || [[ "$SKIP_DASHBOARD_BUILD" -eq 1 ]]; then
    echo "[pack] Note: skipped build steps may leave old artifacts without maps; rebuild without --skip-* to refresh." >&2
  fi
fi

build_plugin_ts
build_dashboard
require_built_plugin
require_built_dashboard
assemble_bundle
zip_bundle

echo ""
echo "Built distribution artifacts:"
echo "  - $TARGET_DIR"
echo "  - $DIST_DIR/clawchips.zip"
if [[ "$SOURCE_MAP" -eq 1 ]]; then
  echo "  (debug: attach debugger with source maps under dist/ and dashboard/dist/)"
fi
