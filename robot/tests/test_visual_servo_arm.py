#!/usr/bin/env python3
"""
Unit Tests for Visual Servo Arm Controller
=============================================================================
Verifies:
  1. Joint definitions and configuration matching the arm kinematics.
  2. Table height safety guard blocking downward motions at table boundary.
  3. Joint model certainty scoring and step pulse limiting.
  4. Simulation visual servo pick workflow and pick verification.
=============================================================================
"""

import sys
from pathlib import Path
import pytest

ROBOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robot.control.visual_servo_arm import (
    VisualServoArmController,
    JointModel,
    JOINT_SHOULDER_FB,
    JOINT_SHOULDER_SIDE,
    JOINT_ELBOW_SIDE,
    JOINT_ELBOW_FB,
    JOINT_WRIST_ROT,
    JOINT_WRIST_PITCH,
    JOINT_GRIPPER,
    MIN_NOTICEABLE_PULSES,
    DIR_CW,
    DIR_CCW,
)


def test_motor_assignments():
    """Verify that motor configurations match the hardware specifications exactly."""
    assert JOINT_SHOULDER_FB == (5, 1), "Shoulder FB must be ESP 5, Motor 1 (Stepper A)"
    assert JOINT_SHOULDER_SIDE == (1, 1), "Shoulder Sideways must be ESP 1, Motor 1"
    assert JOINT_ELBOW_SIDE == (2, 1), "Elbow Sideways must be ESP 2, Motor 1 (Humerus)"
    assert JOINT_ELBOW_FB == (1, 3), "Elbow FB must be ESP 1, Motor 3"
    assert JOINT_WRIST_ROT == (1, 4), "Wrist Ulna Rotation must be ESP 1, Motor 4"
    assert JOINT_WRIST_PITCH == (1, 2), "Wrist Pitch must be ESP 1, Motor 2"
    assert JOINT_GRIPPER == (3, 2), "Fingers must be ESP 3, Motor 2"


def test_table_height_safety_guard():
    """Verify that neither the wrist nor elbow can drive downward past table level."""
    controller = VisualServoArmController(simulation=True)
    controller.table_limit_y = 550.0  # Table boundary at row 550

    # Proposed moves: elbow downward (CW), shoulder downward (CW), sideways (CW)
    test_moves = [
        (JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], 100, DIR_CW),         # Downward
        (JOINT_SHOULDER_SIDE[0], JOINT_SHOULDER_SIDE[1], 100, DIR_CW),  # Horizontal
        (JOINT_SHOULDER_FB[0], JOINT_SHOULDER_FB[1], 100, DIR_CW),    # Downward
    ]

    # 1. When hand is far above table (Y = 300), all moves should pass
    safe_high = controller.filter_downward_moves_at_table_level(test_moves, current_wrist_y=300.0)
    assert len(safe_high) == 3

    # 2. When hand is at or below table safety limit (Y = 540), downward moves must be blocked!
    safe_low = controller.filter_downward_moves_at_table_level(test_moves, current_wrist_y=540.0)
    assert len(safe_low) == 1
    assert safe_low[0][0:2] == JOINT_SHOULDER_SIDE, "Only horizontal shoulder sideways should pass"


def test_joint_model_uncertainty_learning():
    """Verify that certainty starts low, keeps steps small, and grows when consistent motion is observed."""
    model = JointModel("Elbow FB (1-3)", default_axis=(0.0, 20.0, 0.0))
    assert model.certainty == 0.20

    # Simulate observed physical displacement after a 100-pulse CW command
    model.update_observation(dir_moved=DIR_CW, pulses=100, actual_disp=(0.0, 18.0, 0.0))
    assert model.certainty > 0.30
    assert model.samples_collected == 1

    # Additional observations increase certainty further
    model.update_observation(dir_moved=DIR_CW, pulses=100, actual_disp=(0.0, 19.0, 0.0))
    model.update_observation(dir_moved=DIR_CW, pulses=100, actual_disp=(0.0, 21.0, 0.0))
    assert model.certainty >= 0.70


def test_simulated_pick_execution():
    """Verify full pick flow in simulation mode."""
    controller = VisualServoArmController(ball_color="red", simulation=True)
    success = controller.execute_visual_servo_pick(max_attempts=1, max_servo_steps=5)
    assert success is True


if __name__ == "__main__":
    test_motor_assignments()
    test_table_height_safety_guard()
    test_joint_model_uncertainty_learning()
    test_simulated_pick_execution()
    print("\n[ALL TESTS PASSED SUCCESSFULLY!]")
