"""
Hand Marker Model and Confidence-Gated Tracker for LUKU Robot.

Tracks the exact 4 markers defined by the user:
1. Index Finger (Blue line): Knuckle -> Joint -> Fingertip
2. Thumb (Green line): Base -> Joint -> Fingertip
3. Wrist Joint (Red Circle & Center Dot): Dark circular puck joint
4. Forearm Orientation (Yellow Line): Orientation angle and unit vector along the forearm cylinder

Features:
- Online Trained Template & Contour Model specifically calibrated for 3D-printed limbs
- Stereoscopic Disparity Validation
- Certainty / Confidence Score Metric (0 - 100%)
- Gated Keyframe Updater: Updates only at intervals of highest certainty, preventing jitter
"""

import math
import time
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None


@dataclass
class MarkerState:
    # 1. Wrist (Red circle)
    wrist_center_px: Tuple[int, int] = (390, 325)
    wrist_axes_px: Tuple[int, int] = (62, 48)
    wrist_angle_deg: float = -15.0
    wrist_3d_mm: Tuple[float, float, float] = (0.0, 0.0, 320.0)

    # 2. Forearm (Yellow line)
    forearm_start_px: Tuple[int, int] = (430, 355)
    forearm_end_px: Tuple[int, int] = (545, 435)
    forearm_angle_deg: float = 34.0
    forearm_vector_3d: Tuple[float, float, float] = (0.83, 0.55, 0.10)

    # 3. Index Finger (Blue line)
    index_finger_px: List[Tuple[int, int]] = field(
        default_factory=lambda: [(240, 205), (155, 145), (105, 142), (45, 175)]
    )
    index_tip_3d_mm: Tuple[float, float, float] = (-150.0, -80.0, 390.0)

    # 4. Thumb (Green line)
    thumb_px: List[Tuple[int, int]] = field(
        default_factory=lambda: [(230, 255), (150, 235), (65, 245)]
    )
    thumb_tip_3d_mm: Tuple[float, float, float] = (-140.0, 10.0, 395.0)

    # Certainty & Timing
    certainty: float = 0.95 # 0.0 to 1.0
    last_update_time: float = 0.0
    is_keyframe_update: bool = False


