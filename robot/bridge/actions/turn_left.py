#!/usr/bin/env python3
"""
Action File: TURN_LEFT
Rotates head sideways to the LEFT.
Hardware: Encoded DC motor on Pi GPIO 20 (IN1) and GPIO 16 (IN2).
Timer Mode: 1 degree = 100 ms of run time.
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
    code = sys.argv[1] if len(sys.argv) > 1 else "TURN_LEFT"
    val = sys.argv[2] if len(sys.argv) > 2 else "15"
    print(f"[Action: turn_left.py] Received Code='{code}', Value='{val}'")

    try:
        degrees = float(val) if val else 15.0
    except ValueError:
        degrees = 15.0

    # 1 degree = 100ms of run time
    duration_ms = abs(degrees) * 100.0
    print(f"[Action: turn_left.py] Turning head LEFT: {degrees:.1f}° = {duration_ms:.0f}ms run time (100ms/deg)")

    pi_motors = get_pi_motors()
    success = pi_motors.head_turn.turn_degrees(degrees=degrees, direction="left")

    if success:
        print("[Action: turn_left.py] Turn left completed successfully.")
        sys.exit(0)
    else:
        print("[Action: turn_left.py] Turn left failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
