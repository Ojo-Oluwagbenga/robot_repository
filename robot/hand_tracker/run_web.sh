#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
source .venv/bin/activate
echo "Starting Hand Tracker Web Server on http://0.0.0.0:8080 (press Ctrl+C to stop)..."
python3 track_hand.py --web 8080 --eye both "$@"
