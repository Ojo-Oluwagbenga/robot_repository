# Robot hand fingertip tracker

Tracks your 3D-printed robot hand in the stereo camera and draws a live
**red dot on the index fingertip** and a **blue dot on the thumb tip**.
Runs fully on the Raspberry Pi 5 (no internet, no GPU) using a small
YOLO11n-pose model exported to ONNX (~10 MB).

```
hand_tracker/
├── track_hand.py          <- run this on the Pi (live stream)
├── models/hand_tips.onnx  <- the trained model (hand_tips.pt = same model, for retraining)
├── setup_pi.sh            <- one-time install on the Pi
├── requirements-pi.txt
├── requirements-train.txt <- only for retraining (PC)
├── tools/
│   ├── capture.py         <- save frames from the stereo camera
│   ├── label.py           <- click index / thumb / box on those frames
│   ├── make_dataset.py    <- turns labelled photos into thousands of training images
│   └── train.py           <- trains + exports models/hand_tips.onnx
├── data/
│   ├── photos/            <- your 6 photos + labels.json (what the model was trained on)
│   ├── stereo_frames/     <- your stereo capture, both eyes, with the tips you marked
│   └── backgrounds/       <- hand-free crops from your stereo camera (desk clutter)
├── samples/               <- a test video + your stereo capture, to try it without the camera
└── results/               <- what the model produced on my side (photos, stereo capture, video)
```

---

## 1. Copy the folder to the Pi

From your PC (replace the user / host with yours):

```bash
scp -r hand_tracker  <user>@<pi-hostname>.local:~/
```

or unzip `hand_tracker.zip` on the Pi.

## 2. Install (once)

```bash
cd ~/hand_tracker
bash setup_pi.sh
```

This creates a virtual environment in `.venv` and installs `numpy`,
`opencv-python` and `onnxruntime` (all have ARM64 wheels for Python 3.13).
Run it on the Pi itself, not inside your ROS 2 Docker container.

## 3. Check the camera

```bash
v4l2-ctl --list-devices                         # find the camera (usually /dev/video0)
v4l2-ctl -d /dev/video0 --list-formats-ext      # see which sizes it supports
```

The script asks for the full side-by-side frame at **2560x720** by default.
If your camera gives **1280x480** instead (your stereo screenshot looks like
that mode), pass `--width 1280 --height 480`.

Before using the camera, check that everything works with the included test video:

```bash
. .venv/bin/activate
python3 track_hand.py --source samples/test_stereo.mp4 --loop          # with a screen
python3 track_hand.py --source samples/test_stereo.mp4 --loop --web    # headless
```

## 4. Run the live tracker

Every time:

```bash
cd ~/hand_tracker
. .venv/bin/activate
```

**Pi with a monitor / desktop:**
```bash
python3 track_hand.py
```

**Headless / over SSH** (a stream you watch in a browser on your PC or phone):
```bash
python3 track_hand.py --web
# then open  http://<pi-ip>:8080   (the script prints the address)
```

Useful options:

| option | what it does |
|---|---|
| `--camera 1` | use /dev/video1 |
| `--width 1280 --height 480` | other stereo resolution |
| `--eye both` (default) | track in both eyes and only trust what they agree on (see below). Also gives index/thumb **disparity** → depth |
| `--eye left` / `--eye right` | one eye only: about twice the FPS, no stereo checks |
| `--mono` | camera is a normal (not side-by-side) webcam |
| `--kconf 0.6` | only draw a dot when the model is more sure (fewer wrong dots, more gaps) |
| `--conf 0.5` | stricter "is there a hand" threshold |
| `--print` | print the tip coordinates as JSON each frame |
| `--udp 192.168.1.20:5005` | send the JSON to another machine (PC brain, ROS node, ESP32 bridge…) |
| `--save out.mp4` | record the annotated stream |
| `--source some_video.mp4` / `--source folder/` | test without the camera |

Keys in the window: **q** quit, **s** snapshot, **b** show/hide the hand box.

Dot meanings: **filled** = confident, **hollow ring** = unsure: either briefly
lost (last position shown for up to 6 frames), or the two eyes disagree on where
that tip is.

### How "confident" is decided (`--eye both`)
1. The model must find the hand (confidence ≥ 0.40 to start tracking, ≥ 0.20 to keep it).
2. The hand must appear in **both** eyes at the same height and size. A detection
   in only one eye is ignored unless it scores ≥ 0.70. This stopped a white plastic
   bag on your desk from being taken for the hand.
