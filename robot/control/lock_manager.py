"""
Global Lock Manager for the humanoid robot.
Enforces the global single-lock invariant across all 5 ESP32 boards,
manages lock-on transitions, and controls real-time RUN/BRAKE via the shared DATA line.
"""

from typing import Optional, Tuple
import threading

from hardware.gpio_bus import BaseBus
from communication.transmitter import BusTransmitter
from debug.logger import get_logger


class LockManager:
    """
    Manages lock-on states and real-time continuous motor control.

    Key Invariants:
    1. Only ONE motor across all 5 ESP32 boards may be in lock-on mode.
    2. While in lock-on mode, SET is kept HIGH and DATA directly controls RUN/BRAKE:
       - DATA = HIGH: Motor accelerates toward configured lock-on speed.
       - DATA = LOW: Motor brakes/stops.
    3. Transitioning lock-on to a different motor automatically dispatches a command that causes
       the prior ESP to report its net pulses and release the lock.
    """

    def __init__(self, bus: BaseBus, transmitter: BusTransmitter):
        self.bus = bus
        self.transmitter = transmitter
        self.logger = get_logger()
        self._lock = threading.Lock()

        # Currently locked motor: (esp_id, motor_id, direction) or None
        self._locked_motor: Optional[Tuple[int, int, int]] = None
        self._is_running: bool = False

    @property
    def locked_motor(self) -> Optional[Tuple[int, int, int]]:
        """Return the currently locked motor tuple (esp_id, motor_id, direction) or None."""
        with self._lock:
            return self._locked_motor

    def is_locked(self, esp_id: int, motor_id: int) -> bool:
        """Check if a specific motor currently holds the global lock."""
        with self._lock:
            if self._locked_motor is None:
                return False
            return self._locked_motor[0] == esp_id and self._locked_motor[1] == motor_id

    def lock_motor(self, esp_id: int, motor_id: int, direction: int) -> None:
        """
        Request lock-on control for a specific motor.
        If another motor was previously locked, transmitting this new lock-on packet
        instructs the previous ESP to brake, calculate net pulses, and release its lock.
        """
        with self._lock:
            # First ensure DATA is LOW so motor begins in braked state
            self.bus.set_data(0)
            self._is_running = False

            # Transmit 6-bit lock-on command over synchronous bus
            self.transmitter.send_lock_on(esp_id, motor_id, direction)

            self._locked_motor = (esp_id, motor_id, direction)
            self.logger.info(f"Global Lock acquired by ESP:{esp_id} Motor:{motor_id + 1} (Dir:{direction})")

    def run(self) -> None:
        """
        Command the currently locked-on motor to run.
        Drives DATA line HIGH with SET remaining HIGH.
        """
        with self._lock:
            if self._locked_motor is None:
                self.logger.warning("Cannot run: No motor currently holds the global lock.")
                return

            # Keep SET HIGH, drive DATA HIGH
            self.bus.set_set(1)
            self.bus.set_data(1)
            self._is_running = True
            esp_id, motor_id, _ = self._locked_motor
            self.logger.info(f"Lock-on RUN active (DATA=HIGH) for ESP:{esp_id} M{motor_id + 1}")

    def brake(self) -> None:
        """
        Command the currently locked-on motor to brake/stop.
        Drives DATA line LOW with SET remaining HIGH.
        """
        with self._lock:
            if self._locked_motor is None:
                return

            # Keep SET HIGH, drive DATA LOW
            self.bus.set_set(1)
            self.bus.set_data(0)
            self._is_running = False
            esp_id, motor_id, _ = self._locked_motor
            self.logger.info(f"Lock-on BRAKE active (DATA=LOW) for ESP:{esp_id} M{motor_id + 1}")

    def set_run_state(self, running: bool) -> None:
        """Set lock-on RUN (True) or BRAKE (False)."""
        if running:
            self.run()
        else:
            self.brake()

    def clear_all_locks(self) -> None:
        """
        Broadcast the 000000 clear packet.
        Releases any active lock-on motor across all 5 ESP32 boards.
        """
        with self._lock:
            # First drop DATA to brake
            self.bus.set_data(0)
            self._is_running = False

            # Broadcast 000000 packet
            self.transmitter.clear_all_lock_on()
            self._locked_motor = None
            self.logger.info("Global Lock cleared across all ESP32 boards (000000 broadcast).")
