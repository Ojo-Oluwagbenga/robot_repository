"""
Lightweight Web Streaming & Remote Viewer Server for Robot Stereo Camera.
Provides low-latency MJPEG video streaming, interactive mode switching
(Stereo, Left Eye, Right Eye, 3D Anaglyph, Disparity Depth Map, Annotated HUD,
and Live Multi-Ball Color Tracking with 3D Depth & Trajectory Trails),
and instantaneous remote snapshot capture with a modern dark-mode dashboard.
"""

import io
import os
import json
import time
import socket
import logging
import threading
from pathlib import Path
from http import HTTPStatus
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from typing import Optional

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

try:
    from ..hardware.camera import BinocularCamera
    from ..config import CAMERA_WEB_PORT, CAMERA_SAVE_DIR
except (ImportError, ValueError):
    try:
        from robot.hardware.camera import BinocularCamera
        from robot.config import CAMERA_WEB_PORT, CAMERA_SAVE_DIR
    except (ImportError, ValueError):
        from hardware.camera import BinocularCamera
        from config import CAMERA_WEB_PORT, CAMERA_SAVE_DIR

try:
    from .ball_tracker import MultiBallTracker
except (ImportError, ValueError):
    try:
        from robot.vision.ball_tracker import MultiBallTracker
    except (ImportError, ValueError):
        from vision.ball_tracker import MultiBallTracker

