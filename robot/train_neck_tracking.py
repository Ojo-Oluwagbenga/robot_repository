#!/usr/bin/env python3
"""
Launcher for Neck Tracking Training within the robot directory.
"""
import sys
from pathlib import Path

# Add project root and robot dir to sys.path
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
for p in [str(PROJECT_ROOT), str(THIS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from train_neck_tracking import main

if __name__ == "__main__":
    main()
