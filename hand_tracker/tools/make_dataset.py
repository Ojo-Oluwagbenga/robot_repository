#!/usr/bin/env python3
"""
Turn a handful of labelled photos of the robot hand into a large YOLO-pose
training set (index tip + thumb tip), by:
  * cutting the hand out (GrabCut, seeded by your box) and pasting it onto
    new backgrounds at new sizes / angles / positions,
  * re-framing the original photo (rotate, zoom, shift, flip),
  * making images look like the cheap USB stereo camera (low-res, blur, haze,
    colour cast, JPEG artefacts, noise),
  * adding images with no hand at all, so the model learns to say "no hand".

labels.json format (written by tools/label.py):
{
  "frame_001.jpg": {"index": [x, y], "thumb": [x, y], "box": [x0, y0, x1, y1]},
  "frame_002.jpg": {"index": null, "thumb": [x, y], "box": [...]},   # null = not visible
  "empty_001.jpg": {"negative": true}                                  # no hand in view
}
Paths are relative to the folder that holds labels.json.

Usage:
  python3 tools/make_dataset.py --labels data/photos/labels.json \
      --labels data/my_frames/labels.json --out data/dataset --n-train 3000 --n-val 300
"""
import argparse
import json
import math
import os
import random
from pathlib import Path

import cv2
import numpy as np

KPTS = ("index", "thumb")


# ---------------------------------------------------------------- loading
def load_items(label_files, mask_cache):
    items, negatives = [], []
    for lf in label_files:
        lf = Path(lf)
        labels = json.loads(lf.read_text())
        for name, lab in labels.items():
            path = lf.parent / name
            img = cv2.imread(str(path))
            if img is None:
                print("  ! cannot read", path)
                continue
            if lab.get("negative"):
                negatives.append(img)
                continue
            box = [int(v) for v in lab["box"]]
            kp = [lab.get(k) for k in KPTS]
            mask = hand_mask(img, box, kp, mask_cache / (lf.parent.name + "__" + Path(name).stem + ".png"))
            items.append(dict(name=name, img=img, box=box, kp=kp, mask=mask))
    return items, negatives


