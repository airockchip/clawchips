#!/bin/bash
# ADB Wrapper - Forwards ADB commands to remote Windows/Linux PC via SSH
# Loads credentials from ~/.openclaw/workspace/skills/rk-adb/config/config.json
#
# Usage: ./adb_wrapper.sh [adb-command] [args...]
# Examples:
#   ./adb_wrapper.sh devices
#   ./adb_wrapper.sh shell getprop ro.build.fingerprint
#   ./adb_wrapper.sh pull /sdcard/test.txt ./
#   ./adb_wrapper.sh push ./file.apk /sdcard/
#   ./adb_wrapper.sh logcat -d

set -e

# Configuration
CONFIG_FILE="$HOME/.openclaw/workspace/skills/rk-adb/config/config.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

error() { echo -e "${RED}ERROR: $1${NC}" >&2; exit 1; }
warn() { echo -e "${YELLOW}WARNING: $1${NC}" >&2; }
info() { echo -e "${GREEN}$1${NC}" >&2; }

# Check dependencies
command -v sshpass >/dev/null 2>&1 || error "sshpass not found. Install with: sudo apt install sshpass"
command -v jq >/dev/null 2>&1 || error "jq not found. Install with: sudo apt install jq"

# Load configuration
load_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        error "Config file not found: $CONFIG_FILE\nRun: python3 $SCRIPT_DIR/adb_helper.py save-profile --help"
    fi

    # Get last used profile or specified profile
    local profile_name="${ADB_PROFILE:-$(jq -r '.last_used_profile // empty' "$CONFIG_FILE")}"
    if [[ -z "$profile_name" ]]; then
        error "No profile configured. Run: python3 $SCRIPT_DIR/adb_helper.py save-profile --help"
    fi

    # Load profile
    local profile=$(jq -r ".profiles[\"$profile_name\"] // empty" "$CONFIG_FILE")
    if [[ -z "$profile" || "$profile" == "null" ]]; then
        error "Profile '$profile_name' not found in config"
    fi

    REMOTE_HOST=$(echo "$profile" | jq -r '.host')
    REMOTE_USER=$(echo "$profile" | jq -r '.username')
    REMOTE_PASSWORD=$(echo "$profile" | jq -r '.password')
    REMOTE_PLATFORM=$(echo "$profile" | jq -r '.platform // "windows"')
    DEVICE_SERIAL=$(echo "$profile" | jq -r '.device_serial // empty')

    if [[ -z "$REMOTE_HOST" || -z "$REMOTE_USER" ]]; then
        error "Invalid profile: missing host or username"
    fi
}

# SSH command helper
ssh_cmd() {
    sshpass -p "$REMOTE_PASSWORD" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$REMOTE_USER@$REMOTE_HOST" "$@"
}

# SCP command helper
scp_cmd() {
    sshpass -p "$REMOTE_PASSWORD" scp -o StrictHostKeyChecking=no "$@"
}

# Get Windows temp directory (cached)
get_win_temp() {
    if [[ -z "$WIN_TEMP" ]]; then
        WIN_TEMP=$(ssh_cmd 'echo %TEMP%' 2>/dev/null | tr -d '\r' | tr '\\' '/')
    fi
    echo "$WIN_TEMP"
}

# Build adb command with optional device serial
build_adb_cmd() {
    local cmd="adb"
    if [[ -n "$DEVICE_SERIAL" ]]; then
        cmd="adb -s $DEVICE_SERIAL"
    fi
    echo "$cmd"
}

# Handle 'pull' command
handle_pull() {
    local remote_file="$1"
    local local_file="${2:-$(basename "$remote_file")}"
    local adb_cmd=$(build_adb_cmd)

    if [[ "$REMOTE_PLATFORM" == "windows" ]]; then
        local win_temp=$(get_win_temp)
        local temp_file="adb_pull_$$"

        # Pull to Windows temp
        ssh_cmd "$adb_cmd pull \"$remote_file\" %TEMP%\\$temp_file" 2>/dev/null

        # SCP to local
        scp_cmd "$REMOTE_USER@$REMOTE_HOST:${win_temp}/$temp_file" "$local_file" 2>/dev/null

        # Cleanup
        ssh_cmd "del %TEMP%\\$temp_file" 2>/dev/null || true
    else
        # Linux remote
        local temp_file="/tmp/adb_pull_$$"

        ssh_cmd "$adb_cmd pull \"$remote_file\" $temp_file" 2>/dev/null
        scp_cmd "$REMOTE_USER@$REMOTE_HOST:$temp_file" "$local_file" 2>/dev/null
        ssh_cmd "rm -f $temp_file" 2>/dev/null || true
    fi

    echo "Pulled: $remote_file -> $local_file"
}

