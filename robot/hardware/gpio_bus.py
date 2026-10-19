"""
Hardware Abstraction Layer for the custom 50 kHz synchronous bus on Raspberry Pi 5.
Supports native Raspberry Pi 5 GPIO (via lgpio / gpiod) with nanosecond-precision
busy-wait timing, and a complete simulation bus for development and CI testing.
"""

import time
import sys
import glob
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from config import (
    PIN_CLOCK,
    PIN_DATA,
    PIN_SET,
    PIN_POWER_ENABLE,
    FEEDBACK_PINS,
    DEFAULT_GPIOCHIP,
    CLOCK_HALF_PERIOD_NS,
    DATA_SETUP_TIME_NS
)
from debug.logger import get_logger


def delay_ns(nanoseconds: int) -> None:
    """
    Sub-microsecond busy-wait delay using the hardware performance counter.
    Linux time.sleep() cannot reliably achieve microsecond intervals because the OS scheduler
    quantum is typically 50 to 100+ microseconds. time.perf_counter_ns() provides direct access
    to the ARM Cortex-A76 high-resolution timer on Raspberry Pi 5.
    """
    start = time.perf_counter_ns()
    while (time.perf_counter_ns() - start) < nanoseconds:
        pass


class BaseBus(ABC):
    """Abstract interface for the synchronous bus hardware."""

    @abstractmethod
    def set_clock(self, level: int) -> None:
        """Drive the CLOCK line HIGH (1) or LOW (0)."""
        pass

    @abstractmethod
    def set_data(self, level: int) -> None:
        """Drive the DATA line HIGH (1) or LOW (0)."""
        pass

    @abstractmethod
    def set_set(self, level: int) -> None:
        """Drive the SET line HIGH (1) or LOW (0)."""
        pass

    @abstractmethod
    def read_feedback(self, esp_id: int) -> int:
        """Sample the dedicated feedback line for a specific ESP32 (0 or 1)."""
        pass

    @abstractmethod
    def read_all_feedback(self) -> Dict[int, int]:
        """Simultaneously sample all 5 dedicated feedback lines."""
        pass

    @abstractmethod
    def enable_motor_power(self) -> None:
        """Drive motor power enable line HIGH (3.3V) once all pins are ready."""
        pass

    @abstractmethod
    def disable_motor_power(self) -> None:
        """Drive motor power enable line LOW (0.0V) to deactivate motor power system."""
        pass

    @abstractmethod
    def is_motor_power_enabled(self) -> bool:
        """Return True if motor power enable line is currently active (HIGH)."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Safely release GPIO resources."""
        pass


