#!/usr/bin/env python3
"""
Rockchip ADB Remote Helper Script

Provides utilities for:
- Configuration management (read/write ~/.openclaw/workspace/skills/rk-adb/config/config.json)
- SSH connection testing
- Remote ADB device discovery
- Remote ADB command execution

Usage:
    # Test SSH connection
    python3 adb_helper.py test-ssh --host 172.16.21.200 --user admin --password "password"

    # Discover ADB devices
    python3 adb_helper.py discover-devices --host 172.16.21.200 --user admin --password "password"

    # Test ADB connection
    python3 adb_helper.py test-adb --host 172.16.21.200 --user admin --password "password"

    # Execute remote ADB command
    python3 adb_helper.py exec --host 172.16.21.200 --user admin --password "password" --cmd "shell getprop ro.build.fingerprint"

    # Save profile
    python3 adb_helper.py save-profile --name "windows-lab" --host 172.16.21.200 --user admin --password "password" --platform windows

    # List profiles
    python3 adb_helper.py list-profiles
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Configuration file path (in skill directory)
SKILL_DIR = Path(__file__).parent.parent
CONFIG_DIR = SKILL_DIR / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load configuration from file."""
    if not CONFIG_FILE.exists():
        return {"last_used_profile": None, "profiles": {}}

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Save configuration to file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"Configuration saved to {CONFIG_FILE}")


