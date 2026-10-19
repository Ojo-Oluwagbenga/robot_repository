#!/usr/bin/env python3
"""
Interactive Camera Runner and Verification Tool for LUKU Robot.
Captures images from the binocular stereo camera, splits into Left/Right eyes,
computes 3D depth disparity / anaglyph, displays the image via GUI window
(or terminal ANSI preview + web viewer when headless), and saves artifacts to disk.

Usage:
  python3 test_camera.py              # Capture snapshot, show image, and launch web viewer
  python3 test_camera.py --live       # Continuous live stereo stream
  python3 test_camera.py --web        # Run persistent web streaming dashboard
  python3 test_camera.py --depth      # Capture with 3D stereo disparity map
  python3 test_camera.py --res 2560x720 # HD 720p mode (1280x720 per eye)
  python3 test_camera.py --no-web     # Capture and exit without keeping web server open
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import cv2
    import numpy as np
except ImportError:
    print("[ERROR] OpenCV or NumPy is not installed. Run: pip install opencv-python numpy")
    sys.exit(1)

try:
    from robot.hardware.camera import BinocularCamera
    from robot.vision.camera_server import CameraWebServer
    from robot.config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_HD_WIDTH,
        CAMERA_HD_HEIGHT,
        CAMERA_FPS,
        CAMERA_SAVE_DIR,
        CAMERA_WEB_PORT,
    )
except (ImportError, ValueError):
    from hardware.camera import BinocularCamera
    from vision.camera_server import CameraWebServer
    from config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_HD_WIDTH,
        CAMERA_HD_HEIGHT,
        CAMERA_FPS,
        CAMERA_SAVE_DIR,
        CAMERA_WEB_PORT,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("test_camera")


def parse_args():
    parser = argparse.ArgumentParser(
        description="LUKU Robot Binocular Camera Test and Visualization Tool"
    )
    parser.add_argument(
        "--device",
        type=int,
        default=CAMERA_DEVICE_INDEX,
        help=f"Camera video device index (default: {CAMERA_DEVICE_INDEX} -> /dev/video{CAMERA_DEVICE_INDEX})",
    )
    parser.add_argument(
        "--res",
        type=str,
        default="1280x480",
        choices=["1280x480", "2560x720", "640x240"],
        help="Capture resolution (default: 1280x480 for 640x480 VGA per eye)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run continuous live capture and display loop",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Launch persistent HTTP web streaming server on port 8080",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Do not keep web viewer server open when running headless",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CAMERA_WEB_PORT,
        help=f"Port for HTTP web viewer server (default: {CAMERA_WEB_PORT})",
    )
    parser.add_argument(
        "--save-dir",
        type=str,
        default=CAMERA_SAVE_DIR,
        help=f"Directory to store captured images (default: {CAMERA_SAVE_DIR})",
    )
    parser.add_argument(
        "--depth",
        action="store_true",
        help="Compute and display 3D stereo disparity depth map",
    )
    parser.add_argument(
        "--anaglyph",
        action="store_true",
        help="Synthesize and display red-cyan 3D anaglyph image",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Parse resolution
    w_str, h_str = args.res.split("x")
    width, height = int(w_str), int(h_str)

    print("=" * 68)
    print("       LUKU ROBOT | BINOCULAR STEREO CAMERA CONTROLLER")
    print("=" * 68)
    print(f"  Target Device : /dev/video{args.device}")
    print(f"  Resolution    : {width}x{height} ({width // 2}x{height} per eye)")
    print(f"  Target FPS    : {CAMERA_FPS}")
    print(f"  Display Mode  : {'GUI Window' if BinocularCamera.has_display() else 'Headless (Terminal ANSI + Web)'}")
    print(f"  Save Directory: {args.save_dir}")
    print("-" * 68)

    print("[*] Initializing binocular camera...")
    camera = BinocularCamera(
        device_index=args.device,
        width=width,
        height=height,
        fps=CAMERA_FPS,
    )

    if not camera.open():
        print(f"[ERROR] Could not open /dev/video{args.device}!")
        print("  Tips:")
        print("  - Check USB cable connection.")
        print("  - Run 'v4l2-ctl --list-devices' to confirm device nodes.")
        print("  - Verify permissions: user 'pi' should be in the 'video' group.")
        sys.exit(1)

    actual_w, actual_h = camera.resolution
    print(f"[OK] Camera ready! Streaming at {actual_w}x{actual_h} ({actual_w // 2}x{actual_h}/eye)")

    # 1. Capture snapshot
    print("\n[*] Capturing stereo snapshot...")
    t0 = time.time()
    stereo_raw = camera.capture_raw()
    capture_ms = (time.time() - t0) * 1000
    print(f"[OK] Capture completed in {capture_ms:.1f}ms! Image shape: {stereo_raw.shape}")

    # 2. Save high-resolution image artifacts
    print(f"[*] Saving artifacts into '{args.save_dir}'...")
    saved = camera.save_capture(
        output_dir=args.save_dir,
        include_annotated=True,
        include_anaglyph=True,
        include_depth=True,
    )

    print("\n[SAVED ARTIFACTS]")
    for key, path in saved.items():
        size_kb = os.path.getsize(path) / 1024
        print(f"  -> {key:<16}: {path} ({size_kb:.1f} KB)")

    # 3. Choose display frame
    left_eye, right_eye = camera.split_stereo(stereo_raw)
    if args.depth:
        display_frame = camera.compute_disparity(left_eye, right_eye)
        window_title = "LUKU Robot - Stereo Disparity Depth Map"
    elif args.anaglyph:
        display_frame = camera.create_anaglyph(left_eye, right_eye)
        window_title = "LUKU Robot - 3D Red-Cyan Anaglyph"
    else:
        display_frame = camera.create_annotated(stereo_raw)
        window_title = "LUKU Robot - Binocular Stereo Vision HUD"

    # 4. Display logic
    has_gui = BinocularCamera.has_display()

    if has_gui and not args.web:
        print("\n[*] Graphical display detected. Opening OpenCV window...")
        print("    [Press any key or 'q' to close]")

        if args.live:
            print("[*] Running continuous live stream (press 'q' or ESC to stop)...")
            try:
                while True:
                    ret, frame = camera.read_raw()
                    if not ret:
                        continue
                    if args.depth:
                        l, r = camera.split_stereo(frame)
                        show_img = camera.compute_disparity(l, r)
                    elif args.anaglyph:
                        l, r = camera.split_stereo(frame)
                        show_img = camera.create_anaglyph(l, r)
                    else:
                        show_img = camera.create_annotated(frame)

                    cv2.imshow(window_title, show_img)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q")):
                        break
            finally:
                cv2.destroyAllWindows()
        else:
            cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
            cv2.imshow(window_title, display_frame)
            cv2.waitKey(0)
            cv2.destroyAllWindows()

    else:
        # Headless mode: Show ANSI preview in terminal
        print("\n" + "=" * 68)
        print("          TERMINAL PREVIEW (LEFT & RIGHT EYE HUD)")
        print("=" * 68)
        print(camera.render_terminal_ascii(display_frame, width=74))
        print("=" * 68)

        if not args.no_web:
            # Launch instant web viewer
            server = CameraWebServer(camera=camera, port=args.port, save_dir=args.save_dir)
            server.start(block=False)

            local_ip = server._get_local_ip()
            print("\n" + "#" * 68)
            print(f"  >>> WEB CAMERA VIEWER ACTIVE <<<")
            print(f"  Open in your PC / Phone / Tablet browser:")
            print(f"  -> http://{local_ip}:{args.port}/")
            print(f"  -> http://localhost:{args.port}/")
            print("#" * 68)
            print("  Features available in web interface:")
            print("    * Live 30 FPS stereo streaming")
            print("    * Real-time mode switching (Stereo, Left, Right, Anaglyph, Depth)")
            print("    * Instant Snapshot Capture & Download gallery")
            print("\n[INFO] Press Ctrl+C at any time to exit.\n")

            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Shutting down web server...")
                server.stop()

    camera.release()
    print("[*] Camera released cleanly. Done!")


if __name__ == "__main__":
    main()
