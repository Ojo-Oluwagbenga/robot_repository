#!/bin/bash
set -e
cd /home/pi/projects/Robot/robot

echo "=========================================="
echo "OTA FIRMWARE DEPLOYMENT FROM RASPBERRY PI"
echo "=========================================="

# 1. Flash ESP 2
echo "[*] Flashing ESP #2 (10.128.135.196)..."
python3 espota.py -i 10.128.135.196 -I 10.128.135.92 -p 3232 -f esp32_robot_2.bin -d || echo "[!] ESP2 flash attempt finished."

# 2. Flash ESP 3
echo "[*] Flashing ESP #3 (10.128.135.119)..."
python3 espota.py -i 10.128.135.119 -I 10.128.135.92 -p 3232 -f esp32_robot_3.bin -d || echo "[!] ESP3 flash attempt finished."

echo "[*] Flashing sequence completed!"
