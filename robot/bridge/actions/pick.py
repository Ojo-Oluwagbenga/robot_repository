#!/usr/bin/env python3
"""
Action File: PICK
Executes arm lower, gripper grasp, and arm lift.
Invoked by bridge executor when code 'PICK' or 'PICK_UP' is received.
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

try:
    from robot.control.gestures import GestureController
except ImportError:
    from control.gestures import GestureController


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "PICK"
    val = sys.argv[2] if len(sys.argv) > 2 else "1"
    print(f"[Action: pick.py] Received Code='{code}', Value='{val}'")

    controller = GestureController()
    success = controller.pick()

    if success:
        print("[Action: pick.py] Execution finished successfully.")
        sys.exit(0)
    else:
        print("[Action: pick.py] Execution failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