def hand_mask(img, box, kp, cache_path):
    """GrabCut cut-out of the hand inside its box (cached)."""
    if cache_path.exists():
        m = cv2.imread(str(cache_path), cv2.IMREAD_GRAYSCALE)
        if m is not None and m.shape == img.shape[:2]:
            return m
    h, w = img.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    scale = min(1.0, 900 / max(h, w))
    small = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1 else img.copy()
    m = np.full(small.shape[:2], cv2.GC_BGD, np.uint8)
    sx0, sy0, sx1, sy1 = [int(v * scale) for v in (x0, y0, x1, y1)]
    m[sy0:sy1, sx0:sx1] = cv2.GC_PR_FGD
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    sub = m[sy0:sy1, sx0:sx1]
    sub[gray[sy0:sy1, sx0:sx1] < 45] = cv2.GC_PR_BGD          # hand is bright, dark = background
    for p in kp:
        if p:
            cv2.circle(m, (int(p[0] * scale), int(p[1] * scale)), 3, cv2.GC_FGD, -1)
    try:
        bgm, fgm = np.zeros((1, 65)), np.zeros((1, 65))
        cv2.grabCut(small, m, None, bgm, fgm, 6, cv2.GC_INIT_WITH_MASK)
        out = np.where((m == cv2.GC_FGD) | (m == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    except cv2.error:
        out = np.zeros(small.shape[:2], np.uint8)
    out = cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats((out > 127).astype(np.uint8))
    if n > 1:
        k = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        out = np.where(lab == k, 255, 0).astype(np.uint8)
    # bad cut-out -> fall back to the plain box
    if (out > 0).sum() < 0.2 * max(1, (x1 - x0) * (y1 - y0)):
        out = np.zeros((h, w), np.uint8)
        out[y0:y1, x0:x1] = 255
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(cache_path), out)
    return out


HARD = []      # hand-free frames from the real camera (desk clutter etc.) - sampled more often


def background_pool(items, negatives, extra_dirs, n=300):
    """Random crops that do not contain the hand."""
    pool = []
    HARD.clear()
    for img in negatives:
        HARD.append(img)
    for d in extra_dirs or []:
        for p in Path(d).glob("*"):
            im = cv2.imread(str(p))
            if im is not None:
                HARD.append(im)
    tries = 0
    while len(pool) < n and tries < n * 30:
        tries += 1
        it = random.choice(items)
        img, (x0, y0, x1, y1) = it["img"], it["box"]
        h, w = img.shape[:2]
        cw = random.randint(int(0.2 * w), int(0.6 * w))
        ch = int(cw * random.choice([0.75, 0.5625]))
        if ch >= h:
            continue
        cx, cy = random.randint(0, w - cw), random.randint(0, h - ch)
        ix = max(0, min(cx + cw, x1) - max(cx, x0))
        iy = max(0, min(cy + ch, y1) - max(cy, y0))
        if ix * iy > 0.03 * cw * ch:
            continue
        pool.append(img[cy:cy + ch, cx:cx + cw].copy())
    return pool


def synthetic_bg(W, H):
    base = np.random.randint(0, 256, 3)
    g = np.linspace(0, 1, W)[None, :, None] * (np.random.randint(-80, 80, 3))
    img = np.clip(base + g + np.zeros((H, W, 3)), 0, 255).astype(np.uint8)
    for _ in range(random.randint(3, 15)):
        c = tuple(int(v) for v in np.random.randint(0, 256, 3))
        if random.random() < 0.5:
            p1 = (random.randint(0, W), random.randint(0, H))
            p2 = (random.randint(0, W), random.randint(0, H))
            cv2.rectangle(img, p1, p2, c, -1)
        else:
            cv2.line(img, (random.randint(0, W), random.randint(0, H)),
                     (random.randint(0, W), random.randint(0, H)), c, random.randint(2, 25))
    return cv2.GaussianBlur(img, (0, 0), random.uniform(0.5, 3))


def random_bg(pool, W, H):
    r = random.random()
    if r < 0.12 or not (pool or HARD):
        return synthetic_bg(W, H)
    b = random.choice(HARD) if (HARD and (r < 0.45 or not pool)) else random.choice(pool)
    if random.random() < 0.5:
        b = cv2.flip(b, 1)
    h, w = b.shape[:2]
    # random crop with target aspect
    ta = W / H
    if w / h > ta:
        cw, ch = int(h * ta), h
    else:
        cw, ch = w, int(w / ta)
    s = random.uniform(0.6, 1.0)
    cw, ch = max(8, int(cw * s)), max(8, int(ch * s))
    x, y = random.randint(0, w - cw), random.randint(0, h - ch)
    return cv2.resize(b[y:y + ch, x:x + cw], (W, H), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------- geometry
def make_affine(box, W, H, flip):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if random.random() < 0.3:
        ang = random.uniform(-180, 180)
    else:
        ang = random.uniform(-40, 40)
    target = random.uniform(0.25, 0.95) * min(W, H * 1.6)       # size of the hand's long side
    s = target / max(bw, bh)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if random.random() < 0.2:                                  # hand partly out of frame
        tx = random.uniform(-0.1, 1.1) * W
        ty = random.uniform(-0.05, 1.05) * H
    else:
        tx = random.uniform(0.25, 0.75) * W
        ty = random.uniform(0.25, 0.75) * H
    M = cv2.getRotationMatrix2D((cx, cy), ang, s)
    M[0, 2] += tx - cx
    M[1, 2] += ty - cy
    if flip:
        F = np.array([[-1, 0, W], [0, 1, 0], [0, 0, 1]], np.float64)
        M = (F @ np.vstack([M, [0, 0, 1]]))[:2]
    return M


def tf_pt(M, p):
    return (M[0, 0] * p[0] + M[0, 1] * p[1] + M[0, 2], M[1, 0] * p[0] + M[1, 1] * p[1] + M[1, 2])


# ---------------------------------------------------------------- photometric
def photometric(img):
    img = img.astype(np.float32)
    # white balance / colour cast
    img *= np.random.uniform(0.85, 1.15, 3)[None, None, :]
    # brightness / contrast
    a = random.uniform(0.6, 1.4)
    b = random.uniform(-40, 40)
    img = img * a + b
    # haze (washed-out stereo camera look)
    if random.random() < 0.35:
        k = random.uniform(0.1, 0.45)
        haze = np.array([random.uniform(170, 255)] * 3) * np.random.uniform(0.9, 1.05, 3)
        img = img * (1 - k) + haze[None, None, :] * k
    img = np.clip(img, 0, 255).astype(np.uint8)
    # gamma
    if random.random() < 0.3:
        g = random.uniform(0.6, 1.6)
        lut = np.clip(((np.arange(256) / 255.0) ** g) * 255, 0, 255).astype(np.uint8)
        img = cv2.LUT(img, lut)
    # saturation
    if random.random() < 0.5:
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[..., 1] *= random.uniform(0.4, 1.4)
        hsv[..., 0] = (hsv[..., 0] + random.uniform(-6, 6)) % 180
        img = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
    H, W = img.shape[:2]
    # low resolution
    if random.random() < 0.5:
        f = random.uniform(0.3, 0.7)
        img = cv2.resize(cv2.resize(img, (max(8, int(W * f)), max(8, int(H * f))), interpolation=cv2.INTER_AREA),
                         (W, H), interpolation=random.choice([cv2.INTER_LINEAR, cv2.INTER_CUBIC]))
    # blur
    r = random.random()
    if r < 0.35:
        img = cv2.GaussianBlur(img, (0, 0), random.uniform(0.5, 2.0))
    elif r < 0.5:
        k = random.choice([5, 7, 9, 11])
        ker = np.zeros((k, k), np.float32)
        ker[k // 2, :] = 1.0 / k
        ker = cv2.warpAffine(ker, cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), random.uniform(0, 180), 1), (k, k))
        img = cv2.filter2D(img, -1, ker / max(ker.sum(), 1e-6))
    # noise
    if random.random() < 0.3:
        img = np.clip(img.astype(np.float32) + np.random.normal(0, random.uniform(2, 10), img.shape), 0, 255).astype(np.uint8)
    # jpeg
    if random.random() < 0.6:
        ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, random.randint(25, 90)])
        img = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return img