3. Each fingertip must sit on the **same image row** in both eyes (the camera's
   two lenses are side by side). If not, it's drawn hollow and `live` is false.
4. Positions are smoothed with a One-Euro filter, so dots are steady when the hand is
   still and follow quickly when it moves.

JSON per frame (also at `http://<pi-ip>:8080/tips` in web mode):
```json
{"t": 1760870000.12,
 "left": {"hand": 0.83,
          "index": {"x": 212.4, "y": 98.1, "conf": 0.91, "live": true},
          "thumb": {"x": 260.0, "y": 140.7, "conf": 0.88, "live": true}}}
```
Coordinates are pixels in that eye's image. With `--eye both` you also get
`"stereo_agree"` and `"disparity": {"index": …, "thumb": …}` (left x − right x;
bigger = closer to the camera).

**Speed:** about 19 ms per eye on my 2-core test machine. I couldn't run it on a
Pi; expect very roughly 10–20 FPS with both eyes on a Pi 5, about double with
`--eye left`.

## 5. How good is it, honestly?

Trained on your 6 phone photos + your stereo capture (both eyes, with the
index/thumb positions you marked), expanded to ~2,200 synthetic variations.

| test | result |
|---|---|
| your 6 photos | hand found every time (0.87–0.90); tips within 1–6 % of the hand's size |
| your stereo capture | hand found in both eyes (0.90 / 0.89). Thumb on your mark in both eyes; index on your mark in the right eye, slightly above it in the left, so the stereo check draws it hollow ("unsure") |
| previous model on that stereo capture | dots 31–56 % of the hand away, i.e. on the wrong fingers. One real labelled frame fixed that |
| photo 6 when **held out** of an earlier training run | hand found, but the index dot jumped to the thumb. New viewpoints are still the weak spot |

So: **finding the hand is solid; index vs thumb is right in the views it has
seen and uncertain in new ones.** Every labelled frame from the stereo camera
helps a lot (next section).

## 6. Make it more confident on YOUR camera (recommended)

The shipped model learned from only 6 phone photos plus thousands of
synthetic variations of them (new angles, sizes, backgrounds, blur, haze,
clutter, hands cut off at the frame edge). It has never seen a labelled frame
from the stereo camera itself. Twenty to forty labelled real frames make the
biggest difference:

```bash
# on the Pi: grab ~40 frames while moving the arm/fingers and changing light,
# plus ~10 frames with NO hand in view
python3 tools/capture.py                     # SPACE = save, A = auto-save, Q = quit
python3 tools/capture.py --auto 0.5 --count 50   # headless version

# copy data/my_frames to your PC, then label (needs a screen):
python3 tools/label.py data/my_frames
#   click index tip, thumb tip, then 2 corners of the hand box, SPACE = next
#   A = accept the model's guess, N = tip hidden, X = no hand in frame

# retrain on your PC (Core i7, no GPU ≈ 1–2 h). Fine-tune from the shipped model:
pip install -r requirements-train.txt
python3 tools/make_dataset.py --labels data/photos/labels.json \
        --labels data/stereo_frames/labels.json \
        --labels data/my_frames/labels.json --out data/dataset \
        --extra-bg data/backgrounds --n-train 3000 --n-val 300
python3 tools/train.py --data data/dataset/data.yaml --weights models/hand_tips.pt \
        --epochs 20 --lr0 0.0005

# copy the new models/hand_tips.onnx back to the Pi
```

Your PC has only 4 GB RAM. If training runs out of memory, add `--batch 8`.

## Troubleshooting

* **"Could not read from camera"**: try `--camera 1`, and check `ls /dev/video*`.
  Close any other program using the camera (e.g. the ROS container).
* **Low FPS**: use `--eye left` (default), not `both`. Check the Pi isn't
  thermal-throttling (`vcgencmd measure_temp`).
* **Dots jump to the wrong finger**: add real frames as in section 6. The thumb
  and index swap most easily in poses unlike the 6 training photos.
* **Tips are always hollow with `--eye both`**: your camera's halves may not be
  perfectly aligned. Loosen the row check with `--row-tol 0.25`, or turn it
  off with `--no-row-check`.
* **Hand not found when it's half out of view of one eye**: use `--eye left`.
* **No window appears over SSH**: that's expected; use `--web`.
