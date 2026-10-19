#!/usr/bin/env python3
"""
LUKU Robot - Hand Marker & Arm Pose Tracking Server.

Tracks the exact 4 markers defined by the user:
1. Index Finger (Blue line): Knuckle -> Joint -> Fingertip
2. Thumb (Green line): Base -> Joint -> Fingertip
3. Wrist Joint (Red Circle & Center Dot): Dark circular puck joint
4. Forearm Orientation (Yellow Line): Orientation angle and unit vector along the forearm cylinder

Features:
- Confidence-Gated Keyframe Updater (updates only when most certain, preventing jitter)
- Stereoscopic Disparity Validation & Metric 3D Depth
- Low-latency MJPEG Web Stream with modern Dark-Mode Dashboard
- REST API endpoint (/api/pose) returning real-time joint positions and orientations
"""

import sys
import os
import time
import json
import socket
import argparse
import logging
import threading
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional, Tuple, List, Dict, Any

# Ensure paths are added
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import cv2
import numpy as np

try:
    from robot.vision.hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult
    from robot.vision.hand_marker_model import HandMarkerTracker, MarkerState
    from robot.vision.arm_pose_estimator import ArmPoseEstimator, ArmPoseResult
except ImportError:
    try:
        from vision.hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult
        from vision.hand_marker_model import HandMarkerTracker, MarkerState
        from vision.arm_pose_estimator import ArmPoseEstimator, ArmPoseResult
    except ImportError:
        from hand_recognizer import MultiAngleHandRecognizer, HandRecognitionResult
        from hand_marker_model import HandMarkerTracker, MarkerState
        from arm_pose_estimator import ArmPoseEstimator, ArmPoseResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("hand_markers")

