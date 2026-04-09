#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/userdata/skills/rk-iva/"
DEMO_DIR="${INSTALL_DIR}/rockx_rk3588_linux_aarch64/rockiva2_demo"
DATA_PATH="${INSTALL_DIR}/rockx_rk3588_linux_aarch64/data"
MODEL="object_detection_v3_cls8.data"

SRC_IMAGE_PATH=""
DST_IMAGE_PATH=""
CAM_DEVICE=""
CAP_TMP=""
CAPTURE_IS_RAW=0

W="${CAPTURE_WIDTH:-1920}"
H="${CAPTURE_HEIGHT:-1080}"
PIX_FMT=""

usage() {
  cat <<'EOF'
Usage:
  ./run.sh -i <input_image> -o <output_image>
  ./run.sh -o <output_image>

Options:
  -i <path>    Source image path (jpeg/png decode in demo; .yuv/.data/.bin need -f/-w/-h, see below)
  -o <path>    Output image path
  -c <id>      Camera: /dev/videoN or index N (e.g. 12 -> /dev/video12)
  -W <width>   Capture width  (default: $CAPTURE_WIDTH or 1920)
  -H <height>  Capture height (default: $CAPTURE_HEIGHT or 1080)
  -F <fmt>     Pixel format: NV12, MJPG, YUYV, etc. (default: auto-detect per camera type)
  -h           Show this help

Capture uses only v4l2-ctl: USB cameras prefer MJPG; rkisp_selfpath uses NV12.
When -F is given the specified format is used directly for all camera types.

For raw inputs (-i *.yuv / *.data / *.bin), iva_det_demo is called with -f nv12 and
-w/-h matching the capture dimensions (must match file layout).
EOF
}

cleanup() {
  if [[ -n "${CAP_TMP}" && -f "${CAP_TMP}" ]]; then
    rm -f "${CAP_TMP}"
  fi
}
trap cleanup EXIT

is_capture_device() {
  local dev="$1"
  local info=""

  if ! command -v v4l2-ctl >/dev/null 2>&1; then
    return 1
  fi

  info="$(v4l2-ctl -d "$dev" --all 2>/dev/null || true)"
  [[ "$info" == *"Video Capture"* || "$info" == *"Video Capture Multiplanar"* ]]
}

is_usb_camera() {
  local dev="$1"
  local sys_path=""

  sys_path="$(readlink -f "/sys/class/video4linux/${dev##*/}/device" 2>/dev/null || true)"
  [[ "$sys_path" == *"/usb"* ]]
}

is_rkisp_selfpath_camera() {
  local dev="$1"
  local info=""

  info="$(v4l2-ctl -d "$dev" -D 2>/dev/null || true)"
  [[ "$info" == *"rkisp_selfpath"* ]]
}

pick_usb_camera() {
  local dev

  shopt -s nullglob
  for dev in /dev/video*; do
    [[ -r "$dev" ]] || continue
    is_usb_camera "$dev" || continue
    if is_capture_device "$dev"; then
      shopt -u nullglob
      echo "$dev"
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

pick_rkisp_selfpath_camera() {
  local dev
  local info

  shopt -s nullglob
  for dev in /dev/video*; do
    [[ -r "$dev" ]] || continue
    is_rkisp_selfpath_camera "$dev" || continue
    if is_capture_device "$dev"; then
      shopt -u nullglob
      echo "$dev"
      return 0
    fi
  done
  shopt -u nullglob
  return 1
}

pick_default_camera() {
  local dev=""

  dev="$(pick_usb_camera || true)"
  if [[ -n "$dev" ]]; then
    echo "$dev"
    return 0
  fi

  dev="$(pick_rkisp_selfpath_camera || true)"
  if [[ -n "$dev" ]]; then
    echo "$dev"
    return 0
  fi

  echo "run.sh: no usable USB camera or rkisp_selfpath device found" >&2
  return 1
}

normalize_camera() {
  local c="$1"
  if [[ "$c" =~ ^/dev/video ]]; then
    echo "$c"
  elif [[ "$c" =~ ^[0-9]+$ ]]; then
    echo "/dev/video${c}"
  else
    echo "/dev/video${c}"
  fi
}

capture_nv12_v4l2() {
  local dev="$1"
  local out="$2"
  local w="$3"
  local h="$4"

  if ! [[ -r "$dev" ]]; then
    echo "run.sh: camera not readable: ${dev}" >&2
    return 1
  fi
  if ! command -v v4l2-ctl >/dev/null 2>&1; then
    echo "run.sh: v4l2-ctl not found (install v4l-utils)." >&2
    return 1
  fi

  v4l2-ctl -d "$dev" \
    --set-fmt-video=width="${w}",height="${h}",pixelformat=NV12 \
    --stream-mmap=4 \
    --stream-skip=5 \
    --stream-to="$out" \
    --stream-count=1 \
    --stream-poll
}

capture_mjpg_v4l2() {
  local dev="$1"
  local out="$2"
  local w="$3"
  local h="$4"

  if ! [[ -r "$dev" ]]; then
    echo "run.sh: camera not readable: ${dev}" >&2
    return 1
  fi
  if ! command -v v4l2-ctl >/dev/null 2>&1; then
    echo "run.sh: v4l2-ctl not found (install v4l-utils)." >&2
    return 1
  fi

  v4l2-ctl -d "$dev" \
    --set-fmt-video=width="${w}",height="${h}",pixelformat=MJPG \
    --stream-mmap=4 \
    --stream-to="$out" \
    --stream-count=1 \
    --stream-poll
}

capture_v4l2_fmt() {
  local dev="$1"
  local out="$2"
  local w="$3"
  local h="$4"
  local fmt="$5"

  if ! [[ -r "$dev" ]]; then
    echo "run.sh: camera not readable: ${dev}" >&2
    return 1
  fi
  if ! command -v v4l2-ctl >/dev/null 2>&1; then
    echo "run.sh: v4l2-ctl not found (install v4l-utils)." >&2
    return 1
  fi

  v4l2-ctl -d "$dev" \
    --set-fmt-video=width="${w}",height="${h}",pixelformat="${fmt}" \
    --stream-mmap=4 \
    --stream-to="$out" \
    --stream-count=1 \
    --stream-poll
}

fmt_is_raw() {
  local fmt="$1"
  case "${fmt^^}" in
    NV12|NV21|YUYV|UYVY|YU12|YV12) return 0 ;;
    *) return 1 ;;
  esac
}

