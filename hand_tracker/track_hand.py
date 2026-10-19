#!/usr/bin/env python3
"""
Live robot-hand fingertip tracker for Raspberry Pi 5 + USB stereo camera.

  RED dot  = index fingertip
  BLUE dot = thumb tip

Examples
  python3 track_hand.py                         # camera 0, left eye, window on the Pi's screen
  python3 track_hand.py --web                   # no screen: open http://<pi-ip>:8080 on PC/phone
  python3 track_hand.py --eye both              # track in both eyes (prints stereo disparity)
  python3 track_hand.py --source clip.mp4       # test on a video file
  python3 track_hand.py --source photos/        # test on a folder of images (press any key = next)
  python3 track_hand.py --udp 192.168.1.20:5005 # also send tip coordinates as JSON over UDP

Keys in the window:  q = quit,  s = save snapshot,  b = toggle hand box
"""
import argparse
import glob
import json
import math
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
RED, BLUE, WHITE, GREEN = (0, 0, 255), (255, 0, 0), (255, 255, 255), (0, 200, 0)
TIP_NAMES = ("index", "thumb")
TIP_COLORS = {"index": RED, "thumb": BLUE}


# ============================================================== model
class TipModel:
    """YOLO-pose ONNX model: 1 class (hand), 2 keypoints (index tip, thumb tip)."""

    def __init__(self, path, threads=4):
        self.backend = "onnxruntime"
        try:
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
            inp = self.sess.get_inputs()[0]
            self.inp = inp.name
            self.size = int(inp.shape[2])
            self._run = lambda x: self.sess.run(None, {self.inp: x})[0]
        except ImportError:
            self.backend = "opencv-dnn"
            net = cv2.dnn.readNetFromONNX(str(path))
            self.size = 320

            def run(x):
                net.setInput(x)
                return net.forward()
            self._run = run

    def __call__(self, bgr):
        """Returns list of candidates sorted by confidence:
           {conf, box:(x0,y0,x1,y1), index:(x,y,c), thumb:(x,y,c)} in image pixels."""
        h, w = bgr.shape[:2]
        S = self.size
        s = S / max(h, w)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        px, py = (S - nw) // 2, (S - nh) // 2
        canvas = np.full((S, S, 3), 114, np.uint8)
        canvas[py:py + nh, px:px + nw] = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)
        blob = np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
        out = self._run(blob)[0].T                     # (anchors, 4 + 1 + 2*3)
        conf = out[:, 4]
        order = np.argsort(-conf)[:5]
        cands = []
        for i in order:
            if conf[i] < 0.05:
                break
            cx, cy, bw, bh = out[i, :4]
            k = out[i, 5:].reshape(-1, 3)
            box = ((cx - bw / 2 - px) / s, (cy - bh / 2 - py) / s, (cx + bw / 2 - px) / s, (cy + bh / 2 - py) / s)
            d = {"conf": float(conf[i]), "box": tuple(float(v) for v in box)}
            for n, (kx, ky, kc) in zip(TIP_NAMES, k):
                d[n] = ((kx - px) / s, (ky - py) / s, float(kc))
            cands.append(d)
        return cands


