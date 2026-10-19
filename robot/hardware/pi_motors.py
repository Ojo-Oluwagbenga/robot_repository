"""
Direct Raspberry Pi 5 Motor Controller.
=============================================================================
Hardware Configuration:
  1. shoulder_right : Stepper Motor
     - DIR Pin  : BCM GPIO 6
     - STEP Pin : BCM GPIO 5

  2. shoulder_left  : Stepper Motor
     - DIR Pin  : BCM GPIO 9
     - STEP Pin : BCM GPIO 10

  3. turn_head_sideways : Encoded DC Motor (Left / Right Head Pan)
     - IN1 Pin  : BCM GPIO 20
     - IN2 Pin  : BCM GPIO 16
     - Timing   : 1 degree = 100 ms of run time (Timer Mode)
=============================================================================
"""

import os
import sys
import time
import glob
from typing import Optional

# Add system dist-packages path for Raspberry Pi OS virtual environments
for sp in ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages"):
    if sp not in sys.path and os.path.exists(sp):
        sys.path.append(sp)

try:
    from config import (
        PIN_SHOULDER_RIGHT_DIR,
        PIN_SHOULDER_RIGHT_STEP,
        PIN_SHOULDER_LEFT_DIR,
        PIN_SHOULDER_LEFT_STEP,
        PIN_HEAD_PAN_IN1,
        PIN_HEAD_PAN_IN2,
        HEAD_PAN_MS_PER_DEG,
    )
except ImportError:
    from robot.config import (
        PIN_SHOULDER_RIGHT_DIR,
        PIN_SHOULDER_RIGHT_STEP,
        PIN_SHOULDER_LEFT_DIR,
        PIN_SHOULDER_LEFT_STEP,
        PIN_HEAD_PAN_IN1,
        PIN_HEAD_PAN_IN2,
        HEAD_PAN_MS_PER_DEG,
    )

# Try native Pi 5 lgpio
LGPIO_AVAILABLE = False
lgpio = None
try:
    import lgpio as _lgpio
    lgpio = _lgpio
    LGPIO_AVAILABLE = True
except ImportError:
    LGPIO_AVAILABLE = False


