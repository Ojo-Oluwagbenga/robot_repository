#!/usr/bin/env python3
import sys
from pathlib import Path

ROBOT_DIR = Path(__file__).resolve().parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

from hardware.relay_gate import enable_motor_power_relay, disable_motor_power_relay, is_relay_enabled
import subprocess

print("=" * 60)
print("TESTING MOTOR POWER RELAY GATE ON RASPBERRY PI (GPIO 26)")
print("=" * 60)
print("[*] Initial state:", subprocess.check_output(["pinctrl", "get", "26"], text=True).strip())

print("\n[*] Asserting GPIO 26 HIGH to close relay gate for VM...")
ok = enable_motor_power_relay()
print(f"[*] enable_motor_power_relay() -> {ok}")
print(f"[*] is_relay_enabled()          -> {is_relay_enabled()}")
print("[*] Hardware pin state        :", subprocess.check_output(["pinctrl", "get", "26"], text=True).strip())
print("=" * 60)