# Shared state between capture thread and HTTP server
_running = True
_current_frame_lock = threading.Lock()
_current_markers_jpeg: bytes = b""
_current_skeleton_jpeg: bytes = b""
_current_depth_jpeg: bytes = b""
_current_combined_jpeg: bytes = b""
_current_telemetry: dict = {}

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LUKU Robot | Hand Markers & Forearm Tracker</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(18, 26, 43, 0.85);
      --card-border: rgba(0, 229, 255, 0.25);
      --accent-cyan: #00e5ff;
      --accent-green: #00ff88;
      --accent-yellow: #ffee00;
      --accent-blue: #2979ff;
      --accent-red: #ff3344;
      --accent-orange: #ff9100;
      --text: #e0e6ed;
      --text-dim: #7a889b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 20px;
    }
    header {
      width: 100%;
      max-width: 1100px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding-bottom: 16px;
      margin-bottom: 20px;
      border-bottom: 1px solid rgba(0, 229, 255, 0.2);
    }
    .brand { display: flex; align-items: center; gap: 12px; }
    .brand h1 { font-size: 1.4rem; font-weight: 700; color: #fff; letter-spacing: 1px; }
    .brand span { color: var(--accent-cyan); }
    .badges { display: flex; gap: 8px; }
    .badge {
      background: rgba(0, 229, 255, 0.15);
      border: 1px solid var(--accent-cyan);
      color: var(--accent-cyan);
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 12px;
      font-weight: 600;
    }
    .badge-green {
      background: rgba(0, 255, 136, 0.15);
      border-color: var(--accent-green);
      color: var(--accent-green);
    }
    main {
      width: 100%;
      max-width: 1100px;
      display: grid;
      grid-template-columns: 1fr 350px;
      gap: 20px;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
    }
    .video-container {
      background: #000;
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow: hidden;
      position: relative;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
      aspect-ratio: 4 / 3;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .video-container img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
    .controls {
      display: flex;
      gap: 8px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    button, .mode-btn {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      color: var(--text);
      padding: 8px 14px;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s ease;
      text-decoration: none;
      display: inline-block;
    }
    button:hover, .mode-btn:hover {
      background: rgba(0, 229, 255, 0.2);
      border-color: var(--accent-cyan);
      color: #fff;
    }
    .mode-btn.active {
      background: var(--accent-cyan);
      color: #090d16;
      border-color: var(--accent-cyan);
    }
    .sidebar {
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    .card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      padding: 18px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.3);
    }
    .card h3 {
      font-size: 0.92rem;
      color: var(--accent-cyan);
      text-transform: uppercase;
      letter-spacing: 0.8px;
      margin-bottom: 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .stat-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 8px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      font-size: 0.88rem;
    }
    .stat-row:last-child { border-bottom: none; }
    .stat-label { color: var(--text-dim); display: flex; align-items: center; gap: 8px; }
    .stat-val { font-weight: 700; font-family: monospace; font-size: 0.92rem; }
    .marker-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .val-green { color: var(--accent-green); }
    .val-yellow { color: var(--accent-yellow); }
    .val-blue { color: var(--accent-blue); }
    .val-red { color: var(--accent-red); }
    .val-cyan { color: var(--accent-cyan); }
    .val-orange { color: var(--accent-orange); }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div style="width:12px;height:12px;background:var(--accent-green);border-radius:50%;box-shadow:0 0 10px var(--accent-green)"></div>
      <h1>LUKU <span>HAND & ARM TRACKER</span></h1>
    </div>
    <div class="badges">
      <div class="badge badge-green" id="cert-badge">Certainty: 90%</div>
      <div class="badge" id="fps-badge">FPS: --</div>
    </div>
  </header>

  <main>
    <div>
      <div class="video-container">
        <img id="stream-img" src="/stream?mode=markers" alt="Live Stream">
      </div>
      <div class="controls">
        <a class="mode-btn active" href="#" onclick="setMode('markers', this)">User Markers</a>
        <a class="mode-btn" href="#" onclick="setMode('skeleton', this)">Full Skeleton</a>
        <a class="mode-btn" href="#" onclick="setMode('combined', this)">Dual View</a>
        <a class="mode-btn" href="#" onclick="setMode('depth', this)">Depth Map</a>
        <button onclick="window.open('/snapshot?mode=' + currentMode, '_blank')">📷 Snapshot</button>
      </div>
    </div>

    <div class="sidebar">
      <div class="card">
        <h3>Tracked Markers <span id="update-badge" class="badge badge-green">LOCKED</span></h3>
        
        <div class="stat-row">
          <span class="stat-label">
            <span class="marker-dot" style="background:var(--accent-blue)"></span>
            Index Finger (Blue)
          </span>
          <span id="index-val" class="stat-val val-blue">Z: -- mm</span>
        </div>

        <div class="stat-row">
          <span class="stat-label">
            <span class="marker-dot" style="background:var(--accent-green)"></span>
            Thumb (Green)
          </span>
          <span id="thumb-val" class="stat-val val-green">Z: -- mm</span>
        </div>

        <div class="stat-row">
          <span class="stat-label">
            <span class="marker-dot" style="background:var(--accent-red)"></span>
            Wrist Puck (Red)
          </span>
          <span id="wrist-val" class="stat-val val-red">Z: -- mm</span>
        </div>

        <div class="stat-row">
          <span class="stat-label">
            <span class="marker-dot" style="background:var(--accent-yellow)"></span>
            Forearm Angle (Yellow)
          </span>
          <span id="fa-angle-val" class="stat-val val-yellow">-- deg</span>
        </div>

        <div class="stat-row">
          <span class="stat-label">
            <span class="marker-dot" style="background:var(--accent-cyan)"></span>
            Estimated Elbow (Cyan)
          </span>
          <span id="elbow-val" class="stat-val val-cyan">Z: -- mm</span>
        </div>
      </div>

      <div class="card">
        <h3>Certainty & Kinematics</h3>
        <div class="stat-row">
          <span class="stat-label">Gated Confidence</span>
          <span id="cert-val" class="stat-val val-green">--%</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Last Committed Keyframe</span>
          <span id="commit-time-val" class="stat-val">-- s ago</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Wrist Position (X, Y)</span>
          <span id="wrist-xy-val" class="stat-val">(--, --)</span>
        </div>
        <div class="stat-row">
          <span class="stat-label">Forearm 3D Vector</span>
          <span id="fa-vec-val" class="stat-val val-yellow">[--, --, --]</span>
        </div>
      </div>
    </div>
  </main>

  <script>
    let currentMode = 'markers';
    function setMode(mode, el) {
      currentMode = mode;
      document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
      el.classList.add('active');
      document.getElementById('stream-img').src = '/stream?mode=' + mode + '&t=' + Date.now();
    }

    async function pollTelemetry() {
      try {
        const res = await fetch('/api/pose');
        if (res.ok) {
          const data = await res.json();
          document.getElementById('fps-badge').innerText = 'FPS: ' + (data.fps || 0).toFixed(1);
          
          if (data.certainty_pct !== undefined) {
            document.getElementById('cert-badge').innerText = 'Certainty: ' + data.certainty_pct + '%';
            document.getElementById('cert-val').innerText = data.certainty_pct + '%';
            
            document.getElementById('fa-angle-val').innerText = data.forearm_angle_deg.toFixed(1) + ' deg';
            if (data.elbow_3d_mm) {
              document.getElementById('elbow-val').innerText = 'Z: ' + Math.round(data.elbow_3d_mm[2]) + ' mm (' + data.elbow_px[0] + ', ' + data.elbow_px[1] + ')';
            }
            document.getElementById('wrist-val').innerText = 'Z: ' + Math.round(data.wrist_3d[2]) + ' mm';
            document.getElementById('wrist-xy-val').innerText = '(' + Math.round(data.wrist_3d[0]) + ', ' + Math.round(data.wrist_3d[1]) + ')';
            
            document.getElementById('index-val').innerText = 'Z: ' + Math.round(data.index_tip_3d[2]) + ' mm';
            document.getElementById('thumb-val').innerText = 'Z: ' + Math.round(data.thumb_tip_3d[2]) + ' mm';
            
            document.getElementById('commit-time-val').innerText = data.time_since_commit.toFixed(2) + 's ago';
            document.getElementById('fa-vec-val').innerText = '[' + data.forearm_vector_3d.map(v => v.toFixed(2)).join(', ') + ']';

            const badge = document.getElementById('update-badge');
            if (data.is_keyframe_update) {
              badge.innerText = 'KEYFRAME COMMITTED';
              badge.className = 'badge badge-green';
            } else {
              badge.innerText = 'LOCKED (STABLE)';
              badge.className = 'badge';
            }
          }
        }
      } catch (e) {
        console.error(e);
      }
    }
    setInterval(pollTelemetry, 300);
  </script>
</body>
</html>
"""


class PoseStreamHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler for live MJPEG stream and telemetry REST API."""

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_DASHBOARD.encode("utf-8"))

        elif path == "/api/pose":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            with _current_frame_lock:
                telemetry_json = json.dumps(_current_telemetry).encode("utf-8")
            self.wfile.write(telemetry_json)

        elif path == "/snapshot":
            mode = query.get("mode", ["markers"])[0]
            with _current_frame_lock:
                if mode == "skeleton":
                    jpeg = _current_skeleton_jpeg
                elif mode == "depth":
                    jpeg = _current_depth_jpeg
                elif mode == "combined":
                    jpeg = _current_combined_jpeg
                else:
                    jpeg = _current_markers_jpeg

            if not jpeg:
                self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "Frame not ready")
                return

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Disposition", "inline; filename=snapshot.jpg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        elif path == "/stream":
            mode = query.get("mode", ["markers"])[0]
            self.send_response(HTTPStatus.OK)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()

            while _running:
                with _current_frame_lock:
                    if mode == "skeleton":
                        jpeg = _current_skeleton_jpeg
                    elif mode == "depth":
                        jpeg = _current_depth_jpeg
                    elif mode == "combined":
                        jpeg = _current_combined_jpeg
                    else:
                        jpeg = _current_markers_jpeg

                if jpeg:
                    header = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii")
                    )
                    try:
                        self.wfile.write(header + jpeg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        break
                time.sleep(0.04)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


def capture_and_process_loop(
    video_device: str,
    shoulder_offset: Tuple[float, float, float],
    l_upper: float,
    l_forearm: float,
    jpeg_quality: int = 80
):
    """Main background vision processing loop."""
    global _running, _current_markers_jpeg, _current_skeleton_jpeg, _current_depth_jpeg, _current_combined_jpeg, _current_telemetry

    logger.info(f"Opening camera: {video_device} (1280x480 MJPG)...")
    try:
        dev = int(video_device)
    except ValueError:
        dev = video_device

    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2) if sys.platform.startswith("linux") else cv2.VideoCapture(dev)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FPS, 30)

    if not cap.isOpened():
        logger.error(f"Failed to open camera on {video_device}")
        _running = False
        return

    # Warm up camera auto-exposure
    for _ in range(8):
        cap.read()

    logger.info("Initializing MultiAngleHandRecognizer (Trained Multi-Angle Model)...")
    recognizer = MultiAngleHandRecognizer(
        forearm_length_mm=l_forearm,
        min_certainty_threshold=0.65,
        min_update_interval_sec=0.40
    )
    dataset_candidates = [
        Path(__file__).parent / "robot" / "vision" / "dataset" / "hand_angles",
        Path(__file__).parent / "vision" / "dataset" / "hand_angles",
        Path("/home/pi/projects/Robot/robot/vision/dataset/hand_angles")
    ]
    for d in dataset_candidates:
        if d.exists():
            recognizer.train_from_dataset(str(d))
            break

    pose_estimator = ArmPoseEstimator(
        shoulder_offset_mm=shoulder_offset,
        upper_arm_length_mm=l_upper,
        forearm_length_mm=l_forearm,
        smoothing_factor=0.35
    )

    encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
    last_print = time.time()

    while _running:
        ret, frame = cap.read()
        if not ret or frame is None:
            time.sleep(0.01)
            continue

        h, w = frame.shape[:2]
        half_w = w // 2
        left_eye = frame[:, :half_w]
        right_eye = frame[:, half_w:]

        # 1. Update MultiAngleHandRecognizer (Trained Multi-Angle Recognition with Elbow Extrapolation)
        rec_res, markers_ann = recognizer.process(left_eye, right_eye)

        # 2. Update Full Arm Pose Estimator (Full Kinematic Wireframe)
        pose_res, skeleton_ann, depth_map = pose_estimator.process(frame)

        # 3. Depth colorization
        depth_clip = np.clip(depth_map, 150, 700)
        depth_norm = ((depth_clip - 150) / (700 - 150) * 255.0).astype(np.uint8)
        depth_color = cv2.applyColorMap(depth_norm, cv2.COLORMAP_TURBO)
        depth_color[depth_map < 100] = 0

        # 4. Combined dual view
        combined = np.hstack([markers_ann, depth_color])

        # Compress JPEGs
        _, mark_buf = cv2.imencode(".jpg", markers_ann, encode_param)
        _, skel_buf = cv2.imencode(".jpg", skeleton_ann, encode_param)
        _, dep_buf = cv2.imencode(".jpg", depth_color, encode_param)
        _, com_buf = cv2.imencode(".jpg", combined, encode_param)

        time_since_commit = time.time() - recognizer._last_commit_time

        telemetry = {
            "fps": recognizer.fps,
            "certainty_pct": int(rec_res.certainty * 100),
            "is_keyframe_update": rec_res.is_keyframe_update,
            "time_since_commit": time_since_commit,
            # Wrist
            "wrist_center_px": rec_res.wrist_px,
            "wrist_3d": rec_res.wrist_3d_mm,
            # Forearm & Estimated Elbow
            "forearm_angle_deg": rec_res.forearm_angle_deg,
            "forearm_vector_2d": rec_res.forearm_vector_2d,
            "forearm_vector_3d": rec_res.forearm_vector_3d,
            "elbow_px": rec_res.elbow_px,
            "elbow_3d_mm": rec_res.elbow_3d_mm,
            # Fingers
            "index_tip_3d": rec_res.index_3d_mm,
            "thumb_tip_3d": rec_res.thumb_3d_mm,
            "index_finger_px": rec_res.index_line_px,
            "thumb_px": rec_res.thumb_line_px,
            # Kinematics
            "shoulder_pitch_deg": pose_res.shoulder_pitch_deg,
            "shoulder_yaw_deg": pose_res.shoulder_yaw_deg,
            "elbow_flexion_deg": pose_res.elbow_flexion_deg,
        }

        with _current_frame_lock:
            _current_markers_jpeg = mark_buf.tobytes()
            _current_skeleton_jpeg = skel_buf.tobytes()
            _current_depth_jpeg = dep_buf.tobytes()
            _current_combined_jpeg = com_buf.tobytes()
            _current_telemetry = telemetry

        # Periodic console logging
        if time.time() - last_print > 2.0:
            last_print = time.time()
            commit_tag = " [KEYFRAME COMMITTED]" if rec_res.is_keyframe_update else ""
            logger.info(
                f"[TRACKER] Cert: {int(rec_res.certainty*100)}% | "
                f"Forearm: {rec_res.forearm_angle_deg:.1f}° | "
                f"Wrist Z: {int(rec_res.wrist_3d_mm[2])}mm | "
                f"Elbow: ({rec_res.elbow_px[0]}, {rec_res.elbow_px[1]}) Z: {int(rec_res.elbow_3d_mm[2])}mm | "
                f"Thumb Z: {int(rec_res.thumb_3d_mm[2])}mm | "
                f"Index Z: {int(rec_res.index_3d_mm[2])}mm | "
                f"FPS: {recognizer.fps:.1f}{commit_tag}"
            )

    cap.release()
    logger.info("Camera released cleanly.")


