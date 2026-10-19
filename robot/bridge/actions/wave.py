#!/usr/bin/env python3
"""
Action File: WAVE
Executes arm raise and greeting oscillations.
Invoked by bridge executor when code 'WAVE' or 'WAVE_HAND' is received.
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
    code = sys.argv[1] if len(sys.argv) > 1 else "WAVE"
    val = sys.argv[2] if len(sys.argv) > 2 else "2"
    print(f"[Action: wave.py] Received Code='{code}', Value='{val}'")

    cycles = int(val) if str(val).isdigit() else 2
    controller = GestureController()
    success = controller.wave(cycles=cycles)

    if success:
        print("[Action: wave.py] Execution finished successfully.")
        sys.exit(0)
    else:
        print("[Action: wave.py] Execution failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
