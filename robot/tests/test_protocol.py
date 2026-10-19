"""
Unit tests for protocol bit-packing, command serialization, and decoding.
"""

import sys
import os
import unittest

# Ensure robot folder is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from communication.protocol import (
    int_to_bits,
    bits_to_int,
    build_normal_command,
    build_go_ahead_command,
    build_clear_queue_command,
    build_lock_command,
    build_clear_lock_command,
    decode_command_packet
)


class TestProtocol(unittest.TestCase):

    def test_esp_id_encoding(self):
        """Test exact 3-bit binary encoding for all 5 ESP IDs."""
        self.assertEqual(int_to_bits(1, 3), [0, 0, 1])  # ESP1 = 001
        self.assertEqual(int_to_bits(2, 3), [0, 1, 0])  # ESP2 = 010
        self.assertEqual(int_to_bits(3, 3), [0, 1, 1])  # ESP3 = 011
        self.assertEqual(int_to_bits(4, 3), [1, 0, 0])  # ESP4 = 100
        self.assertEqual(int_to_bits(5, 3), [1, 0, 1])  # ESP5 = 101

    def test_motor_id_encoding(self):
        """Test exact 2-bit binary encoding for Motor IDs 0-3."""
        self.assertEqual(int_to_bits(0, 2), [0, 0])  # Motor 1 = 00
        self.assertEqual(int_to_bits(1, 2), [0, 1])  # Motor 2 = 01
        self.assertEqual(int_to_bits(2, 2), [1, 0])  # Motor 3 = 10
        self.assertEqual(int_to_bits(3, 2), [1, 1])  # Motor 4 = 11

    def test_normal_21bit_packet_construction(self):
        """Test complete 21-bit normal packet construction and field bit-exactness."""
        # ESP 3 (011), Motor 2 (01), 1500 pulses (15 bits), Direction 1 (CCW)
        bits = build_normal_command(esp_id=3, motor_id=1, pulse_count=1500, direction=1)
        self.assertEqual(len(bits), 21)

        # Verify bit fields
        self.assertEqual(bits[0:3], [0, 1, 1])   # ESP 3
        self.assertEqual(bits[3:5], [0, 1])      # Motor 2 (ID: 1)
        self.assertEqual(bits_to_int(bits[5:20]), 1500) # 15-bit pulse count
        self.assertEqual(bits[20], 1)            # Direction 1

        # Decode verification
        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "NORMAL")
        self.assertEqual(decoded["length"], 21)
        self.assertEqual(decoded["esp_id"], 3)
        self.assertEqual(decoded["motor_id"], 1)
        self.assertEqual(decoded["pulse_count"], 1500)
        self.assertEqual(decoded["direction"], 1)

    def test_go_ahead_5bit_packet(self):
        """Test 5-bit 10111 (Go Ahead) packet construction and decoding."""
        bits = build_go_ahead_command()
        self.assertEqual(bits, [1, 0, 1, 1, 1])

        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "GO_AHEAD")
        self.assertEqual(decoded["length"], 5)

    def test_clear_queue_5bit_packet(self):
        """Test 5-bit 10100 (Clear Queue) packet construction and decoding."""
        bits = build_clear_queue_command()
        self.assertEqual(bits, [1, 0, 1, 0, 0])

        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "CLEAR_QUEUE")
        self.assertEqual(decoded["length"], 5)

    def test_lock_on_6bit_packet_construction(self):
        """Test 6-bit lock-on packet construction matching prompt example: ESP1, M3, Dir 1 -> 001101."""
        # ESP ID = 001, Motor ID = 10 (Motor 3, 0-indexed = 2), Direction = 1 -> 001101
        bits = build_lock_command(esp_id=1, motor_id=2, direction=1)
        self.assertEqual(len(bits), 6)
        self.assertEqual(bits, [0, 0, 1, 1, 0, 1])

        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "LOCK_ON")
        self.assertEqual(decoded["esp_id"], 1)
        self.assertEqual(decoded["motor_id"], 2)
        self.assertEqual(decoded["direction"], 1)

    def test_clear_lock_packet(self):
        """Test special 000000 clear lock-on broadcast packet."""
        bits = build_clear_lock_command()
        self.assertEqual(bits, [0, 0, 0, 0, 0, 0])

        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "CLEAR_LOCK")
        self.assertEqual(decoded["esp_id"], 0)

    def test_invalid_packet_lengths(self):
        """Test that invalid bit lengths are rejected."""
        invalid_lengths = [0, 1, 2, 3, 4, 8, 19, 22, 32]
        for length in invalid_lengths:
            test_bits = [0] * length
            with self.assertRaises(ValueError):
                decode_command_packet(test_bits)

    def test_esp4_timed_command(self):
        """Test construction and decoding of 7-bit ESP 4 timed commands."""
        from communication.protocol import build_esp4_timed_command
        # Motor 1 (10), 3 units = 600ms (0011), Close (1) -> [1, 0, 0, 0, 1, 1, 1]
        bits = build_esp4_timed_command(motor_id=1, duration_units=3, direction=1)
        self.assertEqual(len(bits), 7)
        self.assertEqual(bits, [1, 0, 0, 0, 1, 1, 1])

        decoded = decode_command_packet(bits)
        self.assertEqual(decoded["type"], "ESP4_TIMED_MOVE")
        self.assertEqual(decoded["esp_id"], 4)
        self.assertEqual(decoded["motor_id"], 1)
        self.assertEqual(decoded["duration_units"], 3)
        self.assertEqual(decoded["duration_ms"], 600)
        self.assertEqual(decoded["direction"], 1)

        # Motor 2 (11), 5 units = 1000ms (0101), Open (0) -> [1, 1, 0, 1, 0, 1, 0]
        bits2 = build_esp4_timed_command(motor_id=2, duration_units=5, direction=0)
        self.assertEqual(bits2, [1, 1, 0, 1, 0, 1, 0])
        decoded2 = decode_command_packet(bits2)
        self.assertEqual(decoded2["motor_id"], 2)
        self.assertEqual(decoded2["duration_ms"], 1000)
        self.assertEqual(decoded2["direction"], 0)

    def test_parameter_bounds(self):
        """Test that invalid parameter values are rejected during construction."""
        # Invalid ESP ID
        with self.assertRaises(ValueError):
            build_normal_command(esp_id=6, motor_id=0, pulse_count=100, direction=0)
        with self.assertRaises(ValueError):
            build_normal_command(esp_id=0, motor_id=0, pulse_count=100, direction=0)

        # Invalid Motor ID
        with self.assertRaises(ValueError):
            build_normal_command(esp_id=1, motor_id=4, pulse_count=100, direction=0)

        # Pulse count overflow (15-bit max is 32767)
        with self.assertRaises(ValueError):
            build_normal_command(esp_id=1, motor_id=0, pulse_count=32768, direction=0)

    def test_neck_command_parsing(self):
        """Test parsing and dispatching of M2, N1 and N2 motor pulse commands."""
        from unittest.mock import MagicMock
        from main import parse_and_execute_neck_command, parse_and_execute_motor_command

        mock_robot = MagicMock()

        # M2_20 -> ESP 1, Motor 4 (id=3), 20 pulses, CW (0)
        handled = parse_and_execute_motor_command(mock_robot, "M2_20")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=1, motor_id=3, pulse_count=20, direction=0
        )

        # M2_-20 -> ESP 1, Motor 4 (id=3), 20 pulses, CCW (1)
        handled = parse_and_execute_motor_command(mock_robot, "M2_-20")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=1, motor_id=3, pulse_count=20, direction=1
        )

        # Direct E1_M4_50 syntax
        handled = parse_and_execute_motor_command(mock_robot, "E1_M4_50")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=1, motor_id=3, pulse_count=50, direction=0
        )

        # N2_20 -> ESP 3, Motor 2 (id=1), 20 pulses, CW (0)
        handled = parse_and_execute_neck_command(mock_robot, "N2_20")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=3, motor_id=1, pulse_count=20, direction=0
        )

        # N2_-20 -> ESP 3, Motor 2 (id=1), 20 pulses, CCW (1)
        handled = parse_and_execute_neck_command(mock_robot, "N2_-20")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=3, motor_id=1, pulse_count=20, direction=1
        )

        # N1_50 -> ESP 3, Motor 1 (id=0), 50 pulses, CW (0)
        handled = parse_and_execute_neck_command(mock_robot, "N1_50")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=3, motor_id=0, pulse_count=50, direction=0
        )

        # N1_-100 -> ESP 3, Motor 1 (id=0), 100 pulses, CCW (1)
        handled = parse_and_execute_neck_command(mock_robot, "n1_-100")
        self.assertTrue(handled)
        mock_robot.motor_manager.move_pulses.assert_called_with(
            esp_id=3, motor_id=0, pulse_count=100, direction=1
        )

        # Unrelated command returns False
        handled = parse_and_execute_neck_command(mock_robot, "norm 1 1 500 0")
        self.assertFalse(handled)


if __name__ == "__main__":
    unittest.main()


