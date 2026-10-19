#!/usr/bin/env python3
"""
Interactive Leg Controller Tester for ESP #4 (Humanoid Robot).
=============================================================================
Allows interactive testing of the leg steering and drive base:
- Type coordinates like (200, 0) to drive forward 200 cm straight.
- Type coordinates like (200, -300) to steer left, drive, and re-center.
- Direct motor pulse tests: 4-1-100-0, 4-2-100-0, 4-3-100-0, 4-4-100-0.
- Emergency stop: 'stop' or 's'.
- Quit: 'q' or 'exit'.

Usage:
  python test_leg_control.py
  python test_leg_control.py --ip 10.204.123.xxx
=============================================================================
"""

import sys
import os
import re
import time
import argparse
from pathlib import Path

# Ensure robot root is importable from either root or robot/ subfolder
PROJECT_ROOT = Path(__file__).resolve().parent
for p in (PROJECT_ROOT, PROJECT_ROOT / "robot", PROJECT_ROOT.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from robot.control.leg_controller import LegController, parse_course_input
except ModuleNotFoundError:
    from control.leg_controller import LegController, parse_course_input


def print_banner():
    banner = r"""
=============================================================================
   _     ______ _____    ____  ____  _____     __  __  ____ _____ ____  ____  
  | |   | ____/ ____|  / ___|/ __ \|  __ \   |  \/  |/ __ \_   _/ __ \|  _ \ 
  | |   |  _|| |  __  | |   | |  | | |  | |  | \  / | |  | || || |  | | |_) |
  | |___| |__| |___| | | |___| |__| | |__| |  | |\/| | |__| || || |__| |  _ < 
  |_____|_____\_____|  \____|\____/|_____/   |_|  |_|\____/ |_| \____/|_| \_\
=============================================================================
  Leg Motor Base Conventions:
    Motor 1 (B1_front): Left Front Steer  (Dir 0: Right, Dir 1: Left)
    Motor 2 (B1_rear) : Left Rear Drive   (Dir 0: Backward, Dir 1: Forward)
    Motor 3 (B2_front): Right Front Steer (Dir 0: Right, Dir 1: Left)
    Motor 4 (B2_rear) : Right Rear Drive  (Dir 0: Backward, Dir 1: Forward)

  Calibration:
    Steering : ~5500 pulses / 360 deg (~15.28 pulses/deg)
    Drive    : 2000 pulses / 5 cm (400 pulses/cm)

  Commands:
    (200, 0)       -> Move forward 200 cm straight (both rear wheels)
    (200, -300)    -> Course 200cm FWD, 300cm LEFT (steers, drives, straightens)
    (150, 150)     -> Course 150cm FWD, 150cm RIGHT
    4-1-100-0      -> Direct pulse: Motor 1, 100 pulses, direction 0
    stop / s       -> Emergency stop all leg motors
    re-center / c  -> Force re-center front wheels to 0 deg
    status         -> Check connection to ESP #4
    help / h       -> Show this help menu
    quit / q       -> Exit tester
=============================================================================
"""
    print(banner)


def main():
    parser = argparse.ArgumentParser(description="Robot Leg & Base Controller Interactive Tester")
    parser.add_argument("--ip", type=str, default=None, help="ESP #4 IP address (or auto-discover)")
    parser.add_argument("--relay-pin", type=int, default=26, help="Raspberry Pi relay GPIO pin (default 26)")
    parser.add_argument("--no-hypotenuse", action="store_true", help="Drive only forward_cm distance along course")
    args = parser.parse_args()

    print_banner()

    print("[*] Initializing Leg Controller...")
    controller = LegController(
        esp_ip=args.ip,
        relay_gpio_pin=args.relay_pin,
        use_hypotenuse=not args.no_hypotenuse,
    )

    try:
        while True:
            try:
                raw_input = input("\033[96mRobot Leg CMD > \033[0m").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[*] Exiting.")
                break

            if not raw_input:
                continue

            lower_cmd = raw_input.lower()

            if lower_cmd in ["q", "quit", "exit"]:
                print("[*] Exiting Leg Controller Tester.")
                break

            if lower_cmd in ["h", "help", "?"]:
                print_banner()
                continue

            if lower_cmd in ["s", "stop"]:
                print("[!] EMERGENCY STOP INITIATED!")
                controller.emergency_stop()
                continue

            if lower_cmd in ["c", "center", "re-center"]:
                print("[*] Re-centering front wheels...")
                controller.center_front_wheels()
                continue

            if lower_cmd in ["status", "ping"]:
                ip = controller.esp_ip or "Auto-discovering..."
                print(f"[*] ESP #4 Target IP: {ip}")
                print(f"[*] Tracked Front Steer Offset: {controller.current_steer_offset_pulses} pulses")
                continue

            # Check for direct pulse format: e.g. "4-1-100-0" or "1-100-0"
            direct_match = re.match(r"^(?:4-)?([1-4])-(\d+)-([01])$", raw_input)
            if direct_match:
                motor_id = int(direct_match.group(1))
                pulses = int(direct_match.group(2))
                direction = int(direct_match.group(3))
                print(f"[*] Manual Command: Motor {motor_id}, {pulses} pulses, Direction {direction}")
                success, net = controller.send_motor_command(motor_id, pulses, direction)
                print(f"[*] Result: {'SUCCESS' if success else 'FAILED'} (Net pulses: {net})")
                continue

            # Check for coordinate course format: e.g. "(200, 0)", "(200, -300)", "200, 200"
            coords = parse_course_input(raw_input)
            if coords is not None:
                fwd_cm, lat_cm = coords
                print(f"[*] Executing Course: Forward {fwd_cm} cm, Lateral {lat_cm} cm...")
                controller.execute_course(fwd_cm, lat_cm, re_center=True)
                continue

            print(f"\033[91m[!] Unrecognized command: '{raw_input}'. Type 'help' for examples.\033[0m")

    finally:
        controller.close()
        print("[*] Leg Controller shutdown complete.")


if __name__ == "__main__":
    main()