class StepperMotor:
    """Controls a stepper motor via Direction and Step pulse pins."""

    def __init__(self, dir_pin: int, step_pin: int, name: str, chip_handle: Optional[int] = None):
        self.dir_pin = dir_pin
        self.step_pin = step_pin
        self.name = name
        self.chip = chip_handle
        self.is_hardware = False

        if self.chip is not None and LGPIO_AVAILABLE:
            try:
                lgpio.gpio_claim_output(self.chip, self.dir_pin, 0)
                lgpio.gpio_claim_output(self.chip, self.step_pin, 0)
                self.is_hardware = True
            except Exception as e:
                print(f"[Stepper: {self.name}] Hardware claim failed ({e}), using simulation.")

    def step(self, steps: int, direction: int = 0, start_delay_s: float = 0.0050, cruise_delay_s: float = 0.0018) -> bool:
        """
        Step the motor with a trapezoidal acceleration/deceleration profile:
        - Starts at max torque (5.0ms / 200 Hz) to overcome inertia & gravity
        - Smoothly accelerates to cruising speed (1.8ms / ~555 Hz)
        - Smoothly decelerates back to max torque before stopping
        """
        dir_name = "CW (0)" if direction == 0 else "CCW (1)"
        print(f"[{self.name}] Stepping {steps} steps {dir_name} (accel: {start_delay_s*1000:.1f}ms -> {cruise_delay_s*1000:.1f}ms)...")

        # Calculate ramp length (up to 30% of total steps, capped at 35 steps)
        if steps <= 4:
            ramp_len = 1
        elif steps <= 20:
            ramp_len = max(steps // 2, 1)
        else:
            ramp_len = min(int(steps * 0.30), 35)

        if self.is_hardware and self.chip is not None:
            try:
                lgpio.gpio_write(self.chip, self.dir_pin, 1 if direction else 0)
                time.sleep(0.002)

                for i in range(steps):
                    dist_from_end = min(i, (steps - 1) - i)
                    if dist_from_end < ramp_len:
                        fraction = dist_from_end / float(ramp_len)
                        smooth_frac = fraction * fraction * (3.0 - 2.0 * fraction)
                        delay = start_delay_s - smooth_frac * (start_delay_s - cruise_delay_s)
                    else:
                        delay = cruise_delay_s

                    half_delay = max(delay / 2.0, 0.0001)
                    lgpio.gpio_write(self.chip, self.step_pin, 1)
                    time.sleep(half_delay)
                    lgpio.gpio_write(self.chip, self.step_pin, 0)
                    time.sleep(half_delay)
                return True
            except Exception as e:
                print(f"[{self.name}] Stepper error: {e}")
                return False
        else:
            # Simulated stepping
            total_duration = min(steps * ((start_delay_s + cruise_delay_s) / 2.0), 1.0)
            time.sleep(total_duration)
            print(f"[{self.name}] (Simulated) Completed {steps} steps {dir_name}.")
            return True


class TimedHeadMotor:
    """
    Controls head turn sideways via H-bridge IN1/IN2.
    Timer mode: 1 degree = 100 ms of run time.
    """

    def __init__(self, in1_pin: int, in2_pin: int, ms_per_deg: int = HEAD_PAN_MS_PER_DEG, chip_handle: Optional[int] = None):
        self.in1_pin = in1_pin
        self.in2_pin = in2_pin
        self.ms_per_deg = ms_per_deg
        self.chip = chip_handle
        self.is_hardware = False

        if self.chip is not None and LGPIO_AVAILABLE:
            try:
                lgpio.gpio_claim_output(self.chip, self.in1_pin, 0)
                lgpio.gpio_claim_output(self.chip, self.in2_pin, 0)
                self.is_hardware = True
            except Exception as e:
                print(f"[HeadMotor] Hardware claim failed ({e}), using simulation.")

    def brake(self) -> None:
        """Stop motor immediately."""
        if self.is_hardware and self.chip is not None:
            try:
                lgpio.gpio_write(self.chip, self.in1_pin, 0)
                lgpio.gpio_write(self.chip, self.in2_pin, 0)
            except Exception:
                pass

    def run_time(self, duration_ms: float, direction: str = "left") -> bool:
        """
        Run motor for specified milliseconds in direction ('left' or 'right').
        """
        duration_s = max(duration_ms / 1000.0, 0.01)
        dir_clean = direction.lower().strip()
        print(f"[HeadMotor] Turning {dir_clean.upper()} for {duration_ms:.0f} ms...")

        if self.is_hardware and self.chip is not None:
            try:
                if dir_clean in ["left", "ccw"]:
                    lgpio.gpio_write(self.chip, self.in1_pin, 1)
                    lgpio.gpio_write(self.chip, self.in2_pin, 0)
                else:
                    lgpio.gpio_write(self.chip, self.in1_pin, 0)
                    lgpio.gpio_write(self.chip, self.in2_pin, 1)

                time.sleep(duration_s)
                self.brake()
                print(f"[HeadMotor] Completed {dir_clean.upper()} turn.")
                return True
            except Exception as e:
                self.brake()
                print(f"[HeadMotor] Run error: {e}")
                return False
        else:
            time.sleep(min(duration_s, 0.5))
            print(f"[HeadMotor] (Simulated) Completed {dir_clean.upper()} turn for {duration_ms:.0f} ms.")
            return True

    def turn_degrees(self, degrees: float, direction: str = "left") -> bool:
        """
        Turn head by specified degrees using the 100ms/degree timer.
        """
        abs_deg = abs(degrees)
        duration_ms = abs_deg * self.ms_per_deg
        print(f"[HeadMotor] {abs_deg:.1f}° {direction.upper()} = {duration_ms:.0f} ms run time (100ms/deg)")
        return self.run_time(duration_ms=duration_ms, direction=direction)


class PiMotorSystem:
    """Manages all direct Raspberry Pi 5 motors."""

    _instance = None

    def __init__(self):
        self.chip = None
        if LGPIO_AVAILABLE:
            for chip_num in [0, 4]:
                try:
                    self.chip = lgpio.gpiochip_open(chip_num)
                    break
                except Exception:
                    pass

        # 1. Right Shoulder: Stepper on GPIO 6 (DIR) and GPIO 5 (STEP)
        self.shoulder_right = StepperMotor(
            dir_pin=PIN_SHOULDER_RIGHT_DIR,
            step_pin=PIN_SHOULDER_RIGHT_STEP,
            name="Shoulder Right",
            chip_handle=self.chip,
        )

        # 2. Left Shoulder: Stepper on GPIO 9 (DIR) and GPIO 10 (STEP)
        self.shoulder_left = StepperMotor(
            dir_pin=PIN_SHOULDER_LEFT_DIR,
            step_pin=PIN_SHOULDER_LEFT_STEP,
            name="Shoulder Left",
            chip_handle=self.chip,
        )

        # 3. Turn Head Sideways: Encoded DC Motor on GPIO 20 (IN1) and GPIO 16 (IN2)
        self.head_turn = TimedHeadMotor(
            in1_pin=PIN_HEAD_PAN_IN1,
            in2_pin=PIN_HEAD_PAN_IN2,
            ms_per_deg=HEAD_PAN_MS_PER_DEG,
            chip_handle=self.chip,
        )

    def close(self):
        """Brake motors and close GPIO chip handle."""
        self.head_turn.brake()
        if self.chip is not None and LGPIO_AVAILABLE:
            try:
                lgpio.gpiochip_close(self.chip)
            except Exception:
                pass
            self.chip = None


# Global singleton access
_PI_MOTORS: Optional[PiMotorSystem] = None

def get_pi_motors() -> PiMotorSystem:
    global _PI_MOTORS
    if _PI_MOTORS is None:
        _PI_MOTORS = PiMotorSystem()
    return _PI_MOTORS
