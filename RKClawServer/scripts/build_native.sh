#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XGRAMMAR_ROOT="${XGRAMMAR_ROOT:-${ROOT_DIR}/native/3rdparty/xgrammar}"
TOKENIZER_ROOT="${TOKENIZER_ROOT:-}"
NATIVE_BUILD_MODE="${NATIVE_BUILD_MODE:-auto}"

case "${NATIVE_BUILD_MODE}" in
    auto|cross|native) ;;
    *)
        echo "NATIVE_BUILD_MODE must be auto, cross, or native" >&2
        exit 1
        ;;
esac

has_cmake_definition() {
    local name="$1"
    shift
    local argument
    for argument in "$@"; do
        if [[ "${argument}" == "-D${name}="* ]]; then
            return 0
        fi
    done
    return 1
}

resolve_compiler_prefix() {
    local prefix compiler
    local -a candidates=()
    if [[ -n "${CROSS_COMPILE:-}" ]]; then
        candidates+=("${CROSS_COMPILE}")
    else
        candidates+=(
            "aarch64-rockchip1240-linux-gnu-"
            "aarch64-none-linux-gnu-"
            "aarch64-linux-gnu-"
        )
    fi
    for prefix in "${candidates[@]}"; do
        compiler="${prefix}gcc"
        if [[ "${compiler}" == */* ]]; then
            [[ -x "${compiler}" && -x "${prefix}g++" ]] || continue
        else
            command -v "${compiler}" >/dev/null 2>&1 || continue
            command -v "${prefix}g++" >/dev/null 2>&1 || continue
        fi
        printf '%s\n' "${prefix}"
        return 0
    done
    return 1
}

host_arch="$(uname -m)"
build_kind="native"
cmake_platform_args=()
has_toolchain=0
has_c_compiler=0
has_cxx_compiler=0
has_system_name=0
has_system_processor=0
has_cmake_definition CMAKE_TOOLCHAIN_FILE "$@" && has_toolchain=1
has_cmake_definition CMAKE_C_COMPILER "$@" && has_c_compiler=1
has_cmake_definition CMAKE_CXX_COMPILER "$@" && has_cxx_compiler=1
has_cmake_definition CMAKE_SYSTEM_NAME "$@" && has_system_name=1
has_cmake_definition CMAKE_SYSTEM_PROCESSOR "$@" && has_system_processor=1

if [[ -n "${CMAKE_TOOLCHAIN_FILE:-}" && "${has_toolchain}" = "0" ]]; then
    cmake_platform_args+=("-DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}")
    has_toolchain=1
fi

if [[ "${has_c_compiler}" != "${has_cxx_compiler}" ]]; then
    echo "Set both CMAKE_C_COMPILER and CMAKE_CXX_COMPILER" >&2
    exit 1
fi

if [[ "${has_toolchain}" = "1" ]]; then
    build_kind="custom-toolchain"
elif [[ "${has_c_compiler}" = "1" ]]; then
    build_kind="custom-toolchain"
    if [[ "${NATIVE_BUILD_MODE}" != "native" ]]; then
        [[ "${has_system_name}" = "1" ]] || cmake_platform_args+=("-DCMAKE_SYSTEM_NAME=Linux")
        [[ "${has_system_processor}" = "1" ]] || cmake_platform_args+=("-DCMAKE_SYSTEM_PROCESSOR=aarch64")
    fi
elif [[ "${NATIVE_BUILD_MODE}" = "cross" || (
        "${NATIVE_BUILD_MODE}" = "auto" && ! "${host_arch}" =~ ^(aarch64|arm64)$ ) ]]; then
    if ! compiler_prefix="$(resolve_compiler_prefix)"; then
        echo "No aarch64 cross compiler was found." >&2
        echo "Set CROSS_COMPILE=/path/to/aarch64-toolchain/bin/aarch64-prefix-" >&2
        echo "or pass -DCMAKE_TOOLCHAIN_FILE=/path/to/toolchain.cmake." >&2
        exit 1
    fi
    build_kind="aarch64-cross"
    cmake_platform_args+=(
        "-DCMAKE_SYSTEM_NAME=Linux"
        "-DCMAKE_SYSTEM_PROCESSOR=aarch64"
        "-DCMAKE_C_COMPILER=${compiler_prefix}gcc"
        "-DCMAKE_CXX_COMPILER=${compiler_prefix}g++"
    )
elif [[ "${NATIVE_BUILD_MODE}" = "native" || "${host_arch}" =~ ^(aarch64|arm64)$ ]]; then
    build_kind="native"
fi

if [[ -z "${TOKENIZER_ROOT}" ]]; then
    if [[ "${RKCLAW_REBUILD_TOKENIZER:-0}" = "1" ]]; then
        if [[ -n "${TOKENIZER_ARCH:-}" ]]; then
            tokenizer_arch="${TOKENIZER_ARCH}"
        elif [[ "${build_kind}" = "native" && "${host_arch}" =~ ^(x86_64|amd64)$ ]]; then
            tokenizer_arch="x86_64"
        else
            tokenizer_arch="aarch64"
        fi
        "${ROOT_DIR}/scripts/build_tokenizer.sh" --arch "${tokenizer_arch}"
        TOKENIZER_ROOT="${ROOT_DIR}/build/deps/tokenizer-${tokenizer_arch}"
    else
        TOKENIZER_ROOT="${ROOT_DIR}/native/3rdparty/tokenizer"
    fi
fi

if [[ "${build_kind}" = "aarch64-cross" ]]; then
    BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build/native-aarch64}"
    INSTALL_DIR="${INSTALL_DIR:-${ROOT_DIR}/dist/native-aarch64}"
elif [[ "${build_kind}" = "custom-toolchain" ]]; then
    BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build/native-custom}"
    INSTALL_DIR="${INSTALL_DIR:-${ROOT_DIR}/dist/native-custom}"
else
    BUILD_DIR="${BUILD_DIR:-${ROOT_DIR}/build/native}"
    INSTALL_DIR="${INSTALL_DIR:-${ROOT_DIR}/dist/native}"
fi

if [[ ! -f "${XGRAMMAR_ROOT}/CMakeLists.txt" ]]; then
    echo "XGrammar v0.2.3 source is missing: ${XGRAMMAR_ROOT}" >&2
    echo "Run: git submodule update --init --recursive native/3rdparty/xgrammar" >&2
    exit 1
fi

echo "Native build mode: ${build_kind} (host=${host_arch})"
if [[ -n "${compiler_prefix:-}" ]]; then
    echo "Cross compiler: ${compiler_prefix}gcc / ${compiler_prefix}g++"
fi
echo "Build directory: ${BUILD_DIR}"
echo "Install directory: ${INSTALL_DIR}"

cmake -S "${ROOT_DIR}/native" -B "${BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="${INSTALL_DIR}" \
    -DTOKENIZER_ROOT="${TOKENIZER_ROOT}" \
    -DXGRAMMAR_ROOT="${XGRAMMAR_ROOT}" \
    "${cmake_platform_args[@]}" \
    "$@"
cmake --build "${BUILD_DIR}" --target rkclaw_native --parallel
cmake --install "${BUILD_DIR}" --component native

echo "RKClawServer native runtime: ${INSTALL_DIR}/lib/librkclaw_native.so"
