#!/usr/bin/env python3
"""
Hardware Pin Test Utility for Raspberry Pi 5 <-> ESP32 Connections.
Toggles bus lines slowly (with 4-second hold times) so you can measure
voltages with a multimeter and observe live transitions on the ESP32.
"""

import time
import sys
import glob
import os

# Ensure system packages are loaded if inside a virtual environment
for sp in ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages"):
    if sp not in sys.path and os.path.exists(sp):
        sys.path.append(sp)

try:
    import lgpio
    print("[OK] lgpio library loaded.")
except ImportError:
    print("[ERROR] lgpio not found. Run: sudo apt install python3-lgpio")
    sys.exit(1)

# Raspberry Pi 5 BCM pin numbers
PIN_CLOCK = 17   # Physical Pin 11 -> ESP32 GPIO 17
PIN_DATA  = 27   # Physical Pin 13 -> ESP32 GPIO 16
PIN_SET   = 22   # Physical Pin 15 -> ESP32 GPIO 4
PIN_FB1   = 23   # Physical Pin 16 <- ESP32 #1 GPIO 2

# Open gpiochip10 (detected on your Pi 5)
CHIP_NUM = 10
try:
    handle = lgpio.gpiochip_open(CHIP_NUM)
    print(f"[OK] Opened gpiochip{CHIP_NUM} successfully.")
except Exception as e:
    print(f"[WARN] Could not open gpiochip{CHIP_NUM}: {e}. Trying chip 0...")
    CHIP_NUM = 0
    handle = lgpio.gpiochip_open(CHIP_NUM)

# Claim output pins
lgpio.gpio_claim_output(handle, PIN_CLOCK, 0)
lgpio.gpio_claim_output(handle, PIN_DATA, 0)
lgpio.gpio_claim_output(handle, PIN_SET, 0)

# Claim feedback pin as input
try:
    lgpio.gpio_claim_input(handle, PIN_FB1, lgpio.SET_PULL_DOWN)
except Exception:
    lgpio.gpio_claim_input(handle, PIN_FB1)

PINS = [
    ("CLOCK", PIN_CLOCK, "Pin 11", "ESP32 GPIO 17"),
    ("DATA",  PIN_DATA,  "Pin 13", "ESP32 GPIO 16"),
    ("SET",   PIN_SET,   "Pin 15", "ESP32 GPIO 4"),
]

HOLD_HIGH_SEC = 4.0  # Seconds to hold HIGH (ample time for multimeter)
HOLD_LOW_SEC  = 2.0  # Seconds to hold LOW

print("\n" + "=" * 65)
print(" RASPBERRY PI 5 <-> ESP32 MULTIMETER & PIN TESTING UTILITY")
print("=" * 65)
print("This script will test each pin one-by-one in an infinite loop.")
print(f"HIGH Phase: {HOLD_HIGH_SEC} seconds (multimeter should read ~3.3V)")
print(f"LOW Phase:  {HOLD_LOW_SEC} seconds (multimeter should read ~0.0V)")
print("Press Ctrl+C to exit.\n" + "=" * 65)

try:
    cycle = 1
    while True:
        print(f"\n--- [CYCLE {cycle}] Starting Sequential Pin Test ---")
        for name, pin, phys_pin, esp_target in PINS:
            # 1. Drive HIGH
            print(f"\n>>> [TESTING {name}] Driving Pi BCM {pin} ({phys_pin}) -> {esp_target} HIGH (3.3V)")
            print(f"    Hold probe on {phys_pin} or {esp_target}. Waiting {HOLD_HIGH_SEC} seconds...")
            lgpio.gpio_write(handle, pin, 1)
            time.sleep(HOLD_HIGH_SEC)

            # 2. Drive LOW
            print(f">>> [TESTING {name}] Driving Pi BCM {pin} ({phys_pin}) -> {esp_target} LOW (0.0V)")
            lgpio.gpio_write(handle, pin, 0)
            time.sleep(HOLD_LOW_SEC)

        # Check FEEDBACK_1 line
        fb_state = lgpio.gpio_read(handle, PIN_FB1)
        print(f"\n[FEEDBACK READ] Pi BCM 23 (Physical Pin 16 <- ESP GPIO 2) current level: {fb_state} ({'HIGH' if fb_state else 'LOW'})")

        cycle += 1
        time.sleep(1.0)

except KeyboardInterrupt:
    print("\n\nStopping test and setting all pins LOW...")
    lgpio.gpio_write(handle, PIN_CLOCK, 0)
    lgpio.gpio_write(handle, PIN_DATA, 0)
    lgpio.gpio_write(handle, PIN_SET, 0)
    lgpio.gpiochip_close(handle)
    print("Test finished cleanly.")
