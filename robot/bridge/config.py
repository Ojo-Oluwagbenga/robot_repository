"""
Configuration for Robot Bridge Subsystem.
Defines endpoints, local storage locations, intervals, and timeouts.
"""

import os
from pathlib import Path

# Base directories
BRIDGE_DIR = Path(__file__).resolve().parent
ROBOT_DIR = BRIDGE_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent

# Default bridge URL (Local Next.js server at robot_bridge_site or Vercel)
DEFAULT_BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://localhost:3000").rstrip("/")

# Local instruction queue file (stashed instructions waiting to run)
# Can be overridden via ROBOT_INSTRUCTIONS_FILE environment variable
INSTRUCTIONS_FILE_PATH = Path(
    os.environ.get("ROBOT_INSTRUCTIONS_FILE", str(BRIDGE_DIR / "instructions.json"))
)

# Persistent file to track stashed instruction IDs and prevent duplicate entries
PROCESSED_HISTORY_PATH = BRIDGE_DIR / ".stashed_ids.json"

# Directory containing default action scripts
ACTIONS_DIR = BRIDGE_DIR / "actions"

# Polling and execution timing
DEFAULT_POLL_INTERVAL = float(os.environ.get("BRIDGE_POLL_INTERVAL", "1.0"))
DEFAULT_EXECUTOR_INTERVAL = float(os.environ.get("EXECUTOR_INTERVAL", "0.25"))

# Maximum duration (in seconds) allowed for an action script to execute before timing out
ACTION_TIMEOUT_SECONDS = int(os.environ.get("ACTION_TIMEOUT_SEC", "45"))

# Auto-acknowledge executed instructions back to the cloud bridge site
AUTO_ACK_CLOUD = True
