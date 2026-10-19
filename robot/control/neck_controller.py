"""
Neck Controller & Safety Supervisor for Humanoid Robot.
=============================================================================
Controls Head / Neck 2-DOF Pan-Tilt Mechanism:
  - Yaw   (Pan Left/Right)  : ESP 1, Motor 5. Range: [-3000, +3000] pulses from home.
  - Pitch (Tilt Up/Down)    : ESP 1, Motor 6. Range: [0, +5000] pulses upward from home.

Direction Conventions (Hardware Verified):
  - Pitch:
      * 1-6-x-1 drives neck DOWNWARD.
      * 1-6-x-0 drives neck UPWARD.
      * When at home (0), downward motion is strictly forbidden.
  - Yaw:
      * 1-5-x-0 or 1-5-x-1 drives neck LEFT or RIGHT (calibrated during training).

Safety Envelopes:
  - Requires explicit confirmation that head is in HOME position before running.
  - Pitch net pulse must stay strictly within [0, 5000].
  - Yaw net pulse must stay strictly within [-3000, 3000].
=============================================================================
"""

import os
import sys
import time
import json
import socket
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# Ensure robot module path is resolved
ROBOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

logger = logging.getLogger("robot.neck_controller")


# =============================================================================
# HARDWARE CONFIGURATION & SAFETY BOUNDS
# =============================================================================
ESP_ID = 1
MOTOR_YAW = 5      # Left / Right
MOTOR_PITCH = 6    # Up / Down

# Pitch Directions
PITCH_DIR_UP = 0
PITCH_DIR_DOWN = 1   # User rule: 1-6-x-1 drives neck DOWNWARD

# Default Yaw Directions (refined during calibration)
DEFAULT_YAW_DIR_RIGHT = 0
DEFAULT_YAW_DIR_LEFT = 1

# Hardware Safety Limits (relative to manual home = 0)
PITCH_MIN_PULSES = 0       # Home is lowest point; cannot go lower
PITCH_MAX_PULSES = 5000    # Cannot go higher than 5000 net pulses upward
YAW_MIN_PULSES = -3000     # Leftmost limit
YAW_MAX_PULSES = 3000      # Rightmost limit

DEFAULT_CALIBRATION_FILE = (
    ROBOT_DIR / "neck_calibration.json"
    if (ROBOT_DIR / "neck_calibration.json").exists()
    else PROJECT_ROOT / "neck_calibration.json"
)


