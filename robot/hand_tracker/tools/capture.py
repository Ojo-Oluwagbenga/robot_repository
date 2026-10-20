#!/usr/bin/env python3
"""
Save frames from the stereo camera to use as extra training images.

  python3 tools/capture.py                       # window: SPACE = save, A = auto-save on/off, Q = quit
  python3 tools/capture.py --auto 0.5 --count 80 # no screen: save one frame every 0.5 s, 80 frames
  python3 tools/capture.py --eye both            # save left AND right eye images

Move the arm / fingers / lighting between shots. Also take ~10 frames with NO hand in view
(those teach the model not to see hands where there are none).
"""
import argparse
import os
import time
from pathlib import Path

import cv2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--width", type=int, default=2560)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--eye", choices=["left", "right", "both", "full"], default="left")
    ap.add_argument("--out", default="data/my_frames")
    ap.add_argument("--auto", type=float, default=0, help="seconds between automatic saves (0 = manual)")
    ap.add_argument("--count", type=int, default=0, help="stop after this many saves")
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(a.camera, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, a.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, a.height)
    ok, f = cap.read()
    if not ok:
        raise SystemExit("camera read failed")
    print(f"camera: {f.shape[1]}x{f.shape[0]}  saving to {out}/")
    window = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    auto = a.auto if a.auto > 0 else (0 if window else 1.0)
    last, saved = 0.0, 0
    while True:
        ok, f = cap.read()
        if not ok:
            continue
        h, w = f.shape[:2]
        stereo = w >= 2.2 * h
        if stereo and a.eye != "full":
            parts = {"left": [("L", f[:, : w // 2])], "right": [("R", f[:, w // 2:])],
                     "both": [("L", f[:, : w // 2]), ("R", f[:, w // 2:])]}[a.eye]
        else:
            parts = [("F", f)]
        now = time.time()
        save = auto and now - last >= auto
        if window:
            vis = cv2.resize(f, None, fx=min(1.0, 1280 / w), fy=min(1.0, 1280 / w))
            cv2.putText(vis, f"saved {saved}  {'AUTO' if auto else 'SPACE=save'}", (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            cv2.imshow("capture", vis)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q"):
                break
            if k == ord(" "):
                save = True
            if k == ord("a"):
                auto = 0 if auto else (a.auto or 0.5)
        if save:
            stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{int(now * 1000) % 1000:03d}"
            for tag, img in parts:
                cv2.imwrite(str(out / f"frame_{stamp}_{tag}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
            saved += 1
            last = now
            print("saved", saved)
            if a.count and saved >= a.count:
                break
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
