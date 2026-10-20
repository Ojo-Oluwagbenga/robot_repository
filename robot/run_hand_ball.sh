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
echo " Starting LUKU Binocular Hand & Ball Stereo Depth Tracker"
echo " Web Stream: http://0.0.0.0:8080/"
echo "=================================================================="
python3 track_hand_ball.py --web 8080 "$@"
