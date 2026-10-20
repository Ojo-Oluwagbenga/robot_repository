#!/usr/bin/env python3
"""
Binocular Hand & Ball Grasp Tracker with Stereo 3D Depth Mapping
=============================================================================
Tracks:
  - Index Fingertip: RED dot
  - Thumb Tip:       BLUE dot
  - Target Ball:     Configurable color (default: GREEN)

Uses binocular dual-lens stereo depth mapping to determine when the ball is positioned
directly in between the index finger and thumb.

Examples:
  python3 track_hand_ball.py                            # Camera 0, web stream on port 8080
  python3 track_hand_ball.py --ball-color yellow        # Track yellow ball
  python3 track_hand_ball.py --source sample.mp4        # Test on stereo video file
  python3 track_hand_ball.py --web 8080 --stereo-view   # Show both eyes in web stream
  python3 track_hand_ball.py --udp 127.0.0.1:5005       # Stream grasp JSON over UDP
"""

import argparse
import json
import logging
import math
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

# Add project root to sys.path
ROBOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from robot.hardware.camera import BinocularCamera
    from robot.vision.hand_ball_tracker import HandBallGraspTracker, GraspResult
    from robot.config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
    )
except (ImportError, ValueError):
    from hardware.camera import BinocularCamera
    from vision.hand_ball_tracker import HandBallGraspTracker, GraspResult
    from config import (
        CAMERA_DEVICE_INDEX,
        CAMERA_DEFAULT_WIDTH,
        CAMERA_DEFAULT_HEIGHT,
        CAMERA_FPS,
        CAMERA_WEB_PORT,
    )

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("track_hand_ball")


