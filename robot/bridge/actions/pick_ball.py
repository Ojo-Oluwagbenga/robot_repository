#!/usr/bin/env python3
"""
Bridge Action: PICK_BALL
=============================================================================
Invoked by bridge executor when code 'PICK_BALL', 'PICK_RED_BALL', or
'PICK_OBJECT' is received from the LUKU App or Gemini Live.
Executes autonomous visual servoing to locate, approach, grasp, and lift the
specified target ball.
=============================================================================
"""

import sys
import os
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent.parent
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from robot.control.visual_servo_arm import VisualServoArmController
except ImportError:
    from control.visual_servo_arm import VisualServoArmController


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "PICK_BALL"
    val = sys.argv[2] if len(sys.argv) > 2 else "red"
    inst_id = sys.argv[3] if len(sys.argv) > 3 else "inst_manual"

    print("=" * 65)
    print(f"[*] [Action: pick_ball.py] >>> STARTING VISUAL SERVO PICK ACTION <<<")
    print(f"    Code           : {code}")
    print(f"    Target Ball    : {val}")
    print(f"    Instruction ID : {inst_id}")
    print("=" * 65)

    # Determine color from val or code
    color = "red"
    val_clean = str(val or "").strip().lower()
    code_clean = str(code or "").strip().lower()

    for c in ["red", "green", "blue", "yellow", "orange", "purple"]:
        if c in val_clean or c in code_clean:
            color = c
            break

    controller = VisualServoArmController(ball_color=color)
    success = controller.execute_visual_servo_pick(max_attempts=3)

    if success:
        print("[Action: pick_ball.py] Visual servo pick finished with SUCCESS!")
        sys.exit(0)
    else:
        print("[Action: pick_ball.py] Visual servo pick completed without confirmed grasp.")
        sys.exit(1)


if __name__ == "__main__":
    main()
