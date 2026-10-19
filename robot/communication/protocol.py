"""
Protocol definitions, bit-level serialization, and validation for the custom synchronous bus.
"""

from typing import List, Tuple, Dict, Any
from config import (
    MAX_PULSE_COUNT,
    MAX_FEEDBACK_MAGNITUDE,
    VALID_ESP_IDS,
    VALID_MOTOR_IDS,
    DIRECTION_CW,
    DIRECTION_CCW,
    BROADCAST_CLEAR_LOCK_ID
)


def int_to_bits(value: int, num_bits: int) -> List[int]:
    """Convert an integer value into a list of bits (MSB at index 0)."""
    if value < 0:
        raise ValueError(f"Cannot convert negative integer {value} using unsigned bit packing.")
    max_val = (1 << num_bits) - 1
    if value > max_val:
        raise ValueError(f"Value {value} exceeds {num_bits}-bit maximum ({max_val}).")
    return [(value >> (num_bits - 1 - i)) & 1 for i in range(num_bits)]


def bits_to_int(bits: List[int]) -> int:
    """Convert a list of bits (MSB at index 0) into an unsigned integer."""
    val = 0
    for b in bits:
        val = (val << 1) | (b & 1)
    return val


def build_normal_command(esp_id: int, motor_id: int, pulse_count: int, direction: int) -> List[int]:
    """
    Construct a 21-bit Normal Motor Command packet.

    Bit structure (21 bits total):
    - Bits 0..2  (3 bits): ESP ID (001 to 101)
    - Bits 3..4  (2 bits): Motor ID (00 to 11)
    - Bits 5..19 (15 bits): Pulse count magnitude (0 to 32767)
    - Bit  20    (1 bit): Direction (0 = CW, 1 = CCW)
    """
    if esp_id not in VALID_ESP_IDS:
        raise ValueError(f"Invalid ESP ID {esp_id}. Must be one of {VALID_ESP_IDS}.")
    if motor_id not in VALID_MOTOR_IDS:
        raise ValueError(f"Invalid Motor ID {motor_id}. Must be one of {VALID_MOTOR_IDS} (0-3).")
    if pulse_count < 0 or pulse_count > MAX_PULSE_COUNT:
        raise ValueError(f"Pulse count {pulse_count} out of range (0 to {MAX_PULSE_COUNT}).")
    if direction not in (DIRECTION_CW, DIRECTION_CCW):
        raise ValueError(f"Invalid direction {direction}. Must be 0 (CW) or 1 (CCW).")

    bits: List[int] = []
    bits.extend(int_to_bits(esp_id, 3))       # Bits 0..2
    bits.extend(int_to_bits(motor_id, 2))     # Bits 3..4
    bits.extend(int_to_bits(pulse_count, 15)) # Bits 5..19
    bits.append(direction & 1)                # Bit 20

    if len(bits) != 21:
        raise RuntimeError(f"Internal error: Normal command length is {len(bits)} bits, expected exactly 21.")
    return bits


def build_go_ahead_command() -> List[int]:
    """
    Construct the special 5-bit broadcast packet: 10111 (Go Ahead).
    Instructs all ESP32 boards to execute all queued motor movements stored in memory.
    """
    return [1, 0, 1, 1, 1]


def build_clear_queue_command() -> List[int]:
    """
    Construct the special 5-bit broadcast packet: 10100 (Clear Queue).
    Instructs all ESP32 boards to clear and discard all queued motor movements in memory.
    """
    return [1, 0, 1, 0, 0]


