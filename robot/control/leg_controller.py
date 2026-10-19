"""
Leg and Base Drive Controller for ESP #4 (Humanoid Robot).
=============================================================================
Controls 4 encoded DC motors driving the robot's leg base:
- Motor 1 (B1_front): Left Front Steering  (0 = Right, 1 = Left)
- Motor 2 (B1_rear):  Left Rear Drive      (0 = Backward, 1 = Forward)
- Motor 3 (B2_front): Right Front Steering (0 = Right, 1 = Left)
- Motor 4 (B2_rear):  Right Rear Drive     (0 = Backward, 1 = Forward)

Hardware Calibration:
- Front Wheels (Steering): ~5500 pulses per 360 degrees (~15.2778 pulses/deg).
- Rear Wheels (Drive):     2000 pulses per 5 cm (400 pulses/cm).

Features:
- Simultaneous dual-motor rear wheel driving (e.g. forward 200 cm).
- Coordinated steering course setting + path drive + automatic re-centering.
- Automatic pulse chunking to respect ESP32 uint16_t limits (65535) and
  the 10-second safety watchdog.
- Relay gate power verification on Raspberry Pi (GPIO 26 -> HIGH).
- Emergency stop broadcast.
"""

import sys
import time
import math
import json
import socket
import threading
import re
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Ensure robot root is on path
CURRENT_FILE = Path(__file__).resolve()
ROBOT_DIR = CURRENT_FILE.parent.parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

# =============================================================================
# CONSTANTS & CALIBRATION
# =============================================================================
ESP_LEG_ID: int = 4
DEFAULT_UDP_PORT: int = 5005

# Motor Identifiers (1-based for ESP32 packet protocol)
MOTOR_FRONT_LEFT_STEER: int = 1   # M1: Left Front Steering
MOTOR_REAR_LEFT_DRIVE: int = 2    # M2: Left Rear Drive
MOTOR_FRONT_RIGHT_STEER: int = 3  # M3: Right Front Steering
MOTOR_REAR_RIGHT_DRIVE: int = 4   # M4: Right Rear Drive

# Direction Conventions based on hardware wiring
STEER_DIR_RIGHT: int = 0
STEER_DIR_LEFT: int = 1

DRIVE_DIR_BACKWARD: int = 0
DRIVE_DIR_FORWARD: int = 1

# Calibration Factors
# 5500 pulses = 360 degrees
STEERING_PULSES_PER_REV: float = 5500.0
STEERING_PULSES_PER_DEGREE: float = STEERING_PULSES_PER_REV / 360.0  # ~15.2778 pulses/deg

# 2000 pulses = 5 cm
DRIVE_PULSES_PER_CM: float = 2000.0 / 5.0  # 400.0 pulses/cm

# Safety Limits
# Chunk size: max pulses sent in a single command packet.
# 10,000 pulses = 25 cm, well below uint16_t (65535) and well within 10s watchdog.
MAX_CHUNK_PULSES: int = 10000

# Default maximum mechanical steering angle limit (degrees)
DEFAULT_MAX_STEER_ANGLE: float = 65.0


def parse_course_input(input_str: str) -> Optional[Tuple[float, float]]:
    """
    Parse course coordinate inputs from various user string formats:
      - "(200, 0)"
      - "((200, -300))"
      - "(200, -300)"
      - "200, -300"
      - "200 -300"
      - "[200, -300]"
    Returns:
      (forward_cm, lateral_cm) or None if invalid.
      lateral_cm: negative is LEFT, positive is RIGHT.
    """
    if not input_str:
        return None
    cleaned = input_str.strip()
    # Find all float or integer numbers (including optional negative signs)
    numbers = re.findall(r"[-+]?(?:\d*\.\d+|\d+)", cleaned)
    if len(numbers) >= 2:
        try:
            forward = float(numbers[0])
            lateral = float(numbers[1])
            return (forward, lateral)
        except ValueError:
            return None
    return None


