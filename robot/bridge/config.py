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

import urllib.request

KNOWN_LOCAL_HOSTS = [
    "10.174.69.172",
    "localhost",
    "127.0.0.1",
]


def is_bridge_reachable(url: str, timeout: float = 0.5) -> bool:
    """Test if a candidate bridge URL is alive and responding on /api/pi_instructions."""
    try:
        req = urllib.request.Request(
            f"{url}/api/pi_instructions?limit=1",
            headers={"User-Agent": "Nova-Bridge-Discovery"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_default_bridge_url() -> str:
    """
    Finds the active local Robot Bridge site running on developer PC or local Pi.
    Order of preference:
      1. Explicit environment variable BRIDGE_URL
      2. SSH client IP (developer machine connecting via SSH) on port 3000
      3. Known local developer host (10.174.69.172:3000)
      4. localhost:3000
      5. Fallback: http://10.174.69.172:3000
    """
    env_url = os.environ.get("BRIDGE_URL")
    if env_url:
        return env_url.rstrip("/")

    # Check SSH client IP if running over SSH
    for env_var in ["SSH_CLIENT", "SSH_CONNECTION"]:
        val = os.environ.get(env_var, "")
        if val:
            client_ip = val.split()[0]
            candidate = f"http://{client_ip}:3000"
            if is_bridge_reachable(candidate, timeout=0.4):
                return candidate

    # Check known local developer hosts
    for host in KNOWN_LOCAL_HOSTS:
        candidate = f"http://{host}:3000"
        if is_bridge_reachable(candidate, timeout=0.4):
            return candidate

    # Default to developer PC local bridge
    return "http://10.174.69.172:3000"


# Default bridge URL (Local Robot Bridge site)
DEFAULT_BRIDGE_URL = get_default_bridge_url()

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
