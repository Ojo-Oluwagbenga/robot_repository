#!/usr/bin/env python3
"""
Multi-Ball Color Tracking & Real-Time Trajectory Follower for LUKU Robot.
Detects, marks, and follows multiple balls of any selected color (red, green, blue, etc.)
with persistent track IDs, motion trails, and 3D stereo depth triangulation.

Usage:
  python3 track_balls.py                     # Track RED balls (default), launches live stream
  python3 track_balls.py --color green       # Track GREEN balls
  python3 track_balls.py --color blue        # Track BLUE balls
  python3 track_balls.py --color yellow      # Track YELLOW balls
  python3 track_balls.py --min-radius 12     # Minimum ball radius in pixels
  python3 track_balls.py --port 8080         # Custom web stream port
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path

# Add project root and robot directory to path
ROBOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import cv2
    import numpy as np
except ImportError:
    print("[ERROR] OpenCV or NumPy is not installed. Run: pip install opencv-python numpy")
    sys.exit(1)

try:
    from robot.hardware.camera import BinocularCamera
    from robot.vision.camera_server import CameraWebServer
    from robot.vision.ball_tracker import MultiBallTracker
    from robot.config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
        CAMERA_SAVE_DIR,
    )
except (ImportError, ValueError):
    from hardware.camera import BinocularCamera
    from vision.camera_server import CameraWebServer
    from vision.ball_tracker import MultiBallTracker
    from config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
        CAMERA_SAVE_DIR,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("track_balls")


def parse_args():
    parser = argparse.ArgumentParser(
        description="LUKU Robot Multi-Ball Color Tracker & Trajectory Follower"
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
        help=f"Video device index (default: {CAMERA_DEVICE_INDEX})",
    )
    parser.add_argument(
        "--min-radius",
        type=int,
        default=8,
        help="Minimum ball radius in pixels to accept (default: 8)",
    )
    parser.add_argument(
        "--max-radius",
        type=int,
        default=240,
        help="Maximum ball radius in pixels to accept (default: 240)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CAMERA_WEB_PORT,
        help=f"HTTP web viewer streaming port (default: {CAMERA_WEB_PORT})",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default=CAMERA_SAVE_DIR,
        help=f"Directory to save snapshots (default: {CAMERA_SAVE_DIR})",
    )
    parser.add_argument(
        "--no-gui",
        action="store_true",
        help="Force headless mode even if graphical display is detected",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("      LUKU ROBOT | MULTI-BALL COLOR TRACKER & TRAJECTORY FOLLOWER")
    print("=" * 70)
    print(f"  Target Color   : {args.color.upper()}")
    print(f"  Target Device  : /dev/video{args.device}")
    print(f"  Resolution     : {CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT} (Stereo Dual-Lens)")
    print(f"  Min Ball Radius: {args.min_radius} px")
    print(f"  Web Stream Port: {args.port}")
    print("-" * 70)

    # 1. Initialize Camera
    print("[*] Initializing binocular camera...")
    camera = BinocularCamera(
        device_index=args.device,
        width=CAMERA_DEFAULT_WIDTH,
        height=CAMERA_DEFAULT_HEIGHT,
        fps=CAMERA_FPS,
    )

    if not camera.open():
        print(f"[ERROR] Failed to open camera device /dev/video{args.device}!")
        sys.exit(1)

    # 2. Initialize MultiBallTracker
    tracker = MultiBallTracker(
        target_color=args.color,
        min_radius=args.min_radius,
        max_radius=args.max_radius,
    )

    # 3. Start Web Streaming Server
    server = CameraWebServer(
        camera=camera,
        port=args.port,
        save_dir=args.save_dir,
        tracker=tracker,
    )
    server.start(block=False)

    local_ip = server._get_local_ip()
    print("\n" + "#" * 70)
    print("  >>> LIVE STREAMING SERVER ACTIVE <<<")
    print(f"  View live feed & controls at: http://{local_ip}:{args.port}/")
    print(f"  Local link                 : http://localhost:{args.port}/")
    print("#" * 70)
    print("  Web Controls:")
    print("    * Real-time color switcher (Red, Green, Blue, Yellow, Orange, Purple)")
    print("    * Live 30 FPS video with glowing reticles & motion trails")
    print("    * Real-time coordinates, radius, speed & 3D stereo depth table")
    print("-" * 70)

    has_gui = BinocularCamera.has_display() and not args.no_gui

    if has_gui:
        window_title = f"LUKU Multi-Ball Live Tracking [{args.color.upper()}]"
        cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
        print(f"[*] Display window active. Keyboard shortcuts:")
        print("    'r': Red | 'g': Green | 'b': Blue | 'y': Yellow | 'o': Orange")
        print("    'c': Clear trails | 's': Save snapshot | 'q' or ESC: Quit")

        try:
            fps_timer = time.time()
            fps_count = 0
            current_fps = 30.0

            while True:
                ret, frame = camera.read_raw()
                if not ret or frame is None:
                    continue

                # Process multi-ball tracking and HUD
                annotated_frame, tracks = tracker.process_frame(frame, is_stereo=True, annotate=True)

                # FPS calculation
                fps_count += 1
                if time.time() - fps_timer >= 1.0:
                    current_fps = fps_count / (time.time() - fps_timer)
                    fps_count = 0
                    fps_timer = time.time()

                cv2.putText(
                    annotated_frame,
                    f"FPS: {current_fps:.1f}",
                    (annotated_frame.shape[1] - 110, 21),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 255, 255),
                    1,
                    cv2.LINE_AA,
                )

                cv2.imshow(window_title, annotated_frame)
                key = cv2.waitKey(1) & 0xFF

                if key in (27, ord("q")):
                    break
                elif key == ord("r"):
                    tracker.set_color("red")
                    cv2.setWindowTitle(window_title, "LUKU Multi-Ball Live Tracking [RED]")
                elif key == ord("g"):
                    tracker.set_color("green")
                    cv2.setWindowTitle(window_title, "LUKU Multi-Ball Live Tracking [GREEN]")
                elif key == ord("b"):
                    tracker.set_color("blue")
                    cv2.setWindowTitle(window_title, "LUKU Multi-Ball Live Tracking [BLUE]")
                elif key == ord("y"):
                    tracker.set_color("yellow")
                    cv2.setWindowTitle(window_title, "LUKU Multi-Ball Live Tracking [YELLOW]")
                elif key == ord("o"):
                    tracker.set_color("orange")
                    cv2.setWindowTitle(window_title, "LUKU Multi-Ball Live Tracking [ORANGE]")
                elif key == ord("c"):
                    tracker.clear_tracks()
                elif key == ord("s"):
                    saved = camera.save_capture(output_dir=args.save_dir)
                    print(f"[SAVED] Snapshot saved: {saved.get('stereo_latest')}")

        finally:
            cv2.destroyAllWindows()

    else:
        # Headless console runner with live telemetry
        print("[*] Running in headless mode. Streaming live to web viewer...")
        print("[*] Press Ctrl+C at any time to exit.\n")

        last_print = 0
        try:
            while True:
                ret, frame = camera.read_raw()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                # Run tracker pipeline
                _, tracks = tracker.process_frame(frame, is_stereo=True, annotate=False)

                # Periodic console telemetry output
                now = time.time()
                if now - last_print >= 1.0:
                    last_print = now
                    active_tracks = [t for t in tracks if t.missed_frames == 0]
                    if active_tracks:
                        t_strs = []
                        for t in active_tracks:
                            if t.pos_vector_3d:
                                X, Y, Z = t.pos_vector_3d
                                vec_str = f"r=[{X:+.1f},{Y:+.1f},{Z:+.1f}]cm (|r|={t.pos_magnitude}cm, \u03b8={t.pos_angle_deg:+.0f}\u00b0)"
                            else:
                                dx, dy = t.pos_vector_2d
                                vec_str = f"r=[{dx:+d},{dy:+d}]px (|r|={t.pos_magnitude}px, \u03b8={t.pos_angle_deg:+.0f}\u00b0)"
                            t_strs.append(f"#{t.track_id}: {vec_str}, pos=({t.centroid[0]},{t.centroid[1]}), v={int(t.speed)}px/s")
                        print(f"[{tracker.target_color.upper()}] {len(active_tracks)} Ball(s) Tracked -> " + " | ".join(t_strs))
                    else:
                        print(f"[{tracker.target_color.upper()}] Scanning feed for balls (0 detected)...")

                time.sleep(0.02)
        except KeyboardInterrupt:
            print("\n[*] Stopping ball tracker...")

    server.stop()
    camera.release()
    print("[*] Camera and tracker released cleanly. Done!")


if __name__ == "__main__":
    main()
