#!/usr/bin/env python3
"""
Interactive Raspberry Pi 5 <-> ESP32 Pin Connection & Multimeter Test
=====================================================================
Step-by-step interactive pin testing. The script will ONLY advance to
the next action or pin when you press [Enter].

CRITICAL: On Raspberry Pi 5, the 40-pin header is controlled by RP1
at /dev/gpiochip0 (or /dev/gpiochip4). gpiochip10 is an internal CPU
chip not connected to the header pins!
"""

import os
import sys
import glob
import time

# Ensure system packages are loaded if inside a virtual environment
for sp in ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages"):
    if sp not in sys.path and os.path.exists(sp):
        sys.path.append(sp)

try:
    import lgpio
    print("[OK] lgpio library loaded successfully.")
except ImportError:
    print("[ERROR] lgpio not found. Run: sudo apt install python3-lgpio")
    sys.exit(1)

# Raspberry Pi 5 BCM pin numbers
PIN_CLOCK = 17         # Physical Pin 11 -> ESP32 GPIO 17
PIN_DATA  = 27         # Physical Pin 13 -> ESP32 GPIO 16
PIN_SET   = 22         # Physical Pin 15 -> ESP32 GPIO 4
PIN_POWER_ENABLE = 26  # Physical Pin 37 -> Motor Power System Enable (BCM 26)

# Feedback inputs from each ESP32 (GPIO 15 on ESP32)
FEEDBACK_PINS = {
    1: 23,  # Physical Pin 16 (BCM 23) <- ESP #1 GPIO 15
    2: 4,   # Physical Pin 7  (BCM 4)  <- ESP #2 GPIO 15
    3: 24,  # Physical Pin 18 (BCM 24) <- ESP #3 GPIO 15
    4: 25,  # Physical Pin 22 (BCM 25) <- ESP #4 GPIO 15
    5: 6,   # Physical Pin 31 (BCM 6)  <- ESP #5 GPIO 15
}

# Open RP1 southbridge gpiochip (0 on current Pi 5 kernels, or 4 on early kernels)
handle = None
chosen_chip = None
for chip in [0, 4]:
    try:
        cand = lgpio.gpiochip_open(chip)
        lgpio.gpio_claim_output(cand, PIN_CLOCK, 0)
        lgpio.gpio_free(cand, PIN_CLOCK)
        handle = cand
        chosen_chip = chip
        print(f"[OK] Successfully opened /dev/gpiochip{chip} (RP1 40-Pin Header Controller)")
        break
    except Exception:
        continue

if handle is None:
    print("[ERROR] Could not open RP1 gpiochip (tried chip 0 and 4).")
    print("If another script is running, stop it first or run with sudo.")
    sys.exit(1)

# Claim output pins (initialize to LOW)
lgpio.gpio_claim_output(handle, PIN_CLOCK, 0)
lgpio.gpio_claim_output(handle, PIN_DATA, 0)
lgpio.gpio_claim_output(handle, PIN_SET, 0)
lgpio.gpio_claim_output(handle, PIN_POWER_ENABLE, 0)

# Claim feedback pins as inputs with pull-down
for esp_id, pin in FEEDBACK_PINS.items():
    try:
        lgpio.gpio_claim_input(handle, pin, lgpio.SET_PULL_DOWN)
    except Exception:
        lgpio.gpio_claim_input(handle, pin)

TEST_PINS = [
    ("CLOCK", PIN_CLOCK, "Physical Pin 11 (Row 1, 6th pin)", "ESP32 GPIO 17"),
    ("DATA",  PIN_DATA,  "Physical Pin 13 (Row 1, 7th pin)", "ESP32 GPIO 16"),
    ("SET",   PIN_SET,   "Physical Pin 15 (Row 1, 8th pin)", "ESP32 GPIO 4"),
    ("POWER_ENABLE", PIN_POWER_ENABLE, "Physical Pin 37 (Row 1, 19th pin)", "Motor Power Relay / Enable Gate (BCM 26)"),
]

