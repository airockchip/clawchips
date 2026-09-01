#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${ROOT_DIR}/release"
OFFLINE=0

usage() {
    cat <<'EOF'
Usage: ./scripts/package_source.sh [options]

Options:
  --output-dir PATH  Write the source archive and checksum to PATH.
  --offline          Do not fetch XGrammar; fail if its fixed source is absent.
  -h, --help         Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            [[ $# -ge 2 ]] || { echo "--output-dir requires a value" >&2; exit 2; }
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --offline)
            OFFLINE=1
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

cd "$ROOT_DIR"
if ! git diff --quiet --ignore-submodules=none || ! git diff --cached --quiet --ignore-submodules=none; then
    echo "Tracked files must be committed before packaging source." >&2
    exit 1
fi

untracked="$(git ls-files --others --exclude-standard)"
if [[ -n "$untracked" ]]; then
    echo "Warning: untracked files are excluded from the source archive:" >&2
    printf '%s\n' "$untracked" | sed 's/^/  /' >&2
fi

VERSION="$(python3 -c 'import pathlib, tomllib; print(tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"])')"
PROJECT_COMMIT="$(git rev-parse HEAD)"
XGRAMMAR_PATH="native/3rdparty/xgrammar"
XGRAMMAR_COMMIT="$(git ls-tree HEAD "$XGRAMMAR_PATH" | awk '{print $3}')"
TOKENIZER_MANIFEST="native/3rdparty/tokenizer/SOURCE-MANIFEST.txt"
TOKENIZER_COMMIT="$(sed -n 's/^commit = //p' "$TOKENIZER_MANIFEST")"
TOKENIZER_X86_SHA="$(sha256sum native/3rdparty/tokenizer/lib/Linux/x86_64/libtokenizer.a | awk '{print $1}')"
TOKENIZER_ARM_SHA="$(sha256sum native/3rdparty/tokenizer/lib/Linux/aarch64/libtokenizer.a | awk '{print $1}')"

xgrammar_ready=0
if [[ -f "$XGRAMMAR_PATH/CMakeLists.txt" ]] &&
   [[ "$(git -C "$XGRAMMAR_PATH" rev-parse HEAD 2>/dev/null || true)" == "$XGRAMMAR_COMMIT" ]]; then
    xgrammar_ready=1
fi
if [[ "$xgrammar_ready" == "1" ]] && git -C "$XGRAMMAR_PATH" submodule status --recursive | grep -q '^-'; then
    xgrammar_ready=0
fi
if [[ "$xgrammar_ready" == "0" ]]; then
    if [[ "$OFFLINE" == "1" ]]; then
        echo "Offline mode: XGrammar $XGRAMMAR_COMMIT is not initialized at $XGRAMMAR_PATH" >&2
        exit 1
    fi
    git submodule update --init --recursive "$XGRAMMAR_PATH"
fi
if [[ "$(git -C "$XGRAMMAR_PATH" rev-parse HEAD)" != "$XGRAMMAR_COMMIT" ]]; then
    echo "XGrammar checkout does not match pinned commit $XGRAMMAR_COMMIT" >&2
    exit 1
fi

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
PREFIX="rk-claw-server-${VERSION}-source"
STAGING="$TEMP_DIR/$PREFIX"
mkdir -p "$STAGING/$XGRAMMAR_PATH"

git archive --format=tar HEAD | tar -xf - -C "$STAGING"
git -C "$XGRAMMAR_PATH" archive --format=tar "$XGRAMMAR_COMMIT" | tar -xf - -C "$STAGING/$XGRAMMAR_PATH"
while read -r submodule_revision submodule_path _; do
    submodule_revision="${submodule_revision#-}"
    submodule_revision="${submodule_revision#+}"
    mkdir -p "$STAGING/$XGRAMMAR_PATH/$submodule_path"
    git -C "$XGRAMMAR_PATH/$submodule_path" archive --format=tar "$submodule_revision" |
        tar -xf - -C "$STAGING/$XGRAMMAR_PATH/$submodule_path"
done < <(git -C "$XGRAMMAR_PATH" submodule status --recursive)

cat > "$STAGING/SOURCE-MANIFEST.txt" <<EOF
RKClawServer.repository = https://github.com/airockchip/RKClawServer.git
RKClawServer.version = $VERSION
RKClawServer.commit = $PROJECT_COMMIT
XGrammar.repository = https://github.com/mlc-ai/xgrammar.git
XGrammar.commit = $XGRAMMAR_COMMIT
Tokenizer.repository = https://github.com/airockchip/rknn3-model-zoo.git
Tokenizer.commit = $TOKENIZER_COMMIT
Tokenizer.x86_64.sha256 = $TOKENIZER_X86_SHA
Tokenizer.aarch64.sha256 = $TOKENIZER_ARM_SHA
EOF

if find "$STAGING" -type f \( -name '*.whl' -o -name '*.so' -o -name '*.pdf' -o -name '*.rknn' -o -name '*.weight' \) -print -quit | grep -q .; then
    echo "Source staging unexpectedly contains a binary product artifact." >&2
    exit 1
fi
if find "$STAGING" -type d -name .git -print -quit | grep -q .; then
    echo "Source staging unexpectedly contains Git metadata." >&2
    exit 1
fi

COMMIT_TIMESTAMP="$(git show -s --format=%ct HEAD)"
find "$STAGING" -exec touch -h -d "@$COMMIT_TIMESTAMP" {} +
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"
ARCHIVE_NAME="$PREFIX.tar.gz"
ARCHIVE_PATH="$OUTPUT_DIR/$ARCHIVE_NAME"
CHECKSUM_PATH="$ARCHIVE_PATH.sha256"
TEMP_ARCHIVE="$OUTPUT_DIR/.${ARCHIVE_NAME}.tmp.$$"
TEMP_CHECKSUM="$OUTPUT_DIR/.${ARCHIVE_NAME}.sha256.tmp.$$"

tar --sort=name \
    --mtime="@$COMMIT_TIMESTAMP" \
    --owner=0 --group=0 --numeric-owner \
    -C "$TEMP_DIR" -cf - "$PREFIX" | gzip -n > "$TEMP_ARCHIVE"
ARCHIVE_SHA="$(sha256sum "$TEMP_ARCHIVE" | awk '{print $1}')"
printf '%s  %s\n' "$ARCHIVE_SHA" "$ARCHIVE_NAME" > "$TEMP_CHECKSUM"
mv -f "$TEMP_ARCHIVE" "$ARCHIVE_PATH"
mv -f "$TEMP_CHECKSUM" "$CHECKSUM_PATH"

echo "Source archive: $ARCHIVE_PATH"
echo "SHA256: $ARCHIVE_SHA"