capture_from_camera() {
  local dev="$1"

  if [[ -n "${PIX_FMT}" ]]; then
    if fmt_is_raw "${PIX_FMT}"; then
      CAP_TMP=$(mktemp "${TMPDIR:-/tmp}/iva_capXXXXXX.yuv")
      capture_v4l2_fmt "$dev" "$CAP_TMP" "$W" "$H" "${PIX_FMT}"
      CAPTURE_IS_RAW=1
    else
      CAP_TMP=$(mktemp "${TMPDIR:-/tmp}/iva_capXXXXXX.jpg")
      capture_v4l2_fmt "$dev" "$CAP_TMP" "$W" "$H" "${PIX_FMT}"
      CAPTURE_IS_RAW=0
    fi
    SRC_IMAGE_PATH="$CAP_TMP"
    return 0
  fi

  if is_usb_camera "$dev"; then
    CAP_TMP=$(mktemp "${TMPDIR:-/tmp}/iva_capXXXXXX.jpg")
    if capture_mjpg_v4l2 "$dev" "$CAP_TMP" "$W" "$H"; then
      CAPTURE_IS_RAW=0
      SRC_IMAGE_PATH="$CAP_TMP"
      return 0
    fi
    rm -f "$CAP_TMP"
    CAP_TMP=$(mktemp "${TMPDIR:-/tmp}/iva_capXXXXXX.yuv")
    capture_nv12_v4l2 "$dev" "$CAP_TMP" "$W" "$H"
    CAPTURE_IS_RAW=1
    SRC_IMAGE_PATH="$CAP_TMP"
    return 0
  fi

  CAP_TMP=$(mktemp "${TMPDIR:-/tmp}/iva_capXXXXXX.yuv")
  capture_nv12_v4l2 "$dev" "$CAP_TMP" "$W" "$H"
  CAPTURE_IS_RAW=1
  SRC_IMAGE_PATH="$CAP_TMP"
}

run_iva_det_demo() {
  local input="$1"
  local output="$2"
  local raw_flags="${3:-0}"

  cd "${DEMO_DIR}"
  if [[ "${raw_flags}" -eq 1 ]]; then
    ./iva_det_demo -d "${DATA_PATH}" -m "${MODEL}" -i "${input}" -o "${output}" -f nv12 -w "${W}" -h "${H}"
  else
    ./iva_det_demo -d "${DATA_PATH}" -m "${MODEL}" -i "${input}" -o "${output}"
  fi
}

needs_raw_nv12_args() {
  local p="$1"
  case "${p}" in
    *.yuv|*.YUV|*.data|*.bin) return 0 ;;
    *) return 1 ;;
  esac
}

while getopts "i:o:c:W:H:F:h" opt; do
  case "$opt" in
    i) SRC_IMAGE_PATH="$OPTARG" ;;
    o) DST_IMAGE_PATH="$OPTARG" ;;
    c) CAM_DEVICE="$OPTARG" ;;
    W) W="$OPTARG" ;;
    H) H="$OPTARG" ;;
    F) PIX_FMT="$OPTARG" ;;
    h)
      usage
      exit 0
      ;;
    *) usage >&2; exit 1 ;;
  esac
done
shift $((OPTIND - 1))
if [[ $# -gt 0 ]]; then
  echo "run.sh: unexpected arguments: $*" >&2
  usage >&2
  exit 1
fi

if [[ -z "${DST_IMAGE_PATH}" ]]; then
  echo "run.sh: -o <output_image> is required" >&2
  usage >&2
  exit 1
fi

if [[ -z "${SRC_IMAGE_PATH}" ]]; then
  # if [[ -z "${CAM_DEVICE}" ]]; then
    CAM_DEVICE="$(pick_default_camera)"
  # else
  #   CAM_DEVICE="$(normalize_camera "${CAM_DEVICE}")"
  # fi
  capture_from_camera "${CAM_DEVICE}"
fi

if [[ "${CAPTURE_IS_RAW}" -eq 1 ]] || needs_raw_nv12_args "${SRC_IMAGE_PATH}"; then
  run_iva_det_demo "${SRC_IMAGE_PATH}" "${DST_IMAGE_PATH}" 1
else
  run_iva_det_demo "${SRC_IMAGE_PATH}" "${DST_IMAGE_PATH}" 0
fi