class SimulationBus(BaseBus):
    """
    Virtual software-simulated bus.
    Allows complete testing and verification of packet generation, lock management,
    feedback parsing, and error handling without physical Raspberry Pi hardware.
    """

    def __init__(self):
        self.logger = get_logger()
        self.clock_state: int = 0
        self.data_state: int = 0
        self.set_state: int = 1  # Default idle is SET HIGH
        self.feedback_lines: Dict[int, int] = {esp: 0 for esp in FEEDBACK_PINS}
        self.motor_power_enabled: bool = False

        # Simulation hooks: test harnesses can inject feedback streams into ESPs
        self.simulated_feedback_queues: Dict[int, List[List[int]]] = {esp: [] for esp in FEEDBACK_PINS}
        self.current_feedback_shift_bits: Dict[int, List[int]] = {esp: [] for esp in FEEDBACK_PINS}
        self.current_feedback_shift_idx: Dict[int, int] = {esp: 0 for esp in FEEDBACK_PINS}

        self.logger.info("SimulationBus initialized: Virtual bus ready (No physical GPIO required).")

    def enable_motor_power(self) -> None:
        self.motor_power_enabled = True
        self.logger.info(f"SimulationBus: Motor power relay ENABLED (Physical Pin 37 / GPIO {PIN_POWER_ENABLE} -> HIGH).")

    def disable_motor_power(self) -> None:
        self.motor_power_enabled = False
        self.logger.info(f"SimulationBus: Motor power relay DISABLED (Physical Pin 37 / GPIO {PIN_POWER_ENABLE} -> LOW).")

    def is_motor_power_enabled(self) -> bool:
        return self.motor_power_enabled

    def queue_simulated_feedback(self, esp_id: int, bits: List[int]) -> None:
        """Inject a simulated 17-bit feedback packet for an ESP to transmit to the Pi."""
        if esp_id in self.simulated_feedback_queues:
            self.simulated_feedback_queues[esp_id].append(list(bits))
            # Assert feedback ready line
            self.feedback_lines[esp_id] = 1

    def set_clock(self, level: int) -> None:
        old_clock = self.clock_state
        self.clock_state = 1 if level else 0

        # Simulate ESP behavior on clock edges
        # ESP shifts next feedback bit on RISING edge (0 -> 1)
        if old_clock == 0 and self.clock_state == 1:
            for esp_id in FEEDBACK_PINS:
                # Start new packet if queue has pending and not currently shifting
                if not self.current_feedback_shift_bits[esp_id] and self.simulated_feedback_queues[esp_id]:
                    packet_bits = self.simulated_feedback_queues[esp_id].pop(0)
                    self.current_feedback_shift_bits[esp_id] = packet_bits
                    self.current_feedback_shift_idx[esp_id] = 0

                # Output current bit
                if self.current_feedback_shift_bits[esp_id]:
                    idx = self.current_feedback_shift_idx[esp_id]
                    bits = self.current_feedback_shift_bits[esp_id]
                    if idx < len(bits):
                        self.feedback_lines[esp_id] = bits[idx]
                        self.current_feedback_shift_idx[esp_id] += 1

        # ESP completes packet and loads next on FALLING edge of 17th bit (1 -> 0)
        elif old_clock == 1 and self.clock_state == 0:
            for esp_id in FEEDBACK_PINS:
                if self.current_feedback_shift_bits[esp_id] and self.current_feedback_shift_idx[esp_id] >= 17:
                    # 17 bits have been sampled by the Pi
                    self.current_feedback_shift_bits[esp_id] = []
                    self.current_feedback_shift_idx[esp_id] = 0
                    if self.simulated_feedback_queues[esp_id]:
                        # Next packet is waiting in queue! Keep FEEDBACK line HIGH
                        self.feedback_lines[esp_id] = 1
                    else:
                        # Queue empty! Release FEEDBACK line to LOW
                        self.feedback_lines[esp_id] = 0

    def set_data(self, level: int) -> None:
        self.data_state = 1 if level else 0

    def set_set(self, level: int) -> None:
        self.set_state = 1 if level else 0

    def read_feedback(self, esp_id: int) -> int:
        return self.feedback_lines.get(esp_id, 0)

    def read_all_feedback(self) -> Dict[int, int]:
        return dict(self.feedback_lines)

    def close(self) -> None:
        self.logger.info("SimulationBus closed.")