# Handle 'push' command
handle_push() {
    local local_file="$1"
    local remote_file="$2"
    local adb_cmd=$(build_adb_cmd)

    if [[ ! -f "$local_file" ]]; then
        error "Local file not found: $local_file"
    fi

    if [[ "$REMOTE_PLATFORM" == "windows" ]]; then
        local win_temp=$(get_win_temp)
        local temp_file="adb_push_$$"

        # SCP to Windows temp
        scp_cmd "$local_file" "$REMOTE_USER@$REMOTE_HOST:${win_temp}/$temp_file" 2>/dev/null

        # Push to device
        ssh_cmd "$adb_cmd push %TEMP%\\$temp_file \"$remote_file\"" 2>/dev/null

        # Cleanup
        ssh_cmd "del %TEMP%\\$temp_file" 2>/dev/null || true
    else
        # Linux remote
        local temp_file="/tmp/adb_push_$$"

        scp_cmd "$local_file" "$REMOTE_USER@$REMOTE_HOST:$temp_file" 2>/dev/null
        ssh_cmd "$adb_cmd push $temp_file \"$remote_file\"" 2>/dev/null
        ssh_cmd "rm -f $temp_file" 2>/dev/null || true
    fi

    echo "Pushed: $local_file -> $remote_file"
}

# Handle 'install' command
handle_install() {
    local adb_cmd=$(build_adb_cmd)
    local local_apk=""
    local other_args=()

    # Find the APK file among arguments
    for arg in "$@"; do
        if [[ "$arg" == *.apk ]]; then
            local_apk="$arg"
        else
            other_args+=("$arg")
        fi
    done

    if [[ -z "$local_apk" ]]; then
        # No APK found, just passthrough
        handle_passthrough "install" "$@"
        return
    fi

    if [[ ! -f "$local_apk" ]]; then
        error "Local APK not found: $local_apk"
    fi

    info "Uploading APK for installation: $local_apk"
    
    if [[ "$REMOTE_PLATFORM" == "windows" ]]; then
        local win_temp=$(get_win_temp)
        local temp_file="adb_install_$$.apk"

        # SCP to Windows temp
        scp_cmd "$local_apk" "$REMOTE_USER@$REMOTE_HOST:${win_temp}/$temp_file" 2>/dev/null

        # Install on device
        ssh_cmd "$adb_cmd install ${other_args[*]} %TEMP%\\$temp_file"

        # Cleanup
        ssh_cmd "del %TEMP%\\$temp_file" 2>/dev/null || true
    else
        # Linux remote
        local temp_file="/tmp/adb_install_$$.apk"

        scp_cmd "$local_apk" "$REMOTE_USER@$REMOTE_HOST:$temp_file" 2>/dev/null
        ssh_cmd "$adb_cmd install ${other_args[*]} $temp_file"
        ssh_cmd "rm -f $temp_file" 2>/dev/null || true
    fi
}

# Handle other commands (passthrough)
handle_passthrough() {
    local adb_cmd=$(build_adb_cmd)
    ssh_cmd "$adb_cmd $*"
}

# Main
main() {
    if [[ $# -eq 0 ]]; then
        echo "Usage: $(basename "$0") [adb-command] [args...]"
        echo ""
        echo "Environment variables:"
        echo "  ADB_PROFILE  - Profile name to use (default: last_used_profile)"
        echo ""
        echo "Examples:"
        echo "  $(basename "$0") devices"
        echo "  $(basename "$0") shell getprop ro.build.fingerprint"
        echo "  $(basename "$0") pull /sdcard/test.txt ./"
        echo "  $(basename "$0") push ./file.apk /sdcard/"
        echo "  $(basename "$0") logcat -d"
        exit 0
    fi

    load_config

    local cmd="$1"
    shift

    case "$cmd" in
        pull)
            handle_pull "$@"
            ;;
        push)
            handle_push "$@"
            ;;
        install)
            handle_install "$@"
            ;;
        *)
            handle_passthrough "$cmd" "$@"
            ;;
    esac
}

main "$@"
