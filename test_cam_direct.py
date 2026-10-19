#!/usr/bin/env python3
"""Direct camera capture test on Raspberry Pi."""
import sys
import os
import time

try:
    import cv2
    print(f"[OK] OpenCV version: {cv2.__version__}")
except ImportError:
    print("[ERROR] OpenCV not installed!")
    sys.exit(1)

device_id = 0
print(f"[*] Opening video device /dev/video{device_id} with V4L2...")

cap = cv2.VideoCapture(device_id, cv2.CAP_V4L2)

if not cap.isOpened():
    print(f"[ERROR] Could not open /dev/video{device_id}!")
    sys.exit(1)

# Configure MJPG for high performance
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
actual_fps = cap.get(cv2.CAP_PROP_FPS)
print(f"[OK] Camera initialized: {actual_w}x{actual_h} @ {actual_fps:.1f} FPS")

# Warm-up / auto-exposure frames
print("[*] Settling auto-exposure (5 warm-up frames)...")
for i in range(5):
    ret, frame = cap.read()
    if not ret:
        print(f"[!] Warm-up frame {i} failed")

# Capture actual frame
t0 = time.time()
ret, frame = cap.read()
capture_time_ms = (time.time() - t0) * 1000

if not ret or frame is None:
    print("[ERROR] Failed to capture frame!")
    cap.release()
    sys.exit(1)

print(f"[OK] Frame captured successfully in {capture_time_ms:.1f}ms! Shape: {frame.shape}")

# Save full side-by-side stereo frame
save_dir = "/home/pi/projects/Robot/robot"
stereo_path = os.path.join(save_dir, "stereo_full.jpg")
cv2.imwrite(stereo_path, frame)
print(f"[SAVED] Full Stereo Image : {stereo_path} ({os.path.getsize(stereo_path):,} bytes)")

# Split into left and right eyes
half_w = actual_w // 2
left_eye = frame[:, :half_w]
right_eye = frame[:, half_w:]

left_path = os.path.join(save_dir, "left_eye.jpg")
right_path = os.path.join(save_dir, "right_eye.jpg")
cv2.imwrite(left_path, left_eye)
cv2.imwrite(right_path, right_eye)
print(f"[SAVED] Left Eye (3D-L)   : {left_path} ({left_eye.shape[1]}x{left_eye.shape[0]})")
print(f"[SAVED] Right Eye (3D-R)  : {right_path} ({right_eye.shape[1]}x{right_eye.shape[0]})")

cap.release()
print("[OK] Camera released successfully.")
