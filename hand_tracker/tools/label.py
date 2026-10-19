#!/usr/bin/env python3
"""
Click-to-label tool for new training frames (needs a screen; run it on your PC or the Pi desktop).

  python3 tools/label.py data/my_frames

For each image click, in this order:
  1. index fingertip      (red)
  2. thumb tip            (blue)
  3. two opposite corners of a box around the hand (wrist to fingertips)

Keys
  A        accept the current model's guess (shown as hollow circles), then fix anything wrong
  N        the tip you are asked for is not visible (hidden) -> skip it
  U        undo last click
  X        this frame has NO hand (negative example) -> next
  SPACE    save and go to the next image
  P        previous image          D   skip this image (leave unlabelled)
  Q        save everything and quit
Labels are written to <folder>/labels.json after every image.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
STEPS = ["index", "thumb", "corner1", "corner2"]
COL = {"index": (0, 0, 255), "thumb": (255, 0, 0), "corner1": (0, 200, 0), "corner2": (0, 200, 0)}


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    folder = Path(sys.argv[1])
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    lab_path = folder / "labels.json"
    labels = json.loads(lab_path.read_text()) if lab_path.exists() else {}
    model = None
    mp = HERE / "models" / "hand_tips.onnx"
    if mp.exists():
        try:
            from track_hand import TipModel
            model = TipModel(mp)
        except Exception as e:                       # labelling still works without the model
            print("model not loaded:", e)

    i = next((k for k, f in enumerate(files) if f.name not in labels), 0)
    state = {}

    def on_mouse(ev, x, y, flags, param):
        if ev == cv2.EVENT_LBUTTONDOWN:
            s = param["scale"]
            step = next((k for k in STEPS if k not in state), None)
            if step:
                state[step] = (int(x / s), int(y / s))

    cv2.namedWindow("label", cv2.WINDOW_AUTOSIZE)
    while 0 <= i < len(files):
        f = files[i]
        img = cv2.imread(str(f))
        state.clear()
        old = labels.get(f.name)
        if old and not old.get("negative"):
            for k in ("index", "thumb"):
                state[k] = tuple(old[k]) if old.get(k) else None
            state["corner1"], state["corner2"] = tuple(old["box"][:2]), tuple(old["box"][2:])
        guess = None
        if model is not None:
            c = model(img)
            if c and c[0]["conf"] > 0.25:
                guess = c[0]
        s = min(1.0, 1280 / img.shape[1], 800 / img.shape[0])
        if s >= 1.0:                                  # enlarge small frames for precise clicks
            s = max(1.0, min(2.5, 900 / img.shape[1], 700 / img.shape[0]))
        param = {"scale": s}
        cv2.setMouseCallback("label", on_mouse, param)
        while True:
            vis = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_LINEAR)
            if guess:
                for k in ("index", "thumb"):
                    x, y, _ = guess[k]
                    cv2.circle(vis, (int(x * s), int(y * s)), 9, COL[k], 1)
                b = [int(v * s) for v in guess["box"]]
                cv2.rectangle(vis, (b[0], b[1]), (b[2], b[3]), (0, 150, 0), 1)
            for k, p in state.items():
                if p is not None and k in ("index", "thumb"):
                    cv2.circle(vis, (int(p[0] * s), int(p[1] * s)), 6, COL[k], -1)
                    cv2.circle(vis, (int(p[0] * s), int(p[1] * s)), 6, (255, 255, 255), 1)
            if state.get("corner1") and state.get("corner2"):
                (x0, y0), (x1, y1) = state["corner1"], state["corner2"]
                cv2.rectangle(vis, (int(x0 * s), int(y0 * s)), (int(x1 * s), int(y1 * s)), (0, 255, 0), 2)
            elif state.get("corner1"):
                x0, y0 = state["corner1"]
                cv2.drawMarker(vis, (int(x0 * s), int(y0 * s)), (0, 255, 0), cv2.MARKER_CROSS, 16, 2)
            step = next((k for k in STEPS if k not in state), None)
            msg = {"index": "click INDEX tip (N = hidden)", "thumb": "click THUMB tip (N = hidden)",
                   "corner1": "click hand box corner 1", "corner2": "click hand box corner 2",
                   None: "SPACE = save & next"}[step]
            cv2.putText(vis, f"[{i + 1}/{len(files)}] {f.name}   {msg}", (8, 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.imshow("label", vis)
            k = cv2.waitKey(30) & 0xFF
            if k == ord("q"):
                lab_path.write_text(json.dumps(labels, indent=1))
                return
            if k == ord("u") and state:
                state.pop(list(state.keys())[-1])
            if k == ord("n") and step in ("index", "thumb"):
                state[step] = None
            if k == ord("a") and guess:
                state.clear()
                for kk in ("index", "thumb"):
                    state[kk] = (int(guess[kk][0]), int(guess[kk][1])) if guess[kk][2] > 0.3 else None
                b = guess["box"]
                state["corner1"], state["corner2"] = (int(b[0]), int(b[1])), (int(b[2]), int(b[3]))
            if k == ord("x"):
                labels[f.name] = {"negative": True}
                lab_path.write_text(json.dumps(labels, indent=1))
                i += 1
                break
            if k == ord("d"):
                i += 1
                break
            if k == ord("p"):
                i = max(0, i - 1)
                break
            if k in (ord(" "), 13) and step is None:
                (x0, y0), (x1, y1) = state["corner1"], state["corner2"]
                labels[f.name] = {
                    "index": list(state["index"]) if state["index"] else None,
                    "thumb": list(state["thumb"]) if state["thumb"] else None,
                    "box": [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                }
                lab_path.write_text(json.dumps(labels, indent=1))
                i += 1
                break
    lab_path.write_text(json.dumps(labels, indent=1))
    print(f"done: {len(labels)} labels in {lab_path}")
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
