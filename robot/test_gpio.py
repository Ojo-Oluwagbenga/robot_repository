#!/usr/bin/env python3
"""
Diagnostic utility for Raspberry Pi 5 GPIO.
Detects available gpiochips, labels, line counts, and tests claiming bus pins.
"""

import os
import sys
import glob

# Ensure system packages are searched if running inside a virtualenv
system_paths = ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages")
for sp in system_paths:
    if sp not in sys.path and os.path.exists(sp):
        sys.path.append(sp)

try:
    import lgpio
    print("[OK] lgpio library loaded successfully.")
except ImportError as e:
    print(f"[ERROR] lgpio import failed: {e}")
    sys.exit(1)

# Pins from config.py
PINS_TO_TEST = [
    (17, "CLOCK (Output)"),
    (27, "DATA (Output)"),
    (22, "SET (Output)"),
    (23, "FEEDBACK_1 (Input)"),
    (24, "FEEDBACK_2 (Input)"),
    (26, "FEEDBACK_3 (Input)"),
    (5,  "FEEDBACK_4 (Input)"),
    (6,  "FEEDBACK_5 (Input)"),
]

print("\n--- Scanning System GPIO Devices ---")
chips = sorted(glob.glob("/dev/gpiochip*"))
if not chips:
    print("[WARN] No /dev/gpiochip* devices found!")
else:
    print(f"Found GPIO device files: {chips}")

working_chip = None

for chip_path in chips:
    try:
        chip_num = int(chip_path.replace("/dev/gpiochip", ""))
    except ValueError:
        continue

    print(f"\nTesting {chip_path} (ID: {chip_num})...")
    try:
        handle = lgpio.gpiochip_open(chip_num)
    except Exception as e:
        print(f"  -> Could not open {chip_path}: {e}")
        continue

    try:
        lines, name, label = lgpio.gpiochip_get_info(handle)
        print(f"  -> Name: {name}, Label: '{label}', Total Lines: {lines}")
    except Exception as e:
        print(f"  -> Opened, but get_info failed: {e}")

    # Test claiming pins
    all_pins_ok = True
    for pin, desc in PINS_TO_TEST:
        try:
            # Try claiming and freeing
            if "Output" in desc:
                lgpio.gpio_claim_output(handle, pin, 0)
            else:
                lgpio.gpio_claim_input(handle, pin)
            lgpio.gpio_free(handle, pin)
            print(f"  -> Pin {pin:2d} ({desc:20s}): OK")
        except Exception as e:
            print(f"  -> Pin {pin:2d} ({desc:20s}): FAILED ({e})")
            all_pins_ok = False

    try:
        lgpio.gpiochip_close(handle)
    except Exception:
        pass

    if all_pins_ok:
        if working_chip is None or chip_num in [0, 4]:
            working_chip = chip_num
        print(f"  *** SUCCESS: {chip_path} claimed all pins! {'(RP1 40-Pin Header Controller)' if chip_num in [0, 4] else '(Internal SoC block)'} ***")

print("\n--- Summary ---")
if working_chip is not None:
    print(f"Recommended chip for config.py: gpiochip{working_chip}")
else:
    print("No chip successfully claimed all pins. Check if another process is running:")
    print("  Run: pgrep -fa python")
    print("  Or run with sudo: sudo python3 test_gpio.py")