def print_banner():
    print("\n" + "=" * 72)
    print("     INTERACTIVE PI 5 <-> ESP32 MULTIMETER & PIN CONNECTION TEST")
    print("=" * 72)
    print("PHYSICAL PIN ASSIGNMENTS:")
    print("  Outputs from Pi:")
    print("    - CLOCK:        Physical Pin 11 (BCM 17) -----> ESP32 GPIO 17")
    print("    - DATA:         Physical Pin 13 (BCM 27) -----> ESP32 GPIO 16")
    print("    - SET:          Physical Pin 15 (BCM 22) -----> ESP32 GPIO 4")
    print("    - MOTOR ENABLE: Physical Pin 37 (BCM 26) -----> Motor Power Relay / Enable")
    print("  Inputs to Pi (Feedback lines from ESP32 GPIO 15):")
    print("    - ESP 1 FB:     Physical Pin 16 (BCM 23) <----- ESP1 GPIO 15")
    print("    - ESP 2 FB:     Physical Pin 7  (BCM 4)  <----- ESP2 GPIO 15")
    print("    - ESP 3 FB:     Physical Pin 18 (BCM 24) <----- ESP3 GPIO 15")
    print("    - ESP 4 FB:     Physical Pin 22 (BCM 25) <----- ESP4 GPIO 15")
    print("    - ESP 5 FB:     Physical Pin 31 (BCM 6)  <----- ESP5 GPIO 15")
    print("  Common:")
    print("    - GND:          Physical Pin 6 (or 9, 14, 20, 25, 30, 34, 39) [MANDATORY]")
    print("=" * 72 + "\n")

print_banner()

cycle = 1
try:
    while True:
        print(f"\n==================== STARTING TEST CYCLE {cycle} ====================")

        for name, pin, phys, esp_pin in TEST_PINS:
            print(f"\n>>> [TESTING {name.upper()}] <<<")
            print(f"    Line: Pi {phys}  ----->  {esp_pin}")

            # 1. Drive HIGH upon Enter
            input(f"--> Press [ENTER] to drive {name} HIGH (3.3V)... ")
            lgpio.gpio_write(handle, pin, 1)
            print(f"[+] {name} is now driven HIGH!")
            print(f"    * Multimeter Target : ~3.3 Volts")
            print(f"    * Red Probe on      : Pi {phys} or {esp_pin}")
            print(f"    * Black Probe on    : GND (Pin 6)")
            print(f"    * Status            : Active (3.3V)")

            # 2. Drive LOW upon Enter
            input(f"--> Press [ENTER] to drive {name} LOW (0.0V)... ")
            lgpio.gpio_write(handle, pin, 0)
            print(f"[-] {name} is now driven LOW!")
            print(f"    * Multimeter Target : ~0.0 Volts")
            print(f"    * Status            : Inactive (0.0V)")

            # 3. Read all FEEDBACK states after each pin test
            fb_readings = {esp: lgpio.gpio_read(handle, p) for esp, p in FEEDBACK_PINS.items()}
            print(f"[*] FEEDBACK Line States (from ESP GPIO 15):")
            for esp, val in fb_readings.items():
                print(f"    ESP {esp} (BCM {FEEDBACK_PINS[esp]}): {val} ({'HIGH' if val else 'LOW'})")

            input(f"--> Press [ENTER] to proceed to next pin... ")

        print(f"\n==================== COMPLETED CYCLE {cycle} ====================")
        cycle += 1
        input("--> Press [ENTER] to start another test cycle (or Ctrl+C to finish)... ")

except KeyboardInterrupt:
    print("\n\nStopping test. Resetting all pins to LOW (0V)...")
    try:
        lgpio.gpio_write(handle, PIN_CLOCK, 0)
        lgpio.gpio_write(handle, PIN_DATA, 0)
        lgpio.gpio_write(handle, PIN_SET, 0)
        lgpio.gpio_write(handle, PIN_POWER_ENABLE, 0)
        for p in [PIN_CLOCK, PIN_DATA, PIN_SET, PIN_POWER_ENABLE] + list(FEEDBACK_PINS.values()):
            try:
                lgpio.gpio_free(handle, p)
            except Exception:
                pass
        lgpio.gpiochip_close(handle)
    except Exception:
        pass
    print("All pins set to LOW, freed, and GPIO handle closed cleanly. Goodbye!")