def test_ssh(host: str, user: str, password: str, timeout: int = 10) -> bool:
    """Test SSH connection to remote host."""
    cmd = [
        'sshpass', '-p', password,
        'ssh', '-o', 'StrictHostKeyChecking=no', '-o', f'ConnectTimeout={timeout}',
        f'{user}@{host}', 'echo connected'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        if 'connected' in result.stdout:
            print(f"✓ SSH connection successful: {user}@{host}")
            return True
        else:
            print(f"✗ SSH connection failed: {result.stderr or result.stdout}")
            print("\n[Tip] If connecting to Windows, Firewall might be blocking port 22.")
            print("      Try disabling firewall temporarily via PowerShell (Admin):")
            print("      Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False")
            return False
    except subprocess.TimeoutExpired:
        print(f"✗ SSH connection timeout after {timeout}s")
        print("\n[Tip] If connecting to Windows, Firewall might be blocking port 22.")
        print("      Try disabling firewall temporarily via PowerShell (Admin):")
        print("      Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled False")
        return False
    except FileNotFoundError:
        print("✗ sshpass not found. Install with: sudo apt install sshpass")
        return False


def discover_devices(host: str, user: str, password: str) -> list:
    """Discover ADB devices on remote host."""
    cmd = [
        'sshpass', '-p', password,
        'ssh', '-o', 'StrictHostKeyChecking=no',
        f'{user}@{host}',
        'adb devices -l'
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        devices = []
        for line in result.stdout.strip().split('\n'):
            # Skip header line and empty lines
            if line.startswith('List of devices') or not line.strip():
                continue
            # Parse device line: "serial_or_ip  state  extra_info"
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ['device', 'offline', 'unauthorized']:
                device_info = {
                    'serial': parts[0],
                    'state': parts[1],
                    'info': ' '.join(parts[2:]) if len(parts) > 2 else ''
                }
                devices.append(device_info)

        if devices:
            print(f"Found {len(devices)} ADB device(s) on {host}:")
            for d in devices:
                status = "✓" if d['state'] == 'device' else "⚠"
                print(f"  {status} {d['serial']} ({d['state']}) {d['info']}")
        else:
            print(f"No ADB devices found on {host}")
            print("  Make sure:")
            print("  - Android device is connected via USB")
            print("  - USB debugging is enabled on the device")
            print("  - ADB is installed and in PATH on remote PC")

        return devices
    except subprocess.TimeoutExpired:
        print("✗ Command timeout")
        return []
    except Exception as e:
        print(f"Error discovering devices: {e}")
        return []


def test_adb(host: str, user: str, password: str, serial: str = None) -> bool:
    """Test ADB connection on remote host."""
    serial_arg = f"-s {serial}" if serial else ""
    cmd = [
        'sshpass', '-p', password,
        'ssh', '-o', 'StrictHostKeyChecking=no',
        f'{user}@{host}',
        f'adb {serial_arg} shell getprop ro.build.fingerprint'.strip()
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        output = result.stdout.strip()

        if output and not output.startswith('error:'):
            print(f"✓ ADB connection successful")
            print(f"  Device fingerprint: {output}")
            return True
        else:
            print(f"✗ ADB connection failed: {result.stderr or output}")
            return False
    except subprocess.TimeoutExpired:
        print("✗ ADB command timeout")
        return False
    except Exception as e:
        print(f"✗ Error testing ADB: {e}")
        return False


def exec_adb(host: str, user: str, password: str, adb_cmd: str, serial: str = None, timeout: int = 30) -> str:
    """Execute ADB command on remote host."""
    serial_arg = f"-s {serial}" if serial else ""
    cmd = [
        'sshpass', '-p', password,
        'ssh', '-o', 'StrictHostKeyChecking=no',
        f'{user}@{host}',
        f'adb {serial_arg} {adb_cmd}'.strip()
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        # Use errors='replace' to handle Windows encoding issues
        stdout = result.stdout.decode('utf-8', errors='replace')
        stderr = result.stderr.decode('utf-8', errors='replace')
        output = stdout + stderr
        print(output)
        return output
    except subprocess.TimeoutExpired:
        print(f"✗ Command timeout after {timeout}s")
        return ""
    except Exception as e:
        print(f"✗ Error executing ADB command: {e}")
        return ""


def save_profile(name: str, host: str, user: str, password: str, platform: str,
                 serial: str = None) -> None:
    """Save a connection profile."""
    config = load_config()

    profile = {
        "platform": platform,
        "host": host,
        "username": user,
        "password": password,
        "last_success": datetime.now().isoformat()
    }

    # Save serial if specified
    if serial:
        profile["device_serial"] = serial

    config["profiles"][name] = profile
    config["last_used_profile"] = name

    save_config(config)
    print(f"Profile '{name}' saved successfully")


def list_profiles() -> None:
    """List all saved profiles."""
    config = load_config()
    profiles = config.get("profiles", {})

    if not profiles:
        print("No saved profiles found.")
        print(f"Configuration file location: {CONFIG_FILE}")
        return

    print(f"Saved profiles ({CONFIG_FILE}):")
    print("-" * 60)

    last_used = config.get("last_used_profile")

    for name, profile in profiles.items():
        marker = " (last used)" if name == last_used else ""
        platform = profile.get("platform", "unknown")
        host = profile.get("host", "")
        user = profile.get("username", "")
        serial = profile.get("device_serial", "auto-detect")

        print(f"\n[{name}]{marker}")
        print(f"  Platform: {platform}")
        print(f"  Host: {user}@{host}")
        print(f"  Device Serial: {serial}")
        if profile.get("last_success"):
            print(f"  Last Success: {profile.get('last_success')}")


def get_profile(name: str = None) -> dict:
    """Get a profile by name, or the last used profile."""
    config = load_config()

    if name is None:
        name = config.get("last_used_profile")

    if name is None:
        return None

    return config.get("profiles", {}).get(name)


def generate_wrapper(profile_name: str, output_path: str = None) -> None:
    """Generate a standalone adb wrapper script with embedded profile."""
    profile = get_profile(profile_name)
    if not profile:
        print(f"✗ Profile '{profile_name}' not found")
        sys.exit(1)

    # Get script directory for wrapper template
    script_dir = Path(__file__).parent
    wrapper_template = script_dir / "adb_wrapper.sh"

    if not wrapper_template.exists():
        print(f"✗ Wrapper template not found: {wrapper_template}")
        sys.exit(1)

    # Determine output path
    if output_path is None:
        output_path = Path.cwd() / "adb"
    else:
        output_path = Path(output_path)

    # Read template
    with open(wrapper_template, 'r') as f:
        wrapper_content = f.read()

    # Write to output
    with open(output_path, 'w') as f:
        f.write(wrapper_content)

    # Make executable
    os.chmod(output_path, 0o755)

    # Update config to set this profile as last used
    config = load_config()
    config["last_used_profile"] = profile_name
    save_config(config)

    print(f"✓ ADB wrapper generated: {output_path}")
    print(f"  Using profile: {profile_name}")
    print(f"  Remote: {profile.get('username')}@{profile.get('host')} ({profile.get('platform')})")
    print(f"\nUsage:")
    print(f"  {output_path} devices")
    print(f"  {output_path} shell getprop ro.build.fingerprint")
    print(f"  {output_path} pull /sdcard/test.txt ./")
    print(f"  {output_path} push ./file.apk /sdcard/")


def main():
    parser = argparse.ArgumentParser(description='Rockchip ADB Remote Helper')
    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # test-ssh command
    ssh_parser = subparsers.add_parser('test-ssh', help='Test SSH connection')
    ssh_parser.add_argument('--host', required=True, help='Remote host IP')
    ssh_parser.add_argument('--user', required=True, help='SSH username')
    ssh_parser.add_argument('--password', required=True, help='SSH password')

    # discover-devices command
    discover_parser = subparsers.add_parser('discover-devices', help='Discover ADB devices')
    discover_parser.add_argument('--host', required=True, help='Remote host IP')
    discover_parser.add_argument('--user', required=True, help='SSH username')
    discover_parser.add_argument('--password', required=True, help='SSH password')

    # test-adb command
    adb_parser = subparsers.add_parser('test-adb', help='Test ADB connection')
    adb_parser.add_argument('--host', required=True, help='Remote host IP')
    adb_parser.add_argument('--user', required=True, help='SSH username')
    adb_parser.add_argument('--password', required=True, help='SSH password')
    adb_parser.add_argument('--serial', help='Device serial (optional, for multi-device)')

    # exec command
    exec_parser = subparsers.add_parser('exec', help='Execute remote ADB command')
    exec_parser.add_argument('--host', required=True, help='Remote host IP')
    exec_parser.add_argument('--user', required=True, help='SSH username')
    exec_parser.add_argument('--password', required=True, help='SSH password')
    exec_parser.add_argument('--cmd', required=True, help='ADB command (without "adb" prefix)')
    exec_parser.add_argument('--serial', help='Device serial (optional)')
    exec_parser.add_argument('--timeout', type=int, default=30, help='Command timeout in seconds')

    # save-profile command
    save_parser = subparsers.add_parser('save-profile', help='Save a connection profile')
    save_parser.add_argument('--name', required=True, help='Profile name')
    save_parser.add_argument('--host', required=True, help='Remote host IP')
    save_parser.add_argument('--user', required=True, help='SSH username')
    save_parser.add_argument('--password', required=True, help='SSH password')
    save_parser.add_argument('--platform', required=True, choices=['linux', 'windows'], help='Remote platform')
    save_parser.add_argument('--serial', help='Device serial (optional)')

    # list-profiles command
    subparsers.add_parser('list-profiles', help='List saved profiles')

    # get-profile command
    get_parser = subparsers.add_parser('get-profile', help='Get profile details')
    get_parser.add_argument('--name', help='Profile name (default: last used)')

    # generate-wrapper command
    wrapper_parser = subparsers.add_parser('generate-wrapper', help='Generate standalone adb wrapper script')
    wrapper_parser.add_argument('--profile', required=True, help='Profile name to use')
    wrapper_parser.add_argument('--output', help='Output path (default: ./adb)')

    args = parser.parse_args()

    if args.command == 'test-ssh':
        success = test_ssh(args.host, args.user, args.password)
        sys.exit(0 if success else 1)

    elif args.command == 'discover-devices':
        devices = discover_devices(args.host, args.user, args.password)
        sys.exit(0 if devices else 1)

    elif args.command == 'test-adb':
        success = test_adb(args.host, args.user, args.password, args.serial)
        sys.exit(0 if success else 1)

    elif args.command == 'exec':
        output = exec_adb(args.host, args.user, args.password, args.cmd, args.serial, args.timeout)
        sys.exit(0 if output else 1)

    elif args.command == 'save-profile':
        save_profile(args.name, args.host, args.user, args.password, args.platform, args.serial)

    elif args.command == 'list-profiles':
        list_profiles()

    elif args.command == 'get-profile':
        profile = get_profile(args.name)
        if profile:
            print(json.dumps(profile, indent=2, ensure_ascii=False))
        else:
            print("Profile not found")
            sys.exit(1)

    elif args.command == 'generate-wrapper':
        generate_wrapper(args.profile, args.output)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
