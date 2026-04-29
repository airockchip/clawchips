#!/bin/bash

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 [--play] <text>"
    echo "Example: $0 \"text to tts\""
    echo "Example: $0 --play \"text to tts and play locally\""
    exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
DEMO_DIR=${RK_TTS_DEMO_DIR:-/userdata/model_hub/rk-tts/rocktts_linux_aarch64_rk3588}
SERVER_PORT=${RK_TTS_SERVER_PORT:-8000}
SERVER_URL=${RK_TTS_SERVER_URL:-http://127.0.0.1:${SERVER_PORT}}
OUTPUT_PATH=${RK_TTS_OUTPUT_PATH:-/userdata/output.wav}
TMP_OUTPUT=/tmp/rk_tts_output_$$.wav
PLAY_DEVICE=${RK_TTS_PLAY_DEVICE:-plughw:CARD=rockchipes8388,DEV=0}
START_SERVER_SCRIPT=${RK_TTS_START_SERVER_SCRIPT:-${DEMO_DIR}/start_tts_server.sh}
STOP_SERVER_SCRIPT=${RK_TTS_STOP_SERVER_SCRIPT:-${DEMO_DIR}/stop_tts_server.sh}
PLAY_MODE=0

if [ "$1" = "--play" ]; then
    PLAY_MODE=1
    shift
fi

if [ $# -eq 0 ]; then
    echo "Usage: $0 [--play] <text>"
    exit 1
fi

cleanup() {
    rm -f "$TMP_OUTPUT"
}
trap cleanup EXIT

TEXT=$(echo "$1" | sed -e 's/\\//g' -e 's/"//g')

play_fallback_demo() {
    if [ -x ./demo/rocktts_demo/tts_play ]; then
        echo "[WARN] fallback to direct tts_play." >&2
        sudo -E sh -c 'export LD_LIBRARY_PATH=./lib/:${LD_LIBRARY_PATH:-} && ./demo/rocktts_demo/tts_play "$0"' "$TEXT"
    else
        echo "[WARN] fallback to direct rocktts_demo." >&2
        sudo -E sh -c 'export LD_LIBRARY_PATH=./lib/:${LD_LIBRARY_PATH:-} && ./demo/rocktts_demo/rocktts_demo "$0"' "$TEXT"
    fi
}

cd "$DEMO_DIR"
if [ "$PLAY_MODE" -eq 1 ]; then
    rm -f "$TMP_OUTPUT"
else
    sudo rm -f "$OUTPUT_PATH" "$TMP_OUTPUT"
fi

if [ "$PLAY_MODE" -eq 1 ]; then
    if python3 "$SCRIPT_DIR/tts_client.py" --server-url "$SERVER_URL" --text "$TEXT" --play --device "$PLAY_DEVICE"; then
        :
    else
        echo "[WARN] tts_server play failed." >&2
        play_fallback_demo
    fi
else
    if python3 "$SCRIPT_DIR/tts_client.py" --server-url "$SERVER_URL" --text "$TEXT" --output "$TMP_OUTPUT"; then
        sudo mv "$TMP_OUTPUT" "$OUTPUT_PATH"
    else
        echo "[WARN] tts_server request failed, fallback to direct rocktts_demo." >&2
        sudo -E sh -c 'export LD_LIBRARY_PATH=./lib/:${LD_LIBRARY_PATH:-} && ./demo/rocktts_demo/rocktts_demo "$0"' "$TEXT"
    fi
fi

if [ "$PLAY_MODE" -eq 0 ]; then
    echo "$OUTPUT_PATH"
fi
