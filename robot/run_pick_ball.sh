#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

# Prioritize systemenv or local .venv
if [ -f "/home/pi/systemenv/bin/activate" ]; then
    source /home/pi/systemenv/bin/activate
elif [ -f "$DIR/.venv/bin/activate" ]; then
    source "$DIR/.venv/bin/activate"
elif [ -f "/home/pi/hand_tracker/.venv/bin/activate" ]; then
    source "/home/pi/hand_tracker/.venv/bin/activate"
fi

echo "=================================================================="
echo " Starting LUKU Autonomous Visual Servoing Arm Ball Picker"
echo "=================================================================="
python3 track_and_pick_ball.py "$@"