logger = logging.getLogger("robot.vision.server")

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>LUKU Robot | Stereo Vision & Ball Tracker</title>
  <style>
    :root {
      --bg: #090d16;
      --card-bg: rgba(18, 26, 43, 0.75);
      --card-border: rgba(0, 229, 255, 0.2);
      --accent-cyan: #00e5ff;
      --accent-emerald: #10b981;
      --accent-amber: #f59e0b;
      --accent-rose: #f43f5e;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      background-image: 
        radial-gradient(circle at 20% 15%, rgba(0, 229, 255, 0.08) 0%, transparent 40%),
        radial-gradient(circle at 80% 85%, rgba(16, 185, 129, 0.06) 0%, transparent 40%);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    header {
      padding: 1rem 1.5rem;
      border-bottom: 1px solid var(--card-border);
      backdrop-filter: blur(12px);
      background: rgba(9, 13, 22, 0.85);
      display: flex;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .pulse-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent-emerald);
      box-shadow: 0 0 10px var(--accent-emerald);
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.4; transform: scale(1.15); }
    }
    h1 {
      font-size: 1.25rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      background: linear-gradient(135deg, #fff 30%, var(--accent-cyan));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .badge {
      font-size: 0.75rem;
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      border: 1px solid var(--card-border);
      background: rgba(0, 229, 255, 0.1);
      color: var(--accent-cyan);
      font-weight: 600;
      letter-spacing: 0.05em;
    }
    main {
      flex: 1;
      max-width: 1300px;
      width: 100%;
      margin: 0 auto;
      padding: 1.5rem;
      display: flex;
      flex-direction: column;
      gap: 1.5rem;
    }
    .view-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      backdrop-filter: blur(16px);
      overflow: hidden;
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.5);
      display: flex;
      flex-direction: column;
    }
    .view-toolbar {
      padding: 0.85rem 1.25rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }
    .tracker-toolbar {
      padding: 0.75rem 1.25rem;
      background: rgba(0, 0, 0, 0.35);
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 0.75rem;
    }
    .btn-group {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .btn {
      padding: 0.45rem 0.9rem;
      border-radius: 8px;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text-muted);
      transition: all 0.2s ease;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
    }
    .btn:hover {
      background: rgba(0, 229, 255, 0.15);
      color: #fff;
      border-color: var(--accent-cyan);
    }
    .btn.active {
      background: linear-gradient(135deg, rgba(0, 229, 255, 0.25), rgba(16, 185, 129, 0.2));
      border-color: var(--accent-cyan);
      color: #fff;
      box-shadow: 0 0 12px rgba(0, 229, 255, 0.3);
    }
    .btn-color {
      border-radius: 6px;
      padding: 0.35rem 0.75rem;
      font-size: 0.8rem;
      font-weight: 700;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.2s ease;
    }
    .btn-color.selected {
      box-shadow: 0 0 14px currentColor;
      transform: scale(1.05);
      border-color: #fff;
    }
    .btn-capture {
      background: linear-gradient(135deg, #00e5ff, #0284c7);
      color: #031024;
      border: none;
      font-weight: 700;
    }
    .btn-capture:hover {
      background: linear-gradient(135deg, #38bdf8, #0ea5e9);
      box-shadow: 0 0 16px rgba(0, 229, 255, 0.5);
      color: #031024;
    }
    .stream-container {
      position: relative;
      background: #000;
      min-height: 380px;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    #streamImg {
      width: 100%;
      height: auto;
      max-height: 720px;
      object-fit: contain;
      display: block;
    }
    .stream-overlay {
      position: absolute;
      bottom: 12px;
      left: 12px;
      background: rgba(0, 0, 0, 0.75);
      padding: 0.4rem 0.85rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-family: monospace;
      color: var(--accent-cyan);
      border: 1px solid rgba(0, 229, 255, 0.3);
      backdrop-filter: blur(4px);
    }
    .telemetry-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .telemetry-title {
      font-size: 1rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .telemetry-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.85rem;
    }
    .telemetry-table th {
      text-align: left;
      padding: 0.6rem 0.75rem;
      color: var(--text-muted);
      border-bottom: 1px solid rgba(255, 255, 255, 0.1);
      font-weight: 600;
    }
    .telemetry-table td {
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      font-family: monospace;
    }
    .gallery-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 1.25rem;
    }
    .gallery-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 1rem;
    }
    .gallery-title {
      font-size: 1rem;
      font-weight: 600;
      color: var(--text-main);
    }
    .gallery-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 1rem;
    }
    .thumb-item {
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 8px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .thumb-img {
      width: 100%;
      height: 150px;
      object-fit: cover;
      background: #000;
    }
    .thumb-meta {
      padding: 0.6rem 0.8rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .thumb-link {
      color: var(--accent-cyan);
      text-decoration: none;
      font-weight: 600;
    }
    .thumb-link:hover { text-decoration: underline; }
    #toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: rgba(16, 185, 129, 0.95);
      color: #031f13;
      font-weight: 600;
      padding: 0.75rem 1.25rem;
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
      display: none;
      z-index: 1000;
    }
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="pulse-dot"></div>
      <h1>LUKU Stereo Vision & Multi-Ball Tracker</h1>
    </div>
    <div style="display: flex; gap: 0.75rem; align-items: center;">
      <span class="badge" id="resBadge">1280x480</span>
      <span class="badge" style="background: rgba(16, 185, 129, 0.1); color: var(--accent-emerald);">LIVE STREAM</span>
    </div>
  </header>

  <main>
    <div class="view-card">
      <div class="view-toolbar">
        <div class="btn-group" id="modeButtons">
          <button class="btn active" data-mode="tracking" onclick="setMode('tracking')">🔴 Multi-Ball Tracker</button>
          <button class="btn" data-mode="stereo" onclick="setMode('stereo')">Full Stereo</button>
          <button class="btn" data-mode="annotated" onclick="setMode('annotated')">HUD Reticles</button>
          <button class="btn" data-mode="left" onclick="setMode('left')">Left Eye (3D-L)</button>
          <button class="btn" data-mode="right" onclick="setMode('right')">Right Eye (3D-R)</button>
          <button class="btn" data-mode="anaglyph" onclick="setMode('anaglyph')">3D Anaglyph</button>
          <button class="btn" data-mode="depth" onclick="setMode('depth')">Disparity Depth</button>
        </div>
        <div>
          <button class="btn btn-capture" onclick="triggerCapture()">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"></path><circle cx="12" cy="13" r="4"></circle></svg>
            Take Snapshot
          </button>
        </div>
      </div>

      <div class="tracker-toolbar">
        <div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
          <span style="font-size: 0.85rem; font-weight: 600; color: var(--text-muted);">Target Ball Color:</span>
          <button class="btn-color selected" data-color="red" style="background: #ef4444; color: #fff;" onclick="setTargetColor('red')">RED</button>
          <button class="btn-color" data-color="green" style="background: #10b981; color: #fff;" onclick="setTargetColor('green')">GREEN</button>
          <button class="btn-color" data-color="blue" style="background: #3b82f6; color: #fff;" onclick="setTargetColor('blue')">BLUE</button>
          <button class="btn-color" data-color="yellow" style="background: #eab308; color: #000;" onclick="setTargetColor('yellow')">YELLOW</button>
          <button class="btn-color" data-color="orange" style="background: #f97316; color: #fff;" onclick="setTargetColor('orange')">ORANGE</button>
          <button class="btn-color" data-color="purple" style="background: #a855f7; color: #fff;" onclick="setTargetColor('purple')">PURPLE</button>
        </div>
        <div style="display: flex; gap: 1rem; align-items: center;">
          <button class="btn" onclick="clearTrails()" style="font-size: 0.8rem; padding: 0.35rem 0.7rem;">Clear Trails</button>
        </div>
      </div>

      <div class="stream-container">
        <img id="streamImg" src="/stream?mode=tracking" alt="Live Stereo Feed">
        <div class="stream-overlay" id="overlayTag">Mode: MULTI-BALL TRACKER | Target: RED</div>
      </div>
    </div>

    <div class="telemetry-card">
      <div class="telemetry-title">
        <span>Live Tracked Balls Telemetry</span>
        <span class="badge" id="ballCountBadge" style="background: rgba(16, 185, 129, 0.15); color: var(--accent-emerald);">0 Balls Detected</span>
      </div>
      <table class="telemetry-table">
        <thead>
          <tr>
            <th>Track ID</th>
            <th>Color</th>
            <th>Position Vector r</th>
            <th>Magnitude |r|</th>
            <th>Bearing θ</th>
            <th>Screen (X, Y)</th>
            <th>Speed</th>
          </tr>
        </thead>
        <tbody id="telemetryBody">
          <tr><td colspan="7" style="color: var(--text-muted); text-align: center;">No balls currently detected in feed</td></tr>
        </tbody>
      </table>
    </div>

    <div class="gallery-card">
      <div class="gallery-header">
        <div class="gallery-title">Recent Captures</div>
        <span style="font-size: 0.8rem; color: var(--text-muted);" id="captureCount">0 saved</span>
      </div>
      <div class="gallery-grid" id="galleryGrid"></div>
    </div>
  </main>

  <div id="toast">Snapshot captured successfully!</div>

  <script>
    let currentMode = 'tracking';
    let currentColor = 'red';

    function setMode(mode) {
      currentMode = mode;
      document.querySelectorAll('#modeButtons .btn').forEach(b => {
        b.classList.toggle('active', b.dataset.mode === mode);
      });
      const img = document.getElementById('streamImg');
      img.src = '/stream?mode=' + mode + '&t=' + Date.now();
      document.getElementById('overlayTag').innerText = 'Mode: ' + mode.toUpperCase() + ' | Target: ' + currentColor.toUpperCase();
    }

    async function setTargetColor(color) {
      currentColor = color;
      document.querySelectorAll('.btn-color').forEach(b => {
        b.classList.toggle('selected', b.dataset.color === color);
      });
      try {
        await fetch('/api/tracker?color=' + color, { method: 'POST' });
        showToast('Tracking target set to: ' + color.toUpperCase());
        document.getElementById('overlayTag').innerText = 'Mode: ' + currentMode.toUpperCase() + ' | Target: ' + color.toUpperCase();
      } catch (err) {
        console.error('Failed to set color:', err);
      }
    }

    async function clearTrails() {
      try {
        await fetch('/api/tracker?action=clear', { method: 'POST' });
        showToast('Tracking trails cleared');
      } catch (err) {
        console.error(err);
      }
    }

    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.innerText = msg;
      toast.style.display = 'block';
      setTimeout(() => { toast.style.display = 'none'; }, 2800);
    }

    async function triggerCapture() {
      try {
        const res = await fetch('/api/capture', { method: 'POST' });
        const data = await res.json();
        if (data.status === 'ok') {
          showToast('Image captured and saved to ' + data.saved_files.stereo_latest);
          loadGallery();
        }
      } catch (err) {
        showToast('Capture error: ' + err.message);
      }
    }

    async function updateTelemetry() {
      try {
        const res = await fetch('/api/tracker');
        const data = await res.json();
        const tbody = document.getElementById('telemetryBody');
        const badge = document.getElementById('ballCountBadge');
        
        badge.innerText = data.tracks.length + ' Balls Detected';

        if (data.tracks.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="color: var(--text-muted); text-align: center;">No balls currently detected in frame</td></tr>';
          return;
        }

        tbody.innerHTML = '';
        data.tracks.forEach(t => {
          const tr = document.createElement('tr');
          tr.innerHTML = `
            <td><strong style="color: #00e5ff;">#${t.id}</strong></td>
            <td><span style="color: #f1f5f9; text-transform: uppercase;">${data.target_color}</span></td>
            <td><strong style="color: #38bdf8;">${t.pos_vector || 'calc...'}</strong></td>
            <td><strong>${t.pos_magnitude}</strong></td>
            <td>${t.pos_angle_deg}&deg;</td>
            <td>(${t.x}, ${t.y})</td>
            <td>${t.speed} px/s</td>
          `;
          tbody.appendChild(tr);
        });
      } catch (err) {
        // silent
      }
    }

    async function loadGallery() {
      try {
        const res = await fetch('/api/captures');
        const data = await res.json();
        const grid = document.getElementById('galleryGrid');
        grid.innerHTML = '';
        document.getElementById('captureCount').innerText = data.files.length + ' saved';
        data.files.forEach(item => {
          const card = document.createElement('div');
          card.className = 'thumb-item';
          card.innerHTML = `
            <a href="${item.url}" target="_blank">
              <img class="thumb-img" src="${item.url}" alt="${item.name}">
            </a>
            <div class="thumb-meta">
              <span>${item.name}</span>
              <a class="thumb-link" href="${item.url}" download>Download</a>
            </div>
          `;
          grid.appendChild(card);
        });
      } catch (err) {
        console.error('Gallery load failed', err);
      }
    }

    // Initial setup
    loadGallery();
    setInterval(updateTelemetry, 500);
  </script>
