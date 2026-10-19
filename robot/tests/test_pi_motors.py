"""
Unit tests for direct Raspberry Pi 5 motors (steppers and timed head pan).
"""

import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
ROBOT_DIR = TEST_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from hardware.pi_motors import StepperMotor, TimedHeadMotor, PiMotorSystem
from bridge.code_mappings import resolve_action_file


class TestPiMotors(unittest.TestCase):
    def test_stepper_simulation(self):
        stepper = StepperMotor(dir_pin=6, step_pin=5, name="Test Right Shoulder")
        success = stepper.step(steps=50, direction=0, step_delay_s=0.001)
        self.assertTrue(success)

    def test_head_turn_simulation(self):
        head = TimedHeadMotor(in1_pin=20, in2_pin=16, ms_per_deg=100)
        # Turn 10 degrees -> 1000ms duration
        success = head.turn_degrees(degrees=10, direction="left")
        self.assertTrue(success)

    def test_pi_motor_system_instantiation(self):
        system = PiMotorSystem()
        self.assertIsNotNone(system.shoulder_right)
        self.assertIsNotNone(system.shoulder_left)
        self.assertIsNotNone(system.head_turn)
        system.close()

    def test_code_mappings_resolve_new_motors(self):
        sr = resolve_action_file("SHOULDER_RIGHT")
        self.assertIsNotNone(sr)
        self.assertEqual(sr.name, "shoulder_right.py")

        sl = resolve_action_file("SHOULDER_LEFT")
        self.assertIsNotNone(sl)
        self.assertEqual(sl.name, "shoulder_left.py")

        tl = resolve_action_file("TURN_LEFT")
        self.assertIsNotNone(tl)
        self.assertEqual(tl.name, "turn_left.py")

        nu = resolve_action_file("NOD_UP")
        self.assertIsNotNone(nu)
        self.assertEqual(nu.name, "nod_up.py")


if __name__ == "__main__":
    unittest.main()
