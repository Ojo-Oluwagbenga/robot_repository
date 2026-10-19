import sys
from pathlib import Path

ROBOT_DIR = Path(__file__).resolve().parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))
