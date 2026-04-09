#!/usr/bin/env bash
set -euo pipefail

PREFIX="https://ftrg.zbox.filez.com/v2/delivery/data/95f00b0fc900458ba134f8b180b3f7a1/claw_agent/skills/rk-model-benchmark"
WORKDIR="/userdata/aicp_test_aarch64"
MODELS_DIR="${WORKDIR}/models"
LIB_DIR="${WORKDIR}/lib"
TEST_IMAGE="${WORKDIR}/test.jpg"
TMP_DIR="/tmp/aicp_downloads"

DEVICE_ID=""
MODEL_TYPE=""
MODEL_NAME=""
RUN_BENCHMARK=0
NPU_FREQ="1000"
LOOP_COUNT="3"
CTX_LEN="16384"
NI="128"
NO="128"
CNN_RKNN_PATH=""
CNN_WEIGHT_PATH=""
LLM_RKNN_PATH=""
LLM_WEIGHT_PATH=""
LLM_TOKENIZER_PATH=""
LLM_EMBED_PATH=""
EFFECTIVE_LIB_DIR=""

usage() {
  cat <<'EOF'
Usage:
  check_env.sh --model-type <llm|cnn> --model-name <name> [--device <ip[:port]|serial>]

Examples:
  check_env.sh --device 192.168.1.10 --model-type llm --model-name qwen2_1.5b
  check_env.sh --model-type cnn --model-name mobilenet_v1
  check_env.sh --device 192.168.1.10 --model-type cnn --model-name yolov5s --loop-count 10
EOF
}

info() {
  echo "[INFO] $*" >&2
}

error() {
  echo "[ERROR] $*" >&2
}

fail() {
  error "$*"
  exit 1
}

