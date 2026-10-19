#!/usr/bin/env python3
"""
Action File: NOD_DOWN
Tilts neck / head downward by specified angle or pulse count.
Invoked by bridge executor when code 'NOD_DOWN' is received.
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
    code = sys.argv[1] if len(sys.argv) > 1 else "NOD_DOWN"
    val = sys.argv[2] if len(sys.argv) > 2 else "30"
    print(f"[Action: nod_down.py] Received Code='{code}', Value='{val}'")

    try:
        from robot.main import RobotSystem, parse_and_execute_motor_command
    except ImportError:
        try:
            from main import RobotSystem, parse_and_execute_motor_command
        except ImportError:
            RobotSystem = None

    pulses = int(val) if str(val).isdigit() else 30
    # In hardware protocol, N1 negative pulses = Down (CCW)
    cmd = f"N1_-{pulses}"
    print(f"[Action: nod_down.py] Dispatching: {cmd}")

    if RobotSystem:
        is_simulation = (os.name == "nt")
        robot = RobotSystem(simulation=is_simulation)
        try:
            parse_and_execute_motor_command(robot, cmd)
        finally:
            robot.shutdown()
    else:
        print(f"[Action: nod_down.py] Simulated tilt downward: -{pulses} pulses.")
        time.sleep(0.3)

    print("[Action: nod_down.py] Execution finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
