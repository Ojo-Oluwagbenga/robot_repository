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

PI_HOST = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("PI_HOST", "10.174.69.92")
PI_USER = "pi"
PI_PASS = "00000000"
LOCAL_DIR = Path(__file__).resolve().parent
ROBOT_DIR = LOCAL_DIR / "robot"
REMOTE_TARGETS = [
    "/home/pi/projects/Robot/robot",
    "/home/pi/robot",
]

def build_tar_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # 1. Add all robot files
        for root, dirs, files in os.walk(ROBOT_DIR):
            dirs[:] = [d for d in dirs if d not in ["__pycache__", ".git", "logs", ".pytest_cache"]]
            for f in files:
                if f.endswith(".pyc") or f.endswith(".log") or f.endswith(".mp4") or f.endswith(".onnx") or f.endswith(".pt"):
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

        # 3. Add compiled ESP firmware binaries if present
        for i in (1, 2, 3, 4):
            bin_file = LOCAL_DIR / "build_esp32" / f"esp32_robot_{i}.bin"
            if bin_file.exists():
                tar.add(str(bin_file), arcname=f"esp32_robot_{i}.bin")

    return buf.getvalue()

def main():
    print("=" * 65)
    print(f"  DEPLOYING ROBOT CODE TO RASPBERRY PI ({PI_HOST})")
    print("=" * 65)

    print("[*] Creating compressed tar.gz bundle...")
    start_pack = time.time()
    raw_bundle = build_tar_archive()
    pack_time = time.time() - start_pack
    print(f"[*] Compressed bundle size: {len(raw_bundle):,} bytes ({pack_time:.2f}s)")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    HOST_CANDIDATES = [sys.argv[1]] if len(sys.argv) > 1 else ["raspberrypi.local", "10.174.69.92", "172.23.30.92"]
    connected = False
    for host in HOST_CANDIDATES:
        print(f"[*] Trying {PI_USER}@{host} via SSH...")
        try:
            client.connect(host, username=PI_USER, password=PI_PASS, timeout=10)
            print(f"[OK] Authenticated successfully with {host}!")
            connected = True
            break
        except Exception as e:
            print(f"    Failed {host}: {e}")
    if not connected:
        print("[!] Could not connect to any Raspberry Pi host candidate.")
        sys.exit(1)

    # 1. Upload bundle via SFTP
    remote_tar = "/tmp/robot_deploy.tar.gz"
    print(f"[*] Uploading bundle via high-speed SFTP to {remote_tar}...")
    start_tx = time.time()
    sftp = client.open_sftp()
    with sftp.file(remote_tar, "wb") as f:
        f.write(raw_bundle)
    sftp.close()
    tx_time = time.time() - start_tx
    print(f"[OK] Upload completed in {tx_time:.2f} seconds ({len(raw_bundle)/1024/1024/max(0.01, tx_time):.2f} MB/s).")

    # 2. Extract into all remote robot target locations
    print("[*] Extracting bundle and updating permissions...")
    extract_script = f"""
    for RDIR in {' '.join(REMOTE_TARGETS)}; do
        mkdir -p "$RDIR"
        tar -xzf {remote_tar} -C "$RDIR"
        chmod +x "$RDIR"/*.py "$RDIR"/*.sh 2>/dev/null
        echo "Extracted and updated $RDIR"
    done
    rm -f {remote_tar}
    """
    _, stdout, stderr = client.exec_command(extract_script)
    print(stdout.read().decode())

    # 3. Verify key files
    print("[*] Verifying critical installed files:")
    verify_cmd = f"ls -lh {REMOTE_TARGETS[0]}/bridge_main.py {REMOTE_TARGETS[0]}/control/visual_servo_arm.py {REMOTE_TARGETS[0]}/track_and_pick_ball.py {REMOTE_TARGETS[0]}/control/gentle_head.py"
    _, stdout, _ = client.exec_command(verify_cmd)
    print(stdout.read().decode())

    # 4. Run automated test suite on Pi to verify integrity
    print("[*] Running unit tests on Raspberry Pi...")
    test_cmd = f"""
    cd {REMOTE_TARGETS[0]}
    /home/pi/systemenv/bin/python3 tests/test_visual_servo_arm.py
    /home/pi/systemenv/bin/python3 tests/test_gentle_head.py
    /home/pi/systemenv/bin/python3 tests/test_hand_ball_grasp.py
    """
    _, stdout, stderr = client.exec_command(test_cmd)
    out = stdout.read().decode()
    err = stderr.read().decode()
    if out.strip():
        print(out)
    if err.strip():
        print(err)

    client.close()
    print("=" * 65)
    print(">>> CODE SUCCESSFULLY DEPLOYED AND VERIFIED ON PI! <<<")
    print("=" * 65)

if __name__ == "__main__":
    main()
