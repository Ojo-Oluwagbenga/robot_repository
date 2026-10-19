#!/usr/bin/env python3
"""
Automated Deployment / Sync Script to Raspberry Pi 5.
Transfers updated robot code, protocol definitions, and firmware to the Raspberry Pi.
Automatically installs the local SSH public key so future syncs are passwordless.
"""

import os
import sys
import getpass
import argparse
import paramiko
from pathlib import Path

# ==============================================================================
# CONFIGURATION
# ==============================================================================
DEFAULT_HOST = os.environ.get("PI_HOST", "raspberrypi.local")  # or "10.207.12.92"
DEFAULT_USER = os.environ.get("PI_USER", "pi")
DEFAULT_REMOTE_DIR = "projects/Robot/robot"  # relative to remote home directory (~/projects/Robot/robot)

LOCAL_PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_ROBOT_DIR = LOCAL_PROJECT_ROOT / "robot"
SSH_PUBKEY_PATH = Path.home() / ".ssh" / "id_ed25519.pub"
SSH_PRIVKEY_PATH = Path.home() / ".ssh" / "id_ed25519"

EXCLUDED_NAMES = {"__pycache__", ".pytest_cache", ".git", ".DS_Store", "venv", ".venv"}
EXCLUDED_EXTS = {".pyc", ".pyo", ".log"}


def sftp_mkdir_p(sftp, remote_dir):
    """Recursively create directories on the remote machine."""
    parts = remote_dir.replace("\\", "/").strip("/").split("/")
    current = ""
    for part in parts:
        current += "/" + part
        try:
            sftp.stat(current)
        except IOError:
            try:
                sftp.mkdir(current)
            except Exception:
                pass


def upload_directory(sftp, local_dir, remote_dir):
    """Recursively upload a local directory to remote directory via SFTP."""
    sftp_mkdir_p(sftp, remote_dir)
    count = 0

    for root, dirs, files in os.walk(local_dir):
        # Filter out excluded directories in-place
        dirs[:] = [d for d in dirs if d not in EXCLUDED_NAMES]

        rel_path = Path(root).relative_to(local_dir)
        target_remote_dir = (Path(remote_dir) / rel_path).as_posix()
        sftp_mkdir_p(sftp, target_remote_dir)

        for f in files:
            if any(f.endswith(ext) for ext in EXCLUDED_EXTS) or f in EXCLUDED_NAMES:
                continue

            local_file = Path(root) / f
            remote_file = (Path(target_remote_dir) / f).as_posix()

            sftp.put(str(local_file), remote_file)
            print(f"  [->] {local_file.relative_to(LOCAL_PROJECT_ROOT)} => {remote_file}")
            count += 1

    return count


def upload_single_file(sftp, local_file, remote_file):
    """Upload a single file to a specific remote path."""
    remote_parent = Path(remote_file).parent.as_posix()
    sftp_mkdir_p(sftp, remote_parent)
    sftp.put(str(local_file), remote_file.as_posix())
    print(f"  [->] {local_file.name} => {remote_file.as_posix()}")


def setup_ssh_key(client):
    """Append local public key to remote ~/.ssh/authorized_keys if not already present."""
    if not SSH_PUBKEY_PATH.exists():
        return

    try:
        with open(SSH_PUBKEY_PATH, "r") as f:
            pub_key = f.read().strip()

        if not pub_key:
            return

        cmd = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"grep -q '{pub_key}' ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo '{pub_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
        )
        _, stdout, stderr = client.exec_command(cmd)
        stdout.channel.recv_exit_status()
        print("[OK] Local SSH public key installed on Pi (future syncs will be passwordless).")
    except Exception as e:
        print(f"[!] Note: Could not auto-install SSH key: {e}")


