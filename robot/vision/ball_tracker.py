"""
Multi-Ball Color Tracker and 3D Stereo Trajectory Follower.
Supports simultaneous multi-object detection, trajectory tracking,
motion trails, and stereo 3D distance estimation for customizable colors.
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

import cv2
import numpy as np


@dataclass
class BallTrack:
    """Represents a tracked ball over multiple consecutive frames."""
    track_id: int
    centroid: Tuple[int, int]          # (x, y)
    radius: float
    color_bgr: Tuple[int, int, int]
    trail: deque = field(default_factory=lambda: deque(maxlen=30))
    last_seen_time: float = 0.0
    missed_frames: int = 0
    velocity: Tuple[float, float] = (0.0, 0.0) # (vx, vy) px/sec
    speed: float = 0.0
    depth_cm: Optional[float] = None   # Stereo estimated depth
    pos_vector_3d: Optional[Tuple[float, float, float]] = None # (X, Y, Z) in cm
    pos_vector_2d: Tuple[int, int] = (0, 0)                   # (dx, dy) in px relative to optical center
    pos_magnitude: float = 0.0                                # |r| in cm (if 3D) or px (if 2D)
    pos_angle_deg: float = 0.0                                # Bearing angle theta in degrees


class MultiBallTracker:
    """
    Real-time Multi-Ball Detector, Tracker, and Trajectory Visualizer.
    Can track multiple balls of a selected color (red, green, blue, yellow, orange, etc.)
    with persistent ID tracking, motion trails, and stereo 3D distance estimation.
    """

    COLOR_PRESETS: Dict[str, Dict[str, Any]] = {
        "red": {
            # Red wraps around 0 and 180 in HSV
            "ranges": [
                (np.array([0, 90, 60]), np.array([10, 255, 255])),
                (np.array([160, 90, 60]), np.array([180, 255, 255])),
            ],
            "bgr": (0, 0, 255),
            "label": "RED",
        },
        "green": {
            "ranges": [
                (np.array([35, 75, 50]), np.array([85, 255, 255])),
            ],
            "bgr": (0, 255, 0),
            "label": "GREEN",
        },
        "blue": {
            "ranges": [
                (np.array([95, 90, 50]), np.array([135, 255, 255])),
            ],
            "bgr": (255, 0, 0),
            "label": "BLUE",
        },
        "yellow": {
            "ranges": [
                (np.array([20, 90, 80]), np.array([35, 255, 255])),
            ],
            "bgr": (0, 255, 255),
            "label": "YELLOW",
        },
        "orange": {
            "ranges": [
                (np.array([10, 100, 80]), np.array([22, 255, 255])),
            ],
            "bgr": (0, 140, 255),
            "label": "ORANGE",
        },
        "purple": {
            "ranges": [
                (np.array([130, 75, 50]), np.array([160, 255, 255])),
            ],
            "bgr": (220, 20, 180),
            "label": "PURPLE",
        },
    }

    def __init__(
        self,
        target_color: str = "red",
        min_radius: int = 8,
        max_radius: int = 240,
        min_circularity: float = 0.50,
        max_distance_match: float = 80.0,
        max_missed_frames: int = 12,
        trail_length: int = 30,
        stereo_baseline_mm: float = 60.0,
        stereo_focal_length_px: float = 620.0,
    ):
        self.target_color = target_color.lower()
        self.min_radius = min_radius
        self.max_radius = max_radius
        self.min_circularity = min_circularity
        self.max_distance_match = max_distance_match
        self.max_missed_frames = max_missed_frames
        self.trail_length = trail_length
        self.stereo_baseline_mm = stereo_baseline_mm
        self.stereo_focal_length_px = stereo_focal_length_px

        self._next_track_id: int = 1
        self._tracks: Dict[int, BallTrack] = {}
        self._last_update_time: float = time.time()

        # Morphological kernels
        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def set_color(self, color_name: str) -> bool:
        """Dynamically update target tracking color preset."""
        name = color_name.lower().strip()
        if name in self.COLOR_PRESETS:
            self.target_color = name
            return True
        return False

    def clear_tracks(self):
        """Reset all active tracking history."""
        self._tracks.clear()
        self._next_track_id = 1

    def create_color_mask(self, hsv_image: np.ndarray) -> np.ndarray:
        """Generate binary mask for the current target color."""
        preset = self.COLOR_PRESETS.get(self.target_color, self.COLOR_PRESETS["red"])
        combined_mask = None

        for lower, upper in preset["ranges"]:
            m = cv2.inRange(hsv_image, lower, upper)
            combined_mask = m if combined_mask is None else cv2.bitwise_or(combined_mask, m)

        # Morphological filtering to clean noise and bridge contours
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN, self._kernel_open)
        combined_mask = cv2.morphologyEx(combined_mask, cv2.MORPH_CLOSE, self._kernel_close)
        return combined_mask

    def detect_balls_in_image(
        self, bgr_image: np.ndarray, x_offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Detect all candidate round balls of the active color in a single frame.
        Returns list of detection dictionaries:
          [{'centroid': (x, y), 'radius': r, 'contour': cnt, 'circularity': c, 'area': a}]
        """
        # 1. Blur and convert to HSV
        blurred = cv2.GaussianBlur(bgr_image, (9, 9), 1.5)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        # 2. Extract color mask
        mask = self.create_color_mask(hsv)

        # 3. Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        detections = []
        min_area = math.pi * (self.min_radius ** 2) * 0.45
        max_area = math.pi * (self.max_radius ** 2) * 1.5

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue

            # Circularity metric: 4 * pi * Area / Perimeter^2 (1.0 for perfect circle)
            circ = (4.0 * math.pi * area) / (peri * peri)
            if circ < self.min_circularity:
                continue

            (cx, cy), radius = cv2.minEnclosingCircle(cnt)
            if not (self.min_radius <= radius <= self.max_radius):
                continue

            # Circle fill ratio check (contour area vs bounding circle area)
            circle_area = math.pi * (radius ** 2)
            circle_fill = area / circle_area if circle_area > 0 else 0
            if circle_fill < 0.45:
                continue

            # Bounding box aspect ratio check
            xb, yb, wb, hb = cv2.boundingRect(cnt)
            aspect = float(wb) / float(hb) if hb > 0 else 0
            if not (0.60 <= aspect <= 1.65):
                continue

            # Centroid from moments for subpixel stability
            m = cv2.moments(cnt)
            if m["m00"] > 0:
                cx_m = int(m["m10"] / m["m00"])
                cy_m = int(m["m01"] / m["m00"])
            else:
                cx_m, cy_m = int(cx), int(cy)

            detections.append({
                "centroid": (cx_m + x_offset, cy_m),
                "radius": float(radius),
                "contour": cnt,
                "circularity": circ,
                "area": area,
            })

        # Sort largest to smallest
        detections.sort(key=lambda d: d["area"], reverse=True)
        return detections

    def update_tracks(
        self, detections: List[Dict[str, Any]], current_time: Optional[float] = None
    ) -> List[BallTrack]:
        """
        Associate detections across frames to maintain persistent BallTrack objects.
        Computes velocity, speed, and updates trajectory history.
        """
        now = current_time or time.time()
        dt = max(0.001, now - self._last_update_time)
        self._last_update_time = now

        unmatched_detections = list(range(len(detections)))
        matched_track_ids = set()

        color_bgr = self.COLOR_PRESETS.get(self.target_color, {}).get("bgr", (0, 255, 255))

        # 1. Match detections to existing tracks
        for track_id, track in list(self._tracks.items()):
            best_det_idx = None
            best_dist = float("inf")

            for det_idx in unmatched_detections:
                det = detections[det_idx]
                dcx = det["centroid"][0] - track.centroid[0]
                dcy = det["centroid"][1] - track.centroid[1]
                dist = math.hypot(dcx, dcy)

                # Dynamic match threshold based on ball size and velocity
                threshold = max(self.max_distance_match, track.radius * 2.2)
                if dist < threshold and dist < best_dist:
                    best_dist = dist
                    best_det_idx = det_idx

            if best_det_idx is not None:
                # Update existing track
                det = detections[best_det_idx]
                prev_cx, prev_cy = track.centroid
                curr_cx, curr_cy = det["centroid"]

                vx = (curr_cx - prev_cx) / dt
                vy = (curr_cy - prev_cy) / dt
                speed = math.hypot(vx, vy)

                # Smooth velocity (low-pass filter)
                track.velocity = (
                    track.velocity[0] * 0.4 + vx * 0.6,
                    track.velocity[1] * 0.4 + vy * 0.6,
                )
                track.speed = speed
                track.centroid = det["centroid"]
                track.radius = det["radius"]
                track.trail.append(det["centroid"])
                track.last_seen_time = now
                track.missed_frames = 0
                track.color_bgr = color_bgr

                matched_track_ids.add(track_id)
                unmatched_detections.remove(best_det_idx)
            else:
                # Track missed in this frame
                track.missed_frames += 1

        # 2. Remove dead tracks
        dead_ids = [
            tid for tid, t in self._tracks.items()
            if t.missed_frames > self.max_missed_frames
        ]
        for tid in dead_ids:
            del self._tracks[tid]

        # 3. Create new tracks for unmatched detections
        for det_idx in unmatched_detections:
            det = detections[det_idx]
            new_id = self._next_track_id
            self._next_track_id += 1

            new_track = BallTrack(
                track_id=new_id,
                centroid=det["centroid"],
                radius=det["radius"],
                color_bgr=color_bgr,
                trail=deque(maxlen=self.trail_length),
                last_seen_time=now,
                missed_frames=0,
            )
            new_track.trail.append(det["centroid"])
            self._tracks[new_id] = new_track

        return list(self._tracks.values())

    def estimate_stereo_depth(
        self,
        left_eye: np.ndarray,
        right_eye: np.ndarray,
        left_tracks: List[BallTrack],
    ):
        """
        Cross-reference Left Eye tracks with Right Eye detections to estimate
        3D depth distance (Z in cm) using stereo disparity triangulation.
        """
        right_dets = self.detect_balls_in_image(right_eye)

        for track in left_tracks:
            lx, ly = track.centroid
            best_right = None
            min_y_diff = 24.0  # Epipolar alignment tolerance

            for r_det in right_dets:
                rx, ry = r_det["centroid"]
                y_diff = abs(ly - ry)
                # Left ball x must be greater than right ball x (disparity > 0)
                disparity = lx - rx

                if y_diff < min_y_diff and disparity > 2:
                    min_y_diff = y_diff
                    best_right = r_det

            if best_right is not None:
                disp = max(1.0, float(lx - best_right["centroid"][0]))
                # Z = (f * B) / disparity
                depth_mm = (self.stereo_focal_length_px * self.stereo_baseline_mm) / disp
                track.depth_cm = round(depth_mm / 10.0, 1)
            else:
                track.depth_cm = None

    def _compute_position_vectors(self, tracks: List[BallTrack], opt_cx: int, opt_cy: int):
        """Compute 2D and 3D position vectors relative to the optical principal axis."""
        for track in tracks:
            cx, cy = track.centroid
            dx = cx - opt_cx
            dy = -(cy - opt_cy)  # Cartesian convention: UP is positive Y

            track.pos_vector_2d = (dx, dy)
            track.pos_angle_deg = round(math.degrees(math.atan2(dy, dx)), 1)

            if track.depth_cm is not None and track.depth_cm > 0:
                # 3D Position vector in camera coordinate frame (Z forward, X right, Y up)
                X_cm = round((dx * track.depth_cm) / self.stereo_focal_length_px, 1)
                Y_cm = round((dy * track.depth_cm) / self.stereo_focal_length_px, 1)
                Z_cm = round(track.depth_cm, 1)
                track.pos_vector_3d = (X_cm, Y_cm, Z_cm)
                track.pos_magnitude = round(math.sqrt(X_cm ** 2 + Y_cm ** 2 + Z_cm ** 2), 1)
            else:
                track.pos_vector_3d = None
                track.pos_magnitude = round(math.hypot(dx, dy), 1)

    def process_frame(
        self,
        stereo_frame: np.ndarray,
        is_stereo: bool = True,
        annotate: bool = True,
    ) -> Tuple[np.ndarray, List[BallTrack]]:
        """
        Full processing pipeline on a frame:
        - Detects multiple balls of target color
        - Tracks centroids and records motion trails
        - Correlates stereo left/right eyes for depth
        - Computes 2D/3D position vectors
        - Draws HUD targeting rings, position vectors, and telemetry
        Returns (annotated_frame, list_of_active_tracks).
        """
        h, w = stereo_frame.shape[:2]

        if is_stereo and w >= 640:
            half_w = w // 2
            left_eye = stereo_frame[:, :half_w]
            right_eye = stereo_frame[:, half_w:]

            # Detect in left eye
            left_dets = self.detect_balls_in_image(left_eye)
            tracks = self.update_tracks(left_dets)

            # Compute stereo depth
            self.estimate_stereo_depth(left_eye, right_eye, tracks)

            # Optical principal center for tracking eye (left eye)
            opt_cx = half_w // 2
            opt_cy = h // 2
            self._compute_position_vectors(tracks, opt_cx, opt_cy)

            if not annotate:
                return stereo_frame, tracks

            # Annotate full frame
            out_frame = stereo_frame.copy()
            self._render_hud(out_frame, tracks, half_w=half_w, opt_cx=opt_cx, opt_cy=opt_cy)
            return out_frame, tracks
        else:
            # Single monocular frame
            dets = self.detect_balls_in_image(stereo_frame)
            tracks = self.update_tracks(dets)

            opt_cx = w // 2
            opt_cy = h // 2
            self._compute_position_vectors(tracks, opt_cx, opt_cy)

            if not annotate:
                return stereo_frame, tracks

            out_frame = stereo_frame.copy()
            self._render_hud(out_frame, tracks, half_w=None, opt_cx=opt_cx, opt_cy=opt_cy)
            return out_frame, tracks

    def _render_hud(
        self,
        img: np.ndarray,
        tracks: List[BallTrack],
        half_w: Optional[int] = None,
        opt_cx: int = 320,
        opt_cy: int = 240,
    ):
        """Render glowing reticles, position vectors, trajectory trails, and telemetry overlay."""
        h, w = img.shape[:2]
        color_info = self.COLOR_PRESETS.get(self.target_color, self.COLOR_PRESETS["red"])
        target_bgr = color_info["bgr"]
        color_label = color_info["label"]

        # 1. Optical Center Marker (Origin (0,0) Reference)
        cv2.drawMarker(img, (opt_cx, opt_cy), (70, 70, 70), cv2.MARKER_CROSS, 20, 1)
        cv2.circle(img, (opt_cx, opt_cy), 4, (100, 100, 100), 1)
        cv2.putText(
            img,
            "ORIGIN (0,0)",
            (opt_cx + 8, opt_cy - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (120, 120, 120),
            1,
            cv2.LINE_AA,
        )

        # Center separator line in stereo mode
        if half_w is not None:
            cv2.line(img, (half_w, 0), (half_w, h), (40, 40, 40), 1)

        # 2. Render position vectors, motion trails, and HUD badges for each ball
        for track in tracks:
            if track.missed_frames > 2:
                continue

            cx, cy = track.centroid
            r = int(track.radius)

            # (A) Trajectory motion trail
            pts = list(track.trail)
            for i in range(1, len(pts)):
                if pts[i - 1] is None or pts[i] is None:
                    continue
                alpha = float(i) / len(pts)
                thickness = max(1, int(4 * alpha))
                trail_color = tuple(int(c * (0.3 + 0.7 * alpha)) for c in target_bgr)
                cv2.line(img, pts[i - 1], pts[i], trail_color, thickness, cv2.LINE_AA)

            # (B) Position Vector Ray from Optical Origin (0,0) to Ball Centroid
            cv2.arrowedLine(
                img,
                (opt_cx, opt_cy),
                (cx, cy),
                (0, 220, 255),  # Cyan-Gold vector ray
                1,
                tipLength=0.04,
                line_type=cv2.LINE_AA,
            )

            # Midpoint magnitude tag along position vector ray
            mid_x = (opt_cx + cx) // 2
            mid_y = (opt_cy + cy) // 2
            unit = "cm" if track.pos_vector_3d else "px"
            cv2.putText(
                img,
                f"|r|={track.pos_magnitude}{unit}",
                (mid_x - 30, mid_y - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.36,
                (0, 220, 255),
                1,
                cv2.LINE_AA,
            )

            # (C) Targeting Ring & Inner Reticle
            cv2.circle(img, (cx, cy), r + 4, target_bgr, 2, cv2.LINE_AA)
            cv2.circle(img, (cx, cy), 3, (255, 255, 255), -1)

            # (D) Local Coordinate Triad (Axes) on Ball Centroid (+X Right, +Y Up)
            axis_len = 22
            cv2.arrowedLine(img, (cx, cy), (cx + axis_len, cy), (0, 100, 255), 2, tipLength=0.3)
            cv2.putText(img, "+X", (cx + axis_len + 3, cy + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 100, 255), 1)

            cv2.arrowedLine(img, (cx, cy), (cx, cy - axis_len), (0, 255, 100), 2, tipLength=0.3)
            cv2.putText(img, "+Y", (cx - 4, cy - axis_len - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (0, 255, 100), 1)

            # (E) Directional Velocity Vector (Movement heading)
            vx, vy = track.velocity
            if track.speed > 15:
                scale = min(1.5, 36.0 / (track.speed + 1e-3))
                end_pt = (int(cx + vx * scale), int(cy + vy * scale))
                cv2.arrowedLine(img, (cx, cy), end_pt, (255, 255, 0), 2, tipLength=0.3)

            # (F) Comprehensive On-Ball Position Vector Badge
            tag_lines = [
                f"#{track.track_id} {color_label} (r:{r}px)",
            ]

            if track.pos_vector_3d is not None:
                X, Y, Z = track.pos_vector_3d
                tag_lines.append(f"r = [{X:+.1f}, {Y:+.1f}, {Z:+.1f}] cm")
                tag_lines.append(f"|r|={track.pos_magnitude}cm | {track.pos_angle_deg:+.0f} deg")
            else:
                dx, dy = track.pos_vector_2d
                tag_lines.append(f"r = [dX:{dx:+d}, dY:{dy:+d}] px")
                tag_lines.append(f"|r|={track.pos_magnitude}px | {track.pos_angle_deg:+.0f} deg")

            tag_lines.append(f"v = {int(track.speed)} px/s")

            # Position badge above or below ball depending on frame bounds
            box_w = 185
            box_h = 16 * len(tag_lines) + 8
            badge_y = cy - r - box_h - 8
            if badge_y < 38:  # If near top, flip below ball
                badge_y = cy + r + 10

            badge_x = max(10, min(cx - box_w // 2, (half_w or w) - box_w - 10))

            # Draw semi-transparent dark HUD card for high contrast
            overlay = img.copy()
            cv2.rectangle(overlay, (badge_x, badge_y), (badge_x + box_w, badge_y + box_h), (10, 14, 22), -1)
            cv2.addWeighted(overlay, 0.85, img, 0.15, 0, img)
            cv2.rectangle(img, (badge_x, badge_y), (badge_x + box_w, badge_y + box_h), target_bgr, 1)

            # Render text lines
            for idx, text in enumerate(tag_lines):
                txt_color = (0, 220, 255) if "r = " in text or "|r|=" in text else (255, 255, 255)
                cv2.putText(
                    img,
                    text,
                    (badge_x + 8, badge_y + 16 + idx * 15),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.38,
                    txt_color,
                    1,
                    cv2.LINE_AA,
                )

        # 3. Top Telemetry Header
        header_h = 32
        overlay = img.copy()
        cv2.rectangle(overlay, (0, 0), (w, header_h), (12, 16, 24), -1)
        cv2.addWeighted(overlay, 0.75, img, 0.25, 0, img)

        active_count = len([t for t in tracks if t.missed_frames == 0])
        header_str = (
            f"MULTI-BALL TRACKER | TARGET: {color_label} | "
            f"BALLS ACTIVE: {active_count} | ORIGIN: ({opt_cx}, {opt_cy})"
        )
        cv2.putText(
            img,
            header_str,
            (14, 21),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.50,
            target_bgr,
            1,
            cv2.LINE_AA,
        )
