import cv2
import numpy as np
import os

data_dir = 'c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles'

# Inspect dimensions and color characteristics
for i in range(1, 6):
    img_path = os.path.join(data_dir, f'angle_{i}.jpg')
    img = cv2.imread(img_path)
    h, w, c = img.shape
    print(f"=== Angle {i} ({w}x{h}) ===")
    
    # Check red LED indicator (high R, low G/B)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Red LED mask
    red_mask1 = cv2.inRange(hsv, np.array([0, 150, 150]), np.array([10, 255, 255]))
    red_mask2 = cv2.inRange(hsv, np.array([170, 150, 150]), np.array([180, 255, 255]))
    red_mask = cv2.bitwise_or(red_mask1, red_mask2)
    red_pts = np.argwhere(red_mask > 0)
    if len(red_pts) > 0:
        red_center = (int(np.mean(red_pts[:, 1])), int(np.mean(red_pts[:, 0])))
        print(f"  Found Red Indicator/LED at {red_center} ({len(red_pts)} pixels)")
    else:
        print("  No bright red LED detected")

    # Check tan/brown thumb pad (H in [10..30], S in [60..220], V in [80..220])
    tan_mask = cv2.inRange(hsv, np.array([10, 60, 60]), np.array([30, 240, 230]))
    tan_pts = np.argwhere(tan_mask > 0)
    print(f"  Tan/Brown thumb pad pixels: {len(tan_pts)}")