def main():
    print("=" * 65)
    print("      RASPBERRY PI CODE DEPLOYMENT / SYNC")
    print("=" * 65)

    parser = argparse.ArgumentParser(description="Deploy robot code to Raspberry Pi")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Raspberry Pi hostname or IP")
    parser.add_argument("--user", default=DEFAULT_USER, help="Raspberry Pi username")
    parser.add_argument("--password", "-p", default=None, help="Raspberry Pi password")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive mode")
    args, unknown = parser.parse_known_args()

    non_interactive = args.yes or not sys.stdin.isatty()
    if non_interactive:
        host = args.host
        user = args.user
    else:
        host = input(f"Enter Pi Hostname / IP [{args.host}]: ").strip() or args.host
        user = input(f"Enter Pi Username [{args.user}]: ").strip() or args.user

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # 1. Attempt key-based connection first
    connected = False
    if SSH_PRIVKEY_PATH.exists():
        try:
            print(f"[*] Trying key authentication to {user}@{host}...")
            client.connect(host, username=user, key_filename=str(SSH_PRIVKEY_PATH), timeout=4)
            connected = True
            print("[OK] Authenticated using local SSH key!")
        except Exception:
            pass

    # 2. Fall back to password prompt if key didn't connect
    if not connected:
        default_pwd = args.password or "00000000"
        pwd = default_pwd if (non_interactive or args.password) else (getpass.getpass(f"Enter password for {user}@{host}: ") or default_pwd)
        try:
            print(f"[*] Connecting to {user}@{host} with password...")
            client.connect(host, username=user, password=pwd, timeout=6)
            connected = True
            print("[OK] Connected successfully!")
            # Install public key for next time
            setup_ssh_key(client)
        except Exception as e:
            print(f"[ERROR] Failed to connect: {e}")
            sys.exit(1)

    # 3. Determine remote home directory
    stdin, stdout, stderr = client.exec_command("echo $HOME")
    remote_home = stdout.read().decode().strip() or f"/home/{user}"
    target_dirs = [f"{remote_home}/{DEFAULT_REMOTE_DIR}", f"{remote_home}/robot"]

    sftp = client.open_sftp()
    try:
        for target_robot_dir in target_dirs:
            print(f"\n[*] Target directory on Pi: {target_robot_dir}")
            print("[*] Uploading files...")

            # Upload robot/ package
            file_count = upload_directory(sftp, LOCAL_ROBOT_DIR, target_robot_dir)

            # Upload root PROTOCOL.md
            protocol_file = LOCAL_PROJECT_ROOT / "PROTOCOL.md"
            if protocol_file.exists():
                upload_single_file(sftp, protocol_file, Path(target_robot_dir) / "PROTOCOL.md")
                file_count += 1

            # Upload pin_test.py
            pin_test_file = LOCAL_PROJECT_ROOT / "pin_test.py"
            if pin_test_file.exists():
                upload_single_file(sftp, pin_test_file, Path(target_robot_dir) / "pin_test.py")
                file_count += 1

            # Upload test_motor_pi.py
            motor_test_file = LOCAL_PROJECT_ROOT / "test_motor_pi.py"
            if motor_test_file.exists():
                upload_single_file(sftp, motor_test_file, Path(target_robot_dir) / "test_motor_pi.py")
                file_count += 1

            # Upload diagnose_encoder.py
            diag_file = LOCAL_PROJECT_ROOT / "diagnose_encoder.py"
            if diag_file.exists():
                upload_single_file(sftp, diag_file, Path(target_robot_dir) / "diagnose_encoder.py")
                file_count += 1

            # Upload test_wifi_comm.py
            wifi_test_file = LOCAL_PROJECT_ROOT / "test_wifi_comm.py"
            if wifi_test_file.exists():
                upload_single_file(sftp, wifi_test_file, Path(target_robot_dir) / "test_wifi_comm.py")
                file_count += 1

            # Upload deploy_esp_ota.py
            ota_file = LOCAL_PROJECT_ROOT / "deploy_esp_ota.py"
            if ota_file.exists():
                upload_single_file(sftp, ota_file, Path(target_robot_dir) / "deploy_esp_ota.py")
                file_count += 1

            # Upload neck training and tracking files
            for neck_extra in ["train_neck_tracking.py", "track_neck_ball.py", "neck_calibration.json"]:
                ne_file = LOCAL_PROJECT_ROOT / neck_extra
                if ne_file.exists():
                    upload_single_file(sftp, ne_file, Path(target_robot_dir) / neck_extra)
                    file_count += 1

            print(f"[SUCCESS] Uploaded {file_count} files to {user}@{host}:{target_robot_dir}!")

            # Make main.py executable
            client.exec_command(f"chmod +x {target_robot_dir}/main.py")
            print(f"[OK] Ensured {target_robot_dir}/main.py has execute permissions.")

    finally:
        sftp.close()
        client.close()

    print("\n" + "=" * 65)
    print("Deployment complete! To run on the Pi:")
    print(f"  ssh {user}@{host}")
    print(f"  cd {DEFAULT_REMOTE_DIR}")
    print(f"  python3 main.py N2_20")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
