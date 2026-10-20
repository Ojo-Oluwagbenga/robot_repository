"""
Unit Tests for Hand & Ball Stereo Depth Grasp Tracker.
Verifies the 3D depth and spatial betweenness conditions.
"""

import math
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robot.vision.hand_ball_tracker import HandBallGraspTracker, GraspResult


def test_ball_directly_in_between_grasp():
    """Verify that when ball is situated centered between index & thumb at same depth, it reports IN_BETWEEN."""
    tracker = HandBallGraspTracker(
        model_path="robot/hand_tracker/models/hand_tips.onnx",
        ball_color="green",
    )

    # Simulated detection: Index at (300, 200), Thumb at (400, 200) -> Midpoint at (350, 200)
    # Ball centered at (350, 200)
    hand_data = {
        "index": {
            "x": 300.0,
            "y": 200.0,
            "live": True,
            "z_cm": 35.0,
            "pos_3d": (-5.0, 2.0, 35.0),
        },
        "thumb": {
            "x": 400.0,
            "y": 200.0,
            "live": True,
            "z_cm": 35.2,
            "pos_3d": (5.0, 2.0, 35.2),
        },
    }

    ball_data = {
        "x": 350.0,
        "y": 200.0,
        "radius": 24.0,
        "color": "green",
        "live": True,
        "z_cm": 35.1,
        "pos_3d": (0.0, 2.0, 35.1),
    }

    res: GraspResult = tracker._evaluate_grasp(hand_data, ball_data)

    assert res.is_in_between is True
    assert res.status in ("IN_BETWEEN", "GRASPED")
    assert 0.4 <= res.projection_t <= 0.6
    assert res.depth_offset_cm < 1.0
    assert res.alignment_score > 80.0
    print("[PASS] Test ball directly in between succeeded!")


def test_ball_out_of_depth_plane():
    """Verify that if ball is 2D-aligned between fingers, but physically behind them (e.g. 50cm vs 35cm),
    it is REJECTED by depth mapping."""
    tracker = HandBallGraspTracker(
        model_path="robot/hand_tracker/models/hand_tips.onnx",
        ball_color="green",
        depth_tolerance_cm=4.0,
    )

    hand_data = {
        "index": {"x": 300.0, "y": 200.0, "live": True, "z_cm": 35.0, "pos_3d": (-5.0, 2.0, 35.0)},
        "thumb": {"x": 400.0, "y": 200.0, "live": True, "z_cm": 35.0, "pos_3d": (5.0, 2.0, 35.0)},
    }

    # Ball visually at midpoint (350, 200), BUT at depth 52.0cm (17cm behind hand!)
    ball_data = {
        "x": 350.0,
        "y": 200.0,
        "radius": 24.0,
        "color": "green",
        "live": True,
        "z_cm": 52.0,
        "pos_3d": (0.0, 2.0, 52.0),
    }

    res: GraspResult = tracker._evaluate_grasp(hand_data, ball_data)

    assert res.is_in_between is False
    assert res.depth_offset_cm == 17.0
    print("[PASS] Test ball out of depth plane correctly rejected!")


def test_ball_outside_pinch_axis():
    """Verify that if ball is at the same depth, but far above the fingers, it is NOT in between."""
    tracker = HandBallGraspTracker(
        model_path="robot/hand_tracker/models/hand_tips.onnx",
        ball_color="green",
    )

    hand_data = {
        "index": {"x": 300.0, "y": 200.0, "live": True, "z_cm": 35.0, "pos_3d": (-5.0, 2.0, 35.0)},
        "thumb": {"x": 400.0, "y": 200.0, "live": True, "z_cm": 35.0, "pos_3d": (5.0, 2.0, 35.0)},
    }

    # Ball at same depth, but y=80 (120 pixels above the fingers)
    ball_data = {
        "x": 350.0,
        "y": 80.0,
        "radius": 24.0,
        "color": "green",
        "live": True,
        "z_cm": 35.0,
        "pos_3d": (0.0, 10.0, 35.0),
    }

    res: GraspResult = tracker._evaluate_grasp(hand_data, ball_data)

    assert res.is_in_between is False
    assert res.lateral_dist_px > 50.0
    print("[PASS] Test ball outside pinch axis correctly rejected!")


if __name__ == "__main__":
    test_ball_directly_in_between_grasp()
    test_ball_out_of_depth_plane()
    test_ball_outside_pinch_axis()
    print("ALL TESTS PASSED!")
