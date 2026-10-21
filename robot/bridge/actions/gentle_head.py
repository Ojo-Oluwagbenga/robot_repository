#!/usr/bin/env python3
"""
Bridge Action: GENTLE_HEAD / HEAD_GREET
=============================================================================
Turns the head gently back and forth using brake profiling (1-5-x-x)
as a responsive gesture for greetings, introductions, or affirmations.
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
    from robot.control.gentle_head import gentle_head_turn_and_return
except ImportError:
    from control.gentle_head import gentle_head_turn_and_return


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "GENTLE_HEAD"
    val = sys.argv[2] if len(sys.argv) > 2 else "1"

    cycles = 1
    try:
        cycles = max(1, int(val))
    except ValueError:
        pass

    print(f"[Action: gentle_head.py] Executing gentle head oscillation (Code='{code}', Cycles={cycles})...")
    ok = gentle_head_turn_and_return(amplitude=160, cycles=cycles)

    if ok:
        print("[Action: gentle_head.py] Completed successfully.")
        sys.exit(0)
    else:
        print("[Action: gentle_head.py] Execution finished with errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
