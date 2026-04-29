#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DETECT_PY="${RK_VL_DETECT_PY:-${SCRIPT_DIR}/detect_target.py}"
PYTHON_BIN="${RK_VL_PYTHON_BIN:-python3}"
STATE_DIR="${RK_VL_STATE_DIR:-${HOME}/.openclaw/rk-vl-monitor}"
PID_FILE="${STATE_DIR}/monitor.pid"
CFG_FILE="${STATE_DIR}/config.env"
LOG_FILE="${STATE_DIR}/current.log"
CAPTURE_DIR="${STATE_DIR}/captures"
OPENCLAW_BIN="${RK_VL_OPENCLAW_BIN:-openclaw}"
KNOWN_USERS_JSON_DEFAULT="${HOME}/.openclaw/qqbot/data/known-users.json"
KNOWN_USERS_JSON="${RK_VL_KNOWN_USERS_JSON:-${KNOWN_USERS_JSON_DEFAULT}}"
DEFAULT_POLL_INTERVAL_SEC="${RK_VL_POLL_INTERVAL_SEC:-2}"
DEFAULT_HIT_CONFIRMATIONS="${RK_VL_HIT_CONFIRMATIONS:-1}"
DEFAULT_MISS_CLEAR_COUNT="${RK_VL_MISS_CLEAR_COUNT:-2}"
DEFAULT_REMIND_COOLDOWN_SEC="${RK_VL_REMIND_COOLDOWN_SEC:-120}"
DEFAULT_REMIND_MAX_SILENCE_SEC="${RK_VL_REMIND_MAX_SILENCE_SEC:-600}"
DEFAULT_CAPTURE_WIDTH="${RK_VL_CAPTURE_WIDTH:-1280}"
DEFAULT_CAPTURE_HEIGHT="${RK_VL_CAPTURE_HEIGHT:-720}"
DEFAULT_MIN_SCORE="${RK_VL_MIN_SCORE:-0.96}"

mkdir -p "${STATE_DIR}" "${CAPTURE_DIR}"

log() {
  printf '[rk-vl-watch] %s\n' "$*" >> "${LOG_FILE}"
}

usage() {
  cat <<'EOF'
Usage:
  ./watch.sh start <query...>
  ./watch.sh stop
  ./watch.sh status

Examples:
  ./watch.sh start 包裹
  ./watch.sh start 门口的快递盒
EOF
}

pid_is_alive() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

read_pid() {
  [[ -f "${PID_FILE}" ]] || return 1
  tr -d ' \n\r\t' < "${PID_FILE}"
}

cleanup_pid_if_stale() {
  local pid=""
  pid="$(read_pid || true)"
  if [[ -n "${pid}" ]] && ! pid_is_alive "${pid}"; then
    rm -f "${PID_FILE}"
  fi
}

stop_existing_if_any() {
  local pid=""
  pid="$(read_pid || true)"
  if [[ -n "${pid}" ]] && pid_is_alive "${pid}"; then
    kill "${pid}" 2>/dev/null || true
    for _ in 1 2 3 4 5; do
      if ! pid_is_alive "${pid}"; then
        break
      fi
      sleep 1
    done
    if pid_is_alive "${pid}"; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${PID_FILE}"
}

pick_latest_target() {
  "${PYTHON_BIN}" - "${KNOWN_USERS_JSON}" <<'PY'
import json
import os
import sys

path = sys.argv[1]
if not os.path.exists(path):
    raise SystemExit(f"known-users.json not found: {path}")
with open(path, "r", encoding="utf-8") as f:
    rows = json.load(f)
rows = [r for r in rows if isinstance(r, dict) and str(r.get("openid", "")).strip()]
if not rows:
    raise SystemExit("no valid qqbot target")
rows.sort(key=lambda r: int(r.get("lastSeenAt", 0) or 0), reverse=True)
row = rows[0]
kind = "group" if row.get("type") == "group" else "c2c"
print(f"qqbot:{kind}:{row['openid'].strip()}")
PY
}

write_config() {
  local query="$1"
  {
    printf 'QUERY=%q\n' "${query}"
    printf 'POLL_INTERVAL_SEC=%q\n' "${DEFAULT_POLL_INTERVAL_SEC}"
    printf 'HIT_CONFIRMATIONS=%q\n' "${DEFAULT_HIT_CONFIRMATIONS}"
    printf 'MISS_CLEAR_COUNT=%q\n' "${DEFAULT_MISS_CLEAR_COUNT}"
    printf 'REMIND_COOLDOWN_SEC=%q\n' "${DEFAULT_REMIND_COOLDOWN_SEC}"
    printf 'REMIND_MAX_SILENCE_SEC=%q\n' "${DEFAULT_REMIND_MAX_SILENCE_SEC}"
    printf 'CAPTURE_WIDTH=%q\n' "${DEFAULT_CAPTURE_WIDTH}"
    printf 'CAPTURE_HEIGHT=%q\n' "${DEFAULT_CAPTURE_HEIGHT}"
    printf 'MIN_SCORE=%q\n' "${DEFAULT_MIN_SCORE}"
    printf 'CAMERA_DEVICE=%q\n' "${RK_VL_CAMERA_DEVICE:-}"
    printf 'CAMERA_INDEX=%q\n' "${RK_VL_CAMERA_INDEX:-}"
  } > "${CFG_FILE}"
}