def build_lock_command(esp_id: int, motor_id: int, direction: int) -> List[int]:
    """
    Construct a 6-bit Lock-On Command packet.

    Bit structure (6 bits total):
    - Bits 0..2 (3 bits): ESP ID (001 to 101)
    - Bits 3..4 (2 bits): Motor ID (00 to 11)
    - Bit  5    (1 bit): Direction (0 = CW, 1 = CCW)
    """
    if esp_id not in VALID_ESP_IDS:
        raise ValueError(f"Invalid ESP ID {esp_id}. Must be one of {VALID_ESP_IDS}.")
    if motor_id not in VALID_MOTOR_IDS:
        raise ValueError(f"Invalid Motor ID {motor_id}. Must be one of {VALID_MOTOR_IDS} (0-3).")
    if direction not in (DIRECTION_CW, DIRECTION_CCW):
        raise ValueError(f"Invalid direction {direction}. Must be 0 (CW) or 1 (CCW).")

    bits: List[int] = []
    bits.extend(int_to_bits(esp_id, 3))    # Bits 0..2
    bits.extend(int_to_bits(motor_id, 2))  # Bits 3..4
    bits.append(direction & 1)             # Bit 5

    if len(bits) != 6:
        raise RuntimeError(f"Internal error: Lock command length is {len(bits)} bits, expected exactly 6.")
    return bits


def build_clear_lock_command() -> List[int]:
    """
    Construct the special 6-bit broadcast packet: 000000.
    Instructs all 5 ESP32 boards to clear and release any active lock-on motor.
    """
    return [0, 0, 0, 0, 0, 0]


def build_esp4_timed_command(motor_id: int, duration_units: int, direction: int) -> List[int]:
    """
    Construct a 7-bit command packet for ESP #4.

    Bit structure (7 bits total):
    - Bits 0..1 (2 bits): Motor Selection (10 = Motor 1, 11 = Motor 2)
    - Bits 2..5 (4 bits): Duration multiplier (0 to 15, in increments of 200 ms)
    - Bit  6    (1 bit) : Direction (0 = Open, 1 = Close)

    Args:
        motor_id: 1 for Motor 1 (binary 10), 2 for Motor 2 (binary 11).
        duration_units: Integer 0 to 15 (duration_ms = duration_units * 200).
        direction: 0 for Open, 1 for Close.
    """
    if motor_id not in (1, 2):
        raise ValueError(f"Invalid motor_id {motor_id} for ESP 4. Must be 1 or 2.")
    if duration_units < 0 or duration_units > 15:
        raise ValueError(f"Duration units {duration_units} out of range (0 to 15).")
    if direction not in (0, 1):
        raise ValueError(f"Invalid direction {direction}. Must be 0 (Open) or 1 (Close).")

    bits: List[int] = []
    # Bits 0..1: 10 for motor 1, 11 for motor 2
    if motor_id == 1:
        bits.extend([1, 0])
    else:
        bits.extend([1, 1])

    # Bits 2..5: 4-bit duration multiplier
    bits.extend(int_to_bits(duration_units, 4))

    # Bit 6: direction
    bits.append(direction & 1)

    if len(bits) != 7:
        raise RuntimeError(f"Internal error: ESP 4 command length is {len(bits)} bits, expected 7.")
    return bits


