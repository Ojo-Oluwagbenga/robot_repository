#!/usr/bin/env python3
"""
Unit and integration tests for LegController.
"""

import sys
import unittest
from pathlib import Path

ROBOT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(ROBOT_ROOT))

from robot.control.leg_controller import (
    LegController,
    parse_course_input,
    STEERING_PULSES_PER_DEGREE,
    DRIVE_PULSES_PER_CM,
)


class TestLegController(unittest.TestCase):
    def test_parse_course_input(self):
        # Tuple syntax
        self.assertEqual(parse_course_input("(200, 0)"), (200.0, 0.0))
        self.assertEqual(parse_course_input("((200, -300))"), (200.0, -300.0))
        self.assertEqual(parse_course_input("(200, 200)"), (200.0, 200.0))
        # Plain comma / space syntax
        self.assertEqual(parse_course_input("200, -300"), (200.0, -300.0))
        self.assertEqual(parse_course_input("150 50"), (150.0, 50.0))
        self.assertIsNone(parse_course_input("invalid"))

    def test_calibration_constants(self):
        # 5500 pulses = 360 deg
        self.assertAlmostEqual(STEERING_PULSES_PER_DEGREE, 5500.0 / 360.0, places=3)
        # 2000 pulses = 5 cm -> 400 pulses/cm
        self.assertEqual(DRIVE_PULSES_PER_CM, 400.0)


if __name__ == "__main__":
    unittest.main()
