"""
Sync Hand Tracker and Merged Hand-Ball Tracker to Raspberry Pi 5.
"""

import io
import os
import tarfile
from pathlib import Path
import paramiko

PI_HOST = "10.208.131.92"
PI_USER = "pi"
PI_PASS = "00000000"

LOCAL_PROJECT_ROOT = Path(r"c:\Users\YOHANNA\Desktop\Projects\Robot Code")
LOCAL_ROBOT = LOCAL_PROJECT_ROOT / "robot"
REMOTE_TARGETS = [
    "/home/pi/projects/Robot/robot",
    "/home/pi/robot",
]

FILES_TO_SYNC = [
    ("robot/vision/hand_ball_tracker.py", "vision/hand_ball_tracker.py"),
    ("robot/vision/__init__.py", "vision/__init__.py"),
    ("robot/track_hand_ball.py", "track_hand_ball.py"),
    ("robot/run_hand_ball.sh", "run_hand_ball.sh"),
    ("robot/tests/test_hand_ball_grasp.py", "tests/test_hand_ball_grasp.py"),
    ("robot/control/visual_servo_arm.py", "control/visual_servo_arm.py"),
    ("robot/track_and_pick_ball.py", "track_and_pick_ball.py"),
    ("robot/run_pick_ball.sh", "run_pick_ball.sh"),
    ("robot/bridge/actions/pick_ball.py", "bridge/actions/pick_ball.py"),
    ("robot/bridge/code_mappings.py", "bridge/code_mappings.py"),
    ("robot/bridge/executor.py", "bridge/executor.py"),
    ("robot/control/gentle_head.py", "control/gentle_head.py"),
    ("robot/bridge/actions/gentle_head.py", "bridge/actions/gentle_head.py"),
    ("robot/tests/test_gentle_head.py", "tests/test_gentle_head.py"),
    ("robot/tests/test_visual_servo_arm.py", "tests/test_visual_servo_arm.py"),
]


def make_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Add files
        for local_rel, arcname in FILES_TO_SYNC:
            local_path = LOCAL_PROJECT_ROOT / local_rel
            if local_path.exists():
                tar.add(str(local_path), arcname=arcname)
                print(f"  + Added: {arcname}")
    return buf.getvalue()


def main():
    print(f"Connecting to {PI_USER}@{PI_HOST} via SSH...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(PI_HOST, username=PI_USER, password=PI_PASS, timeout=10)
    print("Connected!")

    # 1. First, make sure hand_tracker is copied into robot folders on the Pi
    print("\n[*] Setting up hand_tracker inside robot directories on the Pi...")
    setup_hand_tracker_cmd = """
    for RDIR in /home/pi/projects/Robot/robot /home/pi/robot; do
        if [ -d "$RDIR" ]; then
            echo "Setting up in $RDIR..."
            mkdir -p "$RDIR/hand_tracker/models"
            # Copy model from /home/pi/hand_tracker if present
            if [ -f "/home/pi/hand_tracker/models/hand_tips.onnx" ]; then
                cp -u /home/pi/hand_tracker/models/hand_tips.onnx "$RDIR/hand_tracker/models/"
            fi
            # Also copy track_hand.py and samples
            if [ -d "/home/pi/hand_tracker/samples" ]; then
                cp -ru /home/pi/hand_tracker/samples "$RDIR/hand_tracker/"
            fi
            if [ -f "/home/pi/hand_tracker/track_hand.py" ]; then
                cp -u /home/pi/hand_tracker/track_hand.py "$RDIR/hand_tracker/"
            fi
        fi
    done
    """
    stdin, stdout, stderr = client.exec_command(setup_hand_tracker_cmd)
    print(stdout.read().decode())

    # 2. Upload the updated code bundle
    tar_bytes = make_tar()
    remote_tar = "/tmp/hand_ball_update.tar.gz"
    print(f"\n[*] Transferring code update ({len(tar_bytes)} bytes) via SFTP...")
    sftp = client.open_sftp()
    with sftp.file(remote_tar, "wb") as f:
        f.write(tar_bytes)
    sftp.close()

    # 3. Extract in both robot directories
    print("[*] Extracting update in robot directories...")
    extract_cmd = f"""
    for RDIR in /home/pi/projects/Robot/robot /home/pi/robot; do
        if [ -d "$RDIR" ]; then
            tar -xzf {remote_tar} -C "$RDIR"
            chmod +x "$RDIR"/track_hand_ball.py "$RDIR"/run_hand_ball.sh
            echo "Extracted to $RDIR"
        fi
    done
    rm -f {remote_tar}
    """
    stdin, stdout, stderr = client.exec_command(extract_cmd)
    print(stdout.read().decode())

    # 4. Run test on the Pi!
    print("\n[*] Running test_hand_ball_grasp.py on the Pi with systemenv...")
    test_cmd = """
    cd /home/pi/projects/Robot/robot
    /home/pi/systemenv/bin/python3 tests/test_hand_ball_grasp.py
    """
    stdin, stdout, stderr = client.exec_command(test_cmd)
    out = stdout.read().decode()
    err = stderr.read().decode()
    print("Test Output:\n", out)
    if err:
        print("Test Stderr:\n", err)

    client.close()
    print("Finished deployment & verification on Pi!")


if __name__ == "__main__":
    main()
