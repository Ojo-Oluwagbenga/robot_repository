#!/usr/bin/env python3
"""
Action File: SPEAK
=============================================================================
Vocalizes incoming text over the speaker connected to the phone by dispatching
the text packet to the LUKU Live Phone App.

Arguments received from bridge executor:
  sys.argv[1] = code (SPEAK)
  sys.argv[2] = value (The text to speak out loud)
  sys.argv[3] = instruction_id

Can also be invoked directly from command line:
  python3 speak.py "Hello Yohanna"
=============================================================================
"""

import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR), str(SCRIPT_DIR.parent)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robot.bridge.phone_speaker import speak_to_phone


def extract_speech_text() -> tuple:
    """Parses text and instruction ID from CLI or bridge executor arguments."""
    if len(sys.argv) <= 1:
        return "", "SPEAK", "N/A"

    first_arg = sys.argv[1].strip()

    # Case A: Bridge executor format: [0]=script, [1]="SPEAK", [2]="Hello ...", [3]=id
    if first_arg.upper() == "SPEAK":
        text = sys.argv[2].strip() if len(sys.argv) > 2 else ""
        inst_id = sys.argv[3].strip() if len(sys.argv) > 3 else "N/A"
        return text, "SPEAK", inst_id

    # Case B: Direct CLI invocation: python speak.py "Hello Yohanna"
    text = " ".join(sys.argv[1:]).strip()
    return text, "SPEAK", "cli_direct"


def main():
    text, code, inst_id = extract_speech_text()

    print(f"\n[Action: speak.py] Code='{code}', ID='{inst_id}', Text='{text}'")

    if not text:
        print("[Action: speak.py] No text provided to speak.")
        sys.exit(0)

    print(f"[Action: speak.py] Dispatching to phone speaker: \"{text}\"")
    success = speak_to_phone(text)

    if success:
        print(f"[Action: speak.py] Speech successfully dispatched to phone app.")
    else:
        print(f"[Action: speak.py] Warning: Could not deliver speech to phone.")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