# ==============================================================================
# EMBEDDED HIGH-PERFORMANCE MJPEG WEB STREAMER
# ==============================================================================
class WebStream:
    """Zero-dependency HTTP server serving live MJPEG stream and JSON state."""

    def __init__(self, port: int = 8080):
        self.port = port
        self.lock = threading.Lock()
        self.frame_jpeg: Optional[bytes] = None
        self.latest_data: dict = {}
        self.new_frame = threading.Condition(self.lock)
        self.server: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self._start_server()

    def _start_server(self):
        stream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass  # Silence HTTP access logs

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LUKU Robot | Binocular Hand & Ball Grasp Tracker</title>
  <style>
    :root {{
      --bg: #090d16;
      --card: #131b2e;
      --accent: #00ff88;
      --text: #f0f4fc;
      --dim: #7e8ea6;
      --red: #ff3344;
      --blue: #3388ff;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      padding: 16px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }}
    header {{
      width: 100%;
      max-width: 1100px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding: 12px 18px;
      background: var(--card);
      border-radius: 12px;
      border: 1px solid #1f2d4d;
    }}
    h1 {{ font-size: 1.25rem; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }}
    .badge {{
      background: #1a2744;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 0.8rem;
      color: var(--accent);
      font-weight: 600;
      border: 1px solid rgba(0, 255, 136, 0.3);
    }}
    .main-grid {{
      width: 100%;
      max-width: 1100px;
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
    }}
    .video-card {{
      background: #000;
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid #233458;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      position: relative;
    }}
    .video-card img {{
      width: 100%;
      height: auto;
      display: block;
    }}
    .telemetry-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }}
    .metric-card {{
      background: var(--card);
      padding: 14px 16px;
      border-radius: 12px;
      border: 1px solid #1f2d4d;
    }}
    .metric-title {{ font-size: 0.75rem; text-transform: uppercase; color: var(--dim); letter-spacing: 0.05em; }}
    .metric-value {{ font-size: 1.5rem; font-weight: 700; margin-top: 4px; }}
    .status-pill {{
      display: inline-block;
      padding: 4px 12px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 0.9rem;
      margin-top: 4px;
    }}
    .status-in-between {{ background: rgba(0, 255, 136, 0.2); color: #00ff88; border: 1px solid #00ff88; }}
    .status-seeking {{ background: rgba(255, 255, 255, 0.1); color: #aaa; }}
    .status-approaching {{ background: rgba(255, 187, 0, 0.2); color: #ffbb00; border: 1px solid #ffbb00; }}
    .status-grasped {{ background: rgba(0, 220, 255, 0.25); color: #00e5ff; border: 1px solid #00e5ff; }}
    .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }}
    .dot-red {{ background: var(--red); box-shadow: 0 0 8px var(--red); }}
    .dot-blue {{ background: var(--blue); box-shadow: 0 0 8px var(--blue); }}
    .dot-green {{ background: var(--accent); box-shadow: 0 0 8px var(--accent); }}
  </style>
</head>
<body>
  <header>
    <h1>🤖 LUKU Hand & Ball Stereo Depth Tracker</h1>
    <div style="display: flex; gap: 10px; align-items: center;">
      <span class="badge"><span class="dot dot-red"></span>Index</span>
      <span class="badge"><span class="dot dot-blue"></span>Thumb</span>
      <span class="badge"><span class="dot dot-green"></span>Ball</span>
    </div>
  </header>

  <div class="main-grid">
    <div class="video-card">
      <img src="/stream.mjpg" alt="Live Stereo Hand-Ball Tracker">
    </div>

    <div class="telemetry-grid">
      <div class="metric-card">
        <div class="metric-title">Grasp State</div>
        <div id="m-state" class="status-pill status-seeking">CONNECTING</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Pinch Distance (Aperture)</div>
        <div id="m-pinch" class="metric-value">-- cm</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Depth Offset (dZ)</div>
        <div id="m-dz" class="metric-value">-- cm</div>
      </div>
      <div class="metric-card">
        <div class="metric-title">Alignment Score</div>
        <div id="m-align" class="metric-value">0%</div>
      </div>
    </div>
  </div>

  <script>
    async function updateTelemetry() {{
      try {{
        const res = await fetch('/data.json');
        if (!res.ok) return;
        const d = await res.json();
        
        const stateEl = document.getElementById('m-state');
        stateEl.textContent = d.status || 'NO_TARGET';
        stateEl.className = 'status-pill ' + (
          d.is_in_between ? (d.is_pinched ? 'status-grasped' : 'status-in-between') :
          (d.status === 'APPROACHING' ? 'status-approaching' : 'status-seeking')
        );

        document.getElementById('m-pinch').textContent = (d.pinch_distance_cm !== null && d.pinch_distance_cm !== undefined) ? (d.pinch_distance_cm + ' cm') : '-- cm';
        document.getElementById('m-dz').textContent = (d.depth_offset_cm !== null && d.depth_offset_cm !== undefined) ? (d.depth_offset_cm + ' cm') : '-- cm';
        document.getElementById('m-align').textContent = (d.alignment_pct || 0) + '%';
      }} catch (e) {{}}
      setTimeout(updateTelemetry, 150);
    }}
    updateTelemetry();
  </script>
</body>
</html>"""
                    b = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(b)))
                    self.end_headers()
                    self.wfile.write(b)
                elif self.path == "/data.json":
                    with stream.lock:
                        payload = json.dumps(stream.latest_data).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path == "/stream.mjpg":
                    self.send_response(200)
                    self.send_header(
                        "Content-Type", "multipart/x-mixed-replace; boundary=frame"
                    )
                    self.send_header("Cache-Control", "no-cache, private")
                    self.end_headers()
                    while True:
                        with stream.new_frame:
                            stream.new_frame.wait()
                            jpg = stream.frame_jpeg
                        if not jpg:
                            continue
                        try:
                            self.wfile.write(b"--frame\r\n")
                            self.wfile.write(b"Content-Type: image/jpeg\r\n")
                            self.wfile.write(
                                f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                            )
                            self.wfile.write(jpg)
                            self.wfile.write(b"\r\n")
                        except (BrokenPipeError, ConnectionResetError):
                            break
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Web server started at http://0.0.0.0:{self.port}")

    def push(self, frame_bgr: np.ndarray, data: dict):
        """Compress frame to JPEG and notify connected clients."""
        ok, buf = cv2.imencode(".jpg", frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if ok:
            with self.new_frame:
                self.frame_jpeg = buf.tobytes()
                self.latest_data = data
                self.new_frame.notify_all()


# ==============================================================================
# MAIN FUNCTION
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Binocular Hand & Ball Grasp Tracker with Stereo 3D Depth Mapping"
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=CAMERA_DEVICE_INDEX,
        help=f"USB camera device index (default: {CAMERA_DEVICE_INDEX})",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        help="Path to pre-recorded side-by-side stereo video file (optional)",
    )
    parser.add_argument(
        "--ball-color",
        type=str,
        default="green",
        choices=["green", "yellow", "orange", "red", "blue", "purple"],
        help="Target ball color (default: green)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=CAMERA_DEFAULT_WIDTH,
        help=f"Camera total stereo width (default: {CAMERA_DEFAULT_WIDTH})",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=CAMERA_DEFAULT_HEIGHT,
        help=f"Camera stereo height (default: {CAMERA_DEFAULT_HEIGHT})",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=CAMERA_FPS,
        help=f"Camera frame rate (default: {CAMERA_FPS})",
    )
    parser.add_argument(
        "--web",
        type=int,
        default=8080,
        help="Port for live HTTP MJPEG stream (default: 8080)",
    )
    parser.add_argument(
        "--no-web",
        action="store_true",
        help="Disable web server streaming",
    )
    parser.add_argument(
        "--stereo-view",
        action="store_true",
        help="Show both Left and Right eye side-by-side in visualization",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="Run without local GUI window (recommended for headless Pi)",
    )
    parser.add_argument(
        "--udp",
        type=str,
        default=None,
        help="HOST:PORT to send UDP JSON telemetry packets",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print JSON grasp status to stdout",
    )
    parser.add_argument(
        "--save",
        type=str,
        default=None,
        help="Path to output .mp4 file to save annotated video",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=4,
        help="Number of CPU inference threads for ONNX runtime",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    print("=" * 72)
    print("      LUKU ROBOT | BINOCULAR HAND & BALL STEREO DEPTH TRACKER")
    print("=" * 72)
    print(f"  * Fingertip Pose : RED Dot = Index Tip | BLUE Dot = Thumb Tip")
    print(f"  * Ball Target    : {args.ball_color.upper()} Ball")
    print(f"  * Grasp Target   : Detect when Ball is IN BETWEEN Index & Thumb")
    print(f"  * 3D Depth Map   : Stereo Disparity Triangulation (Baseline: 60mm)")
    if args.source:
        print(f"  * Video Source   : {args.source}")
    else:
        print(f"  * Camera Device  : /dev/video{args.camera} ({args.width}x{args.height} @ {args.fps}fps)")
    print("-" * 72)

    # 1. Initialize Grasp Tracker
    print("[*] Loading hand pose model and initializing grasp evaluator...")
    tracker = HandBallGraspTracker(
        ball_color=args.ball_color,
        threads=args.threads,
    )
    print("    Hand pose model loaded successfully!")

    # 2. Setup UDP socket if requested
    udp_socket, udp_addr = None, None
    if args.udp:
        host, port_str = args.udp.rsplit(":", 1)
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_addr = (host, int(port_str))
        print(f"[*] Streaming UDP telemetry to {host}:{port_str}")

    # 3. Setup Web Server
    web = WebStream(port=args.web) if not args.no_web else None

    # 4. Open Camera or Video Source
    cap = None
    if args.source:
        cap = cv2.VideoCapture(args.source)
        if not cap.isOpened():
            print(f"[ERROR] Failed to open video source: {args.source}")
            sys.exit(1)
        print(f"[*] Playing video file: {args.source}")
    else:
        cap = cv2.VideoCapture(args.camera)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_FPS, args.fps)
        # Attempt MJPG hardware decode
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        if not cap.isOpened():
            print(f"[ERROR] Could not open camera /dev/video{args.camera}")
            sys.exit(1)
        print(f"[*] Camera opened successfully on /dev/video{args.camera}")

    # 5. Local Display Window Setup
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    show_gui = has_display and not args.no_window
    window_name = "LUKU Hand & Ball Stereo Tracker (q=quit, c=color)"
    if show_gui:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    # 6. Video Writer Setup
    writer = None
    if args.save:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        # Initialize lazily when first frame size is known

    print("\n" + "#" * 72)
    print("  >>> GRASP TRACKING ACTIVE <<<")
    if web:
        print(f"  Live Web Stream & Controls: http://0.0.0.0:{args.web}/")
    print("  Press Ctrl+C or 'q' to stop.")
    print("#" * 72 + "\n")

    color_cycle = ["green", "yellow", "orange", "blue", "red", "purple"]
    color_idx = color_cycle.index(args.ball_color) if args.ball_color in color_cycle else 0

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                if args.source:
                    # Loop video source
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                else:
                    time.sleep(0.01)
                    continue

            # Split horizontally into Left Eye and Right Eye
            h, w = frame.shape[:2]
            half_w = w // 2
            left_eye = frame[:, :half_w].copy()
            right_eye = frame[:, half_w:].copy()

            # Process frame through Grasp Tracker
            result: GraspResult = tracker.process_stereo_frame(
                left_img=left_eye,
                right_img=right_eye,
                show_right_eye=args.stereo_view,
            )

            vis = result.vis_frame

            # Output UDP JSON telemetry
            if udp_socket and udp_addr:
                try:
                    payload_bytes = json.dumps(result.telemetry).encode("utf-8")
                    udp_socket.sendto(payload_bytes, udp_addr)
                except Exception:
                    pass

            # Output console print
            if args.print:
                status_str = f"STATE: {result.status:<11} | IN_BETWEEN: {str(result.is_in_between):<5} | PINCH: {result.pinch_distance_cm or 0.0:4.1f}cm | dZ: {result.depth_offset_cm or 0.0:4.1f}cm | ALIGN: {result.alignment_score:3.0f}%"
                print(status_str, flush=True)

            # Web stream push
            if web:
                web.push(vis, result.telemetry)

            # Record video
            if args.save:
                if writer is None:
                    writer = cv2.VideoWriter(
                        args.save, fourcc, 20.0, (vis.shape[1], vis.shape[0])
                    )
                writer.write(vis)

            # GUI Window
            if show_gui:
                cv2.imshow(window_name, vis)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):  # 'q' or ESC
                    break
                elif key == ord("c"):  # Cycle ball color
                    color_idx = (color_idx + 1) % len(color_cycle)
                    new_c = color_cycle[color_idx]
                    tracker.set_ball_color(new_c)
                    print(f"[*] Switched ball tracking color to: {new_c.upper()}")

    except KeyboardInterrupt:
        print("\n[*] Stopping Hand-Ball Tracker...")
    finally:
        if cap:
            cap.release()
        if writer:
            writer.release()
        if show_gui:
            cv2.destroyAllWindows()
        print("[*] Tracker cleanly stopped.")


if __name__ == "__main__":
    main()
