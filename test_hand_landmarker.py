import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import numpy as np
import os

model_path = 'c:/Users/YOHANNA/Desktop/Projects/Robot Code/hand_landmarker.task'

base_options = python.BaseOptions(model_asset_path=model_path)
options = vision.HandLandmarkerOptions(
    base_options=base_options,
    num_hands=2,
    min_hand_detection_confidence=0.1,
    min_hand_presence_confidence=0.1,
    min_tracking_confidence=0.1
)
detector = vision.HandLandmarker.create_from_options(options)

images_to_test = [
    ('current_frame_left', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/current_frame.jpg')[:, :640]),
    ('user_marked_reference_left', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/user_marked_reference.png')[:, :512]),
    ('angle_1', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_1.jpg')),
    ('angle_2', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_2.jpg')),
    ('angle_3', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_3.jpg')),
    ('angle_4', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_4.jpg')),
    ('angle_5', cv2.imread('c:/Users/YOHANNA/Desktop/Projects/Robot Code/robot/vision/dataset/hand_angles/angle_5.jpg')),
]

for name, img in images_to_test:
    if img is None:
        continue
    h, w = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    
    detection_result = detector.detect(mp_image)
    if detection_result.hand_landmarks:
        print(f"[SUCCESS] {name}: Detected {len(detection_result.hand_landmarks)} hand(s)!")
        annotated = img.copy()
        for idx, hand_lms in enumerate(detection_result.hand_landmarks):
            # Wrist: 0, Thumb tip: 4, Index tip: 8
            wrist = hand_lms[0]
            thumb_tip = hand_lms[4]
            index_tip = hand_lms[8]
            print(f"   Hand {idx}: Wrist=({wrist.x*w:.1f}, {wrist.y*h:.1f}), ThumbTip=({thumb_tip.x*w:.1f}, {thumb_tip.y*h:.1f}), IndexTip=({index_tip.x*w:.1f}, {index_tip.y*h:.1f})")
            
            # Draw all landmarks
            for lm in hand_lms:
                cx, cy = int(lm.x * w), int(lm.y * h)
                cv2.circle(annotated, (cx, cy), 4, (0, 255, 0), -1)
            # Draw thumb line
            th_pts = [(int(hand_lms[i].x * w), int(hand_lms[i].y * h)) for i in range(1, 5)]
            # Draw index line
            idx_pts = [(int(hand_lms[i].x * w), int(hand_lms[i].y * h)) for i in range(5, 9)]
            for i in range(len(th_pts)-1):
                cv2.line(annotated, th_pts[i], th_pts[i+1], (0, 255, 0), 2)
            for i in range(len(idx_pts)-1):
                cv2.line(annotated, idx_pts[i], idx_pts[i+1], (255, 0, 0), 2)
        cv2.imwrite(f'c:/Users/YOHANNA/Desktop/Projects/Robot Code/mp_{name}.jpg', annotated)
    else:
        print(f"[MISSED] {name}: No hand detected by generic MediaPipe model")
