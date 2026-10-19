#!/usr/bin/env python3
"""
Interactive & Command-Line Wi-Fi Motor Tester for Humanoid Robot.
=============================================================================
Communicates with ESP32 controllers over Wi-Fi UDP (port 5005).

Usage:
  # Single Motor Command (format: <esp>-<motor>-<pulse>-<dir>):
  python test_individual_motor.py 1-2-10-0
    -> ESP:   1
    -> Motor: 2
    -> Pulse: 10
    -> Dir:   0 (0 = Clockwise, 1 = Anticlockwise)

  # Simultaneous Multi-Motor Command (format: <cmd1>|<cmd2>|...):
  python test_individual_motor.py "1-5-10-1|1-6-100-0"
    -> Runs ESP 1 Motor 5 (10 pulses CCW) and Motor 6 (100 pulses CW) SIMULTANEOUSLY!

  # Interactive Prompt Mode:
  python test_individual_motor.py
    -> Launches interactive command prompt where you can enter:
       1-2-10-0
       1-5-10-1|1-6-100-0
       scan
       stop
       q (to quit)
=============================================================================
"""

import sys
import time
import json
import socket
import re
import argparse
import threading
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

# Ensure project paths are resolved
SCRIPT_DIR = Path(__file__).resolve().parent
if (SCRIPT_DIR / "communication").exists():
    ROBOT_DIR = SCRIPT_DIR
elif (SCRIPT_DIR / "robot" / "communication").exists():
    ROBOT_DIR = SCRIPT_DIR / "robot"
else:
    ROBOT_DIR = SCRIPT_DIR.parent / "robot"

if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

try:
    from communication.wifi_bus import get_wifi_bus, WifiBus
except ImportError:
    WifiBus = None
    get_wifi_bus = None


DEFAULT_UDP_PORT = 5005
DEFAULT_TIMEOUT_SEC = 8.0

# ==============================================================================
# RASPBERRY PI 5 DIRECT STEPPER MOTOR CONFIGURATION (ESP #5)
# ==============================================================================
# Stepper A (Motor 1): DIR = GPIO 16 (Pin 36), STEP = GPIO 19 (Pin 35)
# Stepper B (Motor 2): DIR = GPIO 21 (Pin 40), STEP = GPIO 20 (Pin 38)
PI_STEPPER_CONFIG: Dict[int, Dict[str, Any]] = {
    1: {
        "name": "Stepper A",
        "dir_pin": 16,
        "step_pin": 19,
        "phys_dir": "Physical Pin 36",
        "phys_step": "Physical Pin 35",
    },
    2: {
        "name": "Stepper B",
        "dir_pin": 21,
        "step_pin": 20,
        "phys_dir": "Physical Pin 40",
        "phys_step": "Physical Pin 38",
    },
}

_lgpio_chip = None
_lgpio_lock = threading.Lock()
_claimed_pins = set()

def _get_lgpio_chip():
    global _lgpio_chip
    if _lgpio_chip is not None:
        return _lgpio_chip
    try:
        import lgpio
        for chip_num in [0, 4]:
            try:
                _lgpio_chip = lgpio.gpiochip_open(chip_num)
                return _lgpio_chip
            except Exception:
                pass
    except ImportError:
        pass
    return None

# Stepper Timing & Torque Acceleration Constants
STEPPER_START_DELAY_S = 0.0050   # 5.0 ms/step (200 Hz) -> Maximum starting torque to overcome gravity & inertia
STEPPER_CRUISE_DELAY_S = 0.0018  # 1.8 ms/step (~555 Hz) -> Smooth, high-speed cruise
STEPPER_MIN_PULSE_S = 0.0001     # 100 µs minimum half-pulse width