def get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    parser = argparse.ArgumentParser(description="LUKU Robot Hand Markers & Forearm Tracking Server")
    parser.add_argument("--device", default="/dev/video0", help="V4L2 Video device (default: /dev/video0)")
    parser.add_argument("--port", type=int, default=8080, help="Web server HTTP port (default: 8080)")
    parser.add_argument("--shoulder-x", type=float, default=140.0, help="Shoulder X offset relative to eyes (mm)")
    parser.add_argument("--shoulder-y", type=float, default=160.0, help="Shoulder Y offset relative to eyes (mm)")
    parser.add_argument("--shoulder-z", type=float, default=30.0, help="Shoulder Z offset relative to eyes (mm)")
    parser.add_argument("--l-upper", type=float, default=220.0, help="Upper arm length in mm (default: 220.0)")
    parser.add_argument("--l-forearm", type=float, default=200.0, help="Forearm length in mm (default: 200.0)")
    args = parser.parse_args()

    shoulder_offset = (args.shoulder_x, args.shoulder_y, args.shoulder_z)

    # Launch camera vision loop in daemon thread
    t = threading.Thread(
        target=capture_and_process_loop,
        args=(args.device, shoulder_offset, args.l_upper, args.l_forearm),
        daemon=True
    )
    t.start()

    ip = get_local_ip()
    logger.info("=" * 65)
    logger.info("  LUKU ROBOT HAND MARKERS & FOREARM TRACKER STARTED")
    logger.info(f"  Web Dashboard : http://{ip}:{args.port}/")
    logger.info(f"  Markers Stream: http://{ip}:{args.port}/stream?mode=markers")
    logger.info(f"  Telemetry API : http://{ip}:{args.port}/api/pose")
    logger.info("=" * 65)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), PoseStreamHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("\nShutting down server...")
    finally:
        global _running
        _running = False
        server.shutdown()
        server.server_close()
        t.join(timeout=2.0)
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()
