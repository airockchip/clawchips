#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROJECT_VERSION="$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
VERSION="${VERSION:-$PROJECT_VERSION}"
OUT_DIR="${OUT_DIR:-dist}"
PACKAGE_NAME="${PACKAGE_NAME:-rk-claw-server-board-installer-${VERSION}}"
STAGING="${OUT_DIR}/${PACKAGE_NAME}"
BOARD_ROOT="${BOARD_ROOT:-/userdata/RKClawServer}"
SERVICE_NAME="${SERVICE_NAME:-rkclaw-server.service}"
TOOLKIT_LITE_WHEEL="${TOOLKIT_LITE_WHEEL:-}"
DEVICE="${DEVICE:-}"

mkdir -p "$OUT_DIR"

GATEWAY_WHEEL="$(ls -t "$OUT_DIR"/rk_claw_server-*-linux_aarch64.whl 2>/dev/null | head -1 || true)"
if [ -z "$GATEWAY_WHEEL" ]; then
  OUT_DIR="$OUT_DIR" ./scripts/package.sh
  GATEWAY_WHEEL="$(ls -t "$OUT_DIR"/rk_claw_server-*-linux_aarch64.whl | head -1)"
fi

if [ -z "$TOOLKIT_LITE_WHEEL" ]; then
  if [ -n "$DEVICE" ]; then
    TOOLKIT_LITE_WHEEL="/tmp/rknn3_toolkit_lite-1.0.5b2-cp311-cp311-linux_aarch64.whl"
    adb -s "$DEVICE" pull "$BOARD_ROOT/rknn3_toolkit_lite-1.0.5b2-cp311-cp311-linux_aarch64.whl" "$TOOLKIT_LITE_WHEEL"
  else
    echo "TOOLKIT_LITE_WHEEL is required when DEVICE is not set" >&2
    exit 1
  fi
fi

rm -rf "$STAGING"
mkdir -p "$STAGING/packages" "$STAGING/systemd"

cp "$GATEWAY_WHEEL" "$STAGING/packages/"
cp "$TOOLKIT_LITE_WHEEL" "$STAGING/packages/$(basename "$TOOLKIT_LITE_WHEEL")"
cp gateway.toml "$STAGING/gateway.toml"

cat > "$STAGING/systemd/$SERVICE_NAME" <<EOF_SERVICE
[Unit]
Description=RKClawServer OpenAI-compatible inference service
After=network-online.target rknn3.service
Wants=network-online.target
Requires=rknn3.service

[Service]
Type=simple
User=root
WorkingDirectory=$BOARD_ROOT
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -m gateway
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF_SERVICE

cat > "$STAGING/install.sh" <<'EOF_INSTALL'
#!/usr/bin/env bash
set -euo pipefail

BOARD_ROOT="${BOARD_ROOT:-/userdata/RKClawServer}"
SERVICE_NAME="${SERVICE_NAME:-rkclaw-server.service}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root." >&2
  exit 1
fi

echo "[install] target: ${BOARD_ROOT}"
mkdir -p "$BOARD_ROOT/packages" "$BOARD_ROOT/logs"

echo "[install] stopping old services"
for service in rkllm-server.service rkllm-server-claw.service claw-server.service claw-llm-gateway.service; do
  if systemctl list-unit-files "$service" >/dev/null 2>&1 || systemctl list-units --all "$service" >/dev/null 2>&1; then
    systemctl disable --now "$service" >/dev/null 2>&1 || true
  fi
done

echo "[install] stopping manual gateway processes"
for pid in $(pgrep -f "python3 -m gateway" || true); do
  if [ "$pid" != "$$" ]; then
    kill "$pid" >/dev/null 2>&1 || true
  fi
done
sleep 1

echo "[install] copying files"
cp "$SCRIPT_DIR"/packages/*.whl "$BOARD_ROOT/packages/"
cp "$SCRIPT_DIR/gateway.toml" "$BOARD_ROOT/gateway.toml"
chmod 0644 "$BOARD_ROOT/gateway.toml"

echo "[install] installing python wheels"
TOOLKIT_WHEEL="$(ls -t "$BOARD_ROOT"/packages/rknn3_toolkit_lite-*.whl 2>/dev/null | head -1)"
GATEWAY_WHEEL="$(ls -t "$BOARD_ROOT"/packages/rk_claw_server-*.whl | head -1)"
python3 -m pip install --break-system-packages --no-deps --force-reinstall "$TOOLKIT_WHEEL"
python3 -m pip install --break-system-packages --no-deps --force-reinstall "$GATEWAY_WHEEL"

echo "[install] installing service: ${SERVICE_NAME}"
cp "$SCRIPT_DIR/systemd/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "[install] checking services"
systemctl is-active rknn3.service
systemctl is-active "$SERVICE_NAME"
echo "[install] waiting for readyz"
for _ in $(seq 1 90); do
  if curl -fsS --max-time 2 http://127.0.0.1:8081/readyz; then
    break
  fi
  sleep 1
done
echo
echo "[install] done"
echo "logs: journalctl -u ${SERVICE_NAME} -f"
EOF_INSTALL
chmod +x "$STAGING/install.sh"

cat > "$STAGING/README.md" <<EOF_README
# RKClawServer Board Installer

Install on board:

\`\`\`bash
tar -xzf ${PACKAGE_NAME}.tar.gz
cd ${PACKAGE_NAME}
./install.sh
\`\`\`

The installer disables and stops:

- \`rkllm-server.service\`
- \`rkllm-server-claw.service\`
- \`claw-server.service\`

It installs and enables:

- \`${SERVICE_NAME}\`

Default install root: \`${BOARD_ROOT}\`
Native library: bundled in the gateway wheel
EOF_README

find "$STAGING" -type d -exec chmod 0755 {} \;
find "$STAGING" -type f -exec chmod 0644 {} \;
chmod 0755 "$STAGING/install.sh"

tar -C "$OUT_DIR" -czf "$OUT_DIR/${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"
chmod 0644 "$OUT_DIR/${PACKAGE_NAME}.tar.gz"
echo "[installer] built: $OUT_DIR/${PACKAGE_NAME}.tar.gz"
