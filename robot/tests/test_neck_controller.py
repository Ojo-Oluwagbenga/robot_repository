"""
Unit tests for Neck Controller and Safety Envelope.
Verifies hardware protection rules:
  1. Homing confirmation requirement.
  2. Pitch downward restriction at home (net_pitch >= 0).
  3. Pitch upward limit (net_pitch <= 5000).
  4. Yaw sideways limits ([-3000, 3000]).
  5. Proportional centering calculations.
"""

import sys
import unittest
from pathlib import Path

# Setup paths
ROBOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from control.neck_controller import (
    NeckSafetyEnvelope,
    NeckCalibration,
    PITCH_DIR_UP,
    PITCH_DIR_DOWN,
    PITCH_MIN_PULSES,
    PITCH_MAX_PULSES,
    YAW_MIN_PULSES,
    YAW_MAX_PULSES,
)


class TestNeckSafetyEnvelope(unittest.TestCase):

    def setUp(self):
        self.env = NeckSafetyEnvelope()

    def test_unconfirmed_home_blocks_all_movement(self):
        """No movements should be permitted before home position is confirmed."""
        allowed, reason = self.env.clamp_pitch(100, PITCH_DIR_UP)
        self.assertEqual(allowed, 0)
        self.assertIn("REJECTED", reason)

        allowed, reason = self.env.clamp_yaw(100, 0)
        self.assertEqual(allowed, 0)
        self.assertIn("REJECTED", reason)

    def test_home_downward_blocked(self):
        """When at home (net_pitch = 0), downward motion must be strictly blocked!"""
        self.env.set_home_confirmed()
        self.assertEqual(self.env.net_pitch, 0)

        # Attempt to move down (1-6-x-1)
        allowed, reason = self.env.clamp_pitch(200, PITCH_DIR_DOWN)
        self.assertEqual(allowed, 0)
        self.assertIn("BLOCKED", reason)

    def test_pitch_upward_and_downward_limits(self):
        """Verify pitch can move up to 5000 and then back down safely."""
        self.env.set_home_confirmed()

        # Move up 2000 pulses
        allowed, reason = self.env.clamp_pitch(2000, PITCH_DIR_UP)
        self.assertEqual(allowed, 2000)
        self.env.record_pitch_executed(allowed, PITCH_DIR_UP)
        self.assertEqual(self.env.net_pitch, 2000)

        # Now can move down at most 2000
        allowed, reason = self.env.clamp_pitch(1500, PITCH_DIR_DOWN)
        self.assertEqual(allowed, 1500)
        self.env.record_pitch_executed(allowed, PITCH_DIR_DOWN)
        self.assertEqual(self.env.net_pitch, 500)

        # Try to move down 1000: should be clamped to 500
        allowed, reason = self.env.clamp_pitch(1000, PITCH_DIR_DOWN)
        self.assertEqual(allowed, 500)
        self.assertIn("CLAMPED", reason)
        self.env.record_pitch_executed(allowed, PITCH_DIR_DOWN)
        self.assertEqual(self.env.net_pitch, 0)

        # Once back at 0, cannot move down anymore
        allowed, reason = self.env.clamp_pitch(10, PITCH_DIR_DOWN)
        self.assertEqual(allowed, 0)

    def test_pitch_max_limit_5000(self):
        """Verify pitch cannot exceed 5000 pulses upward."""
        self.env.set_home_confirmed()

        # Request 6000 upward: should clamp to 5000
        allowed, reason = self.env.clamp_pitch(6000, PITCH_DIR_UP)
        self.assertEqual(allowed, 5000)
        self.env.record_pitch_executed(allowed, PITCH_DIR_UP)
        self.assertEqual(self.env.net_pitch, 5000)

        # Further upward move must be blocked
        allowed, reason = self.env.clamp_pitch(10, PITCH_DIR_UP)
        self.assertEqual(allowed, 0)
        self.assertIn("BLOCKED", reason)

    def test_yaw_range_limits_3000(self):
        """Verify yaw cannot exceed [-3000, 3000]."""
        self.env.set_home_confirmed()
        dir_right = 0
        dir_left = 1

        # Move right 2000
        allowed, reason = self.env.clamp_yaw(2000, dir_right, dir_right=dir_right)
        self.assertEqual(allowed, 2000)
        self.env.record_yaw_executed(allowed, dir_right, dir_right=dir_right)
        self.assertEqual(self.env.net_yaw, 2000)

        # Try to move right 1500: should clamp to 1000 (reaching 3000 max)
        allowed, reason = self.env.clamp_yaw(1500, dir_right, dir_right=dir_right)
        self.assertEqual(allowed, 1000)
        self.env.record_yaw_executed(allowed, dir_right, dir_right=dir_right)
        self.assertEqual(self.env.net_yaw, 3000)

        # Further right blocked
        allowed, reason = self.env.clamp_yaw(10, dir_right, dir_right=dir_right)
        self.assertEqual(allowed, 0)
        self.assertIn("BLOCKED", reason)

        # Move left 6500: should clamp to 6000 (reaching -3000 min)
        allowed, reason = self.env.clamp_yaw(6500, dir_left, dir_right=dir_right)
        self.assertEqual(allowed, 6000)
        self.env.record_yaw_executed(allowed, dir_left, dir_right=dir_right)
        self.assertEqual(self.env.net_yaw, -3000)

        # Further left blocked
        allowed, reason = self.env.clamp_yaw(10, dir_left, dir_right=dir_right)
        self.assertEqual(allowed, 0)
        self.assertIn("BLOCKED", reason)


