#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOKENIZER_REPOSITORY="https://github.com/airockchip/rknn3-model-zoo.git"
TOKENIZER_COMMIT="174e44c77230735b1458946debb62b3982c1ee58"
CACHE_REPOSITORY="${ROOT_DIR}/build/deps/rknn3-model-zoo"
ARCH=""
SOURCE_DIR=""
UPDATE_BUNDLED=0

usage() {
    cat <<'EOF'
Usage: ./scripts/build_tokenizer.sh --arch <aarch64|x86_64> [options]

Options:
  --source-dir PATH   Use a local rknn3-model-zoo checkout or tokenizer directory.
  --update-bundled   Replace the corresponding bundled library and manifest.
  -h, --help         Show this help.

Environment:
  RKCLAW_OFFLINE=1       Reuse an existing cached checkout without network access.
  CROSS_COMPILE=PREFIX   aarch64 compiler prefix (for example /opt/bin/aarch64-linux-gnu-).
  CMAKE_TOOLCHAIN_FILE   CMake toolchain file for aarch64.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --arch)
            [[ $# -ge 2 ]] || { echo "--arch requires a value" >&2; exit 2; }
            ARCH="$2"
            shift 2
            ;;
        --source-dir)
            [[ $# -ge 2 ]] || { echo "--source-dir requires a value" >&2; exit 2; }
            SOURCE_DIR="$2"
            shift 2
            ;;
        --update-bundled)
            UPDATE_BUNDLED=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$ARCH" in
    aarch64|x86_64) ;;
    *)
        echo "--arch must be aarch64 or x86_64" >&2
        exit 2
        ;;
esac

if [[ -z "$SOURCE_DIR" ]]; then
    SOURCE_REPOSITORY="$CACHE_REPOSITORY"
    if [[ ! -d "$SOURCE_REPOSITORY/.git" ]]; then
        if [[ "${RKCLAW_OFFLINE:-0}" == "1" ]]; then
            echo "Offline mode: cached source is missing: $SOURCE_REPOSITORY" >&2
            exit 1
        fi
        mkdir -p "$(dirname "$SOURCE_REPOSITORY")"
        git clone --filter=blob:none --no-checkout "$TOKENIZER_REPOSITORY" "$SOURCE_REPOSITORY"
        git -C "$SOURCE_REPOSITORY" sparse-checkout init --cone
        git -C "$SOURCE_REPOSITORY" sparse-checkout set tokenizer
    fi

    if ! git -C "$SOURCE_REPOSITORY" cat-file -e "${TOKENIZER_COMMIT}^{commit}" 2>/dev/null; then
        if [[ "${RKCLAW_OFFLINE:-0}" == "1" ]]; then
            echo "Offline mode: cached source does not contain $TOKENIZER_COMMIT" >&2
            exit 1
        fi
        git -C "$SOURCE_REPOSITORY" fetch --filter=blob:none origin "$TOKENIZER_COMMIT"
    fi
    git -C "$SOURCE_REPOSITORY" sparse-checkout set tokenizer
    git -C "$SOURCE_REPOSITORY" checkout --detach "$TOKENIZER_COMMIT"
    SOURCE_DIR="$SOURCE_REPOSITORY/tokenizer"
else
    SOURCE_DIR="$(cd "$SOURCE_DIR" && pwd)"
    if [[ -f "$SOURCE_DIR/CMakeLists.txt" && "$(basename "$SOURCE_DIR")" == "tokenizer" ]]; then
        SOURCE_REPOSITORY="$(git -C "$SOURCE_DIR" rev-parse --show-toplevel 2>/dev/null || true)"
    elif [[ -f "$SOURCE_DIR/tokenizer/CMakeLists.txt" ]]; then
        SOURCE_REPOSITORY="$SOURCE_DIR"
        SOURCE_DIR="$SOURCE_DIR/tokenizer"
    else
        echo "Not an rknn3-model-zoo tokenizer source directory: $SOURCE_DIR" >&2
        exit 1
    fi
fi

if [[ -z "${SOURCE_REPOSITORY:-}" || ! -d "$SOURCE_REPOSITORY/.git" ]]; then
    echo "Tokenizer source must be inside a Git checkout of rknn3-model-zoo" >&2
    exit 1
fi
SOURCE_REVISION="$(git -C "$SOURCE_REPOSITORY" rev-parse HEAD)"
if [[ "$SOURCE_REVISION" != "$TOKENIZER_COMMIT" ]]; then
    echo "Tokenizer source revision mismatch: expected $TOKENIZER_COMMIT, got $SOURCE_REVISION" >&2
    exit 1
fi

OUTPUT_ROOT="${ROOT_DIR}/build/deps/tokenizer-${ARCH}"
BUILD_DIR="${OUTPUT_ROOT}/build"
mkdir -p "$BUILD_DIR"

cmake_args=(
    -S "$SOURCE_DIR"
    -B "$BUILD_DIR"
    -DCMAKE_BUILD_TYPE=Release
    -DBUILD_SHARED_LIBS=OFF
    -DCMAKE_INSTALL_PREFIX="$OUTPUT_ROOT"
)

