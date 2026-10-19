#!/usr/bin/env python3
"""
Fast PTY-compatible sync tool for Raspberry Pi.
Packages the entire robot code and dependencies into a compressed archive
and transfers it over SSH via base64 stream into ~/projects/Robot/robot/.
"""

import os
import sys
import io
import time
import base64
import tarfile
from pathlib import Path
import paramiko

PI_HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PI_HOST", "172.23.30.92")
PI_USER = "pi"
LOCAL_DIR = Path(__file__).resolve().parent
ROBOT_DIR = LOCAL_DIR / "robot"
REMOTE_TARGET = "/home/pi/projects/Robot/robot"

def build_tar_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # 1. Add all robot files
        for root, dirs, files in os.walk(ROBOT_DIR):
            dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git", "logs", ".pytest_cache"]]
            for f in files:
                if f.endswith(".pyc") or f.endswith(".log"):
                    continue
                full_path = Path(root) / f
                arc_name = full_path.relative_to(ROBOT_DIR).as_posix()
                tar.add(str(full_path), arcname=arc_name)

        # 2. Add root support scripts
        extras = [
            "PROTOCOL.md",
            "live_stream.py",
            "test_wifi_comm.py",
            "deploy_esp_ota.py",
            "pin_test.py",
            "test_individual_motor.py",
            "test_leg_control.py",
            "build_and_flash_ota.py",
            "test_camera.py",
            "track_balls.py",
            "train_neck_tracking.py",
            "run_arm_pose.py",
            "neck_calibration.json",
        ]
        for extra in extras:
            p = LOCAL_DIR / extra
            if p.exists():
                tar.add(str(p), arcname=extra)

        # 3. Add compiled ESP firmware binaries
        for i in (1, 2, 3, 4):
            bin_file = LOCAL_DIR / "build_esp32" / f"esp32_robot_{i}.bin"
            if bin_file.exists():
                tar.add(str(bin_file), arcname=f"esp32_robot_{i}.bin")

        # 4. Add espota tool
        espota_src = Path(r"C:\Users\YOHANNA\.gemini\antigravity-ide\brain\9c03f519-8273-4be3-aec7-924f8dab2d12\scratch\espota_fixed.py")
        if espota_src.exists():
            tar.add(str(espota_src), arcname="espota.py")

    return buf.getvalue()

def main():
    print("=" * 65)
    print(f"  DEPLOYING ROBOT CODE TO RASPBERRY PI ({PI_HOST})")
    print("=" * 65)

    print("[*] Creating compressed tar.gz bundle...")
    raw_bundle = build_tar_archive()
    print(f"[*] Compressed bundle size: {len(raw_bundle):,} bytes")

    b64_data = base64.b64encode(raw_bundle).decode("ascii")
    print(f"[*] Base64 payload size: {len(b64_data):,} characters")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"[*] Connecting to {PI_USER}@{PI_HOST} via SSH...")
    try:
        client.connect(PI_HOST, username=PI_USER, timeout=20)
    except Exception:
        client.connect(PI_HOST, username=PI_USER, password="00000000", timeout=20)
    print("[OK] Authenticated successfully!")

    # 1. Ensure target directory exists and clear tmp
    client.exec_command(f"mkdir -p {REMOTE_TARGET}", get_pty=True)
    client.exec_command("rm -f /tmp/bundle.b64", get_pty=True)

    # 2. Upload chunks
    chunk_size = 16384  # 16 KB per command
    total_chunks = (len(b64_data) + chunk_size - 1) // chunk_size
    print(f"[*] Streaming payload in {total_chunks} chunks...")

    start_t = time.time()
    for i in range(total_chunks):
        chunk = b64_data[i * chunk_size : (i + 1) * chunk_size]
        _, stdout, _ = client.exec_command(f"printf '%s' '{chunk}' >> /tmp/bundle.b64", get_pty=True)
        stdout.read()
        percent = int(((i + 1) / total_chunks) * 100)
        sys.stdout.write(f"\r  [Transfer] Chunk {i + 1}/{total_chunks} ({percent}%)")
        sys.stdout.flush()

    elapsed = time.time() - start_t
    print(f"\n[OK] Transfer completed in {elapsed:.2f} seconds.")

    # 3. Decode and extract on Pi
    print(f"[*] Extracting archive into {REMOTE_TARGET}...")
    extract_cmd = (
        f"base64 -d /tmp/bundle.b64 | tar -xzf - -C {REMOTE_TARGET} && "
        f"chmod +x {REMOTE_TARGET}/main.py {REMOTE_TARGET}/test_wifi_comm.py 2>/dev/null && "
        "rm -f /tmp/bundle.b64"
    )
    _, stdout, stderr = client.exec_command(extract_cmd, get_pty=True)
    out = stdout.read().decode()
    if out.strip():
        print(f"  [Output]: {out.strip()}")

    # 4. Verify transferred files
    print("\n[*] Verifying installed files on Raspberry Pi:")
    verify_cmd = (
        f"ls -lh {REMOTE_TARGET}/communication/wifi_bus.py "
        f"{REMOTE_TARGET}/test_wifi_comm.py "
        f"{REMOTE_TARGET}/config.py "
        f"{REMOTE_TARGET}/main.py"
    )
    _, stdout, _ = client.exec_command(verify_cmd, get_pty=True)
    print(stdout.read().decode())

    # Also sync into ~/robot as fallback
    client.exec_command(f"cp -r {REMOTE_TARGET}/* /home/pi/robot/ 2>/dev/null", get_pty=True)

    client.close()
    print("=" * 65)
    print(">>> CODE SUCCESSFULLY DEPLOYED TO RASPBERRY PI! <<<")
    print("=" * 65)

if __name__ == "__main__":
    main()
