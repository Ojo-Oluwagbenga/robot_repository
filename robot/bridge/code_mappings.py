"""
Code-to-File Mapping Definition File.
=============================================================================
HERE IS WHERE YOU DEFINE THE FILE THAT SHOULD RUN WHEN EACH CODE IS RECEIVED.
=============================================================================

When Gemini Live or the cloud bridge sends an instruction code (e.g. NOD_UP, WAVE),
the bridge executor looks up the corresponding file here and runs it.

You can specify:
- Relative path to a Python script (e.g. "actions/nod.py")
- Shell script or executable file
- Full absolute path to any script on the Raspberry Pi

How arguments are passed to your action file:
  sys.argv[1] = code            (e.g. 'NOD_UP')
  sys.argv[2] = value           (e.g. '30' or '2')
  sys.argv[3] = instruction_id  (e.g. 'inst_1728200_abc')
  sys.argv[4] = json_payload    (full instruction JSON string)

Environment variables are also provided to the child process:
  ROBOT_CODE, ROBOT_VALUE, ROBOT_INSTRUCTION_ID
"""

import os
from pathlib import Path
from typing import Dict, Optional

# Base directory for actions
BRIDGE_DIR = Path(__file__).resolve().parent
ACTIONS_DIR = BRIDGE_DIR / "actions"

# =============================================================================
# DEFINE YOUR CODE MAPPINGS HERE:
# [CODE RECEIVED] -> "FILE TO RUN"
# =============================================================================
CODE_TO_FILE: Dict[str, str] = {
    # Head & Neck Gestures
    "NOD": str(ACTIONS_DIR / "nod.py"),
    "NOD_UP": str(ACTIONS_DIR / "nod_up.py"),
    "NOD_DOWN": str(ACTIONS_DIR / "nod_down.py"),
    "TURN_LEFT": str(ACTIONS_DIR / "turn_left.py"),
    "TURN_RIGHT": str(ACTIONS_DIR / "turn_right.py"),

    # Arm & Greeting Gestures
    "WAVE": str(ACTIONS_DIR / "wave.py"),
    "WAVE_HAND": str(ACTIONS_DIR / "wave.py"),
    "PICK": str(ACTIONS_DIR / "pick.py"),
    "PICK_UP": str(ACTIONS_DIR / "pick.py"),

    # Emergency / Motor Stop
    "STOP": str(ACTIONS_DIR / "stop.py"),
    "HALT": str(ACTIONS_DIR / "stop.py"),
    "FREEZE": str(ACTIONS_DIR / "stop.py"),

    # Direct Motor Pulse Controls (M2, N1, N2 or E<x>_M<y>)
    "M2": str(ACTIONS_DIR / "motor_move.py"),
    "N1": str(ACTIONS_DIR / "motor_move.py"),
    "N2": str(ACTIONS_DIR / "motor_move.py"),

    # Direct Stepper Shoulders on Raspberry Pi 5
    "SHOULDER_RIGHT": str(ACTIONS_DIR / "shoulder_right.py"),
    "SHOULDER_LEFT": str(ACTIONS_DIR / "shoulder_left.py"),

    # Vocalize Speech via Robot Mouth
    "SPEAK": str(ACTIONS_DIR / "speak.py"),

    # Fallback action file if a code is received that isn't listed above
    "DEFAULT": str(ACTIONS_DIR / "default_action.py"),
}


def resolve_action_file(code: str) -> Optional[Path]:
    """
    Resolves an incoming instruction code to the target file that should run.

    Resolution order:
    1. Exact match in CODE_TO_FILE (case-insensitive)
    2. Prefix match for motor shortcuts (e.g. "M2_20" or "N1_-30" maps to "M2" or "N1")
    3. Fallback to "DEFAULT" mapping
    """
    clean_code = str(code or "").strip().upper()
    if not clean_code:
        return None

    target_path_str = None

    # 1. Exact match
    if clean_code in CODE_TO_FILE:
        target_path_str = CODE_TO_FILE[clean_code]
    else:
        # Check case-insensitive keys
        for key, path in CODE_TO_FILE.items():
            if key.upper() == clean_code:
                target_path_str = path
                break

    # 2. Prefix match (e.g. M2_20, N1_-20, E1_M4_20)
    if not target_path_str:
        for prefix in ["M2", "N1", "N2", "E1", "E2", "E3", "E4", "E5"]:
            if clean_code.startswith(prefix):
                target_path_str = CODE_TO_FILE.get(prefix) or CODE_TO_FILE.get("M2")
                break

    # 3. Fallback to DEFAULT
    if not target_path_str:
        target_path_str = CODE_TO_FILE.get("DEFAULT")

    if not target_path_str:
        return None

    # Resolve file path
    path = Path(target_path_str)
    if not path.is_absolute():
        # Check relative to ACTIONS_DIR
        candidate = ACTIONS_DIR / path
        if candidate.exists():
            return candidate
        # Check relative to BRIDGE_DIR
        candidate = BRIDGE_DIR / path
        if candidate.exists():
            return candidate

    return path if path.exists() else None
