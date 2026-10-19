"""
Unit tests for global single-lock management, lock-on transitions,
run/brake signaling, and motor-specific isolation.
"""

import sys
import os
import unittest

# Ensure robot folder is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from hardware.gpio_bus import SimulationBus
from communication.transmitter import BusTransmitter
from communication.receiver import FeedbackReceiver
from communication.feedback import FeedbackQueue
from control.lock_manager import LockManager
from control.motor_manager import MotorManager


class TestLockManager(unittest.TestCase):

    def setUp(self):
        self.bus = SimulationBus()
        self.queue = FeedbackQueue()
        self.transmitter = BusTransmitter(self.bus)
        self.receiver = FeedbackReceiver(self.bus, queue=self.queue)
        self.lock_mgr = LockManager(self.bus, self.transmitter)
        self.motor_mgr = MotorManager(self.transmitter, self.receiver, self.lock_mgr)

    def tearDown(self):
        self.bus.close()

    def test_single_lock_invariant(self):
        """Verify that only one motor across all ESPs can hold the global lock."""
        # 1. Lock ESP 1 Motor 2
        self.lock_mgr.lock_motor(esp_id=1, motor_id=1, direction=0)
        self.assertTrue(self.lock_mgr.is_locked(esp_id=1, motor_id=1))
        self.assertEqual(self.lock_mgr.locked_motor, (1, 1, 0))

        # 2. Lock ESP 4 Motor 1 -> ESP 1 Motor 2 must lose lock
        self.lock_mgr.lock_motor(esp_id=4, motor_id=0, direction=1)
        self.assertFalse(self.lock_mgr.is_locked(esp_id=1, motor_id=1))
        self.assertTrue(self.lock_mgr.is_locked(esp_id=4, motor_id=0))
        self.assertEqual(self.lock_mgr.locked_motor, (4, 0, 1))

    def test_run_brake_signals(self):
        """Verify that run() and brake() toggle DATA without affecting SET (which stays HIGH)."""
        self.lock_mgr.lock_motor(esp_id=2, motor_id=3, direction=0)

        # In lock-on mode, SET must remain HIGH
        self.assertEqual(self.bus.set_state, 1)

        # Call run() -> DATA should go HIGH
        self.lock_mgr.run()
        self.assertEqual(self.bus.data_state, 1)
        self.assertEqual(self.bus.set_state, 1)

        # Call brake() -> DATA should go LOW
        self.lock_mgr.brake()
        self.assertEqual(self.bus.data_state, 0)
        self.assertEqual(self.bus.set_state, 1)

    def test_clear_all_locks(self):
        """Verify that clear_all_locks() resets global lock state and drives DATA LOW."""
        self.lock_mgr.lock_motor(esp_id=3, motor_id=2, direction=1)
        self.lock_mgr.run()
        self.assertEqual(self.bus.data_state, 1)

        self.lock_mgr.clear_all_locks()
        self.assertIsNone(self.lock_mgr.locked_motor)
        self.assertEqual(self.bus.data_state, 0)
        self.assertFalse(self.lock_mgr.is_locked(esp_id=3, motor_id=2))

    def test_motor_isolation(self):
        """Verify motor state isolation: commands to Motor 1 do not affect Motor 2."""
        m1 = self.motor_mgr.get_motor_state(esp_id=1, motor_id=0)
        m2 = self.motor_mgr.get_motor_state(esp_id=1, motor_id=1)
        self.assertEqual(m1["mode"], "IDLE")
        self.assertEqual(m2["mode"], "IDLE")

        # Command Motor 1
        self.motor_mgr.move_pulses(esp_id=1, motor_id=0, pulse_count=1000, direction=0)
        m1_after = self.motor_mgr.get_motor_state(esp_id=1, motor_id=0)
        m2_after = self.motor_mgr.get_motor_state(esp_id=1, motor_id=1)

        self.assertEqual(m1_after["mode"], "MOVING_NORMAL")
        self.assertEqual(m1_after["target_pulses"], 1000)
        # Motor 2 must remain completely idle and unaffected
        self.assertEqual(m2_after["mode"], "IDLE")
        self.assertEqual(m2_after["target_pulses"], 0)


if __name__ == "__main__":
    unittest.main()