def occlude(img, n=1):
    H, W = img.shape[:2]
    for _ in range(n):
        w, h = random.randint(W // 20, W // 6), random.randint(H // 20, H // 6)
        x, y = random.randint(0, W - w), random.randint(0, H - h)
        c = tuple(int(v) for v in np.random.randint(0, 256, 3))
        cv2.rectangle(img, (x, y), (x + w, y + h), c, -1)
    return img


# ---------------------------------------------------------------- one sample
def paste_distractors(img, pool, keep_out=None, n=None):
    """Paste clutter patches (white bags, cans, arm parts...) so the model learns they are not hands."""
    H, W = img.shape[:2]
    srcs = HARD + pool
    if not srcs:
        return img
    for _ in range(n or random.randint(1, 3)):
        src = random.choice(srcs)
        sh, sw = src.shape[:2]
        cw, ch = random.randint(sw // 6, max(sw // 6 + 1, sw // 2)), random.randint(sh // 6, max(sh // 6 + 1, sh // 2))
        x, y = random.randint(0, sw - cw), random.randint(0, sh - ch)
        patch = src[y:y + ch, x:x + cw]
        k = random.uniform(0.15, 0.4) * W / max(cw, 1)
        patch = cv2.resize(patch, (max(4, int(cw * k)), max(4, int(ch * k))))
        ph, pw = patch.shape[:2]
        if ph >= H or pw >= W:
            continue
        for _ in range(10):
            px, py = random.randint(0, W - pw), random.randint(0, H - ph)
            if keep_out is None:
                break
            x0, y0, x1, y1 = keep_out
            if px + pw < x0 or px > x1 or py + ph < y0 or py > y1:
                break
        else:
            continue
        img[py:py + ph, px:px + pw] = patch
    return img


def make_sample(items, pool, negatives_ok=True):
    W = random.choice([480, 480, 512])
    H = int(W * random.choice([0.75, 0.75, 0.5625]))
    if negatives_ok and random.random() < 0.15:
        bg = random_bg(pool, W, H)
        if random.random() < 0.5:
            bg = paste_distractors(bg, pool)
        return photometric(bg), None

    it = random.choice(items)
    img, mask, box, kp = it["img"], it["mask"], it["box"], it["kp"]
    for _ in range(20):
        flip = random.random() < 0.5
        M = make_affine(box, W, H, flip)
        wmask = cv2.warpAffine(mask, M, (W, H), flags=cv2.INTER_LINEAR, borderValue=0)
        full_area = (mask > 127).sum() * abs(np.linalg.det(M[:, :2]))
        if (wmask > 127).sum() >= 0.35 * full_area:
            break
    mode = "paste" if random.random() < 0.55 else "full"
    bg = random_bg(pool, W, H)
    if mode == "full":
        warped = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT)
        valid = cv2.warpAffine(np.full(img.shape[:2], 255, np.uint8), M, (W, H), borderValue=0)
        a = (valid.astype(np.float32) / 255.0)[..., None]
        out = (warped * a + bg * (1 - a)).astype(np.uint8)
    else:
        warped = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_CONSTANT)
        soft = cv2.GaussianBlur(wmask, (0, 0), random.uniform(0.7, 2.0)).astype(np.float32) / 255.0
        # small brightness mismatch between hand and new background
        handf = np.clip(warped.astype(np.float32) * random.uniform(0.75, 1.2), 0, 255)
        out = (handf * soft[..., None] + bg * (1 - soft[..., None])).astype(np.uint8)

    ys, xs = np.where(wmask > 127)
    if random.random() < 0.35 and len(xs):
        out = paste_distractors(out, pool, keep_out=(xs.min(), ys.min(), xs.max(), ys.max()))
    if random.random() < 0.15:
        out = occlude(out, random.randint(1, 3))
    out = photometric(out)

    if len(xs) < 30:
        return None
    pts = []
    for p in kp:
        if p is None:
            pts.append(None)
            continue
        q = tf_pt(M, p)
        pts.append(q if (0 <= q[0] < W and 0 <= q[1] < H) else None)
    bx0, by0, bx1, by1 = xs.min(), ys.min(), xs.max(), ys.max()
    for q in pts:
        if q:
            bx0, by0, bx1, by1 = min(bx0, q[0]), min(by0, q[1]), max(bx1, q[0]), max(by1, q[1])
    pad = 0.02 * max(bx1 - bx0, by1 - by0)
    bx0, by0 = max(0, bx0 - pad), max(0, by0 - pad)
    bx1, by1 = min(W - 1, bx1 + pad), min(H - 1, by1 + pad)
    return out, dict(box=(bx0, by0, bx1, by1), kp=pts, W=W, H=H)