if [[ "$ARCH" == "aarch64" ]]; then
    cmake_args+=(
        -DCMAKE_SYSTEM_NAME=Linux
        -DCMAKE_SYSTEM_PROCESSOR=aarch64
    )
    if [[ -n "${CMAKE_TOOLCHAIN_FILE:-}" ]]; then
        cmake_args+=("-DCMAKE_TOOLCHAIN_FILE=${CMAKE_TOOLCHAIN_FILE}")
        COMPILER_DESCRIPTION="toolchain file: ${CMAKE_TOOLCHAIN_FILE}"
    else
        compiler_prefix="${CROSS_COMPILE:-}"
        if [[ -z "$compiler_prefix" ]]; then
            for candidate in aarch64-rockchip1240-linux-gnu- aarch64-none-linux-gnu- aarch64-linux-gnu-; do
                if command -v "${candidate}gcc" >/dev/null 2>&1 && command -v "${candidate}g++" >/dev/null 2>&1; then
                    compiler_prefix="$candidate"
                    break
                fi
            done
        fi
        if [[ -z "$compiler_prefix" ]]; then
            echo "No aarch64 compiler found; set CROSS_COMPILE or CMAKE_TOOLCHAIN_FILE" >&2
            exit 1
        fi
        [[ -x "${compiler_prefix}gcc" || -n "$(command -v "${compiler_prefix}gcc" 2>/dev/null || true)" ]] || {
            echo "C compiler not found: ${compiler_prefix}gcc" >&2
            exit 1
        }
        [[ -x "${compiler_prefix}g++" || -n "$(command -v "${compiler_prefix}g++" 2>/dev/null || true)" ]] || {
            echo "C++ compiler not found: ${compiler_prefix}g++" >&2
            exit 1
        }
        cmake_args+=(
            "-DCMAKE_C_COMPILER=${compiler_prefix}gcc"
            "-DCMAKE_CXX_COMPILER=${compiler_prefix}g++"
        )
        COMPILER_DESCRIPTION="$("${compiler_prefix}g++" --version | head -n 1)"
    fi
else
    c_compiler="${CC:-$(command -v cc)}"
    cxx_compiler="${CXX:-$(command -v c++)}"
    cmake_args+=(
        "-DCMAKE_C_COMPILER=${c_compiler}"
        "-DCMAKE_CXX_COMPILER=${cxx_compiler}"
    )
    COMPILER_DESCRIPTION="$("$cxx_compiler" --version | head -n 1)"
fi

cmake "${cmake_args[@]}"
cmake --build "$BUILD_DIR" --parallel
cmake --install "$BUILD_DIR"

INSTALLED_LIBRARY="$OUTPUT_ROOT/lib/libtokenizer.a"
[[ -f "$INSTALLED_LIBRARY" ]] || { echo "Build did not produce $INSTALLED_LIBRARY" >&2; exit 1; }
mkdir -p "$OUTPUT_ROOT/lib/Linux/$ARCH"
install -m 0644 "$INSTALLED_LIBRARY" "$OUTPUT_ROOT/lib/Linux/$ARCH/libtokenizer.a"
BUILT_LIBRARY="$OUTPUT_ROOT/lib/Linux/$ARCH/libtokenizer.a"

if [[ "$UPDATE_BUNDLED" == "1" ]]; then
    BUNDLE_ROOT="${ROOT_DIR}/native/3rdparty/tokenizer"
    MANIFEST="$BUNDLE_ROOT/SOURCE-MANIFEST.txt"
    mkdir -p "$BUNDLE_ROOT/include" "$BUNDLE_ROOT/lib/Linux/$ARCH"
    install -m 0644 "$SOURCE_DIR/include/Tokenizer.h" "$BUNDLE_ROOT/include/Tokenizer.h"
    install -m 0644 "$BUILT_LIBRARY" "$BUNDLE_ROOT/lib/Linux/$ARCH/libtokenizer.a"
    install -m 0644 "$SOURCE_REPOSITORY/LICENSE" "$BUNDLE_ROOT/LICENSE"

    x86_compiler="$(sed -n 's/^x86_64.compiler = //p' "$MANIFEST" 2>/dev/null || true)"
    arm_compiler="$(sed -n 's/^aarch64.compiler = //p' "$MANIFEST" 2>/dev/null || true)"
    if [[ "$ARCH" == "x86_64" ]]; then
        x86_compiler="$COMPILER_DESCRIPTION"
    else
        arm_compiler="$COMPILER_DESCRIPTION"
    fi
    x86_sha="$(sha256sum "$BUNDLE_ROOT/lib/Linux/x86_64/libtokenizer.a" 2>/dev/null | awk '{print $1}')"
    arm_sha="$(sha256sum "$BUNDLE_ROOT/lib/Linux/aarch64/libtokenizer.a" 2>/dev/null | awk '{print $1}')"
    {
        echo "repository = $TOKENIZER_REPOSITORY"
        echo "commit = $TOKENIZER_COMMIT"
        echo "license = Apache-2.0"
        echo "build_type = Release"
        echo "x86_64.compiler = ${x86_compiler:-not rebuilt in this checkout}"
        echo "x86_64.sha256 = $x86_sha"
        echo "aarch64.compiler = ${arm_compiler:-not rebuilt in this checkout}"
        echo "aarch64.sha256 = $arm_sha"
    } > "$MANIFEST"
    echo "Updated bundled tokenizer for $ARCH"
fi

echo "Tokenizer root: $OUTPUT_ROOT"
echo "Library SHA256: $(sha256sum "$BUILT_LIBRARY" | awk '{print $1}')"