class TestNeckCalibration(unittest.TestCase):

    def test_serialization(self):
        cal = NeckCalibration(
            gain_yaw=4.2,
            gain_pitch=3.8,
            yaw_dir_right=1,
            yaw_dir_left=0,
            pitch_dir_up=0,
            pitch_dir_down=1,
        )
        d = cal.to_dict()
        cal2 = NeckCalibration.from_dict(d)
        self.assertAlmostEqual(cal2.gain_yaw, 4.2)
        self.assertAlmostEqual(cal2.gain_pitch, 3.8)
        self.assertEqual(cal2.yaw_dir_right, 1)
        self.assertEqual(cal2.yaw_dir_left, 0)


class TestAdaptiveGains(unittest.TestCase):

    def test_adaptive_gain_tuning(self):
        from control.neck_controller import NeckController
        neck = NeckController()
        neck.calibration.gain_yaw = 3.0
        neck.calibration.gain_pitch = 3.5

        # Ball was at +80px error, moved 200 pulses, now at +40px error (actual disp = 40px)
        # Observed gain needed = 200 / 40 = 5.0.
        # With alpha = 0.12, new gain = 0.88 * 3.0 + 0.12 * 5.0 = 2.64 + 0.60 = 3.24
        res = neck.adapt_gains(
            current_error_x=40.0,
            current_error_y=0.0,
            prev_error_x=80.0,
            prev_error_y=0.0,
            moved_yaw=200,
            moved_pitch=0,
            alpha=0.12,
        )
        self.assertTrue(res["yaw_adapted"])
        self.assertAlmostEqual(neck.calibration.gain_yaw, 3.24, places=2)

        # Disturbance rejection: if ball moves away by 50px externally, adaptation is skipped
        neck.calibration.gain_yaw = 3.24
        res2 = neck.adapt_gains(
            current_error_x=140.0,
            current_error_y=0.0,
            prev_error_x=80.0,
            prev_error_y=0.0,
            moved_yaw=200,
            moved_pitch=0,
            alpha=0.12,
        )
        self.assertFalse(res2["yaw_adapted"])
        self.assertEqual(neck.calibration.gain_yaw, 3.24)


class TestSimultaneousMovement(unittest.TestCase):

    def test_move_simultaneous_and_center_target(self):
        from control.neck_controller import NeckController, PITCH_DIR_UP, PITCH_DIR_DOWN
        neck = NeckController()
        neck.envelope.set_home_confirmed()

        # Both axes valid: move yaw 300, pitch 500 up
        res = neck.move_simultaneous(
            yaw_pulses=300,
            yaw_direction=neck.calibration.yaw_dir_right,
            pitch_pulses=500,
            pitch_direction=PITCH_DIR_UP,
            wait=False
        )
        self.assertEqual(res["yaw_moved"], 300)
        self.assertEqual(res["pitch_moved"], 500)
        self.assertEqual(res["status_x"], "MOVED")
        self.assertEqual(res["status_y"], "MOVED")
        self.assertEqual(neck.envelope.net_yaw, 300)
        self.assertEqual(neck.envelope.net_pitch, 500)

        # Center target with both errors outside deadband -> triggers simultaneous move
        # error_x = 60 (> 25) -> yaw right; error_y = -80 (< -25) -> pitch up
        res_center = neck.center_target(error_x=60.0, error_y=-80.0)
        self.assertGreater(res_center["yaw_moved"], 0)
        self.assertGreater(res_center["pitch_moved"], 0)
        self.assertEqual(res_center["status_x"], "MOVED")
        self.assertEqual(res_center["status_y"], "MOVED")

        # Clamping check: at net_pitch=780, try to move down 1000 -> clamped to 780
        curr_pitch = neck.envelope.net_pitch
        res_clamped = neck.move_simultaneous(
            yaw_pulses=200,
            yaw_direction=neck.calibration.yaw_dir_left,
            pitch_pulses=curr_pitch + 500,
            pitch_direction=PITCH_DIR_DOWN,
            wait=False
        )
        self.assertEqual(res_clamped["yaw_moved"], 200)
        self.assertEqual(res_clamped["pitch_moved"], curr_pitch)
        self.assertEqual(neck.envelope.net_pitch, 0)


if __name__ == "__main__":
    unittest.main()