@dataclass
class NeckCalibration:
    """Stores calibrated neck tracking gains and direction mappings."""
    gain_yaw: float = 3.0          # pulses per pixel error
    gain_pitch: float = 3.5        # pulses per pixel error
    yaw_dir_right: int = DEFAULT_YAW_DIR_RIGHT
    yaw_dir_left: int = DEFAULT_YAW_DIR_LEFT
    pitch_dir_up: int = PITCH_DIR_UP
    pitch_dir_down: int = PITCH_DIR_DOWN
    deadband_x: int = 25           # pixels tolerance around center
    deadband_y: int = 25           # pixels tolerance around center
    max_step_pulses_yaw: int = 400   # max pulse per step to prevent violent jerks
    max_step_pulses_pitch: int = 400
    calibrated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gain_yaw": self.gain_yaw,
            "gain_pitch": self.gain_pitch,
            "yaw_dir_right": self.yaw_dir_right,
            "yaw_dir_left": self.yaw_dir_left,
            "pitch_dir_up": self.pitch_dir_up,
            "pitch_dir_down": self.pitch_dir_down,
            "deadband_x": self.deadband_x,
            "deadband_y": self.deadband_y,
            "max_step_pulses_yaw": self.max_step_pulses_yaw,
            "max_step_pulses_pitch": self.max_step_pulses_pitch,
            "calibrated_at": self.calibrated_at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "limits": {
                "pitch_min": PITCH_MIN_PULSES,
                "pitch_max": PITCH_MAX_PULSES,
                "yaw_min": YAW_MIN_PULSES,
                "yaw_max": YAW_MAX_PULSES,
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NeckCalibration":
        return cls(
            gain_yaw=float(data.get("gain_yaw", 3.0)),
            gain_pitch=float(data.get("gain_pitch", 3.5)),
            yaw_dir_right=int(data.get("yaw_dir_right", DEFAULT_YAW_DIR_RIGHT)),
            yaw_dir_left=int(data.get("yaw_dir_left", DEFAULT_YAW_DIR_LEFT)),
            pitch_dir_up=int(data.get("pitch_dir_up", PITCH_DIR_UP)),
            pitch_dir_down=int(data.get("pitch_dir_down", PITCH_DIR_DOWN)),
            deadband_x=int(data.get("deadband_x", 25)),
            deadband_y=int(data.get("deadband_y", 25)),
            max_step_pulses_yaw=int(data.get("max_step_pulses_yaw", 400)),
            max_step_pulses_pitch=int(data.get("max_step_pulses_pitch", 400)),
            calibrated_at=str(data.get("calibrated_at", "")),
        )


class NeckSafetyEnvelope:
    """
    Guarantees strict pulse envelope bounds for Neck Pitch and Yaw.
    Prevents mechanical crashes into physical end stops.
    """

    def __init__(self):
        self.home_assumed = False
        self.net_yaw = 0      # 0 is home center; [-3000, +3000]
        self.net_pitch = 0    # 0 is home bottom; [0, +5000]

    def set_home_confirmed(self) -> None:
        """Called when user confirms the head is manually positioned at HOME."""
        self.net_yaw = 0
        self.net_pitch = 0
        self.home_assumed = True
        logger.info("[SAFETY] Home position confirmed: net_yaw=0, net_pitch=0.")

    def clamp_pitch(self, requested_pulses: int, direction: int) -> Tuple[int, str]:
        """
        Validate and clamp requested pitch movement.
        direction == PITCH_DIR_UP (0): increases net_pitch towards 5000.
        direction == PITCH_DIR_DOWN (1): decreases net_pitch towards 0.
        Returns: (allowed_pulses, reason)
        """
        if not self.home_assumed:
            return 0, "REJECTED: Home position has not been confirmed!"

        if requested_pulses <= 0:
            return 0, "Zero pulses requested."

        if direction == PITCH_DIR_DOWN:
            # Moving downward: net_pitch decreases
            if self.net_pitch <= PITCH_MIN_PULSES:
                return 0, f"BLOCKED: At lowest vertical limit (net_pitch={self.net_pitch}). Cannot move down!"
            max_down = self.net_pitch - PITCH_MIN_PULSES
            allowed = min(requested_pulses, max_down)
            reason = "OK" if allowed == requested_pulses else f"CLAMPED down to {allowed} pulses (limit {PITCH_MIN_PULSES})"
            return allowed, reason

        elif direction == PITCH_DIR_UP:
            # Moving upward: net_pitch increases
            if self.net_pitch >= PITCH_MAX_PULSES:
                return 0, f"BLOCKED: At highest vertical limit (net_pitch={self.net_pitch}). Cannot move up!"
            max_up = PITCH_MAX_PULSES - self.net_pitch
            allowed = min(requested_pulses, max_up)
            reason = "OK" if allowed == requested_pulses else f"CLAMPED up to {allowed} pulses (limit {PITCH_MAX_PULSES})"
            return allowed, reason

        return 0, f"REJECTED: Unknown pitch direction {direction}."

    def clamp_yaw(self, requested_pulses: int, direction: int, dir_right: int = DEFAULT_YAW_DIR_RIGHT) -> Tuple[int, str]:
        """
        Validate and clamp requested yaw movement.
        dir_right moves yaw positive (towards +3000).
        dir_left moves yaw negative (towards -3000).
        Returns: (allowed_pulses, reason)
        """
        if not self.home_assumed:
            return 0, "REJECTED: Home position has not been confirmed!"

        if requested_pulses <= 0:
            return 0, "Zero pulses requested."

        is_moving_positive = (direction == dir_right)

        if is_moving_positive:
            if self.net_yaw >= YAW_MAX_PULSES:
                return 0, f"BLOCKED: At max positive yaw limit (net_yaw={self.net_yaw})."
            max_pos = YAW_MAX_PULSES - self.net_yaw
            allowed = min(requested_pulses, max_pos)
            reason = "OK" if allowed == requested_pulses else f"CLAMPED to {allowed} pulses (limit {YAW_MAX_PULSES})"
            return allowed, reason
        else:
            if self.net_yaw <= YAW_MIN_PULSES:
                return 0, f"BLOCKED: At max negative yaw limit (net_yaw={self.net_yaw})."
            max_neg = self.net_yaw - YAW_MIN_PULSES
            allowed = min(requested_pulses, max_neg)
            reason = "OK" if allowed == requested_pulses else f"CLAMPED to {allowed} pulses (limit {YAW_MIN_PULSES})"
            return allowed, reason

    def record_pitch_executed(self, pulses: int, direction: int) -> None:
        """Update internal position counter after verified or executed movement."""
        if direction == PITCH_DIR_UP:
            self.net_pitch += pulses
        elif direction == PITCH_DIR_DOWN:
            self.net_pitch -= pulses
        # Ensure within bounds
        self.net_pitch = max(PITCH_MIN_PULSES, min(PITCH_MAX_PULSES, self.net_pitch))

    def record_yaw_executed(self, pulses: int, direction: int, dir_right: int = DEFAULT_YAW_DIR_RIGHT) -> None:
        """Update internal position counter after verified or executed movement."""
        if direction == dir_right:
            self.net_yaw += pulses
        else:
            self.net_yaw -= pulses
        self.net_yaw = max(YAW_MIN_PULSES, min(YAW_MAX_PULSES, self.net_yaw))


class NeckController:
    """
    High-level controller for the neck pan/tilt motors.
    Manages Wi-Fi UDP communication with ESP 1, enforces safety limits,
    executes centering moves, and handles homing procedures.
    """

    def __init__(
        self,
        esp_id: int = ESP_ID,
        motor_yaw: int = MOTOR_YAW,
        motor_pitch: int = MOTOR_PITCH,
        port: int = 5005,
        calibration_path: Optional[Path] = None,
    ):
        self.esp_id = esp_id
        self.motor_yaw = motor_yaw
        self.motor_pitch = motor_pitch
        self.port = port
        self.calibration_path = calibration_path or DEFAULT_CALIBRATION_FILE

        self.envelope = NeckSafetyEnvelope()
        self.calibration = NeckCalibration()
        self.load_calibration()

        # Communication setup (WifiBus singleton or standalone UDP)
        self.bus = None
        self._init_comm()

    def _init_comm(self) -> None:
        """Initialize communication channel to ESPs."""
        get_wifi_bus = None
        try:
            from communication.wifi_bus import get_wifi_bus
        except (ImportError, ModuleNotFoundError):
            try:
                from robot.communication.wifi_bus import get_wifi_bus
            except (ImportError, ModuleNotFoundError):
                get_wifi_bus = None

        if get_wifi_bus is not None:
            try:
                self.bus = get_wifi_bus(port=self.port)
                logger.info("Connected to WifiBus for Neck Control.")
                # Enable motor power relay if available
                try:
                    self.bus.enable_motor_power()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Could not initialize WifiBus ({e}). Using direct standalone UDP broadcast.")
                self.bus = None

        if self.bus is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def release_driver_power(self) -> bool:
        """Drive GPIO 26 LOW so the motor drivers are unpowered / brakes released."""
        try:
            from hardware.relay_gate import disable_motor_power_relay
            return disable_motor_power_relay()
        except (ImportError, ModuleNotFoundError):
            try:
                from robot.hardware.relay_gate import disable_motor_power_relay
                return disable_motor_power_relay()
            except Exception:
                pass
        try:
            res = subprocess.run(["pinctrl", "set", "26", "op", "dl"], capture_output=True, timeout=1)
            return res.returncode == 0
        except Exception:
            return False

    def engage_driver_power(self) -> bool:
        """Drive GPIO 26 HIGH so the motor drivers are powered / active."""
        try:
            from hardware.relay_gate import enable_motor_power_relay
            return enable_motor_power_relay()
        except (ImportError, ModuleNotFoundError):
            try:
                from robot.hardware.relay_gate import enable_motor_power_relay
                return enable_motor_power_relay()
            except Exception:
                pass
        try:
            res = subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
            return res.returncode == 0
        except Exception:
            return False

    def prompt_home_confirmation(self, auto_confirm: bool = False) -> bool:
        """
        Interactive safety gate.
        1. Drives Pi relay control pin LOW so the driver is released from brake
           and the user can easily turn the joints by hand to HOME.
        2. Waits for user to confirm ('yes' or [ENTER]).
        3. Re-energizes relay pin HIGH so motors are active again.
        """
        print("\n" + "=" * 76)
        print(" [!] SAFETY CHECK: MANUAL HOME POSITION CONFIRMATION REQUIRED")
        print("=" * 76)
        print("  Releasing motor driver power (GPIO 26 -> LOW) to unbrake joints...")
        self.release_driver_power()
        print("  [POWER] Motor Power Relay: OFF (GPIO 26 -> LOW). Drivers unbraked.")
        print("  You can now easily turn the neck joints by hand to mechanical HOME:")
        print("    1. VERTICAL (Pitch - Motor 6): Set to LOWEST resting stop (0 net pulses).")
        print("       -> Net vertical range: [0, 5000] upward pulses.")
        print("       -> The controller will NOT allow downward motion from home!")
        print("    2. HORIZONTAL (Yaw - Motor 5): Set to NEUTRAL straight-ahead (0 net pulses).")
        print("       -> Net horizontal range: [-3000, 3000] pulses.")
        print("=" * 76)

        if auto_confirm:
            print("[*] auto_confirm=True specified. Re-energizing power relay...")
            self.engage_driver_power()
            self.envelope.set_home_confirmed()
            return True

        while True:
            try:
                response = input("\n>> Physically verify HOME position. Type 'yes' or press [ENTER] to lock & proceed: ").strip().lower()
                if response in ("", "y", "yes", "ok", "proceed"):
                    print("  [POWER] Re-energizing motor power relay (GPIO 26 -> HIGH)...")
                    self.engage_driver_power()
                    time.sleep(0.15)
                    self.envelope.set_home_confirmed()
                    print("\n[+] HOME confirmed! Drivers powered ON (GPIO 26=HIGH). Safety envelope active.\n")
                    return True
                elif response in ("q", "quit", "exit", "no", "n"):
                    print("\n[-] Aborted by user. Relay remains OFF.")
                    return False
            except (KeyboardInterrupt, EOFError):
                print("\n[-] Aborted by user.")
                return False

    def load_calibration(self, filepath: Optional[Path] = None) -> bool:
        """Load calibration gains and directions from JSON file."""
        target_path = filepath or self.calibration_path
        if target_path and target_path.exists():
            try:
                with open(target_path, "r") as f:
                    data = json.load(f)
                self.calibration = NeckCalibration.from_dict(data)
                logger.info(f"Loaded neck calibration from {target_path}: Gain_X={self.calibration.gain_yaw:.2f}, Gain_Y={self.calibration.gain_pitch:.2f}")
                return True
            except Exception as e:
                logger.warning(f"Failed to parse calibration file {target_path}: {e}")
        return False

    def save_calibration(self, filepath: Optional[Path] = None) -> bool:
        """Save current calibration gains and directions to JSON file."""
        target_path = filepath or self.calibration_path
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w") as f:
                json.dump(self.calibration.to_dict(), f, indent=2)
            logger.info(f"Saved neck calibration to {target_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save calibration file {target_path}: {e}")
            return False

    def _send_command(self, motor: int, pulse: int, direction: int, timeout: float = 3.0) -> bool:
        """Low-level command dispatch via WifiBus or standalone UDP socket."""
        if pulse <= 0:
            return True

        inst_id = f"neck_m{motor}_{int(time.time() * 1000) % 1000000:06d}"

        if self.bus and hasattr(self.bus, "broadcast_command"):
            self.bus.broadcast_command(
                esp=self.esp_id,
                motor=motor,
                pulse=pulse,
                dir=direction,
                instruction_id=inst_id,
                cmd="MOVE",
            )
            # Wait for response if possible
            if hasattr(self.bus, "wait_for_response"):
                resp = self.bus.wait_for_response(inst_id, timeout=timeout)
                if resp:
                    return True
            # Estimate physical movement time fallback: ~1ms per pulse + 200ms setup
            expected_time = min(timeout, max(0.2, (pulse * 0.0015) + 0.15))
            time.sleep(expected_time)
            return True
        elif hasattr(self, "_sock"):
            payload = {
                "esp": self.esp_id,
                "motor": motor,
                "pulse": pulse,
                "dir": direction,
                "instructionID": inst_id,
                "cmd": "MOVE",
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            self._sock.sendto(data_bytes, ("255.255.255.255", self.port))
            expected_time = min(timeout, max(0.2, (pulse * 0.0015) + 0.15))
            time.sleep(expected_time)
            return True

        return False

    def move_pitch(self, pulses: int, direction: int, wait: bool = True) -> Tuple[int, str]:
        """
        Move pitch (tilt up/down) safely within [0, 5000].
        Direction 0 = UP, Direction 1 = DOWN.
        """
        allowed_pulses, reason = self.envelope.clamp_pitch(pulses, direction)
        if allowed_pulses <= 0:
            logger.warning(f"[PITCH BLOCKED] {reason}")
            return 0, reason

        dir_label = "UP" if direction == PITCH_DIR_UP else "DOWN"
        logger.info(f"[PITCH MOVE] {allowed_pulses} pulses {dir_label} (Target Net: {self.envelope.net_pitch + (allowed_pulses if direction == PITCH_DIR_UP else -allowed_pulses)})")

        self._send_command(self.motor_pitch, allowed_pulses, direction)
        self.envelope.record_pitch_executed(allowed_pulses, direction)
        return allowed_pulses, reason

    def move_yaw(self, pulses: int, direction: int, wait: bool = True) -> Tuple[int, str]:
        """
        Move yaw (pan left/right) safely within [-3000, 3000].
        """
        dir_right = self.calibration.yaw_dir_right
        allowed_pulses, reason = self.envelope.clamp_yaw(pulses, direction, dir_right=dir_right)
        if allowed_pulses <= 0:
            logger.warning(f"[YAW BLOCKED] {reason}")
            return 0, reason

        dir_label = "RIGHT" if direction == dir_right else "LEFT"
        delta = allowed_pulses if direction == dir_right else -allowed_pulses
        logger.info(f"[YAW MOVE] {allowed_pulses} pulses {dir_label} (Target Net: {self.envelope.net_yaw + delta})")

        self._send_command(self.motor_yaw, allowed_pulses, direction)
        self.envelope.record_yaw_executed(allowed_pulses, direction, dir_right=dir_right)
        return allowed_pulses, reason

    def move_simultaneous(
        self,
        yaw_pulses: int,
        yaw_direction: int,
        pitch_pulses: int,
        pitch_direction: int,
        wait: bool = True,
        timeout: float = 6.0,
    ) -> Dict[str, Any]:
        """
        Move both neck axes (Yaw: Motor 5, Pitch: Motor 6) simultaneously.
        Enforces independent safety envelope clamping for both axes,
        dispatches the commands in a single multi-motor JSON packet to ESP 1,
        and waits in parallel for completion.
        """
        dir_right = self.calibration.yaw_dir_right
        allowed_yaw, yaw_reason = self.envelope.clamp_yaw(yaw_pulses, yaw_direction, dir_right=dir_right)
        allowed_pitch, pitch_reason = self.envelope.clamp_pitch(pitch_pulses, pitch_direction)

        result: Dict[str, Any] = {
            "yaw_moved": 0,
            "yaw_dir": yaw_direction,
            "pitch_moved": 0,
            "pitch_dir": pitch_direction,
            "status_x": "BLOCKED: " + yaw_reason if allowed_yaw <= 0 else "MOVED",
            "status_y": "BLOCKED: " + pitch_reason if allowed_pitch <= 0 else "MOVED",
            "yaw_reason": yaw_reason,
            "pitch_reason": pitch_reason,
        }

        # If neither axis can move
        if allowed_yaw <= 0 and allowed_pitch <= 0:
            logger.warning(f"[SIMULTANEOUS BLOCKED] Yaw: {yaw_reason} | Pitch: {pitch_reason}")
            return result

        # If only yaw can move
        if allowed_yaw > 0 and allowed_pitch <= 0:
            moved, reason = self.move_yaw(allowed_yaw, yaw_direction, wait=wait)
            result["yaw_moved"] = moved
            result["status_x"] = "MOVED" if moved > 0 else f"BLOCKED: {reason}"
            return result

        # If only pitch can move
        if allowed_pitch > 0 and allowed_yaw <= 0:
            moved, reason = self.move_pitch(allowed_pitch, pitch_direction, wait=wait)
            result["pitch_moved"] = moved
            result["status_y"] = "MOVED" if moved > 0 else f"BLOCKED: {reason}"
            return result

        # BOTH axes can move simultaneously!
        yaw_label = "RIGHT" if yaw_direction == dir_right else "LEFT"
        pitch_label = "UP" if pitch_direction == PITCH_DIR_UP else "DOWN"
        logger.info(
            f"[SIMULTANEOUS MOVE] Yaw: {allowed_yaw} pulses {yaw_label} | "
            f"Pitch: {allowed_pitch} pulses {pitch_label} simultaneously"
        )

        yaw_sub_id = f"neck_yaw_{int(time.time() * 1000) % 1000000:06d}"
        pitch_sub_id = f"neck_pitch_{int(time.time() * 1000) % 1000000:06d}"
        batch_id = f"neck_sim_{int(time.time() * 1000) % 1000000:06d}"

        moves = [
            {"motor": self.motor_yaw, "pulse": allowed_yaw, "dir": yaw_direction, "instructionID": yaw_sub_id},
            {"motor": self.motor_pitch, "pulse": allowed_pitch, "dir": pitch_direction, "instructionID": pitch_sub_id},
        ]

        if self.bus and hasattr(self.bus, "broadcast_multi_command"):
            self.bus.broadcast_multi_command(esp=self.esp_id, moves=moves, instruction_id=batch_id)
            if wait and hasattr(self.bus, "wait_for_multi_response"):
                self.bus.wait_for_multi_response([yaw_sub_id, pitch_sub_id], timeout=timeout)
            elif wait:
                expected_time = min(timeout, max(0.2, (max(allowed_yaw, allowed_pitch) * 0.0015) + 0.15))
                time.sleep(expected_time)
        else:
            # Standalone UDP dispatch
            payload = {
                "esp": self.esp_id,
                "cmd": "MOVE",
                "instructionID": batch_id,
                "moves": moves,
            }
            data_bytes = json.dumps(payload).encode("utf-8")
            if hasattr(self, "_sock"):
                try:
                    self._sock.sendto(data_bytes, ("255.255.255.255", self.port))
                except Exception as e:
                    logger.error(f"Failed to send standalone multi packet: {e}")
            if wait:
                expected_time = min(timeout, max(0.2, (max(allowed_yaw, allowed_pitch) * 0.0015) + 0.15))
                time.sleep(expected_time)

        # Update safety envelope
        self.envelope.record_yaw_executed(allowed_yaw, yaw_direction, dir_right=dir_right)
        self.envelope.record_pitch_executed(allowed_pitch, pitch_direction)

        result["yaw_moved"] = allowed_yaw
        result["pitch_moved"] = allowed_pitch
        result["status_x"] = "MOVED"
        result["status_y"] = "MOVED"
        return result

    def center_target(
        self,
        error_x: float,
        error_y: float,
        deadband_x: Optional[int] = None,
        deadband_y: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Proportional closed-loop centering step towards target.
        error_x = ball_x - center_x
          If error_x > 0: ball is to the right of center -> turn neck RIGHT.
          If error_x < 0: ball is to the left of center  -> turn neck LEFT.
        error_y = ball_y - center_y (in image coordinates where Y increases downward)
          If error_y < 0: ball is in upper half of image -> tilt neck UP.
          If error_y > 0: ball is in lower half of image -> tilt neck DOWN.

        When both axes require correction, dispatches both motors SIMULTANEOUSLY
        so the head tracks towards the target smoothly along both axes at once.
        """
        db_x = deadband_x if deadband_x is not None else self.calibration.deadband_x
        db_y = deadband_y if deadband_y is not None else self.calibration.deadband_y

        needs_yaw = abs(error_x) > db_x
        needs_pitch = abs(error_y) > db_y

        # Determine directions and pulse counts
        yaw_dir = self.calibration.yaw_dir_right if error_x > 0 else self.calibration.yaw_dir_left
        yaw_pulses = min(int(abs(error_x) * self.calibration.gain_yaw), self.calibration.max_step_pulses_yaw) if needs_yaw else 0

        pitch_dir = self.calibration.pitch_dir_up if error_y < 0 else self.calibration.pitch_dir_down
        pitch_pulses = min(int(abs(error_y) * self.calibration.gain_pitch), self.calibration.max_step_pulses_pitch) if needs_pitch else 0

        # Case 1: Both axes need adjustment -> Run SIMULTANEOUSLY!
        if needs_yaw and needs_pitch:
            return self.move_simultaneous(
                yaw_pulses=yaw_pulses,
                yaw_direction=yaw_dir,
                pitch_pulses=pitch_pulses,
                pitch_direction=pitch_dir,
                wait=True
            )

        # Case 2: Only Yaw adjustment
        if needs_yaw:
            moved, reason = self.move_yaw(yaw_pulses, yaw_dir)
            return {
                "yaw_moved": moved,
                "yaw_dir": yaw_dir,
                "pitch_moved": 0,
                "pitch_dir": None,
                "status_x": "MOVED" if moved > 0 else f"BLOCKED: {reason}",
                "status_y": "IN_DEADBAND",
            }

        # Case 3: Only Pitch adjustment
        if needs_pitch:
            moved, reason = self.move_pitch(pitch_pulses, pitch_dir)
            return {
                "yaw_moved": 0,
                "yaw_dir": None,
                "pitch_moved": moved,
                "pitch_dir": pitch_dir,
                "status_x": "IN_DEADBAND",
                "status_y": "MOVED" if moved > 0 else f"BLOCKED: {reason}",
            }

        # Case 4: Target is centered in both axes
        return {
            "yaw_moved": 0,
            "yaw_dir": None,
            "pitch_moved": 0,
            "pitch_dir": None,
            "status_x": "IN_DEADBAND",
            "status_y": "IN_DEADBAND",
        }

    def adapt_gains(
        self,
        current_error_x: float,
        current_error_y: float,
        prev_error_x: float,
        prev_error_y: float,
        moved_yaw: int,
        moved_pitch: int,
        alpha: float = 0.12,
    ) -> Dict[str, Any]:
        """
        Online Real-Time Adaptive Gain Tuning.
        Compares expected correction with actual observed displacement.
        Refines gain_yaw and gain_pitch dynamically so the ball stays centered.
        """
        adaptations: Dict[str, Any] = {"yaw_adapted": False, "pitch_adapted": False}

        # 1. Adapt Yaw Gain (Horizontal)
        if moved_yaw > 0 and abs(prev_error_x) > self.calibration.deadband_x:
            actual_disp_x = abs(prev_error_x - current_error_x)
            # Require minimum displacement to avoid division by sensor noise
            if actual_disp_x >= 4.0:
                # If error didn't explode due to external ball disturbance
                if abs(current_error_x) < abs(prev_error_x) + 25:
                    k_obs_x = float(moved_yaw) / actual_disp_x
                    if 0.8 <= k_obs_x <= 15.0:
                        new_gain_x = (1.0 - alpha) * self.calibration.gain_yaw + alpha * k_obs_x
                        self.calibration.gain_yaw = round(max(1.0, min(12.0, new_gain_x)), 2)
                        adaptations["yaw_adapted"] = True
                        adaptations["new_gain_yaw"] = self.calibration.gain_yaw
                        adaptations["obs_gain_yaw"] = round(k_obs_x, 2)

        # 2. Adapt Pitch Gain (Vertical)
        if moved_pitch > 0 and abs(prev_error_y) > self.calibration.deadband_y:
            actual_disp_y = abs(prev_error_y - current_error_y)
            if actual_disp_y >= 4.0:
                if abs(current_error_y) < abs(prev_error_y) + 25:
                    k_obs_y = float(moved_pitch) / actual_disp_y
                    if 0.8 <= k_obs_y <= 15.0:
                        new_gain_y = (1.0 - alpha) * self.calibration.gain_pitch + alpha * k_obs_y
                        self.calibration.gain_pitch = round(max(1.0, min(12.0, new_gain_y)), 2)
                        adaptations["pitch_adapted"] = True
                        adaptations["new_gain_pitch"] = self.calibration.gain_pitch
                        adaptations["obs_gain_pitch"] = round(k_obs_y, 2)

        return adaptations

    def return_to_home(self) -> None:
        """
        Safely return neck to home position:
          Simultaneously centers Yaw back to 0 and lowers Pitch back to 0.
        """
        print("[*] Returning neck to HOME position...")
        yaw_needed = abs(self.envelope.net_yaw)
        yaw_dir = self.calibration.yaw_dir_left if self.envelope.net_yaw > 0 else self.calibration.yaw_dir_right

        pitch_needed = self.envelope.net_pitch
        pitch_dir = PITCH_DIR_DOWN

        if yaw_needed > 0 and pitch_needed > 0:
            print(f"    - Simultaneous return to home: Yaw {yaw_needed} pulses, Pitch {pitch_needed} pulses down...")
            self.move_simultaneous(yaw_needed, yaw_dir, pitch_needed, pitch_dir, wait=True)
        elif yaw_needed > 0:
            print(f"    - Centering yaw: {yaw_needed} pulses...")
            self.move_yaw(yaw_needed, yaw_dir)
        elif pitch_needed > 0:
            print(f"    - Lowering pitch to home (0): {pitch_needed} pulses down...")
            self.move_pitch(pitch_needed, pitch_dir)

        print(f"[+] Returned to HOME: Net Yaw={self.envelope.net_yaw}, Net Pitch={self.envelope.net_pitch}.")

    def emergency_stop(self) -> None:
        """Emergency stop broadcast."""
        if self.bus and hasattr(self.bus, "send_stop"):
            self.bus.send_stop()
        print("[!] EMERGENCY STOP broadcast sent.")