def yolo_line(lab):
    W, H = lab["W"], lab["H"]
    x0, y0, x1, y1 = lab["box"]
    s = f"0 {(x0 + x1) / 2 / W:.6f} {(y0 + y1) / 2 / H:.6f} {(x1 - x0) / W:.6f} {(y1 - y0) / H:.6f}"
    for q in lab["kp"]:
        s += f" {q[0] / W:.6f} {q[1] / H:.6f} 2" if q else " 0 0 0"
    return s


def write_split(items, pool, out, split, n, seed):
    random.seed(seed)
    np.random.seed(seed)
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    k = 0
    while k < n:
        r = make_sample(items, pool)
        if r is None:
            continue
        img, lab = r
        stem = f"{split}_{k:05d}"
        cv2.imwrite(str(out / "images" / split / f"{stem}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        (out / "labels" / split / f"{stem}.txt").write_text(yolo_line(lab) + "\n" if lab else "")
        k += 1
        if k % 500 == 0:
            print(f"  {split}: {k}/{n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", action="append", required=True, help="labels.json (repeatable)")
    ap.add_argument("--out", default="data/dataset")
    ap.add_argument("--n-train", type=int, default=3000)
    ap.add_argument("--n-val", type=int, default=300)
    ap.add_argument("--val-from", nargs="*", default=[],
                    help="image names used ONLY for validation (honest test on unseen views)")
    ap.add_argument("--extra-bg", nargs="*", default=[], help="folders of images with no hand")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    out = Path(a.out)
    items, negatives = load_items(a.labels, out / "_masks")
    print(f"{len(items)} labelled hand images, {len(negatives)} empty images")
    tr = [i for i in items if i["name"] not in a.val_from]
    va = [i for i in items if i["name"] in a.val_from] or items
    random.seed(a.seed)
    pool_tr = background_pool(tr, negatives, a.extra_bg)
    pool_va = background_pool(va, negatives, a.extra_bg, n=80)
    write_split(tr, pool_tr, out, "train", a.n_train, a.seed)
    write_split(va, pool_va, out, "val", a.n_val, a.seed + 1)
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
        "kpt_shape: [2, 3]\nflip_idx: [0, 1]\nnames:\n  0: hand\n"
        "kpt_names:\n  0: [index_tip, thumb_tip]\n")
    print("dataset written to", out)


if __name__ == "__main__":
    main()
