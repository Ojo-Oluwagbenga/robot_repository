#!/usr/bin/env python3
"""
Action File: NOD_UP
Nod up and down motor is on ESP 3 Motor 1.
Configured: Runs 10 pulses up (10 * 10ms = 100ms run) and returns (10 pulses down).
Invoked by bridge executor when code 'NOD_UP' is received.
"""

import sys
import os
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "NOD_UP"
    val = sys.argv[2] if len(sys.argv) > 2 else "10"
    print(f"[Action: nod_up.py] Received Code='{code}', Value='{val}'")
    print("[Action: nod_up.py] Sequence: ESP 3 Motor 1 -> 10 pulses up (100ms) and return.")

    try:
        from robot.main import RobotSystem
        from robot.config import DIRECTION_CW, DIRECTION_CCW
    except ImportError:
        try:
            from main import RobotSystem
            from config import DIRECTION_CW, DIRECTION_CCW
        except ImportError:
            RobotSystem = None
            DIRECTION_CW = 0
            DIRECTION_CCW = 1

    if RobotSystem:
        is_simulation = (os.name == "nt")
        robot = RobotSystem(simulation=is_simulation)
        try:
            # ESP 3 Motor 1 (motor_id = 0)
            esp_id = 3
            motor_id = 0
            pulse_count = 10  # 10 pulses = 100ms on ESP32

            print(f"[Action: nod_up.py] Step 1: Tilting UP -> 10 pulses CW (100ms run)...")
            robot.motor_manager.move_pulses(
                esp_id=esp_id,
                motor_id=motor_id,
                pulse_count=pulse_count,
                direction=DIRECTION_CW,
            )
            # Dwell at top
            time.sleep(0.20)

            print(f"[Action: nod_up.py] Step 2: Returning -> 10 pulses CCW (100ms run)...")
            robot.motor_manager.move_pulses(
                esp_id=esp_id,
                motor_id=motor_id,
                pulse_count=pulse_count,
                direction=DIRECTION_CCW,
            )
            time.sleep(0.15)
            print("[Action: nod_up.py] Nod up and return completed successfully.")
        finally:
            robot.shutdown()
    else:
        print("[Action: nod_up.py] (Simulated) Step 1: 10 pulses up (100ms).")
        time.sleep(0.2)
        print("[Action: nod_up.py] (Simulated) Step 2: 10 pulses return (100ms).")
        time.sleep(0.15)

    print("[Action: nod_up.py] Execution finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
