#!/usr/bin/env python3
"""
Action File: MOTOR_MOVE
Direct signed pulse controller for M2 (ESP1 M4), N1 (ESP3 M1), and N2 (ESP3 M2).
Invoked when codes like M2, N1, N2 or formatted commands like M2_20 are received.
"""

import sys
import os
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "M2"
    val = sys.argv[2] if len(sys.argv) > 2 else "20"
    print(f"[Action: motor_move.py] Code='{code}', Value='{val}'")

    # If code already contains pulse count (e.g. M2_20 or N1_-15)
    if "_" in code and (code.split("_", 1)[1].replace("-", "").isdigit()):
        cmd = code
    else:
        # Construct standard syntax: e.g. M2_20
        clean_val = str(val).strip()
        if not clean_val or clean_val == "None":
            clean_val = "20"
        cmd = f"{code}_{clean_val}"

    print(f"[Action: motor_move.py] Formatted command: {cmd}")

    try:
        from robot.main import RobotSystem, parse_and_execute_motor_command
    except ImportError:
        try:
            from main import RobotSystem, parse_and_execute_motor_command
        except ImportError:
            RobotSystem = None

    if RobotSystem:
        is_simulation = (os.name == "nt")
        robot = RobotSystem(simulation=is_simulation)
        try:
            handled = parse_and_execute_motor_command(robot, cmd)
            if not handled:
                print(f"[Action: motor_move.py] Command {cmd} not handled by motor manager.")
        finally:
            robot.shutdown()
    else:
        print(f"[Action: motor_move.py] Simulated motor movement for '{cmd}'.")

    print("[Action: motor_move.py] Execution finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
