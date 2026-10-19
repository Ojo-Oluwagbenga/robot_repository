#!/usr/bin/env bash
# One-time setup on the Raspberry Pi (Debian / Raspberry Pi OS, 64-bit).
set -e
cd "$(dirname "$0")"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip v4l-utils libgl1
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements-pi.txt
echo
echo "Done. Start the tracker with:"
echo "  cd $(pwd) && . .venv/bin/activate && python3 track_hand.py"
