#!/usr/bin/env python3
"""
Deploy and Set Up Hand Tracker on Raspberry Pi 5.
=============================================================================
Bundles the hand_tracker directory, transfers it over SFTP, extracts it to
/home/pi/hand_tracker, sets up the virtual environment, installs dependencies,
and validates the pipeline with a test frame.
"""

import io
import os
import sys
import time
import tarfile
from pathlib import Path
import paramiko

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PI_HOST = os.environ.get("PI_HOST", "10.208.131.92")  # raspberrypi.local
PI_USER = os.environ.get("PI_USER", "pi")
PI_PASS = os.environ.get("PI_PASS", "00000000")

LOCAL_ROOT = Path(__file__).resolve().parent
LOCAL_HAND_TRACKER = LOCAL_ROOT / "hand_tracker"
REMOTE_DIR = "/home/pi/hand_tracker"
REMOTE_TAR = "/tmp/hand_tracker.tar.gz"

EXCLUDES = {"__pycache__", ".venv", "venv", ".git", ".pytest_cache"}


def make_tar_gz() -> bytes:
    """Pack hand_tracker into in-memory tar.gz bundle."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(LOCAL_HAND_TRACKER):
            dirs[:] = [d for d in dirs if d not in EXCLUDES]
            for f in files:
                if f.endswith((".pyc", ".pyo", ".tmp")):
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(LOCAL_HAND_TRACKER).as_posix()
                tar.add(str(full_path), arcname=rel_path)
    return buf.getvalue()


def run_remote_cmd(client, cmd, title=None, check=True):
    """Executes a command on the Pi and streams output."""
    if title:
        print(f"\n[*] {title}...")
    stdin, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out_lines = []
    for line in iter(stdout.readline, ""):
        safe_line = line.rstrip().encode("ascii", errors="replace").decode("ascii")
        print(f"    {safe_line}")
        out_lines.append(line)
    exit_status = stdout.channel.recv_exit_status()
    if check and exit_status != 0:
        raise RuntimeError(f"Command failed with exit code {exit_status}: {cmd}")
    return exit_status, "".join(out_lines)


def main():
    print("=" * 68)
    print("      DEPLOYING & SETTING UP HAND TRACKER ON RASPBERRY PI 5")
    print("=" * 68)
    print(f"  * Pi Host       : {PI_HOST}")
    print(f"  * Pi User       : {PI_USER}")
    print(f"  * Source Folder : {LOCAL_HAND_TRACKER}")
    print(f"  * Destination   : {REMOTE_DIR}")
    print("-" * 68)

    # 1. Archive
    print("[1/5] Building compressed archive of hand_tracker...")
    archive_bytes = make_tar_gz()
    print(f"      Bundle created ({len(archive_bytes) / 1024 / 1024:.2f} MB)")

    # 2. Connect
    print(f"[2/5] Connecting to {PI_USER}@{PI_HOST} via SSH...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(PI_HOST, username=PI_USER, timeout=10)
    except Exception:
        client.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=10)
    print("      Authenticated successfully!")

    # 3. SFTP Upload & Extract
    print(f"[3/5] Transferring archive to {REMOTE_TAR} via SFTP...")
    sftp = client.open_sftp()
    with sftp.file(REMOTE_TAR, "wb") as remote_file:
        remote_file.write(archive_bytes)
    sftp.close()

    print(f"      Extracting archive into {REMOTE_DIR}...")
    extract_cmd = (
        f"mkdir -p {REMOTE_DIR} && "
        f"tar -xzf {REMOTE_TAR} -C {REMOTE_DIR} && "
        f"chmod +x {REMOTE_DIR}/*.py {REMOTE_DIR}/*.sh {REMOTE_DIR}/tools/*.py && "
        f"rm -f {REMOTE_TAR}"
    )
    run_remote_cmd(client, extract_cmd)
    print("      Files extracted and permissions updated.")

    # 4. Virtual Environment & Dependencies
    print(f"[4/5] Setting up Python virtual environment and dependencies...")
    setup_cmd = (
        f"cd {REMOTE_DIR} && "
        f"python3 -m venv .venv && "
        f". .venv/bin/activate && "
        f"pip install --no-input --progress-bar off --upgrade pip setuptools wheel && "
        f"pip install --no-input --progress-bar off -r requirements-pi.txt"
    )
    run_remote_cmd(client, setup_cmd, title="Installing python wheels in .venv")

    # 5. Validation Test on Pi
    print(f"[5/5] Running validation test with sample video on Pi...")
    test_cmd = (
        f"cd {REMOTE_DIR} && "
        ". .venv/bin/activate && "
        'python3 -c "import cv2, numpy as np, onnxruntime as ort; '
        'print(\'OpenCV:\', cv2.__version__, \'| ONNX Runtime:\', ort.__version__)"'
    )
    run_remote_cmd(client, test_cmd, title="Checking imported libraries")

    tracker_smoke_cmd = (
        f"cd {REMOTE_DIR} && "
        f". .venv/bin/activate && "
        f"python3 track_hand.py --source samples/test_stereo.mp4 --no-window --save /tmp/test_out.mp4"
    )
    run_remote_cmd(client, tracker_smoke_cmd, title="Smoke testing track_hand.py on sample video (90 frames)")

    print("\n" + "=" * 68)
    print("  SUCCESS! HAND TRACKER IS FULLY DEPLOYED AND READY ON THE PI")
    print("=" * 68)
    print(f"  To run live tracking with camera on the Pi:")
    print(f"    ssh {PI_USER}@{PI_HOST}")
    print(f"    cd {REMOTE_DIR}")
    print(f"    . .venv/bin/activate")
    print(f"    python3 track_hand.py --web")
    print(f"  Then open: http://{PI_HOST}:8080 in your browser.")
    print("=" * 68 + "\n")

    client.close()


if __name__ == "__main__":
    main()
