"""
Feedback Receiver for Raspberry Pi 5.
Reads 17-bit synchronous feedback packets from dedicated ESP32 lines using the shared 50 kHz clock.
"""

import time
import threading
from typing import Dict, List, Optional, Any

from config import (
    CLOCK_HALF_PERIOD_NS,
    FEEDBACK_PINS,
    VALID_ESP_IDS
)
from hardware.gpio_bus import BaseBus, delay_ns
from communication.protocol import decode_feedback_packet
from communication.feedback import FeedbackPacket, FeedbackQueue
from debug.logger import get_logger


class FeedbackReceiver:
    """
    Manages reception of 17-bit feedback packets across all 5 dedicated ESP lines.
    ESP asserts FEEDBACK line HIGH when a packet is queued; Pi generates 17 clock cycles
    sampling on falling edge.
    """

    def __init__(self, bus: BaseBus, queue: Optional[FeedbackQueue] = None):
        self.bus = bus
        self.queue = queue or FeedbackQueue()
        self.logger = get_logger()
        self._lock = threading.Lock()
        self._running = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stuck_warned: Dict[int, bool] = {}

        # Wire up Wi-Fi JSON feedback listener
        try:
            from communication.wifi_bus import get_wifi_bus
            wifi_bus = get_wifi_bus()
            wifi_bus.on_feedback_callback = self._on_wifi_packet
        except Exception as e:
            self.logger.warning(f"Could not hook WifiBus callback: {e}")

    def _on_wifi_packet(self, packet: Dict[str, Any]) -> None:
        """Handle incoming Wi-Fi JSON telemetry packet from ESP32."""
        try:
            esp_id = int(packet.get("esp", 1))
            motor_num = int(packet.get("motor", 1))
            motor_id = motor_num - 1 if motor_num > 0 else 0
            net_pulses = int(packet.get("totalpulse", 0))
            pkt = FeedbackPacket(
                esp_id=esp_id,
                motor_id=motor_id,
                net_pulses=net_pulses,
                timestamp=time.time()
            )
            self.queue.push(pkt)
            self.logger.info(f"WIFI FEEDBACK <- ESP:{esp_id} M{motor_num}: net={net_pulses:+d} pulses (ID: {packet.get('instructionID')})")
        except Exception as e:
            self.logger.error(f"Failed to process Wi-Fi feedback packet: {e}")

    def read_feedback_bits(self, active_esps: List[int]) -> Dict[int, List[int]]:
        """
        Clock out 17 feedback bits from one or more active ESPs synchronously.

        Timing:
        - SET is held HIGH.
        - For each of 17 bits:
          1. Pi sets CLOCK HIGH (ESP updates FEEDBACK bit on rising edge).
          2. Wait 10 microseconds.
          3. Pi sets CLOCK LOW (Pi samples FEEDBACK on falling edge).
          4. Sample bit.
          5. Wait 10 microseconds.
        """
        bit_streams: Dict[int, List[int]] = {esp: [] for esp in active_esps}

        with self._lock:
            # Enforce SET is HIGH during feedback clocking
            self.bus.set_set(1)

            for bit_num in range(17):
                # 1. Rising edge: prompts ESP to present bit[bit_num]
                self.bus.set_clock(1)
                delay_ns(CLOCK_HALF_PERIOD_NS)

                # 2. Falling edge: Pi samples stable line
                self.bus.set_clock(0)
                delay_ns(CLOCK_HALF_PERIOD_NS)

                # Read active lines
                all_fb = self.bus.read_all_feedback()
                for esp in active_esps:
                    bit = all_fb.get(esp, 0)
                    bit_streams[esp].append(bit)

            # Return clock to idle LOW
            self.bus.set_clock(0)

        return bit_streams

    def poll_feedback(self) -> List[FeedbackPacket]:
        """
        Check dedicated feedback lines for any ready ESP (FEEDBACK line HIGH).
        If ready, clocks out 17 bits, decodes the packet, and pushes to the feedback queue.
        Drains all consecutive packets until all active ESP lines return LOW.

        Returns:
            List of successfully decoded FeedbackPackets.
        """
        received_packets: List[FeedbackPacket] = []
        max_drain_iterations = 16  # Up to 4 motors x 4 queued events per ESP
        stuck_esps = set()

        for _ in range(max_drain_iterations):
            # Quick non-blocking sample of all 5 lines
            fb_states = self.bus.read_all_feedback()
            active_esps = [esp for esp, state in fb_states.items() if state == 1 and esp not in stuck_esps]

            if not active_esps:
                break

            # Read 17 bits from all ready ESPs simultaneously
            raw_bits_by_esp = self.read_feedback_bits(active_esps)

            for esp_id, bits in raw_bits_by_esp.items():
                if len(bits) != 17:
                    self.logger.error(f"ESP {esp_id} feedback stream length error: got {len(bits)} bits, expected 17.")
                    continue

                # Check for stuck HIGH line condition (all 17 bits are 1)
                # Indicates a floating input, shorted wire, or hardware conflict
                if all(b == 1 for b in bits):
                    stuck_esps.add(esp_id)
                    if not self._stuck_warned.get(esp_id, False):
                        from config import FEEDBACK_PINS
                        pin_num = FEEDBACK_PINS.get(esp_id, "?")
                        self.logger.warning(
                            f"FEEDBACK line for ESP {esp_id} (BCM {pin_num}) is stuck HIGH (3.3V). "
                            "Check wire connection, verify pin assignment, and ensure common GND with ESP32. "
                            "Suppressing further stuck line flood."
                        )
                        self._stuck_warned[esp_id] = True
                    continue
                else:
                    self._stuck_warned[esp_id] = False

                try:
                    motor_id, magnitude, sign_bit, net_pulses, is_overflow = decode_feedback_packet(esp_id, bits)
                    packet = FeedbackPacket(
                        esp_id=esp_id,
                        motor_id=motor_id,
                        magnitude=magnitude,
                        sign=sign_bit,
                        net_pulses=net_pulses,
                        is_overflow=is_overflow,
                        raw_bits=bits
                    )
                    self.logger.log_rx_feedback(
                        esp_id=esp_id,
                        motor_id=motor_id,
                        raw_bits=bits,
                        magnitude=magnitude,
                        sign_bit=sign_bit,
                        net_pulses=net_pulses
                    )
                    self.queue.push(packet)
                    received_packets.append(packet)
                except Exception as e:
                    self.logger.error(f"Failed to decode feedback packet from ESP {esp_id}: {e}")

        return received_packets

    def read_feedback(self, esp_id: int, timeout: Optional[float] = None) -> Optional[FeedbackPacket]:
        """
        Public API: Read a feedback packet for a specific ESP ID.
        Checks internal queue first; if empty, polls the hardware bus.
        """
        if esp_id not in VALID_ESP_IDS:
            raise ValueError(f"Invalid ESP ID {esp_id}. Must be one of {VALID_ESP_IDS}.")

        # Check queue
        pkt = self.queue.pop_esp(esp_id, block=False)
        if pkt is not None:
            return pkt

        # Poll hardware bus
        self.poll_feedback()
        return self.queue.pop_esp(esp_id, block=False)

    def start_background_listener(self, interval_sec: float = 0.005) -> None:
        """Start a lightweight background thread to continuously poll for feedback packets."""
        if self._running:
            return
        self._running = True

        def _loop():
            last_err_time = 0.0
            while self._running:
                try:
                    self.poll_feedback()
                except Exception as e:
                    now = time.time()
                    if now - last_err_time > 2.0:
                        self.logger.error(f"Error in background feedback listener: {e}")
                        last_err_time = now
                time.sleep(interval_sec)

        self._poll_thread = threading.Thread(target=_loop, daemon=True, name="FeedbackPollThread")
        self._poll_thread.start()
        self.logger.info("Feedback background listener started.")

    def stop_background_listener(self) -> None:
        """Stop background feedback polling thread."""
        self._running = False
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1.0)
        self.logger.info("Feedback background listener stopped.")
