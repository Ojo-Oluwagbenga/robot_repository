#!/usr/bin/env python3
"""
Action File: SHOULDER_LEFT
Controls left shoulder stepper motor on Raspberry Pi 5.
Hardware: DIR on GPIO 9, STEP on GPIO 10.
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

try:
    from robot.hardware.pi_motors import get_pi_motors
except ImportError:
    from hardware.pi_motors import get_pi_motors


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "SHOULDER_LEFT"
    val = sys.argv[2] if len(sys.argv) > 2 else "200"
    print(f"[Action: shoulder_left.py] Received Code='{code}', Value='{val}'")

    try:
        steps_val = int(val) if val else 200
    except ValueError:
        steps_val = 200

    direction = 0 if steps_val >= 0 else 1
    steps = abs(steps_val)

    pi_motors = get_pi_motors()
    success = pi_motors.shoulder_left.step(steps=steps, direction=direction)

    if success:
        print("[Action: shoulder_left.py] Stepping completed successfully.")
        sys.exit(0)
    else:
        print("[Action: shoulder_left.py] Stepping failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
