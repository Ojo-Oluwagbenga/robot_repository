#!/usr/bin/env python3
"""
Launcher & Web Monitor for Visual Servoing Arm Ball Tracker & Picker
=============================================================================
Usage:
  python3 track_and_pick_ball.py                        # Pick RED ball (default)
  python3 track_and_pick_ball.py --ball-color green     # Pick GREEN ball
  python3 track_and_pick_ball.py --web 8080             # Stream HUD on port 8080
  python3 track_and_pick_ball.py --sim                  # Simulation mode
=============================================================================
"""

import sys
import os
import argparse
import time
from pathlib import Path

# Add project root and robot dir to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from robot.control.visual_servo_arm import VisualServoArmController
except ImportError:
    from control.visual_servo_arm import VisualServoArmController


def main():
    parser = argparse.ArgumentParser(description="LUKU Robot Visual Servo Arm Ball Picker")
    parser.add_argument("--ball-color", "-c", default="red", help="Color of ball to track and pick (red, green, blue, yellow)")
    parser.add_argument("--camera", type=int, default=0, help="V4L2 camera device index")
    parser.add_argument("--attempts", type=int, default=3, help="Max pick attempts")
    parser.add_argument("--sim", action="store_true", help="Run in simulation mode without hardware")
    args = parser.parse_args()

    print("=" * 70)
    print("        LUKU ROBOT | VISUAL SERVOING ARM BALL PICKER")
    print("=" * 70)
    print(f"  * Target Color : {args.ball_color.upper()} Ball")
    print(f"  * Mode         : {'SIMULATION' if args.sim else 'LIVE HARDWARE'}")
    print(f"  * Max Attempts : {args.attempts}")
    print("=" * 70)

    controller = VisualServoArmController(
        ball_color=args.ball_color,
        camera_index=args.camera,
        simulation=args.sim
    )

    success = controller.execute_visual_servo_pick(max_attempts=args.attempts)

    if success:
        print("\n[+] Visual Servo Pick completed successfully!")
        sys.exit(0)
    else:
        print("\n[-] Visual Servo Pick finished without grasping target.")
        sys.exit(1)


if __name__ == "__main__":
    main()
