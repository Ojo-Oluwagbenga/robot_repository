#!/usr/bin/env python3
"""
Real-Time Autonomous Neck Ball Tracking System for LUKU Robot.
=============================================================================
Continuously adjusts robot neck pan-tilt mechanism to center a tracked ball:
  - Neck Yaw   (Pan Left/Right)  : ESP 1, Motor 5  [Range: -3000 to +3000 pulses]
  - Neck Pitch (Tilt Up/Down)    : ESP 1, Motor 6  [Range: 0 to +5000 pulses]

Safety Rules:
  1. Requests manual HOME confirmation before activating motors.
  2. Vertical pitch cannot go lower than HOME (net_pitch >= 0).
  3. Vertical pitch cannot exceed +5000 pulses upward.
  4. Sideways yaw is restricted to [-3000, +3000] pulses from home.
  5. 1-6-x-1 drives downward; 1-6-x-0 drives upward. Downward moves are
     prohibited when net_pitch is at 0.

Usage:
  python track_neck_ball.py                  # Track RED ball (default)
  python track_neck_ball.py --color green    # Track GREEN ball
  python track_neck_ball.py --color blue     # Track BLUE ball
  python track_neck_ball.py --port 8080      # Web viewer streaming port
  python track_neck_ball.py --no-gui         # Headless mode for SSH / Pi
=============================================================================
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# Ensure project paths are resolved
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
        PITCH_MIN_PULSES,
        PITCH_MAX_PULSES,
        YAW_MIN_PULSES,
        YAW_MAX_PULSES,
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
        PITCH_MIN_PULSES,
        PITCH_MAX_PULSES,
        YAW_MIN_PULSES,
        YAW_MAX_PULSES,
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
logger = logging.getLogger("track_neck")


def parse_args():
    parser = argparse.ArgumentParser(
        description="LUKU Robot Real-Time Autonomous Neck Ball Tracker"
    )
    parser.add_argument(
        "--color",
        type=str,
        default="red",
        choices=["red", "green", "blue", "yellow", "orange", "purple"],
        help="Target ball color to track (default: red)",
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
        help="Run in headless console mode without local OpenCV window",
    )
    parser.add_argument(
        "--deadband",
        type=int,
        default=25,
        help="Pixel deadband tolerance around optical center (default: 25 px)",
    )
    parser.add_argument(
        "--max-step",
        type=int,
        default=350,
        help="Maximum pulse correction per step (default: 350 pulses)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.18,
        help="Minimum seconds between neck motor adjustments (default: 0.18s)",
    )
    parser.add_argument(
        "--initial-elevation",
        type=int,
        default=800,
        help="Elevate neck by this many pulses from home on start for down-tilt freedom (default: 800)",
    )
    return parser.parse_args()


def draw_neck_hud(
    frame: np.ndarray,
    opt_cx: int,
    opt_cy: int,
    deadband_x: int,
    deadband_y: int,
    net_yaw: int,
    net_pitch: int,
    target_ball: Optional[Any],
    error_x: float,
    error_y: float,
    status_msg: str,
    gain_yaw: float = 3.0,
    gain_pitch: float = 3.5,
):
    """Draws targeting crosshairs, deadband box, telemetry, and safety position bars."""
    h, w = frame.shape[:2]

    # 1. Deadband target box
    cv2.rectangle(
        frame,
        (opt_cx - deadband_x, opt_cy - deadband_y),
        (opt_cx + deadband_x, opt_cy + deadband_y),
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )

    # 2. Optical Center Crosshairs
    cv2.line(frame, (opt_cx - 15, opt_cy), (opt_cx + 15, opt_cy), (0, 255, 255), 1, cv2.LINE_AA)
    cv2.line(frame, (opt_cx, opt_cy - 15), (opt_cx, opt_cy + 15), (0, 255, 255), 1, cv2.LINE_AA)

    # 3. Vector line to target ball
    if target_ball is not None:
        bx, by = int(target_ball.centroid[0]), int(target_ball.centroid[1])
        cv2.line(frame, (opt_cx, opt_cy), (bx, by), (0, 165, 255), 2, cv2.LINE_AA)
        cv2.circle(frame, (bx, by), int(target_ball.radius) + 6, (0, 165, 255), 2, cv2.LINE_AA)

    # 4. HUD Telemetry Overlay Card (Top Left)
    overlay = frame.copy()
    cv2.rectangle(overlay, (10, 10), (350, 165), (10, 15, 25), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
    cv2.rectangle(frame, (10, 10), (350, 165), (0, 229, 255), 1)

    cv2.putText(frame, "NECK BALL TRACKER TELEMETRY", (20, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 229, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Yaw   (M5): {net_yaw:+5d} pulses [-3000, +3000]", (20, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Pitch (M6): {net_pitch:5d} pulses [0, +5000]", (20, 68), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Error: dX={error_x:+5.1f}px, dY={error_y:+5.1f}px", (20, 88), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 255, 200), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Gains (Adaptive): Kx={gain_yaw:.2f} Ky={gain_pitch:.2f}", (20, 108), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 180, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"Status: {status_msg}", (20, 128), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 200, 0), 1, cv2.LINE_AA)
    cv2.putText(frame, "Home: Verified [0,0] | Safety: Active", (20, 148), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (100, 255, 100), 1, cv2.LINE_AA)


def main():
    args = parse_args()

    print("=" * 76)
    print("      LUKU ROBOT | AUTONOMOUS 2-DOF NECK BALL TRACKER")
    print("=" * 76)
    print("  Motors & Envelopes:")
    print("    - Neck Yaw   (Left/Right) : ESP 1, Motor 5  [Range: -3000 to +3000 pulses]")
    print("    - Neck Pitch (Up/Down)    : ESP 1, Motor 6  [Range: 0 to +5000 pulses]")
    print(f"  Target Color     : {args.color.upper()}")
    print(f"  Deadband Margin  : \u00b1{args.deadband} px")
    print(f"  Max Step Pulses  : {args.max_step} pulses")
    print(f"  Control Interval : {args.interval:.2f}s")
    print("=" * 76)

    # 1. Initialize Neck Controller
    neck = NeckController()

    # Apply command-line overrides to calibration parameters if set
    neck.calibration.deadband_x = args.deadband
    neck.calibration.deadband_y = args.deadband
    neck.calibration.max_step_pulses_yaw = args.max_step
    neck.calibration.max_step_pulses_pitch = args.max_step

    print(f"[*] Calibration Gains Loaded:")
    print(f"    - Gain Yaw   (X) : {neck.calibration.gain_yaw:.2f} pulses/pixel")
    print(f"    - Gain Pitch (Y) : {neck.calibration.gain_pitch:.2f} pulses/pixel")
    print(f"    - Yaw Right Dir  : {neck.calibration.yaw_dir_right} (Left: {neck.calibration.yaw_dir_left})")
    print(f"    - Pitch Up Dir   : {neck.calibration.pitch_dir_up} (Down: {neck.calibration.pitch_dir_down})")

    # 2. Strict Safety Gate: Request manual home confirmation
    if not neck.prompt_home_confirmation():
        print("[!] Safety confirmation aborted. Exiting.")
        sys.exit(0)

    # 3. Initialize Camera
    print("[*] Initializing stereo camera...")
    camera = BinocularCamera(
        device_index=args.device,
        width=CAMERA_DEFAULT_WIDTH,
        height=CAMERA_DEFAULT_HEIGHT,
        fps=CAMERA_FPS,
    )
    if not camera.open():
        print(f"[ERROR] Failed to open camera device /dev/video{args.device}!")
        sys.exit(1)

    # 4. Initialize Multi-Ball Tracker
    tracker = MultiBallTracker(
        target_color=args.color,
        min_radius=10,
        max_radius=240,
    )

    # 5. Start Web Server
    server = CameraWebServer(
        camera=camera,
        port=args.port,
        save_dir=CAMERA_SAVE_DIR,
        tracker=tracker,
    )
    server.start(block=False)
    local_ip = server._get_local_ip()

    print("\n" + "#" * 76)
    print("  >>> LIVE TRACKING WEB VIEWER ACTIVE <<<")
    print(f"  Stream URL: http://{local_ip}:{args.port}/  (or http://localhost:{args.port}/)")
    print("#" * 76)

    # 6. Optional initial elevation to clear home stop
    if args.initial_elevation > 0:
        print(f"[*] Lifting neck to tracking elevation (+{args.initial_elevation} pulses UP)...")
        moved, _ = neck.move_pitch(args.initial_elevation, PITCH_DIR_UP)
        time.sleep(0.4)

    has_gui = BinocularCamera.has_display() and not args.no_gui
    window_title = f"LUKU Autonomous Neck Ball Tracking [{args.color.upper()}]"
    if has_gui:
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        print("[*] Display window active. Press 'q' or ESC to exit, 'h' to return home.")

    last_control_time = 0.0
    last_save_time = time.time()
    pending_adaptation = None  # (prev_err_x, prev_err_y, moved_yaw, moved_pitch)
    status_msg = "SEARCHING"
    last_err_x = 0.0
    last_err_y = 0.0

    try:
        while True:
            ret, frame = camera.read_raw()
            if not ret or frame is None:
                time.sleep(0.01)
                continue

            h, w = frame.shape[:2]
            half_w = w // 2 if w >= 640 else w
            opt_cx = half_w // 2
            opt_cy = h // 2

            # Run tracking pipeline
            annotated_frame, tracks = tracker.process_frame(frame, is_stereo=True, annotate=True)

            active_tracks = [t for t in tracks if t.missed_frames == 0]
            target_ball = None

            if active_tracks:
                # Select primary target (e.g. largest detected ball)
                target_ball = max(active_tracks, key=lambda t: t.radius)
                bx, by = target_ball.centroid

                last_err_x = float(bx - opt_cx)
                last_err_y = float(by - opt_cy)

                # Periodic closed-loop adjustment
                now = time.time()
                if now - last_control_time >= args.interval:
                    last_control_time = now

                    # 1. Real-Time Online Gain Adaptation
                    if pending_adaptation is not None:
                        prev_x, prev_y, myaw, mpitch = pending_adaptation
                        adapt_res = neck.adapt_gains(last_err_x, last_err_y, prev_x, prev_y, myaw, mpitch)
                        if adapt_res.get("yaw_adapted") or adapt_res.get("pitch_adapted"):
                            logger.info(
                                f"[GAIN ADAPT] Live Refined Gains -> Yaw: {neck.calibration.gain_yaw:.2f} (Obs: {adapt_res.get('obs_gain_yaw', 'N/A')}), "
                                f"Pitch: {neck.calibration.gain_pitch:.2f} (Obs: {adapt_res.get('obs_gain_pitch', 'N/A')})"
                            )
                        pending_adaptation = None

                    # 2. Centering Command
                    in_deadband_x = abs(last_err_x) <= neck.calibration.deadband_x
                    in_deadband_y = abs(last_err_y) <= neck.calibration.deadband_y

                    if in_deadband_x and in_deadband_y:
                        status_msg = "CENTERED (Deadband)"
                    else:
                        step_res = neck.center_target(last_err_x, last_err_y)
                        myaw = step_res['yaw_moved']
                        mpitch = step_res['pitch_moved']
                        status_msg = f"ADJUST: Yaw={myaw}, Pitch={mpitch} | Kx={neck.calibration.gain_yaw:.2f} Ky={neck.calibration.gain_pitch:.2f}"
                        if myaw > 0 or mpitch > 0:
                            pending_adaptation = (last_err_x, last_err_y, myaw, mpitch)
                        logger.info(
                            f"[TRACK] Ball=({bx},{by}) Err=({last_err_x:+.0f},{last_err_y:+.0f}) | "
                            f"YawNet={neck.envelope.net_yaw} PitchNet={neck.envelope.net_pitch} | {status_msg}"
                        )

                    # Periodic auto-save of refined gains every 10 seconds
                    if now - last_save_time >= 10.0:
                        neck.save_calibration()
                        last_save_time = now
            else:
                status_msg = "SEARCHING (0 Balls)"
                pending_adaptation = None

            # Draw HUD with active adaptive gains
            draw_neck_hud(
                annotated_frame,
                opt_cx,
                opt_cy,
                neck.calibration.deadband_x,
                neck.calibration.deadband_y,
                neck.envelope.net_yaw,
                neck.envelope.net_pitch,
                target_ball,
                last_err_x,
                last_err_y,
                status_msg,
                gain_yaw=neck.calibration.gain_yaw,
                gain_pitch=neck.calibration.gain_pitch,
            )

            # Local GUI handling
            if has_gui:
                cv2.imshow(window_title, annotated_frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (27, ord("q")):
                    print("\n[*] Quit requested by user.")
                    break
                elif key == ord("h"):
                    neck.return_to_home()
                elif key == ord("r"):
                    tracker.set_color("red")
                elif key == ord("g"):
                    tracker.set_color("green")
                elif key == ord("b"):
                    tracker.set_color("blue")
                elif key == ord("y"):
                    tracker.set_color("yellow")

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n[*] Interrupted by user.")

    finally:
        # Save refined adaptive gains
        try:
            neck.save_calibration()
            print(f"[*] Saved refined adaptive gains (K_yaw={neck.calibration.gain_yaw:.2f}, K_pitch={neck.calibration.gain_pitch:.2f}) to neck_calibration.json.")
        except Exception as e:
            logger.warning(f"Error saving calibration: {e}")

        # Ask or automatically return to home
        try:
            print("\n[*] Exiting. Returning neck to HOME position...")
            neck.return_to_home()
        except Exception as e:
            logger.warning(f"Error returning home: {e}")

        server.stop()
        camera.release()
        if has_gui:
            cv2.destroyAllWindows()
        print("[*] Tracking stopped and resources released cleanly.")


if __name__ == "__main__":
    main()
