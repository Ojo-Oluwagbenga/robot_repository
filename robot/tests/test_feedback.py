"""
Unit tests for 17-bit feedback packet decoding, magnitude/sign representation,
and net pulse calculation.
"""

import sys
import os
import unittest

# Ensure robot folder is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from communication.protocol import (
    decode_feedback_packet,
    encode_feedback_packet,
    int_to_bits,
    bits_to_int
)
from communication.feedback import FeedbackPacket


class TestFeedback(unittest.TestCase):

    def test_positive_feedback_prompt_example(self):
        """
        Test exact prompt example 1:
        Net pulses = +100 +100 +100 -20 -10 = +270.
        Motor 2 (ID: 01)
        Expected:
          Motor ID: 01
          Magnitude: 270 (00000100001110)
          Sign: 1 (Positive)
        """
        # Calculate net pulses
        pulse_stream = [+100, +100, +100, -20, -10]
        net = sum(pulse_stream)
        self.assertEqual(net, 270)

        # Encode into 17-bit packet for Motor 2 (0-indexed ID = 1)
        raw_bits = encode_feedback_packet(motor_id=1, net_pulses=net)
        self.assertEqual(len(raw_bits), 17)

        # Verify bits
        # Bits 0..1: Motor ID = 01
        self.assertEqual(raw_bits[0:2], [0, 1])
        # Bits 2..15: Magnitude = 270 = 00000100001110
        expected_mag_bits = [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1, 1, 0]
        self.assertEqual(raw_bits[2:16], expected_mag_bits)
        self.assertEqual(bits_to_int(raw_bits[2:16]), 270)
        # Bit 16: Sign = 1
        self.assertEqual(raw_bits[16], 1)

        # Decode packet
        motor_id, mag, sign, decoded_net, overflow = decode_feedback_packet(esp_id=2, bits=raw_bits)
        self.assertEqual(motor_id, 1)
        self.assertEqual(mag, 270)
        self.assertEqual(sign, 1)
        self.assertEqual(decoded_net, +270)
        self.assertFalse(overflow)

    def test_negative_feedback_prompt_example(self):
        """
        Test exact prompt example 2:
        Net pulses = -500 +100 -50 = -450.
        Motor 2 (ID: 01)
        Expected:
          Motor ID: 01
          Magnitude: 450 (00000111000010)
          Sign: 0 (Negative)
        """
        pulse_stream = [-500, +100, -50]
        net = sum(pulse_stream)
        self.assertEqual(net, -450)

        raw_bits = encode_feedback_packet(motor_id=1, net_pulses=net)
        self.assertEqual(len(raw_bits), 17)

        # Bits 0..1: Motor ID = 01
        self.assertEqual(raw_bits[0:2], [0, 1])
        # Bits 2..15: Magnitude = 450 = 00000111000010
        expected_mag_bits = [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 1, 0]
        self.assertEqual(raw_bits[2:16], expected_mag_bits)
        self.assertEqual(bits_to_int(raw_bits[2:16]), 450)
        # Bit 16: Sign = 0 (Negative)
        self.assertEqual(raw_bits[16], 0)

        # Decode packet
        motor_id, mag, sign, decoded_net, overflow = decode_feedback_packet(esp_id=2, bits=raw_bits)
        self.assertEqual(motor_id, 1)
        self.assertEqual(mag, 450)
        self.assertEqual(sign, 0)
        self.assertEqual(decoded_net, -450)
        self.assertFalse(overflow)

    def test_zero_net_movement(self):
        """Test zero net pulses (+100 -100 = 0): Magnitude 0, Sign 1 (Positive/Non-negative)."""
        raw_bits = encode_feedback_packet(motor_id=0, net_pulses=0)
        self.assertEqual(len(raw_bits), 17)
        motor_id, mag, sign, net, overflow = decode_feedback_packet(esp_id=1, bits=raw_bits)
        self.assertEqual(mag, 0)
        self.assertEqual(sign, 1)
        self.assertEqual(net, 0)

    def test_invalid_feedback_length(self):
        """Test rejection of corrupted feedback packet lengths."""
        with self.assertRaises(ValueError):
            decode_feedback_packet(esp_id=1, bits=[0] * 16)
        with self.assertRaises(ValueError):
            decode_feedback_packet(esp_id=1, bits=[0] * 18)

    def test_overflow_detection(self):
        """Test explicit overflow handling when magnitude reaches or exceeds 16383."""
        # 16383 is maximum 14-bit unsigned magnitude
        raw_bits = encode_feedback_packet(motor_id=3, net_pulses=20000)
        motor_id, mag, sign, net, overflow = decode_feedback_packet(esp_id=5, bits=raw_bits)
        self.assertEqual(mag, 16383)
        self.assertTrue(overflow)


    def test_multiple_independent_motor_responses(self):
        """
        Test that multiple feedback packets from independent motors (e.g. Motor 1 and Motor 3)
        can be sequentially encoded and decoded, accurately identifying the motor ID and net pulses.
        """
        # Motor 1 moved +500 pulses
        fb1 = encode_feedback_packet(motor_id=0, net_pulses=500)
        # Motor 3 moved -320 pulses
        fb2 = encode_feedback_packet(motor_id=2, net_pulses=-320)

        # Decode packet 1
        m1, mag1, sign1, net1, of1 = decode_feedback_packet(esp_id=1, bits=fb1)
        self.assertEqual(m1, 0)
        self.assertEqual(net1, 500)

        # Decode packet 2
        m2, mag2, sign2, net2, of2 = decode_feedback_packet(esp_id=1, bits=fb2)
        self.assertEqual(m2, 2)
        self.assertEqual(net2, -320)


    def test_receiver_drains_multiple_packets_from_same_esp(self):
        """
        Verify that FeedbackReceiver.poll_feedback() completely drains multiple consecutive
        packets queued on the same ESP (e.g. Motor 1 and Motor 2 both finished).
        """
        from hardware.gpio_bus import SimulationBus
        from communication.receiver import FeedbackReceiver

        bus = SimulationBus()
        receiver = FeedbackReceiver(bus)

        # Queue 2 packets for ESP 1: Motor 1 (+1000) and Motor 2 (-600)
        pkt1 = encode_feedback_packet(motor_id=0, net_pulses=1000)
        pkt2 = encode_feedback_packet(motor_id=1, net_pulses=-600)
        bus.queue_simulated_feedback(1, pkt1)
        bus.queue_simulated_feedback(1, pkt2)

        # Poll feedback: should drain BOTH packets back-to-back in one call!
        drained = receiver.poll_feedback()
        self.assertEqual(len(drained), 2)

        # Packet 1: Motor 1
        self.assertEqual(drained[0].esp_id, 1)
        self.assertEqual(drained[0].motor_id, 0)
        self.assertEqual(drained[0].net_pulses, 1000)

        # Packet 2: Motor 2
        self.assertEqual(drained[1].esp_id, 1)
        self.assertEqual(drained[1].motor_id, 1)
        self.assertEqual(drained[1].net_pulses, -600)

        # Feedback line should now be 0 (LOW)
        self.assertEqual(bus.read_feedback(1), 0)


if __name__ == "__main__":
    unittest.main()
