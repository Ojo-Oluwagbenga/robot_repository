"""
Arm Pose Estimator and Finger Detection for LUKU Robot Binocular Vision.

Features:
- Stereoscopic Disparity & Depth Mapping (3D foreground segmentation)
- Hand Palm & Wrist Joint Identification
- Fingertip Extraction & Classification using Convex Hull & Defect Geometry
- Full Unseen Arm Kinematics Extrapolation (Elbow & Shoulder 3D position and joint angles)
- Temporal Smoothing (Exponential Moving Average) to eliminate frame-to-frame jitter
- Rich Telemetry HUD and 3D Wireframe Projection
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
class FingerTip:
    name: str
    px: Tuple[int, int]         # (u, v) image pixel coordinates
    point_3d: Tuple[float, float, float] # (X, Y, Z) in mm relative to camera
    confidence: float = 1.0


@dataclass
class ArmPoseResult:
    detected: bool = False
    fingers: List[FingerTip] = field(default_factory=list)
    palm_px: Tuple[int, int] = (0, 0)
    palm_radius: int = 0
    palm_3d: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    wrist_px: Tuple[int, int] = (0, 0)
    wrist_3d: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    # Kinematic Estimates for unseen arm
    elbow_px: Tuple[int, int] = (0, 0)
    elbow_3d: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    shoulder_px: Tuple[int, int] = (0, 0)
    shoulder_3d: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    # Joint Angles in degrees
    shoulder_pitch_deg: float = 0.0
    shoulder_yaw_deg: float = 0.0
    elbow_flexion_deg: float = 0.0
    wrist_pitch_deg: float = 0.0
    
    # Raw vectors
    forearm_vector: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    fps: float = 0.0


class ArmPoseEstimator:
    """
    Estimates 3D arm pose and detects fingers from a stereo camera frame.
    Works directly with 3D-printed robotic limbs without requiring skin-color detection.
    """

    def __init__(
        self,
        focal_length: float = 500.0,      # Camera focal length in pixels
        baseline_mm: float = 60.0,         # Stereo camera baseline (inter-ocular distance) in mm
        shoulder_offset_mm: Tuple[float, float, float] = (140.0, 160.0, 30.0), # (X_right, Y_down, Z_forward)
        upper_arm_length_mm: float = 220.0, # Length of upper arm (shoulder to elbow)
        forearm_length_mm: float = 200.0,   # Length of forearm (elbow to wrist)
        smoothing_factor: float = 0.35,     # EMA smoothing factor (0 = frozen, 1 = no smoothing)
        min_disparity: int = 10,
        num_disparities: int = 96,
    ):
        self.focal_length = focal_length
        self.baseline_mm = baseline_mm
        self.shoulder_base_3d = np.array(shoulder_offset_mm, dtype=np.float32)
        self.l_upper = upper_arm_length_mm
        self.l_forearm = forearm_length_mm
        self.alpha = smoothing_factor
        
        # Stereo matcher
        self.num_disparities = num_disparities
        self.min_disparity = min_disparity
        if cv2 is not None:
            self.stereo = cv2.StereoSGBM_create(
                minDisparity=self.min_disparity,
                numDisparities=self.num_disparities,
                blockSize=9,
                P1=8 * 3 * 9 * 9,
                P2=32 * 3 * 9 * 9,
                disp12MaxDiff=2,
                uniquenessRatio=10,
                speckleWindowSize=100,
                speckleRange=32
            )
        else:
            self.stereo = None

        # Filter states for temporal smoothing
        self._prev_wrist_3d: Optional[np.ndarray] = None
        self._prev_palm_3d: Optional[np.ndarray] = None
        self._prev_elbow_3d: Optional[np.ndarray] = None
        self._prev_joint_angles: Optional[Dict[str, float]] = None

        # Performance metrics
        self._last_time = time.time()
        self.fps = 0.0

    def reset_filters(self):
        """Reset temporal smoothing filters."""
        self._prev_wrist_3d = None
        self._prev_palm_3d = None
        self._prev_elbow_3d = None
        self._prev_joint_angles = None

    def _apply_ema(self, current: np.ndarray, prev: Optional[np.ndarray]) -> np.ndarray:
        if prev is None:
            return current.copy()
        return self.alpha * current + (1.0 - self.alpha) * prev

    def compute_disparity(self, left_gray: np.ndarray, right_gray: np.ndarray) -> np.ndarray:
        """Compute disparity map from left and right grayscale eye frames."""
        disp = self.stereo.compute(left_gray, right_gray).astype(np.float32) / 16.0
        return disp

    def depth_from_disparity(self, disparity: np.ndarray) -> np.ndarray:
        """Convert disparity map to metric depth map in millimeters."""
        depth = np.zeros_like(disparity)
        valid = disparity > 10.0
        depth[valid] = (self.focal_length * self.baseline_mm) / disparity[valid]
        return depth

    def get_3d_point(
        self, u: float, v: float, depth_map: np.ndarray, cx: float, cy: float, patch_radius: int = 3
    ) -> np.ndarray:
        """Sample robust depth from neighborhood and calculate (X, Y, Z) in mm."""
        h, w = depth_map.shape
        u_i = int(np.clip(round(u), 0, w - 1))
        v_i = int(np.clip(round(v), 0, h - 1))
        
        patch = depth_map[
            max(0, v_i - patch_radius): min(h, v_i + patch_radius + 1),
            max(0, u_i - patch_radius): min(w, u_i + patch_radius + 1)
        ]
        valid_depths = patch[(patch > 120.0) & (patch < 800.0)]
        if len(valid_depths) > 0:
            z = float(np.median(valid_depths))
        else:
            z = 350.0 # fallback nominal depth in mm
            
        x = (u - cx) * z / self.focal_length
        y = (v - cy) * z / self.focal_length
        return np.array([x, y, z], dtype=np.float32)

    def project_3d_to_pixel(
        self, pt_3d: np.ndarray, cx: float, cy: float
    ) -> Tuple[int, int]:
        """Project 3D point (X, Y, Z) in mm back to image pixel coordinates (u, v)."""
        z = max(float(pt_3d[2]), 10.0)
        u = int(round(cx + (pt_3d[0] * self.focal_length / z)))
        v = int(round(cy + (pt_3d[1] * self.focal_length / z)))
        return (u, v)

    def solve_arm_kinematics(
        self, wrist_3d: np.ndarray, fa_dir_3d: np.ndarray
    ) -> Tuple[np.ndarray, float, float, float]:
        """
        Solve full 2-link spherical inverse kinematics for the unseen arm.
        Returns: (elbow_3d, shoulder_pitch, shoulder_yaw, elbow_flexion)
        """
        shoulder = self.shoulder_base_3d

        # Direction-based raw elbow prediction:
        # Elbow lies at distance L_forearm along forearm vector from wrist
        fa_unit = fa_dir_3d / (np.linalg.norm(fa_dir_3d) + 1e-6)
        raw_elbow = wrist_3d + self.l_forearm * fa_unit

        # Two-sphere intersection constraint between Shoulder and Wrist:
        # ||Elbow - Shoulder|| = L_upper
        # ||Elbow - Wrist|| = L_forearm
        sw_vec = wrist_3d - shoulder
        d_sw = float(np.linalg.norm(sw_vec))

        # Clamp distance to physically reachable range of 2-link chain
        min_reach = abs(self.l_upper - self.l_forearm) + 5.0
        max_reach = (self.l_upper + self.l_forearm) - 5.0
        d_clamped = np.clip(d_sw, min_reach, max_reach)

        u_sw = sw_vec / (d_sw + 1e-6)

        # Distance along sw_vec from shoulder to circle center
        a = (self.l_upper**2 - self.l_forearm**2 + d_clamped**2) / (2.0 * d_clamped)
        # Radius of constraint circle
        h_circle = math.sqrt(max(0.0, self.l_upper**2 - a**2))
        p_circle_center = shoulder + a * u_sw

        # Project raw_elbow onto the circle plane perpendicular to u_sw
        elbow_offset = raw_elbow - (shoulder + float(np.dot(raw_elbow - shoulder, u_sw)) * u_sw)
        radial_norm = float(np.linalg.norm(elbow_offset))
        if radial_norm > 1e-4:
            u_rad = elbow_offset / radial_norm
        else:
            # Default downwards bend if collinear
            u_rad = np.array([0.0, 1.0, 0.0], dtype=np.float32)

        elbow_3d = p_circle_center + h_circle * u_rad

        # Calculate Joint Angles:
        # 1. Elbow Flexion (angle between upper arm and forearm)
        upper_vec = elbow_3d - shoulder
        fore_vec = wrist_3d - elbow_3d
        norm_u = np.linalg.norm(upper_vec)
        norm_f = np.linalg.norm(fore_vec)
        cos_elbow = np.dot(-upper_vec, fore_vec) / (norm_u * norm_f + 1e-6)
        elbow_flexion_deg = float(np.degrees(np.arccos(np.clip(cos_elbow, -1.0, 1.0))))

        # 2. Shoulder Elevation (Pitch) and Azimuth (Yaw)
        sh_pitch_deg = float(np.degrees(math.atan2(upper_vec[1], math.sqrt(upper_vec[0]**2 + upper_vec[2]**2))))
        sh_yaw_deg = float(np.degrees(math.atan2(upper_vec[0], upper_vec[2] + 1e-6)))

        return elbow_3d, sh_pitch_deg, sh_yaw_deg, elbow_flexion_deg

    def process(
        self,
        frame_or_left: np.ndarray,
        right_frame: Optional[np.ndarray] = None,
        crop_width: Optional[int] = None
    ) -> Tuple[ArmPoseResult, np.ndarray, np.ndarray]:
        """
        Process stereo image and detect fingers and arm pose.
        
        Args:
            frame_or_left: Full stereo frame (e.g. 1280x480) OR left eye image (640x480).
            right_frame: Right eye image (if separate).
            crop_width: Width of single eye subframe (defaults to w//2 if full stereo frame).

        Returns:
            result: ArmPoseResult object containing all 3D telemetry and detections.
            annotated_frame: Rendered frame with visual HUD, skeleton wireframe, and fingertips.
            depth_map: Computed metric depth map (mm).
        """
        now = time.time()
        dt = now - self._last_time
        if dt > 0:
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt)
        self._last_time = now

        # Split frames if given combined stereo frame
        if right_frame is None:
            h, w = frame_or_left.shape[:2]
            sub_w = crop_width if crop_width is not None else w // 2
            left_img = frame_or_left[:, :sub_w].copy()
            right_img = frame_or_left[:, sub_w:sub_w * 2].copy()
        else:
            left_img = frame_or_left.copy()
            right_img = right_frame.copy()
            h, sub_w = left_img.shape[:2]

        cx = sub_w / 2.0
        cy = h / 2.0

        gray_left = cv2.cvtColor(left_img, cv2.COLOR_BGR2GRAY)
        gray_right = cv2.cvtColor(right_img, cv2.COLOR_BGR2GRAY)

        # 1. Disparity & Depth
        disparity = self.compute_disparity(gray_left, gray_right)
        depth_map = self.depth_from_disparity(disparity)

        # 2. Foreground Arm Segmentation
        # The robot's arm is in close proximity: 150mm to 650mm
        arm_mask = ((depth_map > 140.0) & (depth_map < 650.0)).astype(np.uint8) * 255

        # Morphological bridge to connect forearm cylinder with hand across wrist collar
        kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        arm_mask = cv2.morphologyEx(arm_mask, cv2.MORPH_CLOSE, kernel_close, iterations=3)
        kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        arm_mask = cv2.morphologyEx(arm_mask, cv2.MORPH_OPEN, kernel_open, iterations=1)

        result = ArmPoseResult(fps=self.fps)
        annotated = left_img.copy()

        # Find Arm Contours
        contours, _ = cv2.findContours(arm_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_contours = [c for c in contours if cv2.contourArea(c) > 5000]

        if not valid_contours:
            # Fallback HUD
            self._render_hud(annotated, result, sub_w, h)
            return result, annotated, depth_map

        # Choose the largest contour (primary arm component)
        arm_cnt = max(valid_contours, key=cv2.contourArea)
        result.detected = True

        # Draw arm boundary
        cv2.drawContours(annotated, [arm_cnt], -1, (60, 190, 60), 2)

        # 3. Locate Palm Center via Distance Transform
        cnt_mask = np.zeros_like(gray_left)
        cv2.drawContours(cnt_mask, [arm_cnt], -1, 255, -1)
        dist_map = cv2.distanceTransform(cnt_mask, cv2.DIST_L2, 5)

        # Suppress bottom/right edges to ensure palm center is on the hand, not forearm
        search_dist = dist_map.copy()
        search_dist[int(h * 0.78):, :] = 0
        search_dist[:, int(sub_w * 0.78):] = 0
        _, max_val, _, palm_loc = cv2.minMaxLoc(search_dist)
        palm_radius = max(15, int(max_val))

        result.palm_px = palm_loc
        result.palm_radius = palm_radius

        # 4. Forearm Orientation Axis
        [vx, vy, x0, y0] = cv2.fitLine(arm_cnt, cv2.DIST_L2, 0, 0.01, 0.01)
        fa_2d_dir = np.array([float(vx.item()), float(vy.item())])
        if fa_2d_dir[1] < 0: # Ensure pointing downwards (elbow/body direction)
            fa_2d_dir = -fa_2d_dir
        fa_2d_dir = fa_2d_dir / (np.linalg.norm(fa_2d_dir) + 1e-6)

        # Wrist Joint 2D location along forearm axis
        wrist_loc_2d = np.array([
            palm_loc[0] + fa_2d_dir[0] * palm_radius * 2.1,
            palm_loc[1] + fa_2d_dir[1] * palm_radius * 2.1
        ])
        wrist_px = (int(np.clip(wrist_loc_2d[0], 0, sub_w - 1)),
                    int(np.clip(wrist_loc_2d[1], 0, h - 1)))
        result.wrist_px = wrist_px

        # 5. Compute 3D Coordinates
        raw_palm_3d = self.get_3d_point(palm_loc[0], palm_loc[1], depth_map, cx, cy)
        raw_wrist_3d = self.get_3d_point(wrist_px[0], wrist_px[1], depth_map, cx, cy)

        # Smooth positions with EMA filter
        palm_3d = self._apply_ema(raw_palm_3d, self._prev_palm_3d)
        wrist_3d = self._apply_ema(raw_wrist_3d, self._prev_wrist_3d)
        self._prev_palm_3d = palm_3d
        self._prev_wrist_3d = wrist_3d

        result.palm_3d = (float(palm_3d[0]), float(palm_3d[1]), float(palm_3d[2]))
        result.wrist_3d = (float(wrist_3d[0]), float(wrist_3d[1]), float(wrist_3d[2]))

        # Forearm 3D vector (from wrist towards elbow)
        fa_3d_vec = wrist_3d - palm_3d
        if np.linalg.norm(fa_3d_vec) < 1e-3:
            fa_3d_vec = np.array([0.5, 0.6, 0.6], dtype=np.float32)
        result.forearm_vector = (float(fa_3d_vec[0]), float(fa_3d_vec[1]), float(fa_3d_vec[2]))

        # 6. Detect Fingertips
        hull_indices = cv2.convexHull(arm_cnt, returnPoints=False)
        hull_pts = cv2.convexHull(arm_cnt, returnPoints=True)

        finger_general_dir = -fa_2d_dir # Fingers point away from wrist/elbow

        candidate_tips = []
        for pt in hull_pts[:, 0]:
            v_from_palm = pt - np.array(palm_loc)
            dist = float(np.linalg.norm(v_from_palm))
            if dist < palm_radius * 1.15:
                continue
            v_norm = v_from_palm / (dist + 1e-6)
            # Dot product with finger direction must be positive (prevents false wrist/forearm points)
            if np.dot(v_norm, finger_general_dir) > 0.10:
                if 10 < pt[0] < sub_w - 10 and 10 < pt[1] < h - 20:
                    candidate_tips.append(pt)

        # Cluster points within 22 pixels
        clustered_tips = []
        for pt in candidate_tips:
            if not any(np.linalg.norm(pt - c) < 22 for c in clustered_tips):
                clustered_tips.append(pt)

        # Sort fingertips angularly around palm center (Thumb to Pinky)
        def _finger_angle(p):
            return math.atan2(p[1] - palm_loc[1], p[0] - palm_loc[0])
        clustered_tips.sort(key=_finger_angle)

        finger_labels = ["Thumb", "Index", "Middle", "Ring", "Pinky"]
        detected_fingers = []
        for i, tip in enumerate(clustered_tips):
            tip_3d = self.get_3d_point(tip[0], tip[1], depth_map, cx, cy)
            label = finger_labels[i] if i < len(finger_labels) else f"F{i+1}"
            f_obj = FingerTip(
                name=label,
                px=(int(tip[0]), int(tip[1])),
                point_3d=(float(tip_3d[0]), float(tip_3d[1]), float(tip_3d[2])),
                confidence=1.0
            )
            detected_fingers.append(f_obj)

        result.fingers = detected_fingers

        # 7. Solve Unseen Arm Kinematics (Elbow & Shoulder)
        raw_elbow_3d, raw_sh_pitch, raw_sh_yaw, raw_elbow_flex = self.solve_arm_kinematics(wrist_3d, fa_3d_vec)
        elbow_3d = self._apply_ema(raw_elbow_3d, self._prev_elbow_3d)
        self._prev_elbow_3d = elbow_3d

        # Smooth joint angles
        curr_angles = {
            "sh_pitch": raw_sh_pitch,
            "sh_yaw": raw_sh_yaw,
            "elbow_flex": raw_elbow_flex
        }
        if self._prev_joint_angles is not None:
            sh_pitch = self.alpha * curr_angles["sh_pitch"] + (1 - self.alpha) * self._prev_joint_angles["sh_pitch"]
            sh_yaw = self.alpha * curr_angles["sh_yaw"] + (1 - self.alpha) * self._prev_joint_angles["sh_yaw"]
            elbow_flex = self.alpha * curr_angles["elbow_flex"] + (1 - self.alpha) * self._prev_joint_angles["elbow_flex"]
        else:
            sh_pitch = curr_angles["sh_pitch"]
            sh_yaw = curr_angles["sh_yaw"]
            elbow_flex = curr_angles["elbow_flex"]
        self._prev_joint_angles = {"sh_pitch": sh_pitch, "sh_yaw": sh_yaw, "elbow_flex": elbow_flex}

        result.shoulder_3d = (float(self.shoulder_base_3d[0]), float(self.shoulder_base_3d[1]), float(self.shoulder_base_3d[2]))
        result.elbow_3d = (float(elbow_3d[0]), float(elbow_3d[1]), float(elbow_3d[2]))
        result.shoulder_pitch_deg = sh_pitch
        result.shoulder_yaw_deg = sh_yaw
        result.elbow_flexion_deg = elbow_flex

        # 8. Project 3D Joint Positions to Pixels
        result.shoulder_px = self.project_3d_to_pixel(self.shoulder_base_3d, cx, cy)
        result.elbow_px = self.project_3d_to_pixel(elbow_3d, cx, cy)

        # 9. Render 3D Skeleton & Telemetry Overlay
        self._render_skeleton(annotated, result)
        self._render_hud(annotated, result, sub_w, h)

        return result, annotated, depth_map

    def _render_skeleton(self, img: np.ndarray, res: ArmPoseResult):
        """Draw arm wireframe and detected fingers on frame."""
        p_sh = res.shoulder_px
        p_el = res.elbow_px
        p_wr = res.wrist_px
        p_pa = res.palm_px

        # Draw Unseen Shoulder & Upper Arm (Cyan/Yellow wireframe)
        cv2.line(img, p_sh, p_el, (255, 220, 0), 3) # Shoulder to Elbow
        cv2.circle(img, p_sh, 9, (255, 200, 0), -1)
        cv2.putText(img, "SHOULDER (Est.)", (p_sh[0] - 55, p_sh[1] - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 200, 0), 2)

        # Unseen Elbow to Wrist
        cv2.line(img, p_el, p_wr, (0, 240, 255), 3) # Elbow to Wrist
        cv2.circle(img, p_el, 8, (0, 240, 255), -1)
        cv2.putText(img, "ELBOW (Est.)", (p_el[0] - 40, p_el[1] + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 240, 255), 2)

        # Visible Wrist to Palm
        cv2.line(img, p_wr, p_pa, (0, 180, 255), 3)
        cv2.circle(img, p_wr, 7, (0, 140, 255), -1)
        cv2.putText(img, f"WRIST [Z={int(res.wrist_3d[2])}mm]", (p_wr[0] - 60, p_wr[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 140, 255), 1)

        # Palm Center
        cv2.circle(img, p_pa, res.palm_radius, (255, 120, 0), 2)
        cv2.circle(img, p_pa, 5, (0, 0, 255), -1)
        cv2.putText(img, "PALM", (p_pa[0] - 18, p_pa[1] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 255), 1)

        # Fingers
        for f in res.fingers:
            cv2.line(img, p_pa, f.px, (0, 255, 100), 2)
            cv2.circle(img, f.px, 6, (0, 255, 0), -1)
            cv2.putText(img, f"{f.name}: {int(f.point_3d[2])}mm", (f.px[0] - 30, f.px[1] - 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 0), 1)

    def _render_hud(self, img: np.ndarray, res: ArmPoseResult, width: int, height: int):
        """Render modern dark glassmorphic HUD telemetry card."""
        overlay = img.copy()
        box_w, box_h = 330, 175
        cv2.rectangle(overlay, (12, 12), (12 + box_w, 12 + box_h), (12, 16, 26), -1)
        cv2.addWeighted(overlay, 0.80, img, 0.20, 0, img)
        cv2.rectangle(img, (12, 12), (12 + box_w, 12 + box_h), (0, 230, 255), 1)

        # Header
        cv2.putText(img, "LUKU ROBOT | ARM & HAND POSE", (22, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 240, 255), 2)
        cv2.line(img, (22, 42), (22 + box_w - 20, 42), (0, 100, 120), 1)

        if res.detected:
            status_color = (0, 255, 120)
            status_text = f"TRACKING ({len(res.fingers)} fingers)"
        else:
            status_color = (100, 100, 255)
            status_text = "SEARCHING FOREARM..."

        cv2.putText(img, f"Status         : {status_text}", (22, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, status_color, 1)
        cv2.putText(img, f"Shoulder Pitch : {res.shoulder_pitch_deg:.1f} deg", (22, 80),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 240, 220), 1)
        cv2.putText(img, f"Shoulder Yaw   : {res.shoulder_yaw_deg:.1f} deg", (22, 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 240, 220), 1)
        cv2.putText(img, f"Elbow Flexion  : {res.elbow_flexion_deg:.1f} deg", (22, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 240, 220), 1)
        cv2.putText(img, f"Wrist Depth (Z): {int(res.wrist_3d[2])} mm", (22, 140),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (220, 240, 220), 1)
        cv2.putText(img, f"FPS: {res.fps:.1f}  |  Stereo SGBM", (22, 162),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (160, 170, 180), 1)
