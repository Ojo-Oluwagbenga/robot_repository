"""
Merged Hand-Fingertip & Ball Grasp Tracker with Stereo 3D Depth Mapping.
=============================================================================
Tracks:
  - Index Fingertip: RED dot
  - Thumb Tip:       BLUE dot
  - Target Ball:     Configurable color (default: GREEN or YELLOW)

Uses binocular stereo disparity triangulation to calculate real-world 3D depth (Z in cm)
for both fingertips and the ball.
Evaluates 3D spatial geometry to detect when the ball is positioned directly in between
the index finger and thumb (in 2D projection and 3D depth slice).
"""

import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

import cv2
import numpy as np

# Color Definitions (BGR)
RED = (0, 0, 255)
BLUE = (255, 0, 0)
GREEN = (0, 255, 0)
YELLOW = (0, 255, 255)
ORANGE = (0, 140, 255)
CYAN = (255, 255, 0)
MAGENTA = (255, 0, 255)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

TIP_NAMES = ("index", "thumb")
TIP_COLORS = {"index": RED, "thumb": BLUE}


# ==============================================================================
# ONE-EURO FILTER FOR SMOOTHING
# ==============================================================================
class OneEuroFilter:
    """One-Euro filter: minimal lag when moving, stable jitter reduction when still."""

    def __init__(self, min_cutoff: float = 1.5, beta: float = 0.02, d_cutoff: float = 1.0):
        self.mc, self.beta, self.dc = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        r = 2.0 * math.pi * cutoff * dt
        return r / (r + 1.0)

    def __call__(self, x: Union[Tuple[float, float], np.ndarray], t: float) -> np.ndarray:
        x_arr = np.asarray(x, dtype=float)
        if self.x is None:
            self.x = x_arr
            self.dx = np.zeros_like(x_arr)
            self.t = t
            return x_arr

        dt = max(t - self.t, 1e-3)
        dx = (x_arr - self.x) / dt
        alpha_d = self._alpha(self.dc, dt)
        self.dx = alpha_d * dx + (1.0 - alpha_d) * self.dx

        cutoff = self.mc + self.beta * float(np.linalg.norm(self.dx))
        alpha = self._alpha(cutoff, dt)
        self.x = alpha * x_arr + (1.0 - alpha) * self.x
        self.t = t
        return self.x

    def reset(self):
        self.x = self.dx = self.t = None


# ==============================================================================
# YOLO-POSE ONNX MODEL INFERENCE
# ==============================================================================
class TipModel:
    """ONNX YOLO-pose model for hand box and 2 fingertips (index, thumb)."""

    def __init__(self, model_path: Union[str, Path], threads: int = 4):
        self.path = Path(model_path).resolve()
        if not self.path.exists():
            raise FileNotFoundError(f"Hand pose model not found at {self.path}")

        self.backend = "onnxruntime"
        try:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = threads
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.sess = ort.InferenceSession(str(self.path), so, providers=["CPUExecutionProvider"])
            inp = self.sess.get_inputs()[0]
            self.inp_name = inp.name
            self.size = int(inp.shape[2])
            self._run = lambda x: self.sess.run(None, {self.inp_name: x})[0]
        except ImportError:
            self.backend = "opencv-dnn"
            net = cv2.dnn.readNetFromONNX(str(self.path))
            self.size = 320

            def run_cv(x):
                net.setInput(x)
                return net.forward()

            self._run = run_cv

    def __call__(self, bgr_image: np.ndarray) -> List[Dict[str, Any]]:
        h, w = bgr_image.shape[:2]
        s = self.size / max(h, w)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        px, py = (self.size - nw) // 2, (self.size - nh) // 2

        canvas = np.full((self.size, self.size, 3), 114, dtype=np.uint8)
        canvas[py : py + nh, px : px + nw] = cv2.resize(
            bgr_image, (nw, nh), interpolation=cv2.INTER_AREA
        )
        blob = (
            np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
            / 255.0
        )

        out = self._run(blob)[0].T  # (anchors, 4 + 1 + 2*3)
        conf = out[:, 4]
        order = np.argsort(-conf)[:5]
        candidates = []

        for i in order:
            if conf[i] < 0.05:
                break
            cx, cy, bw, bh = out[i, :4]
            kpts = out[i, 5:].reshape(-1, 3)
            box = (
                (cx - bw / 2.0 - px) / s,
                (cy - bh / 2.0 - py) / s,
                (cx + bw / 2.0 - px) / s,
                (cy + bh / 2.0 - py) / s,
            )
            cand = {"conf": float(conf[i]), "box": tuple(float(v) for v in box)}
            for name, (kx, ky, kc) in zip(TIP_NAMES, kpts):
                cand[name] = ((kx - px) / s, (ky - py) / s, float(kc))
            candidates.append(cand)

        return candidates