class LegController:
    """
    High-level controller for the robot's 4-motor leg drive subsystem on ESP #4.
    """

    def __init__(
        self,
        port: int = DEFAULT_UDP_PORT,
        esp_ip: Optional[str] = None,
        broadcast_ip: str = "255.255.255.255",
        max_steer_angle: float = DEFAULT_MAX_STEER_ANGLE,
        use_hypotenuse: bool = True,
        simulate: bool = False,
    ):
        self.port = port
        self.esp_ip = esp_ip
        self.broadcast_ip = broadcast_ip
        self.max_steer_angle = max_steer_angle
        self.use_hypotenuse = use_hypotenuse
        self.simulate = simulate

        self._lock = threading.Lock()
        self._running = False
        self._listener_thread: Optional[threading.Thread] = None
        self._pending_responses: Dict[str, Dict[str, Any]] = {}
        self._response_events: Dict[str, threading.Event] = {}

        # Front steering wheel state tracker (offset from neutral in pulses)
        self.current_steer_offset_pulses: int = 0

        # Initialize network socket
        self.sock: Optional[socket.socket] = None
        if not self.simulate:
            self._init_socket()

        # Check / enable Raspberry Pi motor relay gate
        self._ensure_power_relay()

    def _init_socket(self) -> None:
        """Create and bind UDP socket for broadcasting commands and receiving feedback."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except Exception:
                    pass
            self.sock.bind(("0.0.0.0", self.port))
            self.sock.settimeout(0.2)

            self._running = True
            self._listener_thread = threading.Thread(
                target=self._socket_listener, daemon=True, name="LegCtrlUdpListener"
            )
            self._listener_thread.start()
        except Exception as e:
            print(f"[!] Warning: Could not bind UDP port {self.port}: {e}")
            self.sock = None

    def _ensure_power_relay(self) -> bool:
        """Energize the motor driver power gate on Raspberry Pi (GPIO 26 -> HIGH)."""
        if self.simulate:
            return True
        try:
            from hardware.relay_gate import enable_motor_power_relay
            return enable_motor_power_relay()
        except Exception:
            try:
                import subprocess
                res = subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
                return res.returncode == 0
            except Exception:
                return False

    def _socket_listener(self) -> None:
        """Background thread receiving feedback packets from ESP32."""
        while self._running and self.sock:
            try:
                data, (addr, _) = self.sock.recvfrom(4096)
                if not data:
                    continue
                packet = json.loads(data.decode("utf-8", errors="ignore"))
                # Filter out commands, accept only ESP feedbacks
                if not any(k in packet for k in ("status", "totalpulse", "total_pulse", "cmd")):
                    continue

                esp_id = packet.get("esp")
                if esp_id == ESP_LEG_ID and not self.esp_ip:
                    self.esp_ip = addr  # Auto-learn ESP4 IP address

                inst_id = str(packet.get("instructionID") or packet.get("instruction_id") or "")
                if inst_id:
                    with self._lock:
                        self._pending_responses[inst_id] = packet
                        if inst_id in self._response_events:
                            self._response_events[inst_id].set()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.02)

    def send_command(
        self,
        motor: int,
        pulse: int,
        direction: int,
        cmd: str = "MOVE",
        timeout: float = 12.0
    ) -> Tuple[str, threading.Event]:
        """
        Transmit a motor command to ESP4 and prepare response event.
        Returns: (instruction_id, event_handle)
        """
        inst_id = f"leg_m{motor}_{int(time.time() * 1000) % 1000000:06d}"
        payload = {
            "esp": ESP_LEG_ID,
            "motor": motor,
            "pulse": int(pulse),
            "dir": int(direction),
            "instructionID": inst_id,
            "cmd": cmd
        }

        if self.simulate:
            evt = threading.Event()
            evt.set()
            return inst_id, evt

        with self._lock:
            evt = threading.Event()
            self._response_events[inst_id] = evt

        raw = json.dumps(payload).encode("utf-8")
        if self.sock:
            # 1. Broadcast
            try:
                self.sock.sendto(raw, (self.broadcast_ip, self.port))
            except Exception:
                pass
            # 2. Direct Unicast if IP known
            if self.esp_ip:
                try:
                    self.sock.sendto(raw, (self.esp_ip, self.port))
                except Exception:
                    pass

        return inst_id, evt

    def wait_for_response(self, inst_id: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
        """Wait for feedback of a specific instruction."""
        if self.simulate:
            return {"status": "completed", "totalpulse": 100}

        with self._lock:
            evt = self._response_events.get(inst_id)

        if not evt:
            return None

        finished = evt.wait(timeout=timeout)
        with self._lock:
            resp = self._pending_responses.pop(inst_id, None)
            self._response_events.pop(inst_id, None)

        return resp if finished else None

    # =========================================================================
    # CORE MOVEMENT PRIMITIVES
    # =========================================================================

    def emergency_stop(self) -> None:
        """Immediately brake all 4 motors on ESP4."""
        print("[!] EMERGENCY STOP BROADCAST TO ESP #4...")
        payload = {"esp": ESP_LEG_ID, "cmd": "STOP", "instructionID": "EMERGENCY_STOP"}
        if self.sock and not self.simulate:
            raw = json.dumps(payload).encode("utf-8")
            try:
                self.sock.sendto(raw, (self.broadcast_ip, self.port))
                if self.esp_ip:
                    self.sock.sendto(raw, (self.esp_ip, self.port))
            except Exception:
                pass
        self.current_steer_offset_pulses = 0

    def steer_both_front(self, pulses: int, direction: int, timeout: float = 8.0) -> bool:
        """
        Turn both front steering wheels (Motor 1 and Motor 3) simultaneously.
        direction: 0 = RIGHT, 1 = LEFT.
        """
        if pulses <= 0:
            return True

        dir_label = "RIGHT" if direction == STEER_DIR_RIGHT else "LEFT"
        deg = pulses / STEERING_PULSES_PER_DEGREE
        print(f"[*] Steering front wheels: {pulses} pulses ({deg:.1f} deg) -> {dir_label}")

        id1, evt1 = self.send_command(MOTOR_FRONT_LEFT_STEER, pulses, direction, timeout=timeout)
        id3, evt3 = self.send_command(MOTOR_FRONT_RIGHT_STEER, pulses, direction, timeout=timeout)

        # Wait for both wheels to finish
        start_t = time.time()
        ok1 = evt1.wait(timeout=timeout) if not self.simulate else True
        elapsed = time.time() - start_t
        rem_timeout = max(0.5, timeout - elapsed)
        ok3 = evt3.wait(timeout=rem_timeout) if not self.simulate else True

        # Retrieve feedbacks
        resp1 = self.wait_for_response(id1, timeout=0.1) if not self.simulate else {"status": "completed"}
        resp3 = self.wait_for_response(id3, timeout=0.1) if not self.simulate else {"status": "completed"}

        stat1 = resp1.get("status", "completed") if resp1 else ("ok" if ok1 else "timeout")
        stat3 = resp3.get("status", "completed") if resp3 else ("ok" if ok3 else "timeout")

        success = (stat1 == "completed" and stat3 == "completed")
        if success:
            # Update steering offset
            signed_pulses = pulses if direction == STEER_DIR_RIGHT else -pulses
            self.current_steer_offset_pulses += signed_pulses
            print(f"  [+] Front steering set successfully! (Status: M1={stat1}, M3={stat3})")
        else:
            print(f"  [!] Front steering incomplete or timed out: M1={stat1}, M3={stat3}")

        return success

    def drive_both_rear(self, distance_cm: float, timeout_per_chunk: float = 12.0) -> bool:
        """
        Drive both rear motors (Motor 2 and Motor 4) simultaneously for distance_cm.
        Automatically chunks into safe <= MAX_CHUNK_PULSES increments.
        """
        if abs(distance_cm) < 0.01:
            return True

        total_pulses = int(round(abs(distance_cm) * DRIVE_PULSES_PER_CM))
        direction = DRIVE_DIR_FORWARD if distance_cm >= 0 else DRIVE_DIR_BACKWARD
        dir_label = "FORWARD" if direction == DRIVE_DIR_FORWARD else "BACKWARD"

        print(f"[*] Driving both rear wheels: {abs(distance_cm):.1f} cm ({total_pulses} pulses) -> {dir_label}")

        remaining_pulses = total_pulses
        pulses_done = 0
        chunk_idx = 1
        total_chunks = math.ceil(total_pulses / MAX_CHUNK_PULSES)

        while remaining_pulses > 0:
            current_chunk = min(remaining_pulses, MAX_CHUNK_PULSES)
            chunk_cm = current_chunk / DRIVE_PULSES_PER_CM

            print(f"  -> Chunk {chunk_idx}/{total_chunks}: {current_chunk} pulses ({chunk_cm:.1f} cm)...", end="", flush=True)

            id2, evt2 = self.send_command(MOTOR_REAR_LEFT_DRIVE, current_chunk, direction, timeout=timeout_per_chunk)
            id4, evt4 = self.send_command(MOTOR_REAR_RIGHT_DRIVE, current_chunk, direction, timeout=timeout_per_chunk)

            # Wait for both rear motors to complete
            start_t = time.time()
            ok2 = evt2.wait(timeout=timeout_per_chunk) if not self.simulate else True
            elapsed = time.time() - start_t
            rem_timeout = max(0.5, timeout_per_chunk - elapsed)
            ok4 = evt4.wait(timeout=rem_timeout) if not self.simulate else True

            resp2 = self.wait_for_response(id2, timeout=0.1) if not self.simulate else {"status": "completed"}
            resp4 = self.wait_for_response(id4, timeout=0.1) if not self.simulate else {"status": "completed"}

            stat2 = resp2.get("status", "completed") if resp2 else ("ok" if ok2 else "timeout")
            stat4 = resp4.get("status", "completed") if resp4 else ("ok" if ok4 else "timeout")

            if stat2 != "completed" or stat4 != "completed":
                print(f" FAILED! (M2={stat2}, M4={stat4})")
                print(f"[!] Rear drive aborted at {pulses_done + current_chunk}/{total_pulses} pulses.")
                self.emergency_stop()
                return False

            pulses_done += current_chunk
            remaining_pulses -= current_chunk
            chunk_idx += 1
            print(f" OK ({pulses_done}/{total_pulses} pulses)")

            # Brief inter-chunk pause
            if remaining_pulses > 0:
                time.sleep(0.05)

        print(f"  [+] Rear drive completed: {abs(distance_cm):.1f} cm ({pulses_done} pulses)!")
        return True

    def center_front_wheels(self) -> bool:
        """Return front steering wheels to neutral (0° offset) if they were turned."""
        if self.current_steer_offset_pulses == 0:
            print("[*] Front steering wheels are already centered.")
            return True

        pulses = abs(self.current_steer_offset_pulses)
        # If offset > 0 (turned right), we steer left to return to 0, and vice versa
        return_dir = STEER_DIR_LEFT if self.current_steer_offset_pulses > 0 else STEER_DIR_RIGHT
        dir_label = "LEFT" if return_dir == STEER_DIR_LEFT else "RIGHT"
        print(f"[*] Returning front wheels to neutral center: {pulses} pulses -> {dir_label}...")

        ok = self.steer_both_front(pulses, return_dir)
        if ok:
            self.current_steer_offset_pulses = 0
            print("  [+] Front wheels straightened to 0 deg neutral!")
        return ok

    # =========================================================================
    # HIGH-LEVEL COURSE CONTROLLER: (forward_cm, lateral_cm)
    # =========================================================================

    def move_course(
        self,
        forward_cm: float,
        lateral_cm: float,
        re_center: bool = True
    ) -> bool:
        """
        Execute coordinated steering course motion:
        1. Calculate heading course angle θ = atan2(lateral_cm, forward_cm).
        2. If angle != 0: Set front steering wheels (Motor 1 & Motor 3) to that course.
        3. Drive both rear wheels (Motor 2 & Motor 4) simultaneously for target distance.
        4. If re_center is True: Adjust front wheels back to neutral so robot does
           not keep turning in a circle.

        Coordinates:
          forward_cm: + is Forward, - is Backward.
          lateral_cm: - is Left (e.g. -300 = 300cm left), + is Right.
        """
        print("\n" + "=" * 65)
        print(f"[*] INITIATING LEGS COURSE MOTION: ({forward_cm:+.1f} cm, {lateral_cm:+.1f} cm)")
        print("=" * 65)

        # 1. Determine steering angle
        if abs(forward_cm) < 0.001 and abs(lateral_cm) < 0.001:
            print("[*] Zero movement commanded.")
            return True

        angle_rad = math.atan2(lateral_cm, forward_cm)
        angle_deg = math.degrees(angle_rad)

        # Clamp angle if necessary
        angle_mag = abs(angle_deg)
        if angle_mag > self.max_steer_angle:
            print(f"[!] Commanded angle {angle_deg:.1f} deg exceeds max limit {self.max_steer_angle} deg. Clamping.")
            clamped_mag = self.max_steer_angle
        else:
            clamped_mag = angle_mag

        steer_pulses = int(round(clamped_mag * STEERING_PULSES_PER_DEGREE))
        # lateral_cm < 0 is LEFT (dir 1), lateral_cm > 0 is RIGHT (dir 0)
        steer_dir = STEER_DIR_LEFT if lateral_cm < 0 else STEER_DIR_RIGHT

        # 2. Determine drive distance along course
        if self.use_hypotenuse:
            # Euclidean path distance along steered vector
            travel_distance_cm = math.hypot(forward_cm, lateral_cm)
        else:
            travel_distance_cm = abs(forward_cm)

        # If forward_cm < 0, drive backward along vector
        if forward_cm < 0:
            travel_distance_cm = -travel_distance_cm

        print(f"  Target Forward    : {forward_cm:+.1f} cm")
        print(f"  Target Lateral    : {lateral_cm:+.1f} cm ({'LEFT' if lateral_cm < 0 else 'RIGHT' if lateral_cm > 0 else 'CENTER'})")
        print(f"  Course Heading    : {angle_deg:+.2f} deg (Steer Pulses: {steer_pulses})")
        print(f"  Path Travel Dist  : {abs(travel_distance_cm):.1f} cm ({'FORWARD' if travel_distance_cm >= 0 else 'BACKWARD'})")
        print(f"  Drive Pulses      : {int(round(abs(travel_distance_cm) * DRIVE_PULSES_PER_CM))}")
        print("-----------------------------------------------------------------")

        # Step A: Steer front wheels to course
        if steer_pulses > 0:
            print(f"[STEP 1/3] Setting front wheels to course...")
            if not self.steer_both_front(steer_pulses, steer_dir):
                print("[!] Failed to set front steering course! Aborting.")
                return False
            time.sleep(0.1)
        else:
            print(f"[STEP 1/3] Straight course (0 deg), skipping steering.")

        # Step B: Drive rear wheels simultaneously
        print(f"[STEP 2/3] Driving rear wheels simultaneously...")
        drive_ok = self.drive_both_rear(travel_distance_cm)
        if not drive_ok:
            print("[!] Rear drive step failed!")
            if re_center and steer_pulses > 0:
                self.center_front_wheels()
            return False

        # Step C: Re-center front wheels back to neutral
        if re_center and steer_pulses > 0:
            print(f"[STEP 3/3] Adjusting front wheels back to straight neutral...")
            time.sleep(0.1)
            # Re-center by steering in the opposite direction
            return_dir = STEER_DIR_RIGHT if steer_dir == STEER_DIR_LEFT else STEER_DIR_LEFT
            if not self.steer_both_front(steer_pulses, return_dir):
                print("[!] Failed to return front wheels to center!")
                return False
            # Reset tracked steering offset
            self.current_steer_offset_pulses = 0
            print("  [+] Front wheels straightened back to 0 deg neutral!")
        else:
            print(f"[STEP 3/3] Course completed.")

        print("=" * 65)
        print("\033[92m[SUCCESS] LEGS COURSE MOTION COMPLETED SUCCESSFULLY!\033[0m")
        print("=" * 65 + "\n")
        return True

    # Convenience alias for course execution
    execute_course = move_course

    def send_motor_command(
        self,
        motor: int,
        pulse: int,
        direction: int,
        timeout: float = 12.0
    ) -> Tuple[bool, int]:
        """
        Send a direct motor pulse command and wait for completion.
        Returns: (success_bool, net_pulses_int)
        """
        inst_id, evt = self.send_command(motor, pulse, direction, timeout=timeout)
        resp = self.wait_for_response(inst_id, timeout=timeout)
        if self.simulate:
            return True, pulse
        if not resp:
            return False, 0
        status = resp.get("status", "")
        net_pulse = int(resp.get("totalpulse") or resp.get("total_pulse") or pulse)
        return (status == "completed"), net_pulse

    def close(self) -> None:
        """Close controller and release network sockets."""
        self._running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


if __name__ == "__main__":
    # When executed directly, run the interactive CLI tester
    test_runner = ROBOT_DIR.parent / "test_leg_control.py"
    if test_runner.exists():
        import runpy
        runpy.run_path(str(test_runner), run_name="__main__")
    else:
        print("[*] LegController module loaded successfully.")