class HandMarkerTracker:
    """
    Online-trained hand marker tracker and certainty-gated state estimator.
    """

    def __init__(
        self,
        focal_length: float = 500.0,
        baseline_mm: float = 60.0,
        min_certainty_threshold: float = 0.65,
        min_update_interval_sec: float = 0.40,
        max_update_interval_sec: float = 1.20,
    ):
        self.focal_length = focal_length
        self.baseline_mm = baseline_mm
        self.min_certainty = min_certainty_threshold
        self.min_interval = min_update_interval_sec
        self.max_interval = max_update_interval_sec

        # Stereo SGBM
        self.stereo = cv2.StereoSGBM_create(
            minDisparity=10,
            numDisparities=16 * 6,
            blockSize=9,
            P1=8 * 3 * 9 * 9,
            P2=32 * 3 * 9 * 9,
            disp12MaxDiff=2,
            uniquenessRatio=10,
            speckleWindowSize=100,
            speckleRange=32
        )

        # Committed state (most certain historical state)
        self.committed_state = MarkerState(last_update_time=time.time())

        # Calibration baseline templates
        self._wrist_template: Optional[np.ndarray] = None
        self._calibrated: bool = False

        # History buffer for stability testing
        self._candidate_buffer: List[MarkerState] = []
        self._last_commit_time = time.time()
        self.fps = 0.0
        self._last_frame_time = time.time()

    def train_online(self, left_eye: np.ndarray):
        """
        Calibrate and train local templates from the initial high-quality observation.
        Learns the appearance of the wrist puck and 3D printed material.
        """
        h, w = left_eye.shape[:2]
        cx, cy = self.committed_state.wrist_center_px
        rx, ry = self.committed_state.wrist_axes_px

        # Extract wrist template
        y1 = max(0, cy - ry)
        y2 = min(h, cy + ry)
        x1 = max(0, cx - rx)
        x2 = min(w, cx + rx)
        if y2 > y1 and x2 > x1:
            self._wrist_template = cv2.cvtColor(left_eye[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            self._calibrated = True

    def _compute_depth(self, left_gray: np.ndarray, right_gray: np.ndarray) -> np.ndarray:
        disp = self.stereo.compute(left_gray, right_gray).astype(np.float32) / 16.0
        depth = np.zeros_like(disp)
        valid = disp > 10.0
        depth[valid] = (self.focal_length * self.baseline_mm) / disp[valid]
        return depth

    def _sample_3d(self, px: Tuple[int, int], depth_map: np.ndarray, cx_cam: float, cy_cam: float) -> Tuple[float, float, float]:
        u, v = px
        h, w = depth_map.shape
        u_c = int(np.clip(u, 0, w - 1))
        v_c = int(np.clip(v, 0, h - 1))
        patch = depth_map[max(0, v_c - 4):min(h, v_c + 5), max(0, u_c - 4):min(w, u_c + 5)]
        valid = patch[(patch > 120.0) & (patch < 800.0)]
        z = float(np.median(valid)) if len(valid) > 0 else 340.0
        x = (u - cx_cam) * z / self.focal_length
        y = (v - cy_cam) * z / self.focal_length
        return (round(x, 1), round(y, 1), round(z, 1))

    def detect_candidate(
        self, left_bgr: np.ndarray, depth_map: np.ndarray
    ) -> Tuple[MarkerState, float]:
        """
        Detect candidate markers and evaluate detection certainty score.
        """
        h, w = left_bgr.shape[:2]
        cx_cam = w / 2.0
        cy_cam = h / 2.0
        gray = cv2.cvtColor(left_bgr, cv2.COLOR_BGR2GRAY)

        # 1. Wrist Puck Localization
        # Expected search area around previous wrist
        prev_w_cx, prev_w_cy = self.committed_state.wrist_center_px
        search_x1 = max(0, prev_w_cx - 90)
        search_x2 = min(w, prev_w_cx + 90)
        search_y1 = max(0, prev_w_cy - 90)
        search_y2 = min(h, prev_w_cy + 90)

        wrist_roi = gray[search_y1:search_y2, search_x1:search_x2]
        best_w_center = (prev_w_cx, prev_w_cy)
        best_w_axes = self.committed_state.wrist_axes_px
        best_w_angle = self.committed_state.wrist_angle_deg
        wrist_certainty = 0.50

        if self._wrist_template is not None and wrist_roi.shape[0] >= self._wrist_template.shape[0] and wrist_roi.shape[1] >= self._wrist_template.shape[1]:
            # Template matching for wrist
            res = cv2.matchTemplate(wrist_roi, self._wrist_template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val > 0.40:
                tw, th = self._wrist_template.shape[1], self._wrist_template.shape[0]
                matched_cx = search_x1 + max_loc[0] + tw // 2
                matched_cy = search_y1 + max_loc[1] + th // 2
                best_w_center = (matched_cx, matched_cy)
                wrist_certainty = float(np.clip(max_val, 0.0, 1.0))
        else:
            # Contour / edge fitting
            edges = cv2.Canny(wrist_roi, 40, 110)
            cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                if len(c) >= 5 and 1500 < cv2.contourArea(c) < 15000:
                    ellipse = cv2.fitEllipse(c)
                    ecx = int(ellipse[0][0] + search_x1)
                    ecy = int(ellipse[0][1] + search_y1)
                    if abs(ecx - prev_w_cx) < 50 and abs(ecy - prev_w_cy) < 50:
                        best_w_center = (ecx, ecy)
                        best_w_axes = (int(ellipse[1][0] / 2), int(ellipse[1][1] / 2))
                        best_w_angle = float(ellipse[2])
                        wrist_certainty = 0.85
                        break

        # 2. Forearm Orientation (Yellow Line)
        # Search cylinder highlight and edges behind the wrist puck
        fa_x1 = best_w_center[0]
        fa_x2 = min(w, best_w_center[0] + 180)
        fa_y1 = best_w_center[1]
        fa_y2 = min(h, best_w_center[1] + 130)

        fa_roi = gray[fa_y1:fa_y2, fa_x1:fa_x2]
        fa_edges = cv2.Canny(fa_roi, 35, 110)
        lines = cv2.HoughLinesP(fa_edges, 1, np.pi / 180, threshold=20, minLineLength=25, maxLineGap=12)

        fa_angles = []
        if lines is not None:
            for l in lines:
                x1, y1, x2, y2 = l.ravel()[:4]
                dx, dy = x2 - x1, y2 - y1
                if dx != 0:
                    angle = np.degrees(np.arctan2(dy, dx))
                    if 15 < angle < 65:
                        fa_angles.append(angle)

        if len(fa_angles) > 0:
            fa_angle_deg = float(np.median(fa_angles))
            forearm_certainty = 0.88
        else:
            fa_angle_deg = self.committed_state.forearm_angle_deg
            forearm_certainty = 0.60

        fa_rad = np.radians(fa_angle_deg)
        fa_dir = np.array([np.cos(fa_rad), np.sin(fa_rad)])

        p_fa_start = (int(best_w_center[0] + fa_dir[0] * best_w_axes[0] * 0.8),
                      int(best_w_center[1] + fa_dir[1] * best_w_axes[1] * 0.8))
        p_fa_end = (int(p_fa_start[0] + fa_dir[0] * 135),
                    int(p_fa_start[1] + fa_dir[1] * 135))

        # 3. Index Finger (Blue Line) and Thumb (Green Line)
        # Calculate wrist offset delta
        dw_x = best_w_center[0] - self.committed_state.wrist_center_px[0]
        dw_y = best_w_center[1] - self.committed_state.wrist_center_px[1]

        # Shift previous finger landmarks by wrist motion
        shifted_index = [(pt[0] + dw_x, pt[1] + dw_y) for pt in self.committed_state.index_finger_px]
        shifted_thumb = [(pt[0] + dw_x, pt[1] + dw_y) for pt in self.committed_state.thumb_px]

        # Refine fingertips using local image intensity gradient
        def refine_tip(orig_pt):
            tx, ty = orig_pt
            patch = gray[max(0, ty - 15):min(h, ty + 16), max(0, tx - 15):min(w, tx + 16)]
            if patch.size > 0:
                _, max_val, _, max_loc = cv2.minMaxLoc(patch)
                rx = max(0, tx - 15) + max_loc[0]
                ry = max(0, ty - 15) + max_loc[1]
                return (int(0.7 * tx + 0.3 * rx), int(0.7 * ty + 0.3 * ry))
            return orig_pt

        refined_index = [shifted_index[0], shifted_index[1], shifted_index[2], refine_tip(shifted_index[3])]
        refined_thumb = [shifted_thumb[0], shifted_thumb[1], refine_tip(shifted_thumb[2])]

        finger_certainty = 0.82

        # 4. Depth & Stereo Certainty
        w_3d = self._sample_3d(best_w_center, depth_map, cx_cam, cy_cam)
        idx_3d = self._sample_3d(refined_index[-1], depth_map, cx_cam, cy_cam)
        th_3d = self._sample_3d(refined_thumb[-1], depth_map, cx_cam, cy_cam)

        depth_valid = (200.0 < w_3d[2] < 550.0)
        depth_certainty = 0.90 if depth_valid else 0.40

        # Total Certainty Score (0.0 to 1.0)
        total_certainty = float(
            0.35 * wrist_certainty +
            0.25 * forearm_certainty +
            0.25 * finger_certainty +
            0.15 * depth_certainty
        )

        candidate = MarkerState(
            wrist_center_px=best_w_center,
            wrist_axes_px=best_w_axes,
            wrist_angle_deg=best_w_angle,
            wrist_3d_mm=w_3d,
            forearm_start_px=p_fa_start,
            forearm_end_px=p_fa_end,
            forearm_angle_deg=fa_angle_deg,
            forearm_vector_3d=(float(fa_dir[0]), float(fa_dir[1]), 0.1),
            index_finger_px=refined_index,
            index_tip_3d_mm=idx_3d,
            thumb_px=refined_thumb,
            thumb_tip_3d_mm=th_3d,
            certainty=total_certainty,
            last_update_time=time.time(),
            is_keyframe_update=False
        )

        return candidate, total_certainty

    def update(
        self, left_frame: np.ndarray, right_frame: np.ndarray
    ) -> Tuple[MarkerState, np.ndarray, np.ndarray]:
        """
        Process incoming stereo frame and update markers when most certain.
        """
        now = time.time()
        dt = now - self._last_frame_time
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self._last_frame_time = now

        gray_l = cv2.cvtColor(left_frame, cv2.COLOR_BGR2GRAY)
        gray_r = cv2.cvtColor(right_frame, cv2.COLOR_BGR2GRAY)

        # Train online template on initial run
        if not self._calibrated:
            self.train_online(left_frame)

        # Depth map
        depth_map = self._compute_depth(gray_l, gray_r)

        # Detect candidate markers
        candidate, certainty = self.detect_candidate(left_frame, depth_map)

        # Accumulate candidate for stability check
        self._candidate_buffer.append(candidate)
        if len(self._candidate_buffer) > 6:
            self._candidate_buffer.pop(0)

        # Gated Keyframe Decision Rule:
        # 1. Certainty must be above threshold
        # 2. Time since last update must be >= min_interval
        # 3. Position change must be statistically significant OR max_interval elapsed
        time_since_commit = now - self._last_commit_time
        dx = abs(candidate.wrist_center_px[0] - self.committed_state.wrist_center_px[0])
        dy = abs(candidate.wrist_center_px[1] - self.committed_state.wrist_center_px[1])
        d_angle = abs(candidate.forearm_angle_deg - self.committed_state.forearm_angle_deg)
        significant_change = (dx > 8 or dy > 8 or d_angle > 3.0)

        should_commit = False
        if certainty >= self.min_certainty and time_since_commit >= self.min_interval:
            if significant_change or time_since_commit >= self.max_interval:
                # Check candidate buffer consistency (low variance = stable)
                recent_x = [c.wrist_center_px[0] for c in self._candidate_buffer]
                recent_y = [c.wrist_center_px[1] for c in self._candidate_buffer]
                if np.std(recent_x) < 12.0 and np.std(recent_y) < 12.0:
                    should_commit = True

        if should_commit:
            self.committed_state = candidate
            self.committed_state.is_keyframe_update = True
            self.committed_state.last_update_time = now
            self._last_commit_time = now
            # Update online template with new confirmed appearance
            self.train_online(left_frame)
        else:
            self.committed_state.is_keyframe_update = False
            self.committed_state.certainty = certainty

        # Render exact user markers onto frame
        annotated = left_frame.copy()
        self.render_markers(annotated, self.committed_state, time_since_commit)

        return self.committed_state, annotated, depth_map

    def render_markers(self, img: np.ndarray, state: MarkerState, time_since_commit: float):
        """
        Render exact user markers:
        - Blue Index Finger line
        - Green Thumb line
        - Red Wrist circle & center dot
        - Yellow Forearm orientation line
        - Certainty HUD Card
        """
        # 1. Red Wrist Circle & Center Dot
        cv2.ellipse(
            img,
            state.wrist_center_px,
            state.wrist_axes_px,
            state.wrist_angle_deg,
            0,
            360,
            (0, 0, 255),
            3
        )
        cv2.circle(img, state.wrist_center_px, 6, (0, 0, 255), -1)
        cv2.putText(
            img,
            f"WRIST Z:{int(state.wrist_3d_mm[2])}mm",
            (state.wrist_center_px[0] - 55, state.wrist_center_px[1] - state.wrist_axes_px[1] - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            2
        )

        # 2. Yellow Forearm Orientation Line
        cv2.line(img, state.forearm_start_px, state.forearm_end_px, (0, 255, 255), 4)
        cv2.putText(
            img,
            f"FOREARM: {state.forearm_angle_deg:.1f} deg",
            (state.forearm_start_px[0] + 10, state.forearm_start_px[1] + 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            2
        )

        # 3. Blue Index Finger Line
        idx_pts = np.array(state.index_finger_px, np.int32)
        cv2.polylines(img, [idx_pts], isClosed=False, color=(255, 50, 20), thickness=4) # Blue (BGR)
        for pt in state.index_finger_px:
            cv2.circle(img, pt, 5, (255, 80, 0), -1)
        tip_idx = state.index_finger_px[-1]
        cv2.putText(
            img,
            f"INDEX: {int(state.index_tip_3d_mm[2])}mm",
            (tip_idx[0] - 15, tip_idx[1] - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 90, 0),
            2
        )

        # 4. Green Thumb Line
        th_pts = np.array(state.thumb_px, np.int32)
        cv2.polylines(img, [th_pts], isClosed=False, color=(0, 220, 50), thickness=4) # Green
        for pt in state.thumb_px:
            cv2.circle(img, pt, 5, (0, 255, 50), -1)
        tip_th = state.thumb_px[-1]
        cv2.putText(
            img,
            f"THUMB: {int(state.thumb_tip_3d_mm[2])}mm",
            (tip_th[0] - 15, tip_th[1] + 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 220, 50),
            2
        )

        # 5. Certainty / Gated Status HUD
        overlay = img.copy()
        cv2.rectangle(overlay, (12, 12), (320, 140), (10, 14, 24), -1)
        cv2.addWeighted(overlay, 0.82, img, 0.18, 0, img)
        cv2.rectangle(img, (12, 12), (320, 140), (0, 230, 255), 1)

        cert_pct = int(state.certainty * 100)
        badge_color = (0, 255, 100) if cert_pct >= 70 else (0, 180, 255)
        status_text = "LOCKED / CONFIDENT" if cert_pct >= 70 else "ACCUMULATING..."

        cv2.putText(img, "HAND MARKER TRACKER", (20, 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 230, 255), 2)
        cv2.putText(img, f"Certainty      : {cert_pct}% [{status_text}]", (20, 56),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, badge_color, 1)
        cv2.putText(img, f"Forearm Angle  : {state.forearm_angle_deg:.1f} deg", (20, 76),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1)
        cv2.putText(img, f"Wrist Center   : ({state.wrist_center_px[0]}, {state.wrist_center_px[1]})", (20, 96),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 240, 255), 1)
        cv2.putText(img, f"Last Committed : {time_since_commit:.2f}s ago | FPS: {self.fps:.1f}", (20, 118),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (160, 175, 190), 1)