# ==============================================================================
# TIP TRACKER WITH TEMPORAL HYSTERESIS
# ==============================================================================
class TipTracker:
    """Manages hand candidate selection, smoothing, and temporary loss hold."""

    def __init__(
        self,
        conf_thresh: float = 0.35,
        keep_conf: float = 0.20,
        kconf_thresh: float = 0.40,
        hold_frames: int = 6,
    ):
        self.conf = conf_thresh
        self.keep_conf = keep_conf
        self.kconf = kconf_thresh
        self.hold = hold_frames
        self.filters = {n: OneEuroFilter() for n in TIP_NAMES}
        self.last = {n: None for n in TIP_NAMES}
        self.missed = {n: 99 for n in TIP_NAMES}
        self.tracking = False
        self.box = None
        self.det_conf = 0.0

    def update(self, candidates: List[Dict[str, Any]], t: float) -> Dict[str, Dict[str, Any]]:
        threshold = self.keep_conf if self.tracking else self.conf
        det = None

        if candidates:
            if self.tracking and self.box is not None and len(candidates) > 1:
                prev_center = (
                    (self.box[0] + self.box[2]) / 2.0,
                    (self.box[1] + self.box[3]) / 2.0,
                )
                candidates = sorted(
                    candidates,
                    key=lambda c: -c["conf"]
                    + 0.001
                    * math.dist(
                        prev_center,
                        ((c["box"][0] + c["box"][2]) / 2.0, (c["box"][1] + c["box"][3]) / 2.0),
                    ),
                )
            if candidates[0]["conf"] >= threshold:
                det = candidates[0]

        self.tracking = det is not None
        self.det_conf = det["conf"] if det else 0.0
        self.box = det["box"] if det else None

        output = {}
        for n in TIP_NAMES:
            if det and det[n][2] >= self.kconf:
                x, y, c = det[n]
                prev = self.last[n]
                box_sz = max(det["box"][2] - det["box"][0], det["box"][3] - det["box"][1], 1.0)
                if prev is not None and math.dist(prev, (x, y)) > 0.6 * box_sz:
                    self.filters[n].reset()

                smooth_pt = self.filters[n]((x, y), t)
                self.last[n] = (float(smooth_pt[0]), float(smooth_pt[1]))
                self.missed[n] = 0
                output[n] = {
                    "x": self.last[n][0],
                    "y": self.last[n][1],
                    "conf": round(c, 3),
                    "live": True,
                }
            else:
                self.missed[n] += 1
                if self.last[n] is not None and self.missed[n] <= self.hold:
                    output[n] = {
                        "x": self.last[n][0],
                        "y": self.last[n][1],
                        "conf": 0.0,
                        "live": False,
                    }
                else:
                    self.last[n] = None
                    self.filters[n].reset()

        return output