</body>
</html>
"""


class StereoStreamHandler(BaseHTTPRequestHandler):
    """HTTP Request handler for streaming MJPEG, tracking balls, and serving camera API."""

    camera: Optional[BinocularCamera] = None
    tracker: Optional[MultiBallTracker] = None
    save_dir: str = CAMERA_SAVE_DIR

    def log_message(self, format, *args):
        # Suppress noisy streaming HTTP log lines
        pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        query = parse_qs(url.query)

        if path == "/" or path == "/index.html":
            self._serve_dashboard()
        elif path == "/stream":
            mode = query.get("mode", ["tracking"])[0]
            self._serve_mjpeg_stream(mode)
        elif path == "/snapshot":
            mode = query.get("mode", ["tracking"])[0]
            self._serve_single_snapshot(mode)
        elif path == "/api/tracker":
            self._serve_tracker_status()
        elif path == "/api/captures":
            self._serve_captures_list()
        elif path.startswith("/captures/"):
            self._serve_static_capture(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def do_POST(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/api/capture":
            self._handle_api_capture()
        elif url.path == "/api/tracker":
            color = query.get("color", [None])[0]
            action = query.get("action", [None])[0]
            if self.tracker:
                if action == "clear":
                    self.tracker.clear_tracks()
                if color:
                    self.tracker.set_color(color)
            self._serve_tracker_status()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

    def _serve_dashboard(self):
        content = HTML_DASHBOARD.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _serve_mjpeg_stream(self, mode: str):
        if not self.camera:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Camera not initialized")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()

        try:
            while True:
                ret, frame = self.camera.read_raw()
                if not ret or frame is None:
                    time.sleep(0.01)
                    continue

                processed = self._process_frame_by_mode(frame, mode)
                ret_enc, jpeg = cv2.imencode(".jpg", processed, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ret_enc:
                    continue

                payload = jpeg.tobytes()
                self.wfile.write(b"--FRAME\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
                self.wfile.write(payload)
                self.wfile.write(b"\r\n")

                # Cap streaming rate at ~30 FPS
                time.sleep(0.03)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _process_frame_by_mode(self, frame: np.ndarray, mode: str) -> np.ndarray:
        """Transform raw stereo frame based on requested viewer mode."""
        if mode == "tracking":
            if self.tracker:
                processed, _ = self.tracker.process_frame(frame, is_stereo=True, annotate=True)
                return processed
            return frame
        elif mode == "stereo":
            return frame
        elif mode == "annotated":
            return self.camera.create_annotated(frame)
        elif mode == "left":
            left, _ = self.camera.split_stereo(frame)
            return left
        elif mode == "right":
            _, right = self.camera.split_stereo(frame)
            return right
        elif mode == "anaglyph":
            left, right = self.camera.split_stereo(frame)
            return self.camera.create_anaglyph(left, right)
        elif mode == "depth":
            left, right = self.camera.split_stereo(frame)
            return self.camera.compute_disparity(left, right)
        return frame

    def _serve_tracker_status(self):
        tracks_data = []
        target_color = "red"
        if self.tracker:
            target_color = self.tracker.target_color
            for t in self.tracker._tracks.values():
                if t.missed_frames <= 1:
                    if t.pos_vector_3d:
                        pos_str = f"[{t.pos_vector_3d[0]:+.1f}, {t.pos_vector_3d[1]:+.1f}, {t.pos_vector_3d[2]:+.1f}] cm"
                        mag_str = f"{t.pos_magnitude} cm"
                    else:
                        pos_str = f"[\u0394X:{t.pos_vector_2d[0]:+d}, \u0394Y:{t.pos_vector_2d[1]:+d}] px"
                        mag_str = f"{t.pos_magnitude} px"

                    tracks_data.append({
                        "id": t.track_id,
                        "x": t.centroid[0],
                        "y": t.centroid[1],
                        "radius": round(t.radius, 1),
                        "speed": round(t.speed, 1),
                        "depth_cm": t.depth_cm,
                        "pos_vector": pos_str,
                        "pos_magnitude": mag_str,
                        "pos_angle_deg": t.pos_angle_deg,
                    })

        payload = json.dumps({"target_color": target_color, "tracks": tracks_data}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_single_snapshot(self, mode: str):
        if not self.camera:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Camera not initialized")
            return
        frame = self.camera.capture_raw()
        processed = self._process_frame_by_mode(frame, mode)
        ret, jpeg = cv2.imencode(".jpg", processed, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if ret:
            payload = jpeg.tobytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Encoding failed")

    def _handle_api_capture(self):
        if not self.camera:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Camera not initialized")
            return
        saved = self.camera.save_capture(output_dir=self.save_dir)
        response_data = {"status": "ok", "saved_files": saved}
        payload = json.dumps(response_data).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_captures_list(self):
        out_dir = Path(self.save_dir)
        files = []
        if out_dir.exists():
            for f in sorted(out_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:16]:
                files.append({
                    "name": f.name,
                    "url": f"/captures/{f.name}",
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        payload = json.dumps({"files": files}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_static_capture(self, path: str):
        filename = path.replace("/captures/", "")
        file_path = Path(self.save_dir) / filename
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File Not Found")
            return

        with open(file_path, "rb") as f:
            data = f.read()

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)


class CameraWebServer:
    """
    Manager for the background HTTP camera streaming server.
    """

    def __init__(
        self,
        camera: BinocularCamera,
        port: int = CAMERA_WEB_PORT,
        save_dir: str = CAMERA_SAVE_DIR,
        tracker: Optional[MultiBallTracker] = None,
    ):
        self.camera = camera
        self.port = port
        self.save_dir = save_dir
        self.tracker = tracker or MultiBallTracker(target_color="red")
        self.server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self, block: bool = False):
        """Start the web server in background thread or blocking mode."""
        StereoStreamHandler.camera = self.camera
        StereoStreamHandler.tracker = self.tracker
        StereoStreamHandler.save_dir = self.save_dir

        self.server = ThreadingHTTPServer(("0.0.0.0", self.port), StereoStreamHandler)
        ip = self._get_local_ip()
        logger.info(f"Camera Web Stream Server active at http://{ip}:{self.port}/")

        if block:
            self.server.serve_forever()
        else:
            self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the web server."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            logger.info("Camera Web Stream Server stopped.")

    @staticmethod
    def _get_local_ip() -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except Exception:
            return "localhost"
        finally:
            s.close()
