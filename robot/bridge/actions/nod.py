#!/usr/bin/env python3
"""
Action File: NOD
Nods head/neck downward and returns to neutral.
Invoked by bridge executor when code 'NOD' is received.
"""

import sys
import os
from pathlib import Path

# Add project root and robot directory to sys.path
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
    code = sys.argv[1] if len(sys.argv) > 1 else "NOD"
    val = sys.argv[2] if len(sys.argv) > 2 else "1"
    print(f"[Action: nod.py] Received Code='{code}', Value='{val}'")

    cycles = int(val) if str(val).isdigit() else 1
    controller = GestureController()
    success = controller.nod(cycles=cycles)

    if success:
        print("[Action: nod.py] Execution finished successfully.")
        sys.exit(0)
    else:
        print("[Action: nod.py] Execution failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