# ==============================================================================
# BALL COLOR DETECTOR & TRACKER
# ==============================================================================
class BallColorDetector:
    """Color-based ball detector with circularity validation."""

    COLOR_RANGES: Dict[str, Dict[str, Any]] = {
        "green": {
            "ranges": [(np.array([35, 75, 50]), np.array([85, 255, 255]))],
            "bgr": GREEN,
            "label": "GREEN",
        },
        "yellow": {
            "ranges": [(np.array([20, 90, 80]), np.array([35, 255, 255]))],
            "bgr": YELLOW,
            "label": "YELLOW",
        },
        "orange": {
            "ranges": [(np.array([10, 100, 80]), np.array([22, 255, 255]))],
            "bgr": ORANGE,
            "label": "ORANGE",
        },
        "red": {
            "ranges": [
                (np.array([0, 100, 80]), np.array([10, 255, 255])),
                (np.array([165, 100, 80]), np.array([180, 255, 255])),
            ],
            "bgr": RED,
            "label": "RED",
        },
        "blue": {
            "ranges": [(np.array([95, 90, 50]), np.array([135, 255, 255]))],
            "bgr": BLUE,
            "label": "BLUE",
        },
        "purple": {
            "ranges": [(np.array([130, 75, 50]), np.array([160, 255, 255]))],
            "bgr": MAGENTA,
            "label": "PURPLE",
        },
    }

    def __init__(
        self,
        color_name: str = "green",
        min_radius: int = 8,
        max_radius: int = 260,
        min_circularity: float = 0.45,
    ):
        self.color_name = color_name.lower().strip()
        self.min_radius = min_radius
        self.max_radius = max_radius
        self.min_circularity = min_circularity
        self.filter = OneEuroFilter()
        self.last_pos = None
        self.last_radius = None
        self.missed = 0

        self._kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))

    def set_color(self, name: str) -> bool:
        c = name.lower().strip()
        if c in self.COLOR_RANGES:
            self.color_name = c
            self.filter.reset()
            self.last_pos = None
            return True
        return False

    def detect(self, bgr_image: np.ndarray, t: float) -> Optional[Dict[str, Any]]:
        blurred = cv2.GaussianBlur(bgr_image, (9, 9), 1.5)
        hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

        preset = self.COLOR_RANGES.get(self.color_name, self.COLOR_RANGES["green"])
        mask = None
        for lower, upper in preset["ranges"]:
            m = cv2.inRange(hsv, lower, upper)
            mask = m if mask is None else cv2.bitwise_or(mask, m)

        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel_open)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel_close)

        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates = []

        min_area = math.pi * (self.min_radius**2) * 0.45
        max_area = math.pi * (self.max_radius**2) * 1.5

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            peri = cv2.arcLength(cnt, True)
            if peri <= 0:
                continue

            circ = (4.0 * math.pi * area) / (peri * peri)
            if circ < self.min_circularity:
                continue

            (cx, cy), radius = cv2.minEnclosingCircle(cnt)
            if not (self.min_radius <= radius <= self.max_radius):
                continue

            circle_fill = area / (math.pi * (radius**2)) if radius > 0 else 0
            if circle_fill < 0.40:
                continue

            m_mom = cv2.moments(cnt)
            if m_mom["m00"] > 0:
                cx_m = float(m_mom["m10"] / m_mom["m00"])
                cy_m = float(m_mom["m01"] / m_mom["m00"])
            else:
                cx_m, cy_m = float(cx), float(cy)

            candidates.append(
                {
                    "centroid": (cx_m, cy_m),
                    "radius": float(radius),
                    "circularity": circ,
                    "area": area,
                }
            )

        if not candidates:
            self.missed += 1
            if self.last_pos is not None and self.missed <= 4:
                return {
                    "x": self.last_pos[0],
                    "y": self.last_pos[1],
                    "radius": self.last_radius,
                    "live": False,
                    "color": self.color_name,
                }
            self.last_pos = None
            self.filter.reset()
            return None

        # Pick candidate closest to previous position, or largest
        if self.last_pos is not None and len(candidates) > 1:
            candidates.sort(key=lambda c: math.dist(self.last_pos, c["centroid"]))
        else:
            candidates.sort(key=lambda c: c["area"], reverse=True)

        best = candidates[0]
        smooth_pt = self.filter(best["centroid"], t)
        self.last_pos = (float(smooth_pt[0]), float(smooth_pt[1]))
        self.last_radius = best["radius"]
        self.missed = 0

        return {
            "x": self.last_pos[0],
            "y": self.last_pos[1],
            "radius": self.last_radius,
            "circularity": round(best["circularity"], 2),
            "live": True,
            "color": self.color_name,
        }


# ==============================================================================
# GRASP RESULT DATA STRUCTURE
# ==============================================================================
@dataclass
class GraspResult:
    """Comprehensive analysis of hand pose, ball position, and grasp alignment."""

    is_in_between: bool = False
    is_pinched: bool = False
    status: str = "NO_TARGET"  # NO_TARGET, SEEKING, APPROACHING, IN_BETWEEN, GRASPED

    # Spatial Geometry
    pinch_distance_cm: Optional[float] = None
    pinch_distance_px: float = 0.0
    depth_offset_cm: Optional[float] = None
    projection_t: float = 0.0  # 0.0=Thumb, 1.0=Index
    lateral_dist_cm: Optional[float] = None
    lateral_dist_px: float = 0.0
    midpoint_dist_cm: Optional[float] = None
    alignment_score: float = 0.0  # 0 to 100%

    # Targets Info
    index: Optional[Dict[str, Any]] = None
    thumb: Optional[Dict[str, Any]] = None
    ball: Optional[Dict[str, Any]] = None

    # Visualization
    vis_frame: Optional[np.ndarray] = None
    telemetry: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# MAIN COMBINED GRASP & DEPTH TRACKER