normalize_device() {
  local d="$1"
  if [[ "$d" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
    echo "${d}:5555"
  else
    echo "$d"
  fi
}

adb_shell() {
  local device="$1"
  local cmd="$2"
  adb -s "$device" shell "$cmd"
}

normalize_model_key() {
  local s="$1"
  echo "$s" | tr '[:upper:]' '[:lower:]' | tr -cd '[:alnum:]'
}

# Find the directory that contains all required model files.
# Strategy:
#   1. Single full-fs scan for *.rknn to collect candidate directories.
#      Directories whose .rknn filename contains the model key are tried first.
#      If no name match but exactly one .rknn exists on the device, that dir is
#      used as a fallback candidate.
#   2. For each candidate directory, verify every required file pattern exists
#      (filename must contain the model key). Missing any pattern → skip dir,
#      continue to the next candidate.
#   3. Return the first directory that satisfies all patterns.
#
# Usage: find_model_dir <device> <model_key> <pattern1> [pattern2 ...]
find_model_dir() {
  local device="$1"
  local model_key="$2"
  shift 2
  local required_patterns=("$@")
  local model_norm
  model_norm="$(normalize_model_key "$model_key")"

  local -a name_matched_dirs=()
  local -a all_rknn_files=()

  # Pass 1: prioritize /userdata
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    all_rknn_files+=("$file")
    local base_norm
    base_norm="$(normalize_model_key "${file##*/}")"
    if [[ "$base_norm" == *"$model_norm"* ]]; then
      name_matched_dirs+=("${file%/*}")
    fi
  done < <(
    adb_shell "$device" "find /userdata \\
      -type f -iname '*.rknn' -print 2>/dev/null" | tr -d '\r'
  )

  # Pass 2: fallback to other locations (exclude /userdata to avoid duplicates)
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    all_rknn_files+=("$file")
    local base_norm
    base_norm="$(normalize_model_key "${file##*/}")"
    if [[ "$base_norm" == *"$model_norm"* ]]; then
      name_matched_dirs+=("${file%/*}")
    fi
  done < <(
    adb_shell "$device" "find / \\
      -path /proc -prune -o \\
      -path /sys -prune -o \\
      -path /dev -prune -o \\
      -path /userdata -prune -o \\
      -path /tmp -prune -o \\
      -type f -iname '*.rknn' -print 2>/dev/null" | tr -d '\r'
  )

  # Build candidate list: name-matched dirs first, single-file fallback second
  local -a candidates=()
  if [[ ${#name_matched_dirs[@]} -gt 0 ]]; then
    candidates=("${name_matched_dirs[@]}")
  elif [[ ${#all_rknn_files[@]} -eq 1 ]]; then
    info "未找到名称匹配 '${model_key}' 的 .rknn 文件，设备上仅存在一个 .rknn 文件，尝试使用目录: ${all_rknn_files[0]%/*}"
    candidates=("${all_rknn_files[0]%/*}")
  else
    return 1
  fi

  # Verify each candidate directory contains all required file patterns
  local dir
  for dir in "${candidates[@]}"; do
    local ok=1
    for pattern in "${required_patterns[@]}"; do
      local suffix_norm hit
      suffix_norm="$(normalize_model_key "${pattern#*.}")"
      hit=""
      while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        local bn
        bn="$(normalize_model_key "${f##*/}")"
        if [[ "$bn" == *"$model_norm"* ]] && [[ "$bn" == *"$suffix_norm" ]]; then
          hit="$f"
          break
        fi
      done < <(adb_shell "$device" "find ${dir} -maxdepth 1 -type f -iname '${pattern}' 2>/dev/null" | tr -d '\r')

      if [[ -z "$hit" ]]; then
        info "目录 ${dir} 缺少匹配 '${pattern}' 的文件，继续搜索其他目录"
        ok=0
        break
      fi
    done

    if [[ "$ok" -eq 1 ]]; then
      echo "$dir"
      return 0
    fi
  done

  return 1
}

# Locate a specific model file within a known directory (no recursive descent).
find_model_file_in_dir() {
  local device="$1"
  local dir="$2"
  local model_key="$3"
  local find_pattern="$4"
  local model_norm suffix_norm
  model_norm="$(normalize_model_key "$model_key")"
  suffix_norm="$(normalize_model_key "${find_pattern#*.}")"

  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    local base base_norm
    base="${file##*/}"
    base_norm="$(normalize_model_key "$base")"
    if [[ "$base_norm" == *"$model_norm"* ]] && [[ "$base_norm" == *"$suffix_norm" ]]; then
      echo "$file"
      return 0
    fi
  done < <(
    adb_shell "$device" "find ${dir} -maxdepth 1 -type f -iname '${find_pattern}' 2>/dev/null" | tr -d '\r'
  )

  return 1
}

download_file() {
  local remote_name="$1"
  local local_path="$2"
  local http_code

  mkdir -p "$TMP_DIR"
  http_code="$(curl -sL --retry 3 --retry-delay 2 \
    -A "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120.0.0.0 Mobile Safari/537.36" \
    -w "%{http_code}" \
    "${PREFIX}/${remote_name}" -o "$local_path")"

  if [[ "$http_code" != 2* ]]; then
    error "下载 ${remote_name} 失败：HTTP ${http_code}"
    return 1
  fi
}

install_rknn_smi() {
  local device="$1"
  local local_bin="${TMP_DIR}/rknn-smi"

  info "尝试安装 rknn-smi 到设备 ${device}"
  download_file "rknn-smi" "$local_bin" || fail "rknn-smi 下载失败，请检查网络或下载源。"
  chmod +x "$local_bin"
  adb -s "$device" push "$local_bin" /usr/bin/rknn-smi >/dev/null
  adb_shell "$device" "chmod +x /usr/bin/rknn-smi"
}

check_adb() {
  if ! adb version >/dev/null 2>&1; then
    fail "当前环境未安装或无法使用 adb，无法进行设备实测。请先安装 adb 并确认命令可用后再重试。"
  fi
}

select_device() {
  local preferred="${1:-}"
  local devs

  if [[ -n "$preferred" ]]; then
    preferred="$(normalize_device "$preferred")"
    info "尝试连接设备: ${preferred}"
    adb connect "$preferred" >/dev/null 2>&1 || true
    adb devices >&2
    echo "$preferred"
    return 0
  fi

  mapfile -t devs < <(adb devices | awk 'NR>1 && $2=="device" {print $1}')
  if [[ ${#devs[@]} -eq 0 ]]; then
    fail "未检测到已连接设备，请先连接设备后重试。"
  fi

  for dev in "${devs[@]}"; do
    if check_npu "$dev" >/dev/null 2>&1; then
      echo "$dev"
      return 0
    fi
  done

  fail "未检测到可用的 RK1820 NPU，当前设备无法执行模型性能测试。请确认设备已连接、NPU 正常工作，或更换目标设备后重试。"
}

check_npu() {
  local device="$1"
  local out

  set +e
  out="$(adb_shell "$device" "rknn3_transfer_proxy devices" 2>&1)"
  set -e
  echo "$out"
  if [[ "$out" == *"List of ntb devices attached"* ]]; then
    local lines_after
    lines_after="$(sed -n '2,$p' <<< "$out" | grep -v '^$' || true)"
    if [[ -n "$lines_after" ]]; then
      info "$out"
      return 0
    fi
  fi

  # Fallback: try rknn-smi
  set +e
  out="$(adb_shell "$device" "rknn-smi info" 2>&1)"
  set -e

  if [[ "$out" != *"RK1820"* ]]; then
    install_rknn_smi "$device"

    set +e
    out="$(adb_shell "$device" "rknn-smi info" 2>&1)"
    set -e
  fi

  if [[ "$out" != *"RK1820"* ]]; then
    return 1
  fi

  echo "$out"
}

check_paths() {
  local device="$1"

  adb_shell "$device" "ls -la ${WORKDIR}" >/dev/null
}

discover_lib_dir() {
  local device="$1"
  local candidates=("${LIB_DIR}" "/usr/lib64" "/usr/lib")

  EFFECTIVE_LIB_DIR=""
  for d in "${candidates[@]}"; do
    if adb_shell "$device" "test -f ${d}/librknn3_api.so" >/dev/null 2>&1; then
      EFFECTIVE_LIB_DIR="$d"
      break
    fi
  done

  if [[ -z "$EFFECTIVE_LIB_DIR" ]]; then
    fail "设备上未找到 librknn3_api.so，已尝试 ${LIB_DIR}、/usr/lib64、/usr/lib。请补齐依赖后重试。"
  fi

  info "使用动态库目录: ${EFFECTIVE_LIB_DIR}"
}

install_demo() {
  local device="$1"
  local name="$2"
  local local_bin="${TMP_DIR}/${name}"

  info "设备上缺少 ${name}，尝试自动下载并推送"
  download_file "$name" "$local_bin" || fail "${name} 下载失败，请检查网络或下载源。"
  chmod +x "$local_bin"
  adb -s "$device" push "$local_bin" "${WORKDIR}/${name}" >/dev/null
  adb_shell "$device" "chmod +x ${WORKDIR}/${name}"
  info "${name} 安装完成"
}

check_demos() {
  local device="$1"

  if ! adb_shell "$device" "ls -la ${WORKDIR}/rknn3_llm_demo" >/dev/null 2>&1; then
    install_demo "$device" "rknn3_llm_demo"
  fi

  if ! adb_shell "$device" "ls -la ${WORKDIR}/rknn3_cnn_demo" >/dev/null 2>&1; then
    install_demo "$device" "rknn3_cnn_demo"
  fi
}

check_model_llm() {
  local device="$1"
  local model="$2"
  local model_dir

  model_dir="$(find_model_dir "$device" "$model" "*.rknn" "*.weight" "*.tokenizer.gguf" "*.embed.bin" || true)"
  if [[ -z "$model_dir" ]]; then
    fail "LLM 模型 ${model} 的文件不完整。需要以下 4 个文件位于同一目录：\n1. ${model}.rknn\n2. ${model}.weight\n3. ${model}.tokenizer.gguf\n4. ${model}.embed.bin\n\n请从以下地址完整下载后推送到 /userdata/aicp_test_aarch64/models/：\nhttps://console.box.lenovo.com/l/wJLAwi 提取码：rknn"
  fi

  info "在目录 ${model_dir} 中定位 LLM 模型文件"
  LLM_RKNN_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.rknn")"
  LLM_WEIGHT_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.weight")"
  LLM_TOKENIZER_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.tokenizer.gguf")"
  LLM_EMBED_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.embed.bin")"

  info "${LLM_RKNN_PATH}"
  info "${LLM_WEIGHT_PATH}"
  info "${LLM_TOKENIZER_PATH}"
  info "${LLM_EMBED_PATH}"
}

check_model_cnn() {
  local device="$1"
  local model="$2"
  local model_dir

  model_dir="$(find_model_dir "$device" "$model" "*.rknn" "*.weight" || true)"
  if [[ -z "$model_dir" ]]; then
    fail "设备上未找到包含模型 ${model} 完整文件（.rknn 与 .weight 需在同一目录）的目录。请从以下地址下载模型后推送到 /userdata/aicp_test_aarch64/models/：\nhttps://console.box.lenovo.com/l/wJLAwi 提取码：rknn"
  fi

  info "在目录 ${model_dir} 中定位 CNN 模型文件"
  CNN_RKNN_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.rknn")"
  CNN_WEIGHT_PATH="$(find_model_file_in_dir "$device" "$model_dir" "$model" "*.weight")"

  info "${CNN_RKNN_PATH}"
  info "${CNN_WEIGHT_PATH}"
}

check_test_image() {
  local device="$1"

  if adb_shell "$device" "ls -la ${TEST_IMAGE}" >/dev/null 2>&1; then
    return 0
  fi

  info "test.jpg 缺失，尝试自动下载并推送"
  mkdir -p "$TMP_DIR"
  download_file "test.jpg" "${TMP_DIR}/test.jpg" || fail "test.jpg 下载失败，请检查网络或下载源。"
  adb -s "$device" push "${TMP_DIR}/test.jpg" "$TEST_IMAGE" >/dev/null
  info "test.jpg 安装完成"
}

check_fix_freq_script() {
  local device="$1"
  local fix_freq_script="${WORKDIR}/fix_freq_rk3588.sh"

  if adb_shell "$device" "ls -la ${fix_freq_script}" >/dev/null 2>&1; then
    return 0
  fi

  info "定频脚本缺失，尝试自动下载并推送"
  mkdir -p "$TMP_DIR"
  download_file "fix_freq_rk3588.sh" "${TMP_DIR}/fix_freq_rk3588.sh" || fail "fix_freq_rk3588.sh 下载失败，请检查网络或下载源。"
  chmod +x "${TMP_DIR}/fix_freq_rk3588.sh"
  adb -s "$device" push "${TMP_DIR}/fix_freq_rk3588.sh" "$fix_freq_script" >/dev/null
  adb_shell "$device" "chmod +x ${fix_freq_script}"
  info "fix_freq_rk3588.sh 安装完成"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --device)
        DEVICE_ID="${2:-}"
        shift 2
        ;;
      --model-type)
        MODEL_TYPE="${2:-}"
        shift 2
        ;;
      --model-name)
        MODEL_NAME="${2:-}"
        shift 2
        ;;
      --npu-freq)
        NPU_FREQ="${2:-}"
        shift 2
        ;;
      --loop-count)
        LOOP_COUNT="${2:-}"
        shift 2
        ;;
      --ctx)
        CTX_LEN="${2:-}"
        shift 2
        ;;
      --ni)
        NI="${2:-}"
        shift 2
        ;;
      --no)
        NO="${2:-}"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "未知参数: $1"
        ;;
    esac
  done

  if [[ -z "$MODEL_TYPE" ]] || [[ -z "$MODEL_NAME" ]]; then
    usage
    fail "--model-type 与 --model-name 为必填参数"
  fi

  if [[ "$MODEL_TYPE" != "llm" ]] && [[ "$MODEL_TYPE" != "cnn" ]]; then
    fail "--model-type 仅支持 llm 或 cnn"
  fi
}

set_performance_mode() {
  local device="$1"

  info "设置性能模式与 NPU 频率: ${NPU_FREQ} MHz"
  adb_shell "$device" "echo performance | tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor" >/dev/null || true
  adb_shell "$device" "rknn-smi set -d 0 -c 0 -t npu_freq -s ${NPU_FREQ}" >/dev/null
  adb_shell "$device" "rknn-smi info" >/dev/null
}

run_benchmark() {
  local device="$1"
  local raw_output
  local results
  local fix_freq_script="${WORKDIR}/fix_freq_rk3588.sh"

  # 执行定频脚本
  info "执行定频脚本"
  adb_shell "$device" "bash ${fix_freq_script}" >/dev/null 2>&1 || true

  info "BENCHMARK_START device=${device} model_type=${MODEL_TYPE} model_name=${MODEL_NAME} npu_freq=${NPU_FREQ}"
  if [[ "$MODEL_TYPE" == "llm" ]]; then
    info "执行 LLM 测试"
    raw_output="$(adb_shell "$device" "cd ${WORKDIR} && export LD_LIBRARY_PATH=${EFFECTIVE_LIB_DIR}:\$LD_LIBRARY_PATH && ./rknn3_llm_demo -m ${LLM_RKNN_PATH} -w ${LLM_WEIGHT_PATH} -tk ${LLM_TOKENIZER_PATH} -em ${LLM_EMBED_PATH} -ctx ${CTX_LEN} -ni ${NI} -no ${NO} -cl ${LOOP_COUNT}" 2>&1 || true)"
  else
    info "执行 CNN 测试"
    raw_output="$(adb_shell "$device" "cd ${WORKDIR} && export LD_LIBRARY_PATH=${EFFECTIVE_LIB_DIR}:\$LD_LIBRARY_PATH && ./rknn3_cnn_demo -m ${CNN_RKNN_PATH} -w ${CNN_WEIGHT_PATH} -i ${TEST_IMAGE} -cl ${LOOP_COUNT}" 2>&1 || true)"
  fi

  # Extract results summary
  local marker=""
  if [[ "$MODEL_TYPE" == "llm" ]]; then
    marker="RKNN3_LLM_TEST Results Summary"
  else
    marker="RKNN3_CNN_TEST Results Summary"
  fi

  if [[ "$raw_output" == *"$marker"* ]]; then
    results="$(sed -n "/${marker}/,\$p" <<< "$raw_output" | tail -n +2)"
    info "$results"
  else
    fail "Benchmark 执行失败：未找到结果汇总"
  fi
}

main() {
  parse_args "$@"

  info "开始环境检测"
  check_adb
  adb devices >&2

  local device
  device="$(select_device "$DEVICE_ID")"
  info "使用设备: ${device}"

  if ! check_npu "$device" >/dev/null; then
    fail "未检测到可用的 RK1820 NPU，当前设备无法执行模型性能测试。请确认设备已连接、NPU 正常工作，或更换目标设备后重试。"
  fi

  check_paths "$device"
  discover_lib_dir "$device"
  check_demos "$device"

  if [[ "$MODEL_TYPE" == "llm" ]]; then
    check_model_llm "$device" "$MODEL_NAME"
  else
    check_model_cnn "$device" "$MODEL_NAME"
    check_test_image "$device"
  fi

  check_fix_freq_script "$device"

  echo "ENV_CHECK_OK device=${device} model_type=${MODEL_TYPE} model_name=${MODEL_NAME}"
  run_benchmark "$device"
  echo "BENCHMARK_DONE device=${device} model_type=${MODEL_TYPE} model_name=${MODEL_NAME} npu_freq=${NPU_FREQ}"
}

main "$@"