require_dependencies() {
  [[ -f "${DETECT_PY}" ]] || {
    echo "watch.sh: detect_target.py not found: ${DETECT_PY}" >&2
    return 1
  }
  command -v "${PYTHON_BIN}" >/dev/null 2>&1 || {
    echo "watch.sh: python not found: ${PYTHON_BIN}" >&2
    return 1
  }
  command -v "${OPENCLAW_BIN}" >/dev/null 2>&1 || {
    echo "watch.sh: openclaw command not found: ${OPENCLAW_BIN}" >&2
    return 1
  }
  pick_latest_target >/dev/null
}

# Fills global WATCH_EXEC (array) with python + detect_target.py + watch flags.
# Must be called after write_config / with CFG_FILE present.
load_watch_exec_array() {
  [[ -f "${CFG_FILE}" ]] || {
    echo "watch.sh: config not found: ${CFG_FILE}" >&2
    return 1
  }
  # shellcheck disable=SC1090
  source "${CFG_FILE}"

  WATCH_EXEC=(
    "${PYTHON_BIN}"
    "${DETECT_PY}"
    "--watch"
    "--query" "${QUERY}"
    "--capture-dir" "${CAPTURE_DIR}"
    "--state-file" "${STATE_DIR}/state.json"
    "--openclaw-bin" "${OPENCLAW_BIN}"
    "--known-users-json" "${KNOWN_USERS_JSON}"
    "--poll-interval" "${POLL_INTERVAL_SEC}"
    "--hit-confirmations" "${HIT_CONFIRMATIONS}"
    "--miss-clear-count" "${MISS_CLEAR_COUNT}"
    "--remind-cooldown-sec" "${REMIND_COOLDOWN_SEC}"
    "--remind-max-silence-sec" "${REMIND_MAX_SILENCE_SEC}"
    "--capture-width" "${CAPTURE_WIDTH}"
    "--capture-height" "${CAPTURE_HEIGHT}"
    "--min-score" "${MIN_SCORE}"
  )

  if [[ -n "${CAMERA_DEVICE:-}" ]]; then
    WATCH_EXEC+=("--camera-device" "${CAMERA_DEVICE}")
  fi
  if [[ -n "${CAMERA_INDEX:-}" ]]; then
    WATCH_EXEC+=("--camera-index" "${CAMERA_INDEX}")
  fi
}

build_watch_command() {
  load_watch_exec_array || return 1
  printf '%q ' "${WATCH_EXEC[@]}"
  echo
}

do_status() {
  cleanup_pid_if_stale
  local pid=""
  pid="$(read_pid || true)"
  if [[ -n "${pid}" ]] && pid_is_alive "${pid}"; then
    echo "running pid=${pid}"
    [[ -f "${CFG_FILE}" ]] && sed -n '1,6p' "${CFG_FILE}"
  else
    echo "stopped"
  fi
}

do_stop() {
  cleanup_pid_if_stale
  local pid=""
  pid="$(read_pid || true)"
  if [[ -z "${pid}" ]]; then
    echo "当前没有运行中的摄像头监控"
    return 0
  fi
  stop_existing_if_any
  echo "已停止摄像头监控"
}

do_start() {
  local query="${*:-}"
  if [[ -z "${query// }" ]]; then
    echo "watch.sh: start requires a non-empty query" >&2
    return 1
  fi
  require_dependencies
  stop_existing_if_any
  # Absolute path + PATH hints so nohup'd Python can exec openclaw and Node shebangs find `node`.
  local _ocw="${OPENCLAW_BIN}"
  if [[ "${_ocw}" != /* ]]; then
    _ocw="$(command -v "${_ocw}")"
  fi
  OPENCLAW_BIN="${_ocw}"
  export PATH="$(dirname "${OPENCLAW_BIN}"):${PATH}"
  if command -v node >/dev/null 2>&1; then
    export PATH="$(dirname "$(command -v node)"):${PATH}"
  fi
  write_config "${query}"
  load_watch_exec_array || return 1
  # Record the Python PID, not a wrapper shell. Otherwise `stop` kills bash and
  # leaves the interpreter running (orphaned), so logs keep growing.
  nohup "${WATCH_EXEC[@]}" >> "${LOG_FILE}" 2>&1 &
  echo $! > "${PID_FILE}"
  echo "已启动摄像头监控: ${query}"
}

main() {
  local cmd="${1:-}"
  shift || true
  case "${cmd}" in
    start) do_start "$@" ;;
    stop) do_stop ;;
    status) do_status ;;
    *)
      usage >&2
      exit 1
      ;;
  esac
}

if [[ "${WATCH_SH_SOURCE_ONLY:-0}" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

main "$@"
