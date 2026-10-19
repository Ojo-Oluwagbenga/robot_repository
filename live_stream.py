#!/usr/bin/env python3
"""
Dedicated Real-Time Camera Streamer for Raspberry Pi 5.
Streams low-latency live video to any browser at 30 FPS.

Usage:
  python3 live_stream.py
  python3 live_stream.py --port 8080
  python3 live_stream.py --device 0
  python3 live_stream.py --gui       # If you have a monitor/desktop connected
"""

import sys
import time
import socket
import argparse
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

try:
    import cv2
    import numpy as np
except ImportError:
    print("[ERROR] OpenCV not installed! Run: pip install opencv-python numpy")
    sys.exit(1)


DEFAULT_PORT = 8080
DEFAULT_DEVICE = 0

# Shared global state
_cap = None
_lock = threading.Lock()
_current_frame = None
_view_mode = "stereo"  # "stereo", "left", "right"
_running = True


def get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def camera_capture_loop(device_id: int):
    global _cap, _current_frame, _running
    print(f"[*] Opening /dev/video{device_id}...")
    _cap = cv2.VideoCapture(device_id, cv2.CAP_V4L2)
    if not _cap.isOpened():
        _cap = cv2.VideoCapture(device_id)

    if not _cap.isOpened():
        print(f"[ERROR] Could not open camera /dev/video{device_id}!")
        _running = False
        return

    _cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    _cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    _cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    _cap.set(cv2.CAP_PROP_FPS, 30)
    _cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    w = int(_cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(_cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = _cap.get(cv2.CAP_PROP_FPS)
    print(f"[OK] Camera streaming at {w}x{h} @ {fps:.1f} FPS")

    while _running:
        ret, frame = _cap.read()
        if ret and frame is not None:
            with _lock:
                _current_frame = frame
        else:
            time.sleep(0.01)


HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Robot Camera Live Stream</title>
  <style>
    body {
      background: #0b0f19;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    .header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }
    .badge {
      background: #ef4444;
      color: #fff;
      font-weight: 700;
      font-size: 0.75rem;
      padding: 4px 10px;
      border-radius: 999px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .badge::before {
      content: "";
      width: 8px;
      height: 8px;
      background: #fff;
      border-radius: 50%;
      animation: pulse 1.5s infinite;
    }
    @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    .toolbar {
      display: flex;
      gap: 8px;
      margin-bottom: 16px;
      flex-wrap: wrap;
      justify-content: center;
    }
    button {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.2);
      color: #fff;
      padding: 8px 16px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s;
    }
    button:hover {
      background: #00e5ff;
      color: #000;
      border-color: #00e5ff;
    }
    button.active {
      background: #00e5ff;
      color: #000;
      border-color: #00e5ff;
    }
    .stream-box {
      border: 2px solid #00e5ff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 0 24px rgba(0, 229, 255, 0.25);
      background: #000;
      max-width: 95vw;
    }
    #streamImg {
      display: block;
      width: 100%;
      height: auto;
      max-height: 80vh;
      object-fit: contain;
    }
  </style>
</head>
<body>
  <div class="header">
    <h2 style="margin:0;">LUKU Robot - Live Camera Feed</h2>
    <span class="badge">LIVE 30 FPS</span>
  </div>

  <div class="toolbar">
    <button class="active" onclick="setMode('stereo')">Full Stereo</button>
    <button onclick="setMode('left')">Left Eye</button>
    <button onclick="setMode('right')">Right Eye</button>
    <button onclick="takeSnapshot()">Save Snapshot</button>
  </div>

  <div class="stream-box">
    <img id="streamImg" src="/video_feed?mode=stereo" alt="Live Camera Feed">
  </div>

  <p style="margin-top: 14px; font-size: 0.85rem; color: #94a3b8;">
    Stream active on port <strong>8080</strong> | Real-time MJPEG Stream
  </p>

  <script>
    function setMode(mode) {
      document.querySelectorAll('.toolbar button').forEach(b => {
        b.classList.toggle('active', b.innerText.toLowerCase().includes(mode));
      });
      const img = document.getElementById('streamImg');
      img.src = '/video_feed?mode=' + mode + '&t=' + Date.now();
    }

    function takeSnapshot() {
      window.open('/snapshot.jpg', '_blank');
    }
  </script>
</body>
</html>
"""


class LiveStreamHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        global _current_frame

        if self.path in ("/", "/index.html"):
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif self.path.startswith("/video_feed"):
            mode = "stereo"
            if "mode=left" in self.path:
                mode = "left"
            elif "mode=right" in self.path:
                mode = "right"

            self.send_response(200)
            self.send_header("Cache-Control", "no-cache, private, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()

            try:
                while _running:
                    with _lock:
                        if _current_frame is None:
                            frame = None
                        else:
                            frame = _current_frame.copy()

                    if frame is None:
                        time.sleep(0.01)
                        continue

                    h, w = frame.shape[:2]
                    half_w = w // 2

                    if mode == "left":
                        disp = frame[:, :half_w]
                    elif mode == "right":
                        disp = frame[:, half_w:]
                    else:
                        disp = frame

                    ret, jpeg = cv2.imencode(".jpg", disp, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if not ret:
                        continue

                    raw = jpeg.tobytes()
                    self.wfile.write(b"--FRAME\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii"))
                    self.wfile.write(raw)
                    self.wfile.write(b"\r\n")

                    time.sleep(0.03)  # ~30 FPS
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif self.path.startswith("/snapshot"):
            with _lock:
                frame = None if _current_frame is None else _current_frame.copy()
            if frame is not None:
                ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                if ret:
                    raw = jpeg.tobytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Disposition", "inline; filename=snapshot.jpg")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
            self.send_error(503, "Camera busy or frame not ready")

        else:
            self.send_error(404)


def main():
    global _running

    parser = argparse.ArgumentParser(description="Live Camera Streamer for Raspberry Pi")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP Port (default: {DEFAULT_PORT})")
    parser.add_argument("--device", type=int, default=DEFAULT_DEVICE, help=f"Camera index (default: {DEFAULT_DEVICE})")
    parser.add_argument("--gui", action="store_true", help="Open local OpenCV desktop window")
    args = parser.parse_args()

    # Start camera capture thread
    cap_thread = threading.Thread(target=camera_capture_loop, args=(args.device,), daemon=True)
    cap_thread.start()

    # Wait for first frame
    print("[*] Waiting for camera feed...")
    for _ in range(50):
        with _lock:
            if _current_frame is not None:
                break
        time.sleep(0.1)

    with _lock:
        if _current_frame is None:
            print("[ERROR] Camera did not produce any frames! Check /dev/video0 connection.")
            sys.exit(1)

    # Start Web Server
    server = ThreadingHTTPServer(("0.0.0.0", args.port), LiveStreamHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    local_ip = get_local_ip()
    print("\n" + "=" * 65)
    print(f"  >>> LIVE CAMERA STREAM IS RUNNING! <<<")
    print("=" * 65)
    print(f"  Open this link in your browser on your phone / PC:")
    print(f"  -> http://{local_ip}:{args.port}")
    print(f"  -> http://raspberrypi.local:{args.port}")
    print("=" * 65)
    print("  Press Ctrl+C to stop the stream.\n")

    try:
        if args.gui:
            while _running:
                with _lock:
                    frame = None if _current_frame is None else _current_frame.copy()
                if frame is not None:
                    cv2.imshow("Robot Live Camera", frame)
                if cv2.waitKey(30) & 0xFF in (27, ord('q')):
                    break
        else:
            while _running:
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping live stream...")
    finally:
        _running = False
        if _cap is not None:
            _cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