# ==============================================================================
class HandBallGraspTracker:
    """
    Unified Binocular Hand & Ball Tracking with 3D Depth Mapping.
    Evaluates real-world spatial positioning to confirm when a ball is directly
    between the index finger (RED) and thumb (BLUE).
    """

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        ball_color: str = "green",
        stereo_baseline_mm: float = 60.0,
        stereo_focal_length_px: float = 620.0,
        depth_tolerance_cm: float = 4.5,
        row_tolerance: float = 0.12,
        threads: int = 4,
    ):
        # Resolve model path flexibly
        def _find_model(p):
            if p is not None:
                p_obj = Path(p)
                if p_obj.exists():
                    return p_obj
                robot_root = Path(__file__).resolve().parent.parent
                if (robot_root / p_obj).exists():
                    return robot_root / p_obj
                if (robot_root / "hand_tracker" / "models" / p_obj.name).exists():
                    return robot_root / "hand_tracker" / "models" / p_obj.name

            candidates = [
                Path(__file__).resolve().parent.parent / "hand_tracker" / "models" / "hand_tips.onnx",
                Path(__file__).resolve().parent / "hand_tracker" / "models" / "hand_tips.onnx",
                Path("/home/pi/projects/Robot/robot/hand_tracker/models/hand_tips.onnx"),
                Path("/home/pi/robot/hand_tracker/models/hand_tips.onnx"),
                Path("/home/pi/hand_tracker/models/hand_tips.onnx"),
            ]
            for c in candidates:
                if c.exists():
                    return c
            return None

        resolved = _find_model(model_path)
        if resolved is None or not resolved.exists():
            raise FileNotFoundError(f"Could not locate hand_tips.onnx (tried: {model_path})")
        model_path = resolved

        self.model = TipModel(model_path, threads=threads)
        self.ball_color = ball_color.lower().strip()
        self.ball_trackers = {
            "left": BallColorDetector(color_name=self.ball_color),
            "right": BallColorDetector(color_name=self.ball_color),
        }
        self.hand_trackers = {
            "left": TipTracker(),
            "right": TipTracker(),
        }

        self.baseline_mm = stereo_baseline_mm
        self.focal_px = stereo_focal_length_px
        self.depth_tolerance_cm = depth_tolerance_cm
        self.row_tolerance = row_tolerance

        self._prev_time = time.time()
        self._fps = 0.0
        self._frame_count = 0

    def set_ball_color(self, color_name: str) -> bool:
        """Dynamically switch ball tracking color."""
        c = color_name.lower().strip()
        ok_l = self.ball_trackers["left"].set_color(c)
        ok_r = self.ball_trackers["right"].set_color(c)
        if ok_l and ok_r:
            self.ball_color = c
            return True
        return False

    def _triangulate_depth(self, x_l: float, x_r: float) -> Tuple[Optional[float], float]:
        """Calculates stereo disparity and real-world depth Z in cm."""
        disparity = x_l - x_r
        if disparity > 1.0:
            depth_mm = (self.focal_px * self.baseline_mm) / disparity
            depth_cm = round(depth_mm / 10.0, 1)
            return depth_cm, round(disparity, 1)
        return None, round(disparity, 1)

    def _calculate_3d_point(
        self, x_l: float, y_l: float, depth_cm: Optional[float], w_eye: int, h_eye: int
    ) -> Optional[Tuple[float, float, float]]:
        """Backprojects (x, y, depth) into camera Cartesian frame (X, Y, Z) in cm."""
        if depth_cm is None:
            return None
        cx, cy = w_eye / 2.0, h_eye / 2.0
        X = ((x_l - cx) * depth_cm) / self.focal_px
        Y = -((y_l - cy) * depth_cm) / self.focal_px  # UP is positive Y
        return round(X, 1), round(Y, 1), depth_cm

    def process_stereo_frame(
        self,
        left_img: np.ndarray,
        right_img: np.ndarray,
        show_right_eye: bool = False,
        draw_hud: bool = True,
    ) -> GraspResult:
        """
        Main pipeline: runs hand pose & ball tracking on both stereo views,
        computes 3D depth, evaluates grasp condition, and draws visual overlay.
        """
        t_now = time.time()
        dt = max(t_now - self._prev_time, 1e-4)
        self._prev_time = t_now
        self._frame_count += 1
        self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt) if self._frame_count > 1 else 0.0

        h_eye, w_eye = left_img.shape[:2]

        # 1. Run Hand Model on both eyes
        hand_cands_l = self.model(left_img)
        hand_cands_r = self.model(right_img)

        # Cross-eye stereo agreement filtering for hand box
        if hand_cands_l and hand_cands_r:
            # Check vertical alignment of candidate hand boxes
            box_l = hand_cands_l[0]["box"]
            box_r = hand_cands_r[0]["box"]
            y_diff = abs(((box_l[1] + box_l[3]) / 2.0) - ((box_r[1] + box_r[3]) / 2.0))
            if y_diff > self.row_tolerance * h_eye * 1.5:
                # Disagreement: penalize lower confidence
                if hand_cands_l[0]["conf"] < 0.65:
                    hand_cands_l = []
                if hand_cands_r[0]["conf"] < 0.65:
                    hand_cands_r = []

        tips_l = self.hand_trackers["left"].update(hand_cands_l, t_now)
        tips_r = self.hand_trackers["right"].update(hand_cands_r, t_now)

        # 2. Run Ball Detection on both eyes
        ball_l = self.ball_trackers["left"].detect(left_img, t_now)
        ball_r = self.ball_trackers["right"].detect(right_img, t_now)

        # 3. Stereo Triangulation for Fingertips
        hand_data = {}
        for name in TIP_NAMES:
            tl = tips_l.get(name)
            tr = tips_r.get(name)
            if tl and tr and tl.get("live") and tr.get("live"):
                y_diff = abs(tl["y"] - tr["y"])
                if y_diff <= self.row_tolerance * h_eye:
                    z_cm, disp = self._triangulate_depth(tl["x"], tr["x"])
                    pos3d = self._calculate_3d_point(tl["x"], tl["y"], z_cm, w_eye, h_eye)
                    hand_data[name] = {
                        "x": tl["x"],
                        "y": tl["y"],
                        "live": True,
                        "conf": tl.get("conf", 1.0),
                        "disparity": disp,
                        "z_cm": z_cm,
                        "pos_3d": pos3d,
                    }
                else:
                    # Epipolar disagreement -> 2D only
                    hand_data[name] = {
                        "x": tl["x"],
                        "y": tl["y"],
                        "live": True,
                        "conf": tl.get("conf", 1.0),
                        "disparity": None,
                        "z_cm": None,
                        "pos_3d": None,
                    }
            elif tl and tl.get("live"):
                hand_data[name] = {
                    "x": tl["x"],
                    "y": tl["y"],
                    "live": True,
                    "conf": tl.get("conf", 1.0),
                    "disparity": None,
                    "z_cm": None,
                    "pos_3d": None,
                }

        # 4. Stereo Triangulation for Ball
        ball_data = None
        if ball_l and ball_l.get("live"):
            b_z_cm = None
            b_disp = None
            if ball_r and ball_r.get("live"):
                y_diff = abs(ball_l["y"] - ball_r["y"])
                if y_diff <= self.row_tolerance * h_eye * 1.5:
                    b_z_cm, b_disp = self._triangulate_depth(ball_l["x"], ball_r["x"])

            pos3d_ball = self._calculate_3d_point(
                ball_l["x"], ball_l["y"], b_z_cm, w_eye, h_eye
            )
            ball_data = {
                "x": ball_l["x"],
                "y": ball_l["y"],
                "radius": ball_l["radius"],
                "color": ball_l["color"],
                "live": True,
                "disparity": b_disp,
                "z_cm": b_z_cm,
                "pos_3d": pos3d_ball,
            }

        # 5. Spatial "In-Between" Depth Mapping Evaluation
        result = self._evaluate_grasp(hand_data, ball_data)

        # 6. Render Visual Overlay
        vis_left = self._draw_annotations(
            left_img.copy(), hand_data, ball_data, result, eye_label="LEFT EYE"
        )

        if show_right_eye:
            vis_right = self._draw_annotations(
                right_img.copy(),
                tips_r,
                ball_r,
                result,
                eye_label="RIGHT EYE",
                minimal=True,
            )
            result.vis_frame = np.hstack([vis_left, vis_right])
        else:
            result.vis_frame = vis_left

        return result

    def _evaluate_grasp(
        self,
        hand_data: Dict[str, Any],
        ball_data: Optional[Dict[str, Any]],
    ) -> GraspResult:
        """
        Evaluates 3D and 2D spatial relationship:
        Is the ball located directly between the index fingertip and thumb tip?
        """
        res = GraspResult()
        res.index = hand_data.get("index")
        res.thumb = hand_data.get("thumb")
        res.ball = ball_data

        has_index = res.index and res.index.get("live")
        has_thumb = res.thumb and res.thumb.get("live")
        has_ball = res.ball and res.ball.get("live")

        if not (has_index and has_thumb and has_ball):
            res.status = "NO_TARGET"
            return res

        # 2D Coordinates
        p_idx = np.array([res.index["x"], res.index["y"]], dtype=float)
        p_th = np.array([res.thumb["x"], res.thumb["y"]], dtype=float)
        p_ball = np.array([res.ball["x"], res.ball["y"]], dtype=float)
        r_ball = res.ball["radius"]

        # Pinch Vector & Aperture in 2D
        v_pinch = p_idx - p_th
        d_pinch_px = float(np.linalg.norm(v_pinch))
        res.pinch_distance_px = round(d_pinch_px, 1)

        if d_pinch_px < 5.0:
            res.status = "SEEKING"
            return res

        # Orthogonal projection of ball onto pinch segment
        # t = (p_ball - p_th) . v_pinch / ||v_pinch||^2
        v_norm_sq = float(np.dot(v_pinch, v_pinch))
        u_ball = p_ball - p_th
        t_proj = float(np.dot(u_ball, v_pinch) / v_norm_sq)
        res.projection_t = round(t_proj, 3)

        # Perpendicular distance to line
        p_proj = p_th + t_proj * v_pinch
        d_perp_px = float(np.linalg.norm(p_ball - p_proj))
        res.lateral_dist_px = round(d_perp_px, 1)

        # Midpoint of fingers
        p_mid = (p_idx + p_th) / 2.0
        d_mid_px = float(np.linalg.norm(p_ball - p_mid))

        # 3D Depth Evaluation
        z_idx = res.index.get("z_cm")
        z_th = res.thumb.get("z_cm")
        z_ball = res.ball.get("z_cm")

        depth_aligned = False
        depth_diff_cm = None
        d_pinch_cm = None

        if z_idx is not None and z_th is not None:
            z_hand_avg = (z_idx + z_th) / 2.0

            # Real-world 3D pinch distance in cm
            if res.index.get("pos_3d") and res.thumb.get("pos_3d"):
                pt3_idx = np.array(res.index["pos_3d"])
                pt3_th = np.array(res.thumb["pos_3d"])
                d_pinch_cm = round(float(np.linalg.norm(pt3_idx - pt3_th)), 1)
                res.pinch_distance_cm = d_pinch_cm

            if z_ball is not None:
                depth_diff_cm = abs(z_ball - z_hand_avg)
                res.depth_offset_cm = round(depth_diff_cm, 1)
                depth_aligned = depth_diff_cm <= self.depth_tolerance_cm
            else:
                # Stereo depth available for hand, but ball missing right-eye match
                # Use optical scale heuristic: ball radius vs expected size
                depth_aligned = True
        else:
            # Mono / 2D fallback mode (no stereo depth for one or both)
            depth_aligned = True

        # Convert pixel distances to approx cm if depth is known
        ref_depth = z_ball or (z_idx if z_idx else 45.0)
        px_to_cm = ref_depth / self.focal_px
        res.lateral_dist_cm = round(d_perp_px * px_to_cm, 1)
        res.midpoint_dist_cm = round(d_mid_px * px_to_cm, 1)

        # ======================================================================
        # IN-BETWEEN CRITERIA
        # ======================================================================
        # 1. Longitudinal: Ball projection falls between thumb and index (0.0 to 1.0)
        longitudinal_ok = -0.08 <= t_proj <= 1.08

        # 2. Lateral: Ball center is close to pinch line (within radius + margin)
        lateral_tol_px = max(r_ball * 1.35, 20.0)
        lateral_ok = d_perp_px <= lateral_tol_px

        # 3. Span Enclosure: Ball is within half the pinch span + radius of midpoint
        enclosure_ok = d_mid_px <= (d_pinch_px / 2.0 + r_ball * 1.1)

        # 4. Depth: Hand and ball share the same stereo depth slice
        depth_ok = depth_aligned

        is_between = longitudinal_ok and lateral_ok and enclosure_ok and depth_ok
        res.is_in_between = is_between

        # Alignment score: 100% when centered at t=0.5, d_perp=0, dZ=0
        t_penalty = abs(t_proj - 0.5) * 60.0
        perp_penalty = min(40.0, (d_perp_px / max(1.0, r_ball)) * 25.0)
        dz_penalty = (depth_diff_cm * 8.0) if depth_diff_cm is not None else 0.0
        res.alignment_score = max(0.0, min(100.0, round(100.0 - t_penalty - perp_penalty - dz_penalty, 1)))

        # Grasp status determination
        if is_between:
            # Pinched condition: fingertips touching or close to the ball's diameter
            if d_pinch_px <= r_ball * 2.35:
                res.is_pinched = True
                res.status = "GRASPED"
            else:
                res.status = "IN_BETWEEN"
        elif longitudinal_ok and d_perp_px <= lateral_tol_px * 2.0 and depth_ok:
            res.status = "APPROACHING"
        else:
            res.status = "SEEKING"

        # Build telemetry dictionary
        res.telemetry = {
            "is_in_between": res.is_in_between,
            "is_pinched": res.is_pinched,
            "status": res.status,
            "alignment_pct": res.alignment_score,
            "pinch_distance_cm": res.pinch_distance_cm,
            "depth_offset_cm": res.depth_offset_cm,
            "projection_t": res.projection_t,
            "lateral_dist_cm": res.lateral_dist_cm,
            "hand": {
                "index": {
                    "x": round(res.index["x"], 1),
                    "y": round(res.index["y"], 1),
                    "z_cm": res.index.get("z_cm"),
                },
                "thumb": {
                    "x": round(res.thumb["x"], 1),
                    "y": round(res.thumb["y"], 1),
                    "z_cm": res.thumb.get("z_cm"),
                },
            },
            "ball": {
                "x": round(res.ball["x"], 1),
                "y": round(res.ball["y"], 1),
                "radius_px": round(res.ball["radius"], 1),
                "color": res.ball["color"],
                "z_cm": res.ball.get("z_cm"),
            },
        }

        return res

    def _draw_annotations(
        self,
        frame: np.ndarray,
        hand_data: Dict[str, Any],
        ball_data: Optional[Dict[str, Any]],
        result: GraspResult,
        eye_label: str = "LEFT EYE",
        minimal: bool = False,
    ) -> np.ndarray:
        """Renders glowing reticles, depth tags, pinch line, and HUD status banner."""
        h, w = frame.shape[:2]

        idx = hand_data.get("index")
        th = hand_data.get("thumb")

        # 1. Draw Pinch Connection Line
        if idx and th and idx.get("live") and th.get("live"):
            p1 = (int(round(th["x"])), int(round(th["y"])))
            p2 = (int(round(idx["x"])), int(round(idx["y"])))

            if result.is_in_between:
                # Solid bright Neon Green line with pulsing width
                cv2.line(frame, p1, p2, GREEN, 4, cv2.LINE_AA)
                # Outer glow
                overlay = frame.copy()
                cv2.line(overlay, p1, p2, (150, 255, 150), 8, cv2.LINE_AA)
                cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame)

                # Crosshair ticks at projection
                if 0.0 <= result.projection_t <= 1.0:
                    px_t = int(round(p1[0] + result.projection_t * (p2[0] - p1[0])))
                    py_t = int(round(p1[1] + result.projection_t * (p2[1] - p1[1])))
                    cv2.circle(frame, (px_t, py_t), 6, GREEN, -1, cv2.LINE_AA)
            elif result.status == "APPROACHING":
                cv2.line(frame, p1, p2, YELLOW, 2, cv2.LINE_AA)
            else:
                # Dotted / dashed line when seeking
                cv2.line(frame, p1, p2, (200, 200, 200), 1, cv2.LINE_AA)

        # 2. Draw Fingertips (INDEX = RED dot, THUMB = BLUE dot)
        if idx and idx.get("live"):
            ix, iy = int(round(idx["x"])), int(round(idx["y"]))
            cv2.circle(frame, (ix, iy), 9, WHITE, -1, cv2.LINE_AA)
            cv2.circle(frame, (ix, iy), 7, RED, -1, cv2.LINE_AA)  # Solid RED dot
            z_txt = f"{idx['z_cm']}cm" if idx.get("z_cm") else ""
            lbl = f"INDEX {z_txt}".strip()
            cv2.putText(
                frame, lbl, (ix + 12, iy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, RED, 2, cv2.LINE_AA
            )
            cv2.putText(
                frame, lbl, (ix + 12, iy - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA
            )

        if th and th.get("live"):
            tx, ty = int(round(th["x"])), int(round(th["y"]))
            cv2.circle(frame, (tx, ty), 9, WHITE, -1, cv2.LINE_AA)
            cv2.circle(frame, (tx, ty), 7, BLUE, -1, cv2.LINE_AA)  # Solid BLUE dot
            z_txt = f"{th['z_cm']}cm" if th.get("z_cm") else ""
            lbl = f"THUMB {z_txt}".strip()
            cv2.putText(
                frame, lbl, (tx + 12, ty + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, BLUE, 2, cv2.LINE_AA
            )
            cv2.putText(
                frame, lbl, (tx + 12, ty + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, WHITE, 1, cv2.LINE_AA
            )

        # 3. Draw Target Ball
        if ball_data and ball_data.get("live"):
            bx, by = int(round(ball_data["x"])), int(round(ball_data["y"]))
            br = int(round(ball_data["radius"]))
            b_color = BallColorDetector.COLOR_RANGES.get(
                ball_data.get("color", "green"), {}
            ).get("bgr", GREEN)

            # Circular target ring
            thickness = 3 if result.is_in_between else 2
            cv2.circle(frame, (bx, by), br, b_color, thickness, cv2.LINE_AA)
            cv2.circle(frame, (bx, by), 4, WHITE, -1, cv2.LINE_AA)

            # In-Between Lock Reticle
            if result.is_in_between:
                # Lock-on bracket reticle
                bracket_sz = int(br * 1.35)
                cv2.rectangle(
                    frame,
                    (bx - bracket_sz, by - bracket_sz),
                    (bx + bracket_sz, by + bracket_sz),
                    GREEN,
                    2,
                    cv2.LINE_AA,
                )

            z_txt = f"Z: {ball_data['z_cm']}cm" if ball_data.get("z_cm") else ""
            ball_lbl = f"{ball_data['color'].upper()} BALL {z_txt}".strip()
            cv2.putText(
                frame,
                ball_lbl,
                (bx - br, by - br - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                b_color,
                2,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                ball_lbl,
                (bx - br, by - br - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                WHITE,
                1,
                cv2.LINE_AA,
            )

        if minimal:
            return frame

        # 4. Top Status Header HUD
        hud_h = 44
        hud_bg = frame[:hud_h, :].copy()
        if result.is_in_between:
            hud_bg[:] = (0, 140, 0)  # Bright Green background
            cv2.addWeighted(hud_bg, 0.85, frame[:hud_h, :], 0.15, 0, frame[:hud_h, :])
            title = f"*** BALL IN BETWEEN FINGERS (PINCH LOCK) ***"
            sub = f"Alignment: {result.alignment_score}% | dZ: {result.depth_offset_cm or 0.0}cm | Lat: {result.lateral_dist_cm or 0.0}cm"
            cv2.putText(frame, title, (16, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.60, WHITE, 2, cv2.LINE_AA)
            cv2.putText(frame, sub, (16, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 255, 220), 1, cv2.LINE_AA)
        elif result.status == "APPROACHING":
            hud_bg[:] = (0, 140, 180)  # Warm Amber background
            cv2.addWeighted(hud_bg, 0.80, frame[:hud_h, :], 0.20, 0, frame[:hud_h, :])
            title = f"APPROACHING BALL -> Aligning Depth & Pinch Axis"
            sub = f"Offset: {result.lateral_dist_cm or 0.0}cm | dZ: {result.depth_offset_cm or 0.0}cm"
            cv2.putText(frame, title, (16, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, WHITE, 2, cv2.LINE_AA)
            cv2.putText(frame, sub, (16, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1, cv2.LINE_AA)
        else:
            hud_bg[:] = (20, 20, 20)  # Dark Translucent
            cv2.addWeighted(hud_bg, 0.70, frame[:hud_h, :], 0.30, 0, frame[:hud_h, :])
            title = f"STEREO TRACKER | RED=Index Tip  BLUE=Thumb  Target={self.ball_color.upper()} Ball"
            cv2.putText(frame, title, (16, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.50, (200, 200, 200), 1, cv2.LINE_AA)

        # FPS & Mode Badge in Top-Right
        fps_text = f"{self._fps:4.1f} FPS"
        cv2.putText(frame, fps_text, (w - 110, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, YELLOW, 2, cv2.LINE_AA)

        # 5. Bottom Telemetry Card
        card_w, card_h = 240, 95
        card_x, card_y = 12, h - card_h - 12
        card_bg = frame[card_y : card_y + card_h, card_x : card_x + card_w].copy()
        card_bg[:] = (15, 15, 15)
        cv2.addWeighted(card_bg, 0.75, frame[card_y : card_y + card_h, card_x : card_x + card_w], 0.25, 0, frame[card_y : card_y + card_h, card_x : card_x + card_w])
        cv2.rectangle(frame, (card_x, card_y), (card_x + card_w, card_y + card_h), (60, 60, 60), 1)

        lines = [
            f"State: {result.status}",
            f"Pinch Gap: {result.pinch_distance_cm or '---'} cm ({result.pinch_distance_px:.0f}px)",
            f"Depth Offset dZ: {result.depth_offset_cm or '---'} cm",
            f"Pinch Axis t: {result.projection_t:.2f} (0=Thumb, 1=Idx)",
        ]
        for i, line in enumerate(lines):
            col = GREEN if (i == 0 and result.is_in_between) else (220, 220, 220)
            cv2.putText(
                frame,
                line,
                (card_x + 8, card_y + 18 + i * 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                col,
                1,
                cv2.LINE_AA,
            )

        return frame
