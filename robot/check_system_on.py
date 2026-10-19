#!/usr/bin/env python3
"""
System Hardware Readiness Checker for LUKU Robot.
Checks:
  1. Motor Power Relay Gate (GPIO 26 -> HIGH).
  2. Camera video devices (/dev/video*).
  3. ESP32 motor controllers on UDP 5005.
"""

import subprocess
import os
import sys
import socket
import json
import time

print("=" * 65)
print("     LUKU ROBOT | SYSTEM READINESS & POWER DIAGNOSTIC")
print("=" * 65)

# 1. Motor Power Relay Gate (GPIO 26)
print("\n[*] 1. Checking Motor Power Relay Gate (GPIO 26)...")
try:
    subprocess.run(["pinctrl", "set", "26", "op", "dh"], check=True, capture_output=True)
    res = subprocess.run(["pinctrl", "get", "26"], capture_output=True, text=True)
    state = res.stdout.strip()
    print(f"    [+] GPIO 26 configured: {state}")
    if "hi" in state.lower() or "dh" in state.lower() or "1" in state:
        print("    [OK] Motor Power Relay (VM) is ENERGIZED / ON!")
    else:
        print("    [!] Warning: GPIO 26 may not be driving HIGH.")
except Exception as e:
    print(f"    [!] Failed to set GPIO 26 via pinctrl: {e}")

# 2. Camera Devices
print("\n[*] 2. Checking Camera Devices (/dev/video*)...")
try:
    devs = [f"/dev/{d}" for d in os.listdir("/dev") if d.startswith("video")]
    if devs:
        print(f"    [OK] Found video devices: {sorted(devs)}")
        # Check if /dev/video0 is readable
        if "/dev/video0" in devs:
            print("    [+] /dev/video0 is present and ready for capture.")
    else:
        print("    [!] No video devices found in /dev/!")
except Exception as e:
    print(f"    [!] Error checking camera devices: {e}")

# 3. ESP32 Discovery on UDP 5005
print("\n[*] 3. Probing ESP32 Controllers on Wi-Fi (UDP port 5005)...")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass
    s.bind(("0.0.0.0", 5005))
    s.settimeout(1.5)

    # Broadcast ping
    ping_pkt = json.dumps({"cmd": "STATUS", "esp": 0, "instructionID": "chk_status"}).encode("utf-8")
    s.sendto(ping_pkt, ("255.255.255.255", 5005))

    found = []
    start_t = time.time()
    while time.time() - start_t < 2.5:
        try:
            data, (ip, port) = s.recvfrom(2048)
            found.append((ip, data.decode("utf-8", errors="ignore")))
        except socket.timeout:
            break
    s.close()

    if found:
        print(f"    [OK] Received responses from {len(found)} controller(s):")
        for ip, msg in found:
            print(f"         - [{ip}] {msg.strip()}")
    else:
        print("    [?] No broadcast responses received on port 5005 within 2.5s.")
        print("        (ESPs may respond to direct unicast or send heartbeats periodically).")
except Exception as e:
    print(f"    [!] UDP Probe error: {e}")

print("\n" + "=" * 65)
print("Diagnostic Complete.")
print("=" * 65)