# ============================================================== smoothing / tracking
class OneEuro:
    """One-Euro filter: smooth when still, responsive when moving."""

    def __init__(self, min_cutoff=1.5, beta=0.02, d_cutoff=1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _a(cut, dt):
        r = 2 * math.pi * cut * dt
        return r / (r + 1)

    def __call__(self, x, t):
        x = np.asarray(x, float)
        if self.x is None:
            self.x, self.dx, self.t = x, np.zeros_like(x), t
            return x
        dt = max(t - self.t, 1e-3)
        dx = (x - self.x) / dt
        ad = self._a(self.dc, dt)
        self.dx = ad * dx + (1 - ad) * self.dx
        cut = self.mc + self.beta * np.linalg.norm(self.dx)
        a = self._a(cut, dt)
        self.x = a * x + (1 - a) * self.x
        self.t = t
        return self.x

    def reset(self):
        self.x = self.dx = self.t = None


class TipTracker:
    """Picks the hand, applies hysteresis + smoothing, holds briefly when lost."""

    def __init__(self, conf=0.40, keep_conf=0.20, kconf=0.45, hold=6):
        self.conf, self.keep_conf, self.kconf, self.hold = conf, keep_conf, kconf, hold
        self.filters = {n: OneEuro() for n in TIP_NAMES}
        self.last = {n: None for n in TIP_NAMES}
        self.missed = {n: 99 for n in TIP_NAMES}
        self.tracking = False
        self.box = None
        self.det_conf = 0.0

    def update(self, cands, t):
        thr = self.keep_conf if self.tracking else self.conf
        det = None
        if cands:
            if self.tracking and self.box is not None and len(cands) > 1:
                # prefer the candidate closest to where the hand was
                bx = ((self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2)
                cands = sorted(cands, key=lambda c: -c["conf"] + 0.001 * math.dist(
                    bx, ((c["box"][0] + c["box"][2]) / 2, (c["box"][1] + c["box"][3]) / 2)))
            if cands[0]["conf"] >= thr:
                det = cands[0]
        self.tracking = det is not None
        self.det_conf = det["conf"] if det else 0.0
        self.box = det["box"] if det else None
        out = {}
        for n in TIP_NAMES:
            if det and det[n][2] >= self.kconf:
                x, y, c = det[n]
                prev = self.last[n]
                size = max(det["box"][2] - det["box"][0], det["box"][3] - det["box"][1], 1)
                if prev is not None and math.dist(prev, (x, y)) > 0.6 * size:
                    self.filters[n].reset()            # big jump: don't drag the old position along
                p = self.filters[n]((x, y), t)
                self.last[n] = (float(p[0]), float(p[1]))
                self.missed[n] = 0
                out[n] = {"x": self.last[n][0], "y": self.last[n][1], "conf": round(c, 3), "live": True}
            else:
                self.missed[n] += 1
                if self.last[n] is not None and self.missed[n] <= self.hold:
                    out[n] = {"x": self.last[n][0], "y": self.last[n][1], "conf": 0.0, "live": False}
                else:
                    self.last[n] = None
                    self.filters[n].reset()
        return out


def stereo_pair(cl, cr, h, single_conf):
    """Side-by-side stereo: the real hand appears in BOTH eyes at about the same height and size,
    shifted left in the right eye. Keep the best pair that agrees; reject one-eyed detections
    unless they are very confident."""
    best, best_score = None, -1.0
    for a in cl[:5]:
        for b in cr[:5]:
            ay, by = (a["box"][1] + a["box"][3]) / 2, (b["box"][1] + b["box"][3]) / 2
            ah, bh = a["box"][3] - a["box"][1], b["box"][3] - b["box"][1]
            ax, bx = (a["box"][0] + a["box"][2]) / 2, (b["box"][0] + b["box"][2]) / 2
            if abs(ay - by) > 0.2 * max(ah, bh, 0.05 * h):
                continue
            if not 0.6 < (ah + 1) / (bh + 1) < 1.66:
                continue
            if ax - bx < -0.08 * h:                     # right-eye image must not be to the right
                continue
            sc = a["conf"] + b["conf"]
            if sc > best_score:
                best, best_score = (a, b), sc
    if best:
        return [best[0]], [best[1]], True
    keep = lambda c: [c[0]] if c and c[0]["conf"] >= single_conf else []
    return keep(cl), keep(cr), False


# ============================================================== drawing
def draw(frame, tips, box=None, conf=None, show_box=True):
    h, w = frame.shape[:2]
    r = max(5, int(min(h, w) / 45))
    if show_box and box is not None:
        x0, y0, x1, y1 = [int(v) for v in box]
        cv2.rectangle(frame, (x0, y0), (x1, y1), GREEN, 1)
        if conf is not None:
            cv2.putText(frame, f"hand {conf:.2f}", (x0, max(12, y0 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, GREEN, 1)
    for n, p in tips.items():
        c = (int(round(p["x"])), int(round(p["y"])))
        col = TIP_COLORS[n]
        if p["live"]:
            cv2.circle(frame, c, r, col, -1, cv2.LINE_AA)
            cv2.circle(frame, c, r, WHITE, 2, cv2.LINE_AA)
        else:                                          # held (briefly lost): hollow ring
            cv2.circle(frame, c, r, col, 2, cv2.LINE_AA)
        cv2.putText(frame, n, (c[0] + r + 3, c[1] + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
    return frame


# ============================================================== sources
class CameraGrabber:
    """Reads frames in a background thread so we always process the newest one."""

    def __init__(self, index, width, height, fps):
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(index)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if fps:
            self.cap.set(cv2.CAP_PROP_FPS, fps)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, f = self.cap.read()
        if not ok:
            raise SystemExit(f"Could not read from camera {index}. Check `ls /dev/video*` and try --camera 1")
        print(f"camera {index}: {f.shape[1]}x{f.shape[0]}")
        self.frame, self.id, self.ok = f, 0, True
        self.lock = threading.Lock()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.ok:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self.lock:
                self.frame, self.id = f, self.id + 1

    def read(self, last_id):
        while True:
            with self.lock:
                if self.id != last_id:
                    return self.frame, self.id
            time.sleep(0.002)

    def release(self):
        self.ok = False
        time.sleep(0.05)
        self.cap.release()


def file_frames(src):
    p = Path(src)
    if p.is_dir() or any(ch in src for ch in "*?"):
        files = sorted(glob.glob(str(p / "*") if p.is_dir() else src))
        files = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp"))]
        for f in files:
            im = cv2.imread(f)
            if im is not None:
                yield im, f
        return
    if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        yield cv2.imread(src), src
        return
    cap = cv2.VideoCapture(src)
    while True:
        ok, f = cap.read()
        if not ok:
            break
        yield f, None
    cap.release()


def split_eyes(frame, mode, force_stereo=None):
    """Return list of (name, image, x_offset) for the eyes we want."""
    h, w = frame.shape[:2]
    stereo = force_stereo if force_stereo is not None else (w >= 2.2 * h)
    if not stereo or mode == "full":
        return [("full", frame, 0)]
    half = w // 2
    L, R = frame[:, :half], frame[:, half:half * 2]
    if mode == "left":
        return [("left", L, 0)]
    if mode == "right":
        return [("right", R, 0)]
    return [("left", L, 0), ("right", R, half)]


# ============================================================== web stream
class WebStream:
    def __init__(self, port):
        self.jpg = None
        self.data = {}
        self.cond = threading.Condition()
        stream = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            with stream.cond:
                                stream.cond.wait(timeout=2)
                                jpg = stream.jpg
                            if jpg is None:
                                continue
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                             + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
                elif self.path.startswith("/tips"):
                    body = json.dumps(stream.data).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = (b"<html><head><title>Hand tracker</title><meta name=viewport content='width=device-width'>"
                            b"</head><body style='margin:0;background:#111;color:#ddd;font-family:sans-serif'>"
                            b"<img src='/stream' style='width:100%;max-width:1400px;display:block;margin:auto'>"
                            b"<pre id=t style='padding:8px'></pre><script>setInterval(()=>fetch('/tips').then(r=>r.json())"
                            b".then(j=>t.textContent=JSON.stringify(j)),300)</script></body></html>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(body)

        srv = ThreadingHTTPServer(("0.0.0.0", port), H)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"web stream: http://{local_ip()}:{port}  (open on your PC or phone)")

    def push(self, frame, data):
        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with self.cond:
            self.jpg = enc.tobytes()
            self.data = data
            self.cond.notify_all()


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "localhost"


# ============================================================== main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=str(HERE / "models" / "hand_tips.onnx"))
    ap.add_argument("--source", help="video file, image, or folder of images (default: camera)")
    ap.add_argument("--loop", action="store_true", help="repeat a video/image source forever")
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=2560, help="requested camera width (whole stereo frame)")
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--eye", choices=["left", "right", "both", "full"], default="both",
                    help="both = track in both eyes and keep only detections that agree (most reliable)")
    ap.add_argument("--mono", action="store_true", help="camera is not side-by-side stereo")
    ap.add_argument("--conf", type=float, default=0.40, help="hand confidence to start tracking")
    ap.add_argument("--keep-conf", type=float, default=0.20, help="hand confidence to keep tracking")
    ap.add_argument("--kconf", type=float, default=0.45, help="fingertip confidence to draw a dot")
    ap.add_argument("--row-tol", type=float, default=0.12,
                    help="--eye both: max height difference of a tip between eyes (fraction of hand height)")
    ap.add_argument("--no-row-check", action="store_true", help="draw tips even if the two eyes disagree")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--web", nargs="?", const=8080, type=int, help="serve MJPEG stream on this port (default 8080)")
    ap.add_argument("--no-window", action="store_true")
    ap.add_argument("--udp", help="HOST:PORT to send JSON tip coordinates every frame")
    ap.add_argument("--print", action="store_true", help="print JSON tip coordinates every frame")
    ap.add_argument("--save", help="write the annotated stream to this .mp4 (or folder for image sources)")
    ap.add_argument("--display-width", type=int, default=1280, help="max width of the shown/streamed image")
    a = ap.parse_args()

    if not Path(a.model).exists():
        raise SystemExit(f"model not found: {a.model}")
    model = TipModel(a.model, a.threads)
    print(f"model: {Path(a.model).name}  input {model.size}px  backend {model.backend}")

    headless = not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    show_window = not a.no_window and not headless
    if headless and a.web is None and not a.no_window and not a.source:
        print("no screen detected -> starting web stream on port 8080")
        a.web = 8080
    web = WebStream(a.web) if a.web else None
    udp = None
    if a.udp:
        host, port = a.udp.rsplit(":", 1)
        udp = (socket.socket(socket.AF_INET, socket.SOCK_DGRAM), (host, int(port)))

    trackers = {}
    show_box = True
    writer = None
    still_images = False
    if a.source:
        frames = file_frames(a.source)
        still_images = Path(a.source).is_dir() or Path(a.source).suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp") \
            or any(ch in a.source for ch in "*?")
        cam = None
    else:
        cam = CameraGrabber(a.camera, a.width, a.height, a.fps)
    last_id, fps, t_prev, n = -1, 0.0, time.time(), 0
    force_stereo = False if a.mono else None

    try:
        while True:
            if cam:
                frame, last_id = cam.read(last_id)
                name = None
            else:
                try:
                    frame, name = next(frames)
                except StopIteration:
                    if not a.loop:
                        break
                    frames = file_frames(a.source)
                    continue
                if not still_images:
                    time.sleep(max(0.0, 1 / 15 - (time.time() - t_prev)))   # play files at ~15 fps
                if still_images:
                    trackers = {}                       # independent photos: no smoothing between them
            t = time.time()
            eyes = split_eyes(frame, a.eye, force_stereo)
            panels, payload = [], {"t": round(t, 3)}
            cands = {eye: model(img) for eye, img, _ in eyes}
            if len(eyes) == 2:
                cands["left"], cands["right"], agreed = stereo_pair(
                    cands["left"], cands["right"], eyes[0][1].shape[0], single_conf=min(0.95, a.conf + 0.3))
                payload["stereo_agree"] = agreed
            all_tips = {}
            for eye, img, _ in eyes:
                tr = trackers.setdefault(eye, TipTracker(a.conf, a.keep_conf, a.kconf))
                all_tips[eye] = tr.update(cands[eye], t)
            if len(eyes) == 2:
                # a real fingertip sits on the same image row in both eyes; if not, it's a guess
                hL = trackers["left"].box
                hgt = (hL[3] - hL[1]) if hL else eyes[0][1].shape[0] / 4
                for n_ in TIP_NAMES:
                    l, r = all_tips["left"].get(n_), all_tips["right"].get(n_)
                    if l and r and l["live"] and r["live"]:
                        ok = abs(l["y"] - r["y"]) <= a.row_tol * hgt
                        l["stereo_ok"] = r["stereo_ok"] = ok
                        if ok:
                            payload.setdefault("disparity", {})[n_] = round(l["x"] - r["x"], 1)
                        elif not a.no_row_check:
                            l["live"] = r["live"] = False
            for eye, img, _ in eyes:
                tips, tr = all_tips[eye], trackers[eye]
                payload[eye] = {"hand": round(tr.det_conf, 3), **{k: {kk: (round(vv, 1) if isinstance(vv, float) else vv)
                                                                      for kk, vv in v.items()} for k, v in tips.items()}}
                panels.append(draw(img.copy(), tips, tr.box, tr.det_conf, show_box))
            vis = np.hstack(panels) if len(panels) > 1 else panels[0]

            n += 1
            dt = t - t_prev
            t_prev = t
            fps = 0.9 * fps + 0.1 * (1.0 / max(dt, 1e-3)) if n > 1 else 0.0
            if not still_images:
                cv2.putText(vis, f"{fps:4.1f} fps", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            if vis.shape[1] > a.display_width:
                k = a.display_width / vis.shape[1]
                vis = cv2.resize(vis, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)

            if a.print:
                print(json.dumps(payload) if not name else f"{name}: {json.dumps(payload)}", flush=True)
            if udp:
                udp[0].sendto(json.dumps(payload).encode(), udp[1])
            if web:
                web.push(vis, payload)
            if a.save:
                if still_images:
                    Path(a.save).mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(Path(a.save) / (Path(name).stem + "_tips.jpg")), vis)
                else:
                    if writer is None:
                        writer = cv2.VideoWriter(a.save, cv2.VideoWriter_fourcc(*"mp4v"), 15, (vis.shape[1], vis.shape[0]))
                    writer.write(vis)
            if show_window:
                cv2.imshow("hand tracker  (q quit, s snapshot, b box)", vis)
                key = cv2.waitKey(0 if still_images else 1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    fn = f"snapshot_{time.strftime('%H%M%S')}.jpg"
                    cv2.imwrite(fn, vis)
                    print("saved", fn)
                if key == ord("b"):
                    show_box = not show_box
    except KeyboardInterrupt:
        pass
    finally:
        if cam:
            cam.release()
        if writer:
            writer.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
