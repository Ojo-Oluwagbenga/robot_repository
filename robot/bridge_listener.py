#!/usr/bin/env python3
"""
Bridge Listener for Raspberry Pi (Entry Point Alias).
Directs to the modular Nova Robot Bridge subsystem.
Main loop is located at: robot/bridge_main.py
Modular components are located at: robot/bridge/
"""

import sys
from pathlib import Path

# Add robot directory to sys.path
ROBOT_DIR = Path(__file__).resolve().parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

from bridge_main import main

if __name__ == "__main__":
    main()