class GPIOBus(BaseBus):
    """
    Physical Raspberry Pi 5 GPIO driver using the official lgpio library
    (targeting the RP1 peripheral chip on Raspberry Pi OS Bookworm).
    """

    def __init__(self, chip_index: int = DEFAULT_GPIOCHIP):
        self.logger = get_logger()
        self.chip_index = chip_index
        self.handle: Optional[int] = None
        self._lgpio = None
        self.motor_power_enabled: bool = False
        self.allocated_feedback_pins: set = set()

        try:
            import lgpio
            self._lgpio = lgpio
        except ImportError as e:
            # If running inside a virtual environment (e.g. systemenv), packages installed via
            # 'sudo apt install python3-lgpio' live in system dist-packages.
            system_paths = ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages")
            loaded = False
            for sp in system_paths:
                if sp not in sys.path and os.path.exists(sp):
                    sys.path.append(sp)
                    try:
                        import lgpio
                        self._lgpio = lgpio
                        loaded = True
                        break
                    except ImportError:
                        pass
            if not loaded:
                raise RuntimeError(
                    f"lgpio library not found ({e}).\n"
                    "To fix on Raspberry Pi 5:\n"
                    "  Option 1 (Inside virtualenv): pip install rpi-lgpio\n"
                    "  Option 2 (Using system Python): deactivate && sudo apt-get install -y python3-lgpio && python3 main.py\n"
                    "  Option 3 (Recreate virtualenv): python3 -m venv --system-site-packages <env_name>"
                )

        # Dynamically discover all existing /dev/gpiochip* devices on this Raspberry Pi
        # On Raspberry Pi 5, the 40-pin header is on RP1 (gpiochip0 or gpiochip4).
        # gpiochip10-13 are internal BCM2712 hardware blocks NOT wired to header pins!
        rp1_chips = []
        other_chips = []
        for dev in sorted(glob.glob("/dev/gpiochip*")):
            try:
                num = int(dev.replace("/dev/gpiochip", ""))
                # Check label in sysfs if available
                label_path = f"/sys/bus/gpio/devices/gpiochip{num}/label"
                label = ""
                if os.path.exists(label_path):
                    with open(label_path, "r") as f:
                        label = f.read().strip().lower()

                if "rp1" in label or num in [0, 4]:
                    rp1_chips.append(num)
                elif num < 10:  # Exclude internal 10+ chips
                    other_chips.append(num)
            except Exception:
                pass

        # Prioritize DEFAULT_GPIOCHIP (0 on Pi 5), then other RP1 chips
        candidate_chips = []
        for pref in [self.chip_index, DEFAULT_GPIOCHIP, 0, 4]:
            if pref is not None and pref not in candidate_chips:
                candidate_chips.append(pref)
        for num in rp1_chips + other_chips:
            if num not in candidate_chips:
                candidate_chips.append(num)

        if not candidate_chips:
            candidate_chips = [0, 4]

        claimed = False
        last_err = None

        for candidate_chip in candidate_chips:
            try:
                cand_handle = self._lgpio.gpiochip_open(candidate_chip)
            except Exception as ex:
                last_err = f"gpiochip{candidate_chip} open failed: {ex}"
                continue

            try:
                # 1. Clear any prior reservations and claim control pins and motor power enable
                for out_pin in [PIN_CLOCK, PIN_DATA, PIN_SET, PIN_POWER_ENABLE]:
                    try:
                        self._lgpio.gpio_free(cand_handle, out_pin)
                    except Exception:
                        pass

                # Safe initial states:
                # SET = HIGH (idle/stopped), CLOCK = LOW, DATA = LOW
                # POWER_ENABLE = LOW (Physical Pin 37 held at 0V so motors never run while pins initialize)
                self._lgpio.gpio_claim_output(cand_handle, PIN_CLOCK, 0)
                self._lgpio.gpio_claim_output(cand_handle, PIN_DATA, 0)
                self._lgpio.gpio_claim_output(cand_handle, PIN_SET, 1)
                self._lgpio.gpio_claim_output(cand_handle, PIN_POWER_ENABLE, 0)

                # Settling delay while motors are safely de-energized
                time.sleep(0.05)

                # 2. Claim feedback input pins individually with automatic fallback for busy pins
                claimed_fb_count = 0
                self.allocated_feedback_pins.clear()
                for esp_id, pin in list(FEEDBACK_PINS.items()):
                    try:
                        self._lgpio.gpio_free(cand_handle, pin)
                    except Exception:
                        pass
                    try:
                        self._lgpio.gpio_claim_input(cand_handle, pin, self._lgpio.SET_PULL_DOWN)
                        self.allocated_feedback_pins.add(pin)
                        claimed_fb_count += 1
                    except Exception as e:
                        self.logger.warning(
                            f"[WARN] FEEDBACK pin {pin} (BCM {pin}) for ESP:{esp_id} could not be claimed: {e}. "
                            "Physical control lines (CLOCK, DATA, SET) are ACTIVE and unaffected."
                        )

                self.handle = cand_handle
                self.chip_index = candidate_chip
                claimed = True
                self.logger.info(
                    f"Connected to gpiochip{candidate_chip} (RP1 40-pin header). "
                    f"Physical control bus (CLK, DATA, SET) and {claimed_fb_count} feedback lines claimed successfully!"
                )

                # 3. All pins are ready and steady: activate motor power system (GPIO 26 / Pin 37 goes HIGH)
                self.enable_motor_power()
                time.sleep(0.05)  # Allow relay contacts to debounce and motor power rails to stabilize
                break
            except Exception as ex:
                last_err = f"gpiochip{candidate_chip} control bus claim failed: {ex}"
                try:
                    self._lgpio.gpiochip_close(cand_handle)
                except Exception:
                    pass

        if not claimed:
            raise RuntimeError(
                f"Could not claim physical bus on any gpiochip (tried {candidate_chips}): {last_err}.\n"
                "Run: 'sudo fuser -k /dev/gpiochip*' to release any stale GPIO locks."
            )

        self.logger.info(f"GPIOBus initialized successfully on Raspberry Pi (gpiochip{self.chip_index}).")
        self.logger.info(f"Bus Pins: CLK={PIN_CLOCK}, DATA={PIN_DATA}, SET={PIN_SET}, POWER_EN={PIN_POWER_ENABLE}, FB={FEEDBACK_PINS}")

    def enable_motor_power(self) -> None:
        """Drive motor power enable line HIGH (3.3V) to activate motor power system."""
        if self.handle is not None:
            self._lgpio.gpio_write(self.handle, PIN_POWER_ENABLE, 1)
            self.motor_power_enabled = True
            self.logger.info(f"[POWER] Motor power system ACTIVATED: Physical Pin 37 (GPIO {PIN_POWER_ENABLE}) is HIGH (3.3V).")

    def disable_motor_power(self) -> None:
        """Drive motor power enable line LOW (0.0V) to deactivate motor power system."""
        if self.handle is not None:
            self._lgpio.gpio_write(self.handle, PIN_POWER_ENABLE, 0)
            self.motor_power_enabled = False
            self.logger.info(f"[POWER] Motor power system DEACTIVATED: Physical Pin 37 (GPIO {PIN_POWER_ENABLE}) is LOW (0.0V).")

    def is_motor_power_enabled(self) -> bool:
        return self.motor_power_enabled

    def set_clock(self, level: int) -> None:
        self._lgpio.gpio_write(self.handle, PIN_CLOCK, 1 if level else 0)

    def set_data(self, level: int) -> None:
        self._lgpio.gpio_write(self.handle, PIN_DATA, 1 if level else 0)

    def set_set(self, level: int) -> None:
        self._lgpio.gpio_write(self.handle, PIN_SET, 1 if level else 0)

    def read_feedback(self, esp_id: int) -> int:
        pin = FEEDBACK_PINS.get(esp_id)
        if pin is not None and pin in self.allocated_feedback_pins and self.handle is not None:
            try:
                return self._lgpio.gpio_read(self.handle, pin)
            except Exception:
                return 0
        return 0

    def read_all_feedback(self) -> Dict[int, int]:
        result = {}
        for esp, pin in FEEDBACK_PINS.items():
            if pin in self.allocated_feedback_pins and self.handle is not None:
                try:
                    result[esp] = self._lgpio.gpio_read(self.handle, pin)
                except Exception:
                    result[esp] = 0
            else:
                result[esp] = 0
        return result

    def close(self) -> None:
        if self.handle is not None:
            # Safely cut motor power first, float/drop outputs, and free all line allocations before closing
            try:
                self.disable_motor_power()
                self.set_set(1)
                self.set_clock(0)
                self.set_data(0)
                for pin in [PIN_CLOCK, PIN_DATA, PIN_SET, PIN_POWER_ENABLE]:
                    try:
                        self._lgpio.gpio_free(self.handle, pin)
                    except Exception:
                        pass
                for pin in list(self.allocated_feedback_pins):
                    try:
                        self._lgpio.gpio_free(self.handle, pin)
                    except Exception:
                        pass
                self.allocated_feedback_pins.clear()
                self._lgpio.gpiochip_close(self.handle)
            except Exception:
                pass
            self.handle = None
            self.logger.info("GPIOBus closed successfully.")


def create_bus(simulation: bool = False) -> BaseBus:
    """
    Bus factory function.
    Automatically detects Raspberry Pi 5 hardware or falls back cleanly to SimulationBus.
    """
    logger = get_logger()
    if simulation:
        logger.info("Creating requested SimulationBus.")
        return SimulationBus()

    # Check if running on Linux and if lgpio is available
    if os.name == "posix":
        try:
            return GPIOBus()
        except Exception as e:
            logger.warning(f"Failed to initialize physical GPIOBus ({e}). Falling back to SimulationBus.")
            return SimulationBus()
    else:
        logger.info("Non-Linux host detected (Windows/macOS). Using SimulationBus.")
        return SimulationBus()