def decode_command_packet(bits: List[int]) -> Dict[str, Any]:
    """
    Decode and validate a command bit array (useful for debugging, testing, and simulation).
    Valid lengths are 5, 6, 7, 20, or 21 bits.
    """
    length = len(bits)
    if length not in (5, 6, 7, 20, 21):
        raise ValueError(f"Invalid packet length {length} bits. Valid lengths are 5, 6, 7, 20, or 21 bits.")

    if length == 7:
        m_code = (bits[0] << 1) | bits[1]
        motor_id = 1 if m_code == 2 else (2 if m_code == 3 else None)
        dur_units = (bits[2] << 3) | (bits[3] << 2) | (bits[4] << 1) | bits[5]
        direction = bits[6]
        return {
            "type": "ESP4_TIMED_MOVE",
            "length": 7,
            "esp_id": 4,
            "motor_id": motor_id,
            "duration_units": dur_units,
            "duration_ms": dur_units * 200,
            "direction": direction,
            "raw_bits": bits
        }

    if length == 5:
        if bits == [1, 0, 1, 1, 1]:
            return {
                "type": "GO_AHEAD",
                "length": 5,
                "esp_id": 0,
                "motor_id": None,
                "direction": None,
                "raw_bits": bits
            }
        elif bits == [1, 0, 1, 0, 0]:
            return {
                "type": "CLEAR_QUEUE",
                "length": 5,
                "esp_id": 0,
                "motor_id": None,
                "direction": None,
                "raw_bits": bits
            }
        else:
            raise ValueError(f"Unknown 5-bit command: {bits}")

    esp_id = bits_to_int(bits[0:3])

    if length == 6:
        # Check special clear-lock command (000000)
        if bits == [0, 0, 0, 0, 0, 0]:
            return {
                "type": "CLEAR_LOCK",
                "length": 6,
                "esp_id": BROADCAST_CLEAR_LOCK_ID,
                "motor_id": None,
                "direction": None,
                "raw_bits": bits
            }

        motor_id = bits_to_int(bits[3:5])
        direction = bits[5]
        return {
            "type": "LOCK_ON",
            "length": 6,
            "esp_id": esp_id,
            "motor_id": motor_id,
            "direction": direction,
            "raw_bits": bits
        }

    if length == 21:
        motor_id = bits_to_int(bits[3:5])
        pulse_count = bits_to_int(bits[5:20])
        direction = bits[20]
        return {
            "type": "NORMAL",
            "length": 21,
            "esp_id": esp_id,
            "motor_id": motor_id,
            "pulse_count": pulse_count,
            "direction": direction,
            "raw_bits": bits
        }

    # Length == 20: Legacy Normal Command
    motor_id = bits_to_int(bits[3:5])
    pulse_count = bits_to_int(bits[5:19])
    direction = bits[19]

    return {
        "type": "NORMAL",
        "length": 20,
        "esp_id": esp_id,
        "motor_id": motor_id,
        "pulse_count": pulse_count,
        "direction": direction,
        "raw_bits": bits
    }


def decode_feedback_packet(esp_id: int, bits: List[int]) -> Tuple[int, int, int, int, bool]:
    """
    Decode a 17-bit feedback packet from an ESP32.

    Bit structure (17 bits total):
    - Bits 0..1  (2 bits): Motor ID (00 to 11)
    - Bits 2..15 (14 bits): Net pulse magnitude (0 to 16383, NOT two's complement)
    - Bit  16    (1 bit): Sign (1 = positive >= 0, 0 = negative < 0)

    Returns:
        (motor_id, magnitude, sign_bit, net_pulses, is_overflow)
    """
    if len(bits) != 17:
        raise ValueError(f"Invalid feedback packet length: {len(bits)} bits. Expected exactly 17 bits.")

    motor_id = bits_to_int(bits[0:2])
    magnitude = bits_to_int(bits[2:16])
    sign_bit = bits[16] & 1

    # Explicit overflow check
    is_overflow = (magnitude >= MAX_FEEDBACK_MAGNITUDE)

    # Calculate signed net pulses
    # Sign: 1 = positive, 0 = negative
    if sign_bit == 1:
        net_pulses = magnitude
    else:
        net_pulses = -magnitude

    return motor_id, magnitude, sign_bit, net_pulses, is_overflow


def encode_feedback_packet(motor_id: int, net_pulses: int) -> List[int]:
    """
    Encode signed net pulses into a 17-bit feedback packet.
    Used by the simulation engine and verification tests.
    """
    if motor_id not in VALID_MOTOR_IDS:
        raise ValueError(f"Invalid Motor ID {motor_id}. Must be 0-3.")

    magnitude = abs(net_pulses)
    if magnitude > MAX_FEEDBACK_MAGNITUDE:
        magnitude = MAX_FEEDBACK_MAGNITUDE  # Clamp and flag overflow

    sign_bit = 1 if net_pulses >= 0 else 0

    bits: List[int] = []
    bits.extend(int_to_bits(motor_id, 2))     # Bits 0..1
    bits.extend(int_to_bits(magnitude, 14))   # Bits 2..15
    bits.append(sign_bit)                     # Bit 16

    if len(bits) != 17:
        raise RuntimeError(f"Feedback packet encoding produced {len(bits)} bits, expected 17.")
    return bits
