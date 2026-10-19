#!/usr/bin/env python3
"""
Action File: STOP
Emergency stop handler: halts all motor motion and clears active locks.
Invoked when codes like STOP, HALT, or FREEZE are received.
"""

import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def main():
    print("[Action: stop.py] >>> EMERGENCY STOP TRIGGERED <<<")
    try:
        from robot.main import RobotSystem
    except ImportError:
        try:
            from main import RobotSystem
        except ImportError:
            RobotSystem = None

    if RobotSystem:
        is_simulation = (os.name == "nt")
        robot = RobotSystem(simulation=is_simulation)
        try:
            robot.bus.disable_motor_power()
            robot.lock_manager.clear_all_locks()
            print("[Action: stop.py] Motor power cut and all hardware locks cleared.")
        finally:
            robot.shutdown()
    else:
        print("[Action: stop.py] (Simulated) Emergency stop executed.")

    print("[Action: stop.py] Execution finished successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()
