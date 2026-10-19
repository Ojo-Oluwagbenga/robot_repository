import cv2
import mediapipe as mp
import numpy as np
import os

print(f"MediaPipe version: {mp.__version__}")

# Use MediaPipe Tasks or Solutions Hands
try:
    mp_hands = mp.solutions.hands
    hands = mp_hands.Hands(
        static_image_mode=True,
        max_num_hands=2,
        min_detection_confidence=0.1,
        min_tracking_confidence=0.1
    )
    solutions_available = True
except Exception as e:
    print(f"Solutions Hands exception: {e}")
    solutions_available = False

# Test on current_frame
images_to_test = [
    ('current_frame_left', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/current_frame.jpg')[:, :640]),
    ('user_marked_reference_left', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/user_marked_reference.png')[:, :512]),
    ('angle_1', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_1.jpg')),
    ('angle_2', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_2.jpg')),
    ('angle_3', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_3.jpg')),
]

for name, img in images_to_test:
    if img is None:
        print(f"Could not load {name}")
        continue
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    res = hands.process(rgb)
    if res.multi_hand_landmarks:
        print(f"[FOUND] {name}: MediaPipe detected {len(res.multi_hand_landmarks)} hand(s)!")
        for h_idx, lms in enumerate(res.multi_hand_landmarks):
            # Print wrist (0), thumb tip (4), index tip (8)
            wrist = lms.landmark[mp_hands.HandLandmark.WRIST]
            thumb_tip = lms.landmark[mp_hands.HandLandmark.THUMB_TIP]
            index_tip = lms.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP]
            print(f"   Hand {h_idx}: Wrist=({wrist.x*w:.1f}, {wrist.y*h:.1f}), ThumbTip=({thumb_tip.x*w:.1f}, {thumb_tip.y*h:.1f}), IndexTip=({index_tip.x*w:.1f}, {index_tip.y*h:.1f})")
            
            # Draw landmarks
            mp_drawing = mp.solutions.drawing_utils
            annotated = img.copy()
            mp_drawing.draw_landmarks(annotated, lms, mp_hands.HAND_CONNECTIONS)
            cv2.imwrite(f'c:/Users/YOHANNA/Desktop/Projects/Robot Code/mp_{name}.jpg', annotated)
    else:
        print(f"[NOT FOUND] {name}: MediaPipe did NOT detect any hands (score too low for 3D printed arm).")

        # Let's test with contrast enhancement or HSV skin conversion
        # CLAHE or brightness normalization:
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        cl = clahe.apply(l)
        limg = cv2.merge((cl, a, b))
        enhanced = cv2.cvtColor(limg, cv2.COLOR_LAB2RGB)
        res_enh = hands.process(enhanced)
        if res_enh.multi_hand_landmarks:
            print(f"   --> Detected after CLAHE enhancement!")
