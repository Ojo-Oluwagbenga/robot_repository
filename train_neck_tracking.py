#!/usr/bin/env python3
"""
Neck Ball Tracking Calibration & Training System for LUKU Robot.
=============================================================================
Calibrates proportional motor gains for 2-DOF Neck Ball Tracking:
  - Yaw   (Pan Left/Right) : ESP 1, Motor 5. Range: [-3000, +3000] pulses.
  - Pitch (Tilt Up/Down)   : ESP 1, Motor 6. Range: [0, +5000] pulses.

Hardware Rules Enforced:
  1. Head must be manually set to HOME before running (Pitch=0 bottom, Yaw=0 center).
  2. Pitch at home (0) CANNOT move down; must move up first.
  3. Pitch net pulses cannot exceed 5000; Yaw cannot exceed [-3000, 3000].
  4. Refines gain across multiple pulse movements (averaging K_yaw & K_pitch).
  5. Saves learned gains & direction maps to 'neck_calibration.json'.

Usage:
  python train_neck_tracking.py                  # Calibrate with RED ball (default)
  python train_neck_tracking.py --color green    # Calibrate with GREEN ball
  python train_neck_tracking.py --port 8080      # Custom web streaming port
  python train_neck_tracking.py --no-gui         # Headless mode (no OpenCV GUI window)
=============================================================================
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Setup project paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR / "robot" if (SCRIPT_DIR / "robot").exists() else SCRIPT_DIR
PROJECT_ROOT = SCRIPT_DIR if (SCRIPT_DIR / "robot").exists() else SCRIPT_DIR.parent
for p in [str(PROJECT_ROOT), str(SCRIPT_DIR), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import cv2
    import numpy as np
except ImportError:
    print("[ERROR] OpenCV / NumPy not installed. Run: pip install opencv-python numpy")
    sys.exit(1)

try:
    from robot.control.neck_controller import (
        NeckController,
        NeckCalibration,
        PITCH_DIR_UP,
        PITCH_DIR_DOWN,
        DEFAULT_CALIBRATION_FILE,
    )
    from robot.hardware.camera import BinocularCamera
    from robot.vision.ball_tracker import MultiBallTracker
    from robot.vision.camera_server import CameraWebServer
    from robot.config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
        CAMERA_SAVE_DIR,
    )
except (ImportError, ModuleNotFoundError):
    from control.neck_controller import (
        NeckController,
        NeckCalibration,
        PITCH_DIR_UP,
        PITCH_DIR_DOWN,
        DEFAULT_CALIBRATION_FILE,
    )
    from hardware.camera import BinocularCamera
    from vision.ball_tracker import MultiBallTracker
    from vision.camera_server import CameraWebServer
    from config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
        CAMERA_SAVE_DIR,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("train_neck")


def parse_args():
    parser = argparse.ArgumentParser(
        description="LUKU Robot Neck Ball Tracking Training & Calibration"
    )
    parser.add_argument(
        "--color",
        type=str,
        default="red",
        choices=["red", "green", "blue", "yellow", "orange", "purple"],
        help="Target ball color to track during calibration (default: red)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=CAMERA_DEVICE_INDEX,
        help=f"Camera video device index (default: {CAMERA_DEVICE_INDEX})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CAMERA_WEB_PORT,
        help=f"Web stream viewer port (default: {CAMERA_WEB_PORT})",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Run headless without local X11/OpenCV display window",
    )
    parser.add_argument(
        "--baseline-pitch",
        type=int,
        default=1200,
        help="Elevation pulses to lift neck from home before testing (default: 1200)",
    )
    parser.add_argument(
        "--test-pulses",
        nargs="+",
        type=int,
        default=[250, 450],
        help="List of test pulse magnitudes to refine gains with (default: 250 450)",
    )
    return parser.parse_args()


def wait_for_stable_ball(
    camera: BinocularCamera,
    tracker: MultiBallTracker,
    server: Optional[CameraWebServer],
    required_consecutive: int = 5,
    timeout: float = 30.0,
    has_gui: bool = False,
    window_title: str = "Training Preview",
) -> Optional[Tuple[int, int]]:
    """
    Acquires camera frames until a target ball is stably detected in view.
    Returns (opt_cx, opt_cy, ball_cx, ball_cy) or None if timeout.
    """
    print("\n[*] Looking for target ball in camera field of view...")
    print("    Please hold the ball steady in front of the robot camera.")

    start_t = time.time()
    consecutive = 0
    last_centroid = None

    while time.time() - start_t < timeout:
        ret, frame = camera.read_raw()
        if not ret or frame is None:
            time.sleep(0.02)
            continue

        annotated_frame, tracks = tracker.process_frame(frame, is_stereo=True, annotate=True)

        if has_gui:
            cv2.imshow(window_title, annotated_frame)
            if (cv2.waitKey(1) & 0xFF) in (27, ord("q")):
                return None

        # Filter active tracks
        active = [t for t in tracks if t.missed_frames == 0]
        if active:
            # Pick primary track
            primary = max(active, key=lambda t: t.radius)
            consecutive += 1
            last_centroid = primary.centroid
            if consecutive >= required_consecutive:
                h, w = frame.shape[:2]
                half_w = w // 2 if w >= 640 else w
                opt_cx = half_w // 2
                opt_cy = h // 2
                print(f"[+] Ball locked stably at ({last_centroid[0]}, {last_centroid[1]}) px. Optical center is ({opt_cx}, {opt_cy}) px.")
                return (opt_cx, opt_cy, last_centroid[0], last_centroid[1])
        else:
            consecutive = 0

        time.sleep(0.03)

    print("[!] Timeout: No target ball detected.")
    return None


def get_current_ball_centroid(
    camera: BinocularCamera,
    tracker: MultiBallTracker,
    num_samples: int = 5,
    has_gui: bool = False,
    window_title: str = "Training Preview",
) -> Optional[Tuple[float, float]]:
    """Average centroid across several frames to filter noise."""
    samples_x: List[int] = []
    samples_y: List[int] = []

    for _ in range(num_samples + 5):
        ret, frame = camera.read_raw()
        if not ret or frame is None:
            time.sleep(0.01)
            continue

        annotated, tracks = tracker.process_frame(frame, is_stereo=True, annotate=True)
        if has_gui:
            cv2.imshow(window_title, annotated)
            cv2.waitKey(1)

        active = [t for t in tracks if t.missed_frames == 0]
        if active:
            primary = max(active, key=lambda t: t.radius)
            samples_x.append(primary.centroid[0])
            samples_y.append(primary.centroid[1])
            if len(samples_x) >= num_samples:
                break
        time.sleep(0.02)

    if not samples_x:
        return None
    return (float(np.mean(samples_x)), float(np.mean(samples_y)))


def main():
    args = parse_args()

    print("=" * 76)
    print("      LUKU ROBOT | NECK BALL TRACKING TRAINING & GAIN CALIBRATOR")
    print("=" * 76)
    print("  Axes & Hardware Assignment:")
    print("    - Neck Yaw   (Left/Right) : ESP 1, Motor 5  [Range: -3000 to +3000 pulses]")
    print("    - Neck Pitch (Up/Down)    : ESP 1, Motor 6  [Range: 0 to +5000 pulses]")
    print(f"  Target Ball Color : {args.color.upper()}")
    print(f"  Camera Device     : /dev/video{args.device}")
    print(f"  Baseline Pitch    : +{args.baseline_pitch} pulses upward")
    print(f"  Test Step Pulses  : {args.test_pulses}")
    print("=" * 76)

    # 1. Initialize Neck Controller
    neck = NeckController()

    # 2. Safety Check: User must physically set head to HOME
    if not neck.prompt_home_confirmation():
        print("[!] Exiting. No hardware changes made.")
        sys.exit(0)

    # 3. Initialize Camera & Tracker
    print("[*] Initializing binocular stereo camera...")
    camera = BinocularCamera(
        device_index=args.device,
        width=CAMERA_DEFAULT_WIDTH,
        height=CAMERA_DEFAULT_HEIGHT,
        fps=CAMERA_FPS,
    )
    if not camera.open():
        print(f"[ERROR] Failed to open camera device /dev/video{args.device}!")
        sys.exit(1)

    tracker = MultiBallTracker(
        target_color=args.color,
        min_radius=10,
        max_radius=220,
    )

    # 4. Start Web Streaming Server (for live view on phone/PC browser)
    server = CameraWebServer(
        camera=camera,
        port=args.port,
        save_dir=CAMERA_SAVE_DIR,
        tracker=tracker,
    )
    server.start(block=False)
    local_ip = server._get_local_ip()

    print("\n" + "#" * 76)
    print("  >>> LIVE CALIBRATION WEB VIEWER ACTIVE <<<")
    print(f"  Open in browser: http://{local_ip}:{args.port}/  (or http://localhost:{args.port}/)")
    print("#" * 76)

    has_gui = BinocularCamera.has_display() and not args.no_gui
    window_title = f"Neck Calibration - Tracking [{args.color.upper()}]"
    if has_gui:
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)

    try:
        # 5. Elevate head to working baseline so vertical has room to move up AND down
        print(f"\n[*] Elevating neck to baseline working elevation (+{args.baseline_pitch} pulses UP)...")
        print("    (Rule: 1-6-x-0 drives UPWARD. This gives clearance above home limit 0).")
        moved, reason = neck.move_pitch(args.baseline_pitch, PITCH_DIR_UP)
        if moved <= 0:
            print(f"[ERROR] Failed to elevate neck: {reason}")
            return
        time.sleep(0.5)

        # 6. Wait for ball in view
        loc = wait_for_stable_ball(
            camera, tracker, server,
            required_consecutive=6,
            timeout=40.0,
            has_gui=has_gui,
            window_title=window_title,
        )
        if not loc:
            print("[ERROR] Could not detect target ball. Training aborted.")
            return

        opt_cx, opt_cy, init_bx, init_by = loc

        # =========================================================================
        # PHASE 1: HORIZONTAL / YAW CALIBRATION (ESP 1, Motor 5)
        # =========================================================================
        print("\n" + "-" * 76)
        print(" [PHASE 1] CALIBRATING HORIZONTAL / YAW (MOTOR 5)")
        print("-" * 76)
        print(" Goal: Learn pulses-per-pixel gain and verify Left/Right direction mapping.")

        yaw_gains: List[float] = []
        calibrated_dir_right = 0
        calibrated_dir_left = 1

        for step_idx, test_pulse in enumerate(args.test_pulses, 1):
            c_before = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
            if not c_before:
                print("  [!] Ball lost before yaw test step. Skipping.")
                continue

            test_dir = 0  # Command test movement with dir 0
            print(f"\n  [Step {step_idx}] Testing Yaw Step: {test_pulse} pulses (Dir: {test_dir})...")
            moved, reason = neck.move_yaw(test_pulse, test_dir)
            if moved <= 0:
                print(f"  [!] Move blocked: {reason}")
                continue

            time.sleep(0.4)
            c_after = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
            if not c_after:
                print("  [!] Ball lost after yaw step!")
                continue

            delta_x = c_after[0] - c_before[0]
            print(f"    Centroid X: {c_before[0]:.1f} -> {c_after[0]:.1f} px | Shift: \u0394X = {delta_x:+.1f} px")

            if abs(delta_x) < 4.0:
                print("    [!] Warning: Shift was very small (<4px). Motor may not have moved or ball is too far.")
            else:
                gain_val = float(test_pulse) / abs(delta_x)
                yaw_gains.append(gain_val)
                print(f"    Calculated Gain Step {step_idx}: {gain_val:.2f} pulses/pixel")

                # Determine direction:
                # When camera turns RIGHT, the image shifts LEFT (delta_x < 0)
                # When camera turns LEFT, the image shifts RIGHT (delta_x > 0)
                if step_idx == 1:
                    if delta_x < 0:
                        calibrated_dir_right = 0
                        calibrated_dir_left = 1
                        print("    Direction Resolved: Dir 0 = Turns RIGHT | Dir 1 = Turns LEFT")
                    else:
                        calibrated_dir_right = 1
                        calibrated_dir_left = 0
                        print("    Direction Resolved: Dir 1 = Turns RIGHT | Dir 0 = Turns LEFT")

            # Return yaw back to where it was
            return_dir = calibrated_dir_left if test_dir == calibrated_dir_right else calibrated_dir_right
            print(f"    Returning Yaw by {moved} pulses (Dir {return_dir})...")
            neck.move_yaw(moved, return_dir)
            time.sleep(0.4)

        if yaw_gains:
            final_gain_yaw = float(np.mean(yaw_gains))
            print(f"\n  [+] Yaw Calibration Complete! Tested gains: {[round(g, 2) for g in yaw_gains]}")
            print(f"      -> Refined Yaw Gain: {final_gain_yaw:.2f} pulses/pixel")
        else:
            final_gain_yaw = 3.0
            print(f"\n  [!] Could not compute yaw gain. Using fallback: {final_gain_yaw:.2f}")

        # =========================================================================
        # PHASE 2: VERTICAL / PITCH CALIBRATION (ESP 1, Motor 6)
        # =========================================================================
        print("\n" + "-" * 76)
        print(" [PHASE 2] CALIBRATING VERTICAL / PITCH (MOTOR 6)")
        print("-" * 76)
        print(" Goal: Learn pulses-per-pixel gain for Up/Down movement.")
        print(" Verification: 1-6-x-0 = UP (increases Y in image), 1-6-x-1 = DOWN.")

        pitch_gains: List[float] = []

        for step_idx, test_pulse in enumerate(args.test_pulses, 1):
            c_before = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
            if not c_before:
                print("  [!] Ball lost before pitch test step. Skipping.")
                continue

            # Test UPWARD move (Dir 0)
            print(f"\n  [Step {step_idx}] Testing Pitch Step: {test_pulse} pulses UP (Dir: {PITCH_DIR_UP})...")
            moved, reason = neck.move_pitch(test_pulse, PITCH_DIR_UP)
            if moved <= 0:
                print(f"  [!] Move blocked: {reason}")
                continue

            time.sleep(0.4)
            c_after = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
            if not c_after:
                print("  [!] Ball lost after pitch step!")
                continue

            delta_y = c_after[1] - c_before[1]
            print(f"    Centroid Y: {c_before[1]:.1f} -> {c_after[1]:.1f} px | Shift: \u0394Y = {delta_y:+.1f} px")

            if abs(delta_y) < 4.0:
                print("    [!] Warning: Shift was very small (<4px). Motor may not have moved.")
            else:
                gain_val = float(test_pulse) / abs(delta_y)
                pitch_gains.append(gain_val)
                print(f"    Calculated Gain Step {step_idx}: {gain_val:.2f} pulses/pixel")

            # Return pitch down by the same amount back to baseline
            print(f"    Returning Pitch down by {moved} pulses (Dir: {PITCH_DIR_DOWN})...")
            neck.move_pitch(moved, PITCH_DIR_DOWN)
            time.sleep(0.4)

        if pitch_gains:
            final_gain_pitch = float(np.mean(pitch_gains))
            print(f"\n  [+] Pitch Calibration Complete! Tested gains: {[round(g, 2) for g in pitch_gains]}")
            print(f"      -> Refined Pitch Gain: {final_gain_pitch:.2f} pulses/pixel")
        else:
            final_gain_pitch = 3.5
            print(f"\n  [!] Could not compute pitch gain. Using fallback: {final_gain_pitch:.2f}")

        # Update and save calibration in memory and on disk
        cal = NeckCalibration(
            gain_yaw=round(final_gain_yaw, 2),
            gain_pitch=round(final_gain_pitch, 2),
            yaw_dir_right=calibrated_dir_right,
            yaw_dir_left=calibrated_dir_left,
            pitch_dir_up=PITCH_DIR_UP,
            pitch_dir_down=PITCH_DIR_DOWN,
            deadband_x=25,
            deadband_y=25,
            max_step_pulses_yaw=400,
            max_step_pulses_pitch=400,
        )
        neck.calibration = cal
        neck.save_calibration()

        # Also save copy inside robot/config if directory exists
        config_cal_path = ROBOT_DIR / "config" / "neck_calibration.json"
        if config_cal_path.parent.exists():
            neck.save_calibration(config_cal_path)

        # =========================================================================
        # PHASE 3: LIVE CENTERING TEST / VALIDATION
        # =========================================================================
        print("\n" + "=" * 76)
        print(" [PHASE 3] VERIFYING CENTERING WITH LEARNED GAINS")
        print("=" * 76)
        time.sleep(0.5)

        c_now = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
        if c_now:
            err_x = c_now[0] - opt_cx
            err_y = c_now[1] - opt_cy
            print(f"  Current Ball Position: ({c_now[0]:.1f}, {c_now[1]:.1f}) px")
            print(f"  Optical Screen Center: ({opt_cx}, {opt_cy}) px")
            print(f"  Error Vector         : (dX={err_x:+.1f}, dY={err_y:+.1f}) px")

            print("\n  Executing proportional centering correction...")
            step_result = neck.center_target(err_x, err_y)
            print(f"  Result: {step_result}")

            time.sleep(0.5)
            c_after = get_current_ball_centroid(camera, tracker, has_gui=has_gui, window_title=window_title)
            if c_after:
                new_err_x = c_after[0] - opt_cx
                new_err_y = c_after[1] - opt_cy
                print(f"  Post-Centering Ball Pos: ({c_after[0]:.1f}, {c_after[1]:.1f}) px")
                print(f"  Post-Centering Error   : (dX={new_err_x:+.1f}, dY={new_err_y:+.1f}) px")
                print("  [+] Centering test completed successfully!")

        print("\n" + "=" * 76)
        print("       CALIBRATION & REFINEMENT FINISHED SUCCESSFULLY!")
        print("=" * 76)
        print(f"  Gain Yaw   (Left/Right) : {cal.gain_yaw:.2f} pulses / px")
        print(f"  Gain Pitch (Up/Down)    : {cal.gain_pitch:.2f} pulses / px")
        print(f"  Saved to                : {DEFAULT_CALIBRATION_FILE}")
        print("=" * 76)

        # Ask to return home
        try:
            ret_home = input("\n>> Return neck to HOME position now? (Y/n): ").strip().lower()
            if ret_home in ("", "y", "yes"):
                neck.return_to_home()
        except (KeyboardInterrupt, EOFError):
            neck.return_to_home()

    finally:
        server.stop()
        camera.release()
        if has_gui:
            cv2.destroyAllWindows()
        print("[*] Camera and viewer server released cleanly.")


if __name__ == "__main__":
    main()