def run_pi_stepper(
    motor: int,
    steps: int,
    direction: int,
    start_delay_s: float = STEPPER_START_DELAY_S,
    cruise_delay_s: float = STEPPER_CRUISE_DELAY_S
) -> Tuple[bool, int, str]:
    """
    Step the Raspberry Pi connected stepper motor using a trapezoidal acceleration profile:
    - Starts at MAX TORQUE (low speed / 5.0 ms delay) to break inertia & lift load
    - Smoothly accelerates to cruising speed (1.8 ms delay)
    - Smoothly decelerates back to maximum torque before stopping
    motor: 1 (Stepper A) or 2 (Stepper B)
    steps: number of step pulses
    direction: 0 (CW) or 1 (CCW)
    Returns: (success: bool, steps_taken: int, status_message: str)
    """
    if motor not in PI_STEPPER_CONFIG:
        return False, 0, f"Invalid stepper motor ID {motor}. Must be 1 (Stepper A) or 2 (Stepper B)."

    cfg = PI_STEPPER_CONFIG[motor]
    dir_pin = cfg["dir_pin"]
    step_pin = cfg["step_pin"]
    name = cfg["name"]

    # Ensure relay power gate is closed (GPIO 26 -> HIGH) so VM is active
    try:
        from hardware.relay_gate import enable_motor_power_relay
        enable_motor_power_relay()
    except Exception:
        try:
            import subprocess
            subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
        except Exception:
            pass

    # Calculate ramp length (up to 30% of total steps, capped at 35 steps)
    if steps <= 4:
        ramp_len = 1
    elif steps <= 20:
        ramp_len = max(steps // 2, 1)
    else:
        ramp_len = min(int(steps * 0.30), 35)

    chip = _get_lgpio_chip()
    if chip is not None:
        try:
            import lgpio
            with _lgpio_lock:
                if dir_pin not in _claimed_pins:
                    lgpio.gpio_claim_output(chip, dir_pin, 0)
                    _claimed_pins.add(dir_pin)
                if step_pin not in _claimed_pins:
                    lgpio.gpio_claim_output(chip, step_pin, 0)
                    _claimed_pins.add(step_pin)

            # Set direction: 0 = CW (LOW), 1 = CCW (HIGH)
            lgpio.gpio_write(chip, dir_pin, 1 if direction else 0)
            time.sleep(0.002)  # Settle time for direction line

            for i in range(steps):
                # Trapezoidal ramp with smoothstep easing (3x^2 - 2x^3)
                dist_from_end = min(i, (steps - 1) - i)
                if dist_from_end < ramp_len:
                    fraction = dist_from_end / float(ramp_len)
                    smooth_frac = fraction * fraction * (3.0 - 2.0 * fraction)
                    delay = start_delay_s - smooth_frac * (start_delay_s - cruise_delay_s)
                else:
                    delay = cruise_delay_s

                half_delay = max(delay / 2.0, STEPPER_MIN_PULSE_S)
                lgpio.gpio_write(chip, step_pin, 1)
                time.sleep(half_delay)
                lgpio.gpio_write(chip, step_pin, 0)
                time.sleep(half_delay)

            return True, steps, "completed"
        except Exception as e:
            return False, 0, f"Hardware stepping failed: {e}"
    else:
        # Simulated run on Windows / dev PC
        sim_duration = min(steps * ((start_delay_s + cruise_delay_s) / 2.0), 1.0)
        time.sleep(sim_duration)
        return True, steps, "completed (simulated)"


def parse_motor_command_string(cmd_str: str) -> Optional[Tuple[int, int, int, int]]:
    """
    Parse a motor command string like '1-2-10-0' or '1,2,10,0' or '1 2 10 0'.
    Returns: (esp_id, motor_id, pulse_count, direction) or None if invalid.
    """
    clean_str = cmd_str.strip()
    if not clean_str:
        return None

    # Match 4 numbers separated by -, _, :, comma, or whitespace
    parts = re.split(r"[-_:, \t]+", clean_str)
    if len(parts) != 4:
        return None

    try:
        esp = int(parts[0])
        motor = int(parts[1])
        pulse = int(parts[2])
        direction = int(parts[3])
    except ValueError:
        return None

    # Validate ranges
    if esp < 1 or esp > 5:
        print(f"[!] Invalid ESP ID: {esp}. Must be 1, 2, 3, 4 (ESP32) or 5 (Raspberry Pi Steppers).")
        return None

    if esp in (1, 2):
        max_motors = 6
    elif esp == 3:
        max_motors = 2
    elif esp == 4:
        max_motors = 4
    elif esp == 5:
        max_motors = 2  # 1 = Stepper A (16/19), 2 = Stepper B (21/20)

    if motor < 1 or motor > max_motors:
        target_desc = "Pi Steppers (1=Stepper A, 2=Stepper B)" if esp == 5 else f"ESP #{esp}"
        print(f"[!] Invalid Motor ID: {motor} for {target_desc}. Must be 1 to {max_motors}.")
        return None

    if pulse < 0 or pulse > 65535:
        print(f"[!] Invalid Pulse Count: {pulse}. Must be between 0 and 65535.")
        return None

    if direction not in (0, 1):
        print(f"[!] Invalid Direction: {direction}. Must be 0 (Clockwise) or 1 (Anticlockwise).")
        return None

    return (esp, motor, pulse, direction)


def parse_multi_motor_string(cmd_str: str) -> Optional[list]:
    """
    Parse a single or piped multi-motor command string.
    Example:
      '1-5-10-1' -> [(1, 5, 10, 1)]
      '1-5-10-1|1-6-100-0' -> [(1, 5, 10, 1), (1, 6, 100, 0)]
      '1-5-10-1|1-6-100-0|2-1-50-0' -> [(1, 5, 10, 1), (1, 6, 100, 0), (2, 1, 50, 0)]
    """
    clean_str = cmd_str.strip()
    if not clean_str:
        return None

    tokens = [t.strip() for t in clean_str.split("|") if t.strip()]
    if not tokens:
        return None

    parsed_cmds = []
    for tok in tokens:
        parsed = parse_motor_command_string(tok)
        if not parsed:
            print(f"[!] Failed to parse sub-command: '{tok}'")
            return None
        parsed_cmds.append(parsed)

    return parsed_cmds


KNOWN_ESP_IPS: Dict[int, str] = {
    1: "172.28.29.170",
    2: "172.28.29.196",
    3: "172.28.29.119",
}

ALT_ESP_IPS: Dict[int, str] = {
    1: "172.23.22.170",
    2: "172.23.22.196",
    3: "172.23.22.119",
}


class StandaloneUdpComm:
    """Fallback standalone UDP transceiver if WifiBus is not loaded."""

    def __init__(self, port: int = DEFAULT_UDP_PORT):
        self.port = port
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

    def broadcast(self, payload: Dict[str, Any], target_esp: Optional[int] = None) -> None:
        raw = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(raw, ("255.255.255.255", self.port))
        except Exception:
            pass
        if target_esp and target_esp in KNOWN_ESP_IPS:
            try:
                self.sock.sendto(raw, (KNOWN_ESP_IPS[target_esp], self.port))
            except Exception:
                pass
        if target_esp and target_esp in ALT_ESP_IPS:
            try:
                self.sock.sendto(raw, (ALT_ESP_IPS[target_esp], self.port))
            except Exception:
                pass

    def broadcast_multi(self, esp: int, moves: list, batch_id: str) -> None:
        payload = {
            "esp": esp,
            "cmd": "MOVE",
            "instructionID": batch_id,
            "moves": moves
        }
        raw = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(raw, ("255.255.255.255", self.port))
        except Exception:
            pass
        if esp in KNOWN_ESP_IPS:
            try:
                self.sock.sendto(raw, (KNOWN_ESP_IPS[esp], self.port))
            except Exception:
                pass
        if esp in ALT_ESP_IPS:
            try:
                self.sock.sendto(raw, (ALT_ESP_IPS[esp], self.port))
            except Exception:
                pass

    def wait_for_feedback(self, inst_id: str, timeout: float = DEFAULT_TIMEOUT_SEC) -> Optional[Dict[str, Any]]:
        start_t = time.time()
        while time.time() - start_t < timeout:
            try:
                data, addr = self.sock.recvfrom(4096)
                pkt = json.loads(data.decode("utf-8", errors="ignore"))
                # Match instruction ID or feedback
                if str(pkt.get("instructionID")) == str(inst_id) or str(pkt.get("instruction_id")) == str(inst_id):
                    return pkt
            except (socket.timeout, json.JSONDecodeError):
                continue
            except Exception:
                break
        return None

    def wait_for_multiple_feedbacks(self, inst_ids: list, timeout: float = DEFAULT_TIMEOUT_SEC) -> dict:
        start_t = time.time()
        remaining_ids = set(inst_ids)
        results = {i: None for i in inst_ids}
        while time.time() - start_t < timeout and remaining_ids:
            try:
                data, addr = self.sock.recvfrom(4096)
                pkt = json.loads(data.decode("utf-8", errors="ignore"))
                pkt_id = str(pkt.get("instructionID") or pkt.get("instruction_id") or "")
                if pkt_id in remaining_ids:
                    results[pkt_id] = pkt
                    remaining_ids.remove(pkt_id)
            except (socket.timeout, json.JSONDecodeError):
                continue
            except Exception:
                break
        return results

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def execute_motor_test(
    esp: int,
    motor: int,
    pulse: int,
    direction: int,
    bus = None,
    timeout: float = DEFAULT_TIMEOUT_SEC
) -> bool:
    """Send motor command over Wi-Fi or step local Pi Stepper (ESP #5), and print report."""
    dir_name = "Clockwise (0)" if direction == 0 else "Anticlockwise (1)"
    inst_id = f"test_e{esp}m{motor}_{int(time.time() * 1000) % 100000:05d}"

    if esp == 5:
        # Direct Raspberry Pi Stepper Motor
        cfg = PI_STEPPER_CONFIG.get(motor, {"name": f"Stepper #{motor}", "dir_pin": "?", "step_pin": "?", "phys_dir": "?", "phys_step": "?"})
        name = cfg["name"]
        dir_pin = cfg["dir_pin"]
        step_pin = cfg["step_pin"]
        phys_dir = cfg["phys_dir"]
        phys_step = cfg["phys_step"]

        print("\n" + "=" * 65)
        print(f"[*] INITIATING PI STEPPER MOTOR TEST -> {name.upper()} (ESP #5 | MOTOR #{motor})")
        print("=" * 65)
        print(f"  Target Device     : Raspberry Pi 5 (Direct GPIO)")
        print(f"  Motor Label       : {name}")
        print(f"  Direction Pin     : GPIO {dir_pin} ({phys_dir})")
        print(f"  Step Pin          : GPIO {step_pin} ({phys_step})")
        print(f"  Power Relay Gate  : GPIO 26 (CLOSED / Active)")
        print(f"  Commanded Steps   : {pulse} steps")
        print(f"  Rotation Direction: {dir_name}")
        print("-----------------------------------------------------------------")
        print(f"[*] Pulsing GPIO {step_pin} on Raspberry Pi 5...")

        start_time = time.time()
        success, taken, msg = run_pi_stepper(motor=motor, steps=pulse, direction=direction)
        elapsed = time.time() - start_time

        if success:
            print("\n" + "=" * 65)
            print(f"\033[92m[SUCCESS] {name.upper()} EXECUTION COMPLETED!\033[0m")
            print("=" * 65)
            print(f"  Target Steps     : {pulse}")
            print(f"  Steps Executed   : {taken}")
            print(f"  Direction        : {dir_name}")
            print(f"  Execution Time   : {elapsed:.2f}s")
            print(f"  Final Status     : {msg.upper()}")
            print("=" * 65 + "\n")
            return True
        else:
            print("\n" + "=" * 65)
            print(f"\033[91m[ERROR] {name.upper()} STEPPING FAILED!\033[0m")
            print("=" * 65)
            print(f"  Error Details    : {msg}")
            print(f"  Execution Time   : {elapsed:.2f}s")
            print("=" * 65 + "\n")
            return False

    print("\n" + "=" * 65)
    print(f"[*] INITIATING MOTOR TEST -> ESP #{esp} | MOTOR #{motor}")
    print("=" * 65)
    print(f"  Target ESP ID     : {esp}")
    print(f"  Target Motor      : Motor {motor}")
    print(f"  Commanded Pulses  : {pulse} pulses (Single-Pin Mode)")
    print(f"  Rotation Direction: {dir_name}")
    # Ensure motor power relay gate is closed (GPIO 26 -> HIGH) so VM enters driver
    relay_active = False
    try:
        from hardware.relay_gate import enable_motor_power_relay
        relay_active = enable_motor_power_relay()
    except Exception:
        try:
            import subprocess
            res = subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
            relay_active = (res.returncode == 0)
        except Exception:
            pass

    if relay_active:
        print("  Motor Power Gate  : CLOSED / ACTIVE (GPIO 26 -> HIGH, VM energized)")
    print("-----------------------------------------------------------------")
    print(f"[*] Transmitting Wi-Fi UDP command packet...")

    payload = {
        "esp": esp,
        "motor": motor,
        "pulse": pulse,
        "dir": direction,
        "instructionID": inst_id,
        "cmd": "MOVE"
    }

    start_time = time.time()

    if bus is not None and hasattr(bus, "broadcast_command"):
        bus.broadcast_command(
            esp=esp,
            motor=motor,
            pulse=pulse,
            dir=direction,
            instruction_id=inst_id
        )
        print(f"[*] Packet dispatched! Waiting up to {timeout:.1f}s for ESP #{esp} response...")
        resp = bus.wait_for_response(inst_id, timeout=timeout)
    else:
        comm = StandaloneUdpComm(DEFAULT_UDP_PORT)
        try:
            comm.broadcast(payload, target_esp=esp)
            print(f"[*] Packet dispatched! Waiting up to {timeout:.1f}s for ESP #{esp} response...")
            resp = comm.wait_for_feedback(inst_id, timeout=timeout)
        finally:
            comm.close()

    elapsed = time.time() - start_time

    # Evaluate response
    if resp is None:
        print("\n" + "=" * 65)
        print(f"\033[91m[ERROR] NO RESPONSE FROM ESP #{esp} (Timed out after {elapsed:.2f}s)\033[0m")
        print("=" * 65)
        print(f"  -> ESP #{esp} did not reply over the Wi-Fi network.")
        print(f"  -> Please verify:")
        print(f"       1. ESP #{esp} is powered on and running.")
        print(f"       2. ESP #{esp} is connected to Wi-Fi hotspot 'el_Johannu'.")
        print(f"       3. Type 'scan' to see currently online ESP boards.")
        print("=" * 65 + "\n")
        return False

    status = str(resp.get("status", "unknown")).lower()
    total_pulse = resp.get("totalpulse", resp.get("total_pulse", 0))

    if status == "completed":
        print("\n" + "=" * 65)
        print(f"\033[92m[SUCCESS] ESP #{esp} MOTOR #{motor} EXECUTION COMPLETED!\033[0m")
        print("=" * 65)
        print(f"  Target Pulses    : {pulse}")
        print(f"  Recorded Net     : {total_pulse} pulses")
        print(f"  Direction        : {dir_name}")
        print(f"  Execution Time   : {elapsed:.2f}s")
        print(f"  Final Status     : {status.upper()}")
        print("=" * 65 + "\n")
        return True

    elif status == "stalled":
        print("\n" + "=" * 65)
        print(f"\033[93m[ALERT] ESP #{esp} MOTOR #{motor} REPORTED STALL / NO PULSES!\033[0m")
        print("=" * 65)
        print(f"  Commanded Pulses : {pulse}")
        print(f"  Pulses Counted   : {total_pulse}")
        print(f"  Status           : STALLED")
        print(f"  Diagnostic Notes :")
        print(f"    * The motor was commanded to run, but encoder pulse wire on")
        print(f"      Motor {motor} didn't detect sufficient pulses.")
        print(f"    * Motor automatically braked safely.")
        print(f"    * Check the single encoder pulse wire connection.")
        print("=" * 65 + "\n")
        return False

    elif status == "timeout":
        print("\n" + "=" * 65)
        print(f"\033[93m[WARN] ESP #{esp} MOTOR #{motor} SAFETY WATCHDOG CUTOFF!\033[0m")
        print("=" * 65)
        print(f"  Commanded Pulses : {pulse}")
        print(f"  Pulses Counted   : {total_pulse}")
        print(f"  Status           : TIMEOUT")
        print("=" * 65 + "\n")
        return False

    else:
        print("\n" + "=" * 65)
        print(f"\033[91m[REPORT] ESP #{esp} MOTOR #{motor} REPORTED: {status.upper()}\033[0m")
        print("=" * 65)
        print(f"  Raw Feedback     : {json.dumps(resp)}")
        print("=" * 65 + "\n")
        return False


def execute_multi_motor_test(
    commands: list,
    bus = None,
    timeout: float = DEFAULT_TIMEOUT_SEC
) -> bool:
    """
    Execute multiple motor commands simultaneously across one or more ESPs.
    commands: list of (esp, motor, pulse, direction) tuples.
    """
    batch_id = f"multi_{int(time.time() * 1000) % 1000000:06d}"
    num_moves = len(commands)

    print("\n" + "=" * 70)
    print(f"[*] INITIATING SIMULTANEOUS MULTI-MOTOR TEST ({num_moves} motors)")
    print("=" * 70)
    print(f"  Batch Instruction ID : {batch_id}")
    print("  Commanded Moves      :")
    for idx, (e, m, p, d) in enumerate(commands, start=1):
        d_str = "CW  (0)" if d == 0 else "CCW (1)"
        print(f"    [{idx}] ESP #{e} -> Motor {m} | {p:>5} pulses | {d_str}")

    # Ensure motor power relay gate is closed (GPIO 26 -> HIGH) so VM enters driver
    relay_active = False
    try:
        from hardware.relay_gate import enable_motor_power_relay
        relay_active = enable_motor_power_relay()
    except Exception:
        try:
            import subprocess
            res = subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
            relay_active = (res.returncode == 0)
        except Exception:
            pass

    if relay_active:
        print("  Motor Power Gate     : CLOSED / ACTIVE (GPIO 26 -> HIGH, VM energized)")
    print("-" * 70)
    # Group commands: separate Wi-Fi ESPs from Pi Stepper motors (ESP #5)
    esp_groups: Dict[int, list] = {}
    sub_map: Dict[str, Tuple[int, int, int, int]] = {}
    all_sub_ids: list = []
    stepper_threads: List[threading.Thread] = []
    stepper_results: Dict[str, Dict[str, Any]] = {}

    def _stepper_worker(sub_id: str, motor_num: int, steps_cnt: int, direction_val: int):
        ok, taken, status_msg = run_pi_stepper(motor=motor_num, steps=steps_cnt, direction=direction_val)
        stepper_results[sub_id] = {
            "status": "completed" if ok else "failed",
            "totalpulse": taken,
            "instructionID": sub_id,
            "msg": status_msg
        }

    for idx, (e, m, p, d) in enumerate(commands, start=1):
        sub_id = f"{batch_id}_e{e}m{m}_{idx}"
        all_sub_ids.append(sub_id)
        sub_map[sub_id] = (e, m, p, d)

        if e == 5:
            # Launch local Raspberry Pi stepper motor thread
            t = threading.Thread(
                target=_stepper_worker,
                args=(sub_id, m, p, d),
                daemon=True
            )
            stepper_threads.append(t)
            t.start()
        else:
            if e not in esp_groups:
                esp_groups[e] = []
            esp_groups[e].append({
                "motor": m,
                "pulse": p,
                "dir": d,
                "instructionID": sub_id
            })

    start_time = time.time()
    results: Dict[str, Optional[Dict[str, Any]]] = {}

    # Only dispatch Wi-Fi UDP if there are ESP moves
    if esp_groups:
        wifi_sub_ids = [sid for sid in all_sub_ids if sub_map[sid][0] != 5]
        if bus is not None and hasattr(bus, "broadcast_multi_command"):
            for esp_id, moves in esp_groups.items():
                bus.broadcast_multi_command(
                    esp=esp_id,
                    moves=moves,
                    instruction_id=f"{batch_id}_esp{esp_id}"
                )
            print(f"[*] Dispatched to {len(esp_groups)} ESP(s)! Waiting up to {timeout:.1f}s for responses...")
            results = bus.wait_for_multi_response(wifi_sub_ids, timeout=timeout)
        else:
            comm = StandaloneUdpComm(DEFAULT_UDP_PORT)
            try:
                for esp_id, moves in esp_groups.items():
                    comm.broadcast_multi(esp=esp_id, moves=moves, batch_id=f"{batch_id}_esp{esp_id}")
                print(f"[*] Dispatched to {len(esp_groups)} ESP(s)! Waiting up to {timeout:.1f}s for responses...")
                results = comm.wait_for_multiple_feedbacks(wifi_sub_ids, timeout=timeout)
            finally:
                comm.close()

    # Wait for all local stepper threads to finish
    for t in stepper_threads:
        t.join(timeout=timeout)

    # Merge stepper results into results dictionary
    results.update(stepper_results)

    total_elapsed = time.time() - start_time

    # Display results table
    print("\n" + "=" * 70)
    print(f"{'#':<3} | {'ESP':<4} | {'MTR':<4} | {'CMD PULSE':<9} | {'DIR':<4} | {'NET PULSES':<10} | {'STATUS':<15}")
    print("-" * 70)

    all_ok = True
    for idx, sub_id in enumerate(all_sub_ids, start=1):
        e, m, p, d = sub_map[sub_id]
        d_str = "CW" if d == 0 else "CCW"
        resp = results.get(sub_id)

        if resp is None:
            all_ok = False
            status_str = "\033[91mNO_RESPONSE\033[0m"
            net_str = "-"
        else:
            status = str(resp.get("status", "unknown")).lower()
            net = resp.get("totalpulse", resp.get("total_pulse", 0))
            net_str = f"{net} p"
            if status == "completed":
                status_str = "\033[92mCOMPLETED\033[0m"
            elif status == "stalled":
                all_ok = False
                status_str = "\033[93mSTALLED\033[0m"
            elif status == "timeout":
                all_ok = False
                status_str = "\033[93mTIMEOUT\033[0m"
            else:
                all_ok = False
                status_str = f"\033[91m{status.upper()}\033[0m"

        print(f"{idx:<3} | ESP#{e:<2} | M#{m:<3} | {p:<9} | {d_str:<4} | {net_str:<10} | {status_str}")

    print("=" * 70)
    if all_ok:
        print(f"\033[92m[SUCCESS] ALL {num_moves} MOTORS EXECUTED SIMULTANEOUSLY in {total_elapsed:.2f}s!\033[0m")
    else:
        print(f"\033[93m[PARTIAL/FAILED] One or more motors did not complete successfully (Total time: {total_elapsed:.2f}s).\033[0m")
    print("=" * 70 + "\n")

    return all_ok


def scan_network_for_esps(timeout: float = 2.0) -> None:
    """Discover active ESP controllers by broadcasting status inquiry."""
    print("\n[*] Scanning network for active ESP controllers on UDP port 5005...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)

    ping_payload = b'{"cmd":"STATUS","esp":0,"instructionID":"scan_check"}'
    for _ in range(2):
        try:
            sock.sendto(ping_payload, ("255.255.255.255", DEFAULT_UDP_PORT))
        except Exception:
            pass
        for ip in list(KNOWN_ESP_IPS.values()) + list(ALT_ESP_IPS.values()):
            try:
                sock.sendto(ping_payload, (ip, DEFAULT_UDP_PORT))
            except Exception:
                pass
        time.sleep(0.05)

    online: Dict[int, Dict[str, Any]] = {}
    start_t = time.time()
    while time.time() - start_t < timeout:
        try:
            data, (addr, port) = sock.recvfrom(2048)
            pkt = json.loads(data.decode("utf-8", errors="ignore"))
            esp_id = pkt.get("esp")
            if esp_id and int(esp_id) > 0:
                online[int(esp_id)] = {
                    "ip": addr,
                    "hostname": pkt.get("hostname", f"esp32-robot-{esp_id}"),
                    "status": pkt.get("status", "online")
                }
        except (socket.timeout, json.JSONDecodeError):
            continue
        except Exception:
            break

    sock.close()

    print("-" * 65)
    print(f"{'BOARD':<11} | {'IP ADDRESS':<16} | {'STATUS'}")
    print("-" * 65)
    for i in range(1, 6):
        if i == 5:
            print(f"ESP #5 (PI) | {'127.0.0.1 (Local)':<16} | ONLINE (2 Stepper Motors: A=16/19, B=21/20)")
        elif i in online:
            desc = "6 Encoded Motors" if i in (1, 2) else ("2 Limit-Switch Motors (Pins 26, 25)" if i == 3 else "4 Leg Encoded Motors")
            print(f"ESP #{i:<7} | {online[i]['ip']:<16} | ONLINE ({desc})")
        else:
            print(f"ESP #{i:<7} | {'-':<16} | OFFLINE")
    print("-" * 65 + "\n")


def send_emergency_stop() -> None:
    """Broadcast emergency stop packet to all ESPs."""
    print("[*] Broadcasting EMERGENCY STOP to all ESP controllers...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    payload = b'{"esp":0,"cmd":"STOP","instructionID":"estop_cli"}'
    try:
        sock.sendto(payload, ("255.255.255.255", DEFAULT_UDP_PORT))
        for ip in list(KNOWN_ESP_IPS.values()) + list(ALT_ESP_IPS.values()):
            try:
                sock.sendto(payload, (ip, DEFAULT_UDP_PORT))
            except Exception:
                pass
        print("[+] STOP command sent! All motors braked.\n")
    finally:
        sock.close()


def print_banner():
    print("""
=================================================================
       HUMANOID ROBOT - MOTOR TESTING CONSOLE (WI-FI & PI)
=================================================================
Command Format:
  Single Motor : <esp>-<motor>-<pulses>-<dir>
  Simultaneous : <cmd1>|<cmd2>|...  (separated by '|')
  * esp    : ESP Board ID (1 to 5)
             - ESP 1: Right Arm / Neck (Motors 1 to 6, Encoders)
             - ESP 2: Left Arm / Body  (Motors 1 to 6, Encoders)
             - ESP 3: Head / Gripper   (Motors 1 to 2, Limit Switches)
             - ESP 4: Leg Driving Board(Motors 1 to 4, Encoders)
             - ESP 5: Raspberry Pi 5 Direct Stepper Motors
                      * Motor 1: Stepper A (DIR: GPIO 16, STEP: GPIO 19)
                      * Motor 2: Stepper B (DIR: GPIO 21, STEP: GPIO 20)
  * motor  : Motor Number (1-6 on ESP 1/2; 1-2 on ESP 3/5; 1-4 on ESP 4)
  * pulses : Number of encoder pulses (ESP 1..4) or step pulses (ESP 5)
  * dir    : Direction: 0 = Clockwise (CW), 1 = Anticlockwise (CCW)

Examples:
  5-1-200-0                 -> Run Stepper A (GPIO 16/19) for 200 steps CW
  5-2-200-1                 -> Run Stepper B (GPIO 21/20) for 200 steps CCW
  5-1-200-0|5-2-200-1       -> Run Stepper A & Stepper B SIMULTANEOUSLY
  1-5-10-0|5-1-100-1        -> Run ESP 1 Motor 5 & Stepper A SIMULTANEOUSLY
  1-2-10-0                  -> Run ESP 1, Motor 2 for 10 pulses CW
  1-5-10-1|1-6-100-0        -> Run ESP 1 Motor 5 & Motor 6 SIMULTANEOUSLY
  1-5-50-0|2-1-50-1         -> Run ESP 1 Motor 5 & ESP 2 Motor 1 SIMULTANEOUSLY

Other Commands:
  scan        -> Scan Wi-Fi for online ESP boards
  stop        -> Emergency Stop all motors on all boards
  q / exit    -> Exit the tester
=================================================================
""")


def interactive_loop(bus = None):
    print_banner()
    while True:
        try:
            cmd = input("RobotTester > ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting tester.")
            break

        if not cmd:
            continue

        lower = cmd.lower()
        if lower in ("q", "quit", "exit"):
            print("Exiting tester.")
            break
        elif lower == "scan":
            scan_network_for_esps()
            continue
        elif lower == "stop":
            send_emergency_stop()
            continue
        elif lower in ("relay on", "relay_on", "power on"):
            try:
                from hardware.relay_gate import enable_motor_power_relay
                enable_motor_power_relay()
                print("[POWER] Motor Power Relay Gate CLOSED: GPIO 26 -> HIGH (VM active).\n")
            except Exception as e:
                print(f"[!] Failed to enable relay: {e}\n")
            continue
        elif lower in ("relay off", "relay_off", "power off"):
            try:
                from hardware.relay_gate import disable_motor_power_relay
                disable_motor_power_relay()
                print("[POWER] Motor Power Relay Gate OPENED: GPIO 26 -> LOW (VM disconnected).\n")
            except Exception as e:
                print(f"[!] Failed to disable relay: {e}\n")
            continue
        elif lower in ("relay", "relay status", "power"):
            try:
                from hardware.relay_gate import is_relay_enabled
                state = is_relay_enabled()
                print(f"[POWER] Relay Gate Status: {'CLOSED / HIGH (VM ON)' if state else 'OPEN / LOW (VM OFF)'}\n")
            except Exception as e:
                print(f"[!] Failed to read relay: {e}\n")
            continue
        elif lower in ("help", "h", "?"):
            print_banner()
            continue

        parsed_list = parse_multi_motor_string(cmd)
        if not parsed_list:
            print("[!] Unrecognized input. Use format: <esp>-<motor>-<pulses>-<dir> (e.g. 1-2-10-0 or 1-5-10-1|1-6-100-0)")
            print("    Commands: 'relay on', 'relay off', 'scan', 'stop', 'help'")
            continue

        if len(parsed_list) == 1:
            esp, motor, pulse, direction = parsed_list[0]
            execute_motor_test(esp, motor, pulse, direction, bus=bus)
        else:
            execute_multi_motor_test(parsed_list, bus=bus)


def main():
    parser = argparse.ArgumentParser(description="Humanoid Robot Individual Motor Tester over Wi-Fi")
    parser.add_argument("command", nargs="?", default=None, help="Motor command (e.g. 1-2-10-0 or 1-5-10-1|1-6-100-0)")
    parser.add_argument("--scan", action="store_true", help="Scan for online ESP boards")
    parser.add_argument("--stop", action="store_true", help="Broadcast emergency stop")
    parser.add_argument("--relay-on", action="store_true", help="Turn ON motor power relay gate (GPIO 26 -> HIGH)")
    parser.add_argument("--relay-off", action="store_true", help="Turn OFF motor power relay gate (GPIO 26 -> LOW)")
    parser.add_argument("--relay-status", action="store_true", help="Check motor power relay gate state")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC, help="Response timeout in seconds")
    args = parser.parse_args()

    # Handle explicit relay commands
    if args.relay_on:
        from hardware.relay_gate import enable_motor_power_relay
        ok = enable_motor_power_relay()
        print(f"[POWER] Relay Gate CLOSED: GPIO 26 -> HIGH (Result: {ok})")
        return

    if args.relay_off:
        from hardware.relay_gate import disable_motor_power_relay
        ok = disable_motor_power_relay()
        print(f"[POWER] Relay Gate OPENED: GPIO 26 -> LOW (Result: {ok})")
        return

    if args.relay_status:
        from hardware.relay_gate import is_relay_enabled
        state = is_relay_enabled()
        print(f"[POWER] Relay Gate Status: {'CLOSED / HIGH (VM ON)' if state else 'OPEN / LOW (VM OFF)'}")
        return

    # Automatically ensure motor power relay gate is closed on startup
    try:
        from hardware.relay_gate import enable_motor_power_relay
        enable_motor_power_relay()
        print("[POWER] Motor Power Relay Gate CLOSED: Raspberry Pi GPIO 26 is HIGH (VM energized).")
    except Exception:
        pass

    # Initialize bus if available
    bus = get_wifi_bus() if get_wifi_bus else None

    if args.scan:
        scan_network_for_esps(timeout=args.timeout)
        return

    if args.stop:
        send_emergency_stop()
        return

    if args.command:
        parsed_list = parse_multi_motor_string(args.command)
        if not parsed_list:
            print(f"[!] Invalid motor command string: '{args.command}'")
            print("    Format must be: <esp>-<motor>-<pulse>-<dir> (e.g. 1-2-10-0 or 1-5-10-1|1-6-100-0)")
            sys.exit(1)

        if len(parsed_list) == 1:
            esp, motor, pulse, direction = parsed_list[0]
            success = execute_motor_test(esp, motor, pulse, direction, bus=bus, timeout=args.timeout)
        else:
            success = execute_multi_motor_test(parsed_list, bus=bus, timeout=args.timeout)
        sys.exit(0 if success else 1)

    # Interactive mode
    interactive_loop(bus=bus)


if __name__ == "__main__":
    main()
