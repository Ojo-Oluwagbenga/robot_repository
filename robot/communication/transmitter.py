"""
50 kHz Synchronous Bus Transmitter for Raspberry Pi 5.
Generates deterministic 50 kHz clock waveforms with falling-edge data sampling on ESP32.
"""

import time
from typing import List, Optional
import threading

from config import (
    CLOCK_HALF_PERIOD_NS,
    DATA_SETUP_TIME_NS,
    INTER_PACKET_DELAY_US,
    DEBUG_LEVEL
)
from hardware.gpio_bus import BaseBus, delay_ns
from communication.protocol import (
    build_normal_command,
    build_go_ahead_command,
    build_clear_queue_command,
    build_lock_command,
    build_clear_lock_command,
    build_esp4_timed_command
)
from debug.logger import get_logger


class BusTransmitter:
    """
    Handles synchronous bitstream serialization and transmission to all ESP32 boards
    over the shared 3-wire bus (CLOCK, DATA, SET).
    """

    def __init__(self, bus: BaseBus):
        self.bus = bus
        self.logger = get_logger()
        self._lock = threading.Lock()

        # Initialize bus pins to safe idle state
        # SET = 1 (Framing idle), CLOCK = 0, DATA = 0
        self.bus.set_set(1)
        self.bus.set_clock(0)
        self.bus.set_data(0)

    def transmit_packet(self, bits: List[int], packet_type: str, esp_id: int) -> None:
        """
        Transmit an arbitrary bit packet over the synchronous bus at 50 kHz.
        Enforces exact falling-edge sampling for ESP32 slaves.

        GPIO Sequence:
        1. Acquire bus lock.
        2. Set CLOCK = LOW, DATA = bit[0].
        3. Transition SET from HIGH to LOW (Enables ESP serial reception).
        4. For each bit:
           a. Drive DATA to bit state.
           b. Drive CLOCK HIGH (Setup window).
           c. Wait 10 microseconds (High phase).
           d. Drive CLOCK LOW (ESP32 samples DATA on this falling edge).
           e. Wait 10 microseconds (Low phase).
        5. Ensure CLOCK = LOW.
        6. Transition SET from LOW to HIGH (Signals ESP packet completion and triggers latching).
        7. Delay inter-packet pause.
        8. Release bus lock.
        """
        with self._lock:
            # Diagnostics
            self.logger.log_tx_packet(esp_id=esp_id, packet_type=packet_type, bits=bits)

            # Step 1: Pre-condition bus
            self.bus.set_clock(0)
            if bits:
                self.bus.set_data(bits[0])

            # Step 2: Set SET = LOW to begin frame
            self.bus.set_set(0)
            delay_ns(DATA_SETUP_TIME_NS)

            # Step 3: Clock each bit at 50 kHz
            for bit_idx, bit_val in enumerate(bits):
                # Set DATA line for current bit
                self.bus.set_data(bit_val)

                # Drive CLOCK HIGH (Rising edge)
                self.bus.set_clock(1)
                delay_ns(CLOCK_HALF_PERIOD_NS)

                # Drive CLOCK LOW (Falling edge: ESP32 samples here!)
                self.bus.set_clock(0)
                delay_ns(CLOCK_HALF_PERIOD_NS)

                if self.logger.level >= 3:
                    self.logger.trace(f"TX Bit [{bit_idx:02d}]: {bit_val}")

            # Step 4: Stop clock and return SET to HIGH
            self.bus.set_clock(0)
            self.bus.set_data(0)
            delay_ns(DATA_SETUP_TIME_NS)
            self.bus.set_set(1)

            # Inter-packet settling delay
            time.sleep(INTER_PACKET_DELAY_US / 1_000_000.0)

    def send_normal_command(
        self,
        esp_id: int,
        motor_id: int,
        pulse_count: int,
        direction: int,
        instruction_id: Optional[str] = None
    ) -> List[int]:
        """
        Transmit a Normal Motor Command.
        Dispatches via Wi-Fi UDP JSON packet broadcast to all ESP32s:
        {"esp": esp_id, "motor": motor_id + 1, "pulse": pulse_count, "dir": direction, "instructionID": instruction_id}
        Also maintains backward-compatible bit generation.
        """
        # 1. Dispatch over Wi-Fi JSON Protocol
        try:
            from communication.wifi_bus import get_wifi_bus
            wifi_bus = get_wifi_bus()
            wifi_bus.broadcast_command(
                esp=esp_id,
                motor=motor_id + 1,  # 1-indexed (1 to 4) for ESP JSON protocol
                pulse=pulse_count,
                dir=direction,
                instruction_id=instruction_id
            )
        except Exception as e:
            self.logger.warning(f"WifiBus broadcast warning: {e}")

        # 2. Maintain bitstream logging and optional physical bus transmission
        bits = build_normal_command(esp_id, motor_id, pulse_count, direction)
        self.logger.log_command(
            esp_id=esp_id,
            cmd_type="NORMAL_COMMAND",
            motor_id=motor_id,
            pulses=pulse_count,
            direction=direction
        )
        try:
            self.transmit_packet(bits, packet_type="NORMAL_COMMAND_21BIT", esp_id=esp_id)
        except Exception:
            pass
        return bits

    def send_go_ahead(self) -> List[int]:
        """
        Broadcast the Go Ahead command.
        Dispatches via Wi-Fi JSON protocol and optional physical bus.
        """
        try:
            from communication.wifi_bus import get_wifi_bus
            get_wifi_bus().broadcast_command(esp=0, motor=0, pulse=0, dir=0, instruction_id="GO_AHEAD", cmd="GO_AHEAD")
        except Exception:
            pass

        bits = build_go_ahead_command()
        self.logger.log_command(
            esp_id=0,
            cmd_type="GO_AHEAD_EXECUTE",
            motor_id=None
        )
        try:
            self.transmit_packet(bits, packet_type="GO_AHEAD_5BIT", esp_id=0)
        except Exception:
            pass
        return bits

    def send_clear_queue(self) -> List[int]:
        """
        Broadcast the Clear Queue / Stop command.
        """
        try:
            from communication.wifi_bus import get_wifi_bus
            get_wifi_bus().send_stop()
        except Exception:
            pass

        bits = build_clear_queue_command()
        self.logger.log_command(
            esp_id=0,
            cmd_type="CLEAR_QUEUE",
            motor_id=None
        )
        try:
            self.transmit_packet(bits, packet_type="CLEAR_QUEUE_5BIT", esp_id=0)
        except Exception:
            pass
        return bits

    def send_lock_on(self, esp_id: int, motor_id: int, direction: int) -> List[int]:
        """
        Transmit a 6-bit Lock-On Command.

        Args:
            esp_id: Target ESP ID (1 to 5).
            motor_id: Target Motor ID (0 to 3).
            direction: 0 = CW, 1 = CCW.

        Returns:
            The transmitted 6-bit array.
        """
        bits = build_lock_command(esp_id, motor_id, direction)
        self.logger.log_command(
            esp_id=esp_id,
            cmd_type="LOCK_ON_COMMAND",
            motor_id=motor_id,
            direction=direction
        )
        self.transmit_packet(bits, packet_type="LOCK_ON_COMMAND_6BIT", esp_id=esp_id)
        return bits

    def clear_all_lock_on(self) -> List[int]:
        """
        Broadcast the 6-bit 000000 command to clear lock-on state across all 5 ESP32 boards.

        Returns:
            The transmitted 6-bit array [0, 0, 0, 0, 0, 0].
        """
        bits = build_clear_lock_command()
        self.logger.log_command(
            esp_id=0,
            cmd_type="CLEAR_ALL_LOCK_ON",
            motor_id=None
        )
        self.transmit_packet(bits, packet_type="CLEAR_LOCK_BROADCAST_6BIT", esp_id=0)
        return bits

    def send_esp4_command(self, motor_id: int, duration_units: int, direction: int) -> List[int]:
        """
        Transmit a 7-bit command specifically to ESP #4.

        Args:
            motor_id: 1 for Motor 1 (binary 10), 2 for Motor 2 (binary 11).
            duration_units: Integer 0-15 representing multiples of 200 ms (e.g. 3 = 600 ms).
            direction: 0 = Open, 1 = Close.

        Returns:
            The transmitted 7-bit array.
        """
        bits = build_esp4_timed_command(motor_id, duration_units, direction)
        duration_ms = duration_units * 200
        self.logger.log_command(
            esp_id=4,
            cmd_type="ESP4_7BIT_COMMAND",
            motor_id=motor_id,
            pulses=duration_ms,
            direction=direction
        )
        self.transmit_packet(bits, packet_type="ESP4_TIMED_MOVE_7BIT", esp_id=4)
        return bits

