#!/usr/bin/env python3
"""
Action File: DEFAULT_ACTION
Fallback handler executed when an incoming code is not explicitly listed in code_mappings.py.
Attempts fuzzy similarity matching to existing actions (e.g. NOD, WAVE, PICK),
otherwise logs the custom instruction safely.
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


def main():
    code = sys.argv[1] if len(sys.argv) > 1 else "UNKNOWN"
    val = sys.argv[2] if len(sys.argv) > 2 else ""
    inst_id = sys.argv[3] if len(sys.argv) > 3 else "N/A"
    print(f"[Action: default_action.py] Code='{code}', Value='{val}', ID='{inst_id}'")

    # Check similarity with known gestures
    try:
        from robot.control.similarity import find_most_similar_instruction
        from robot.control.gestures import GestureController
        has_similarity = True
    except ImportError:
        try:
            from control.similarity import find_most_similar_instruction
            from control.gestures import GestureController
            has_similarity = True
        except ImportError:
            has_similarity = False

    handled = False
    if has_similarity:
        sim_inst, score, _, _ = find_most_similar_instruction(code, threshold=0.35)
        if sim_inst:
            print(f"[Action: default_action.py] High similarity match found: [{sim_inst}] (score: {score:.2f})")
            controller = GestureController()
            handled = controller.execute(sim_inst, details=f"Matched from '{code}'")

    if not handled:
        print(f"[Action: default_action.py] Unmapped code '{code}' acknowledged and processed safely.")
        time.sleep(0.2)

    print("[Action: default_action.py] Completed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
