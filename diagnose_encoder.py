#!/usr/bin/env python3
"""
Quick Diagnostic Tool for Motor Encoder on Raspberry Pi
=======================================================
Pin: BCM GPIO 21 (Physical Pin 40 on the 40-pin header)
"""

import sys
import glob
import time

for sp in ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages"):
    if sp not in sys.path:
        sys.path.append(sp)

PIN_ENCODER = 21  # Physical Pin 40

print("=" * 65)
print("       ENCODER PIN DIAGNOSTIC & WIRING VERIFIER")
print("=" * 65)
print(f"Target Pin: BCM GPIO {PIN_ENCODER} (Physical Pin 40 - very last pin)")
print("=" * 65)

try:
    import lgpio
    h = None
    for chip in [0, 4]:
        try:
            cand = lgpio.gpiochip_open(chip)
            lgpio.gpio_claim_input(cand, PIN_ENCODER, lgpio.SET_PULL_UP)
            lgpio.gpio_free(cand, PIN_ENCODER)
            h = cand
            break
        except Exception:
            continue

    if h is None:
        print("[!] Could not open gpiochip with lgpio. Another process might be using it.")
        print("    Try running: sudo killall python3")
        sys.exit(1)

    print("\n--- 1. Testing Internal Pull Resistors ---")
    # Test Pull Up
    lgpio.gpio_claim_input(h, PIN_ENCODER, lgpio.SET_PULL_UP)
    val_up = lgpio.gpio_read(h, PIN_ENCODER)
    lgpio.gpio_free(h, PIN_ENCODER)

    # Test Pull Down
    lgpio.gpio_claim_input(h, PIN_ENCODER, lgpio.SET_PULL_DOWN)
    val_down = lgpio.gpio_read(h, PIN_ENCODER)
    lgpio.gpio_free(h, PIN_ENCODER)

    print(f"  * Reading with Pull-Up   : {val_up}  ({'HIGH - 3.3V' if val_up == 1 else 'LOW - 0V'})")
    print(f"  * Reading with Pull-Down : {val_down}  ({'HIGH - 3.3V' if val_down == 1 else 'LOW - 0V'})")

    if val_up == 0 and val_down == 0:
        print("  [!] Pin is stuck at LOW (0V). Check if encoder wire is tied to GND or shorted.")
    elif val_up == 1 and val_down == 0:
        print("  [+] Pin responds to pull resistors (floating input, waiting for encoder drive).")
    elif val_up == 1 and val_down == 1:
        print("  [!] Pin is held HIGH (3.3V) externally.")

    print("\n--- 2. Live Signal Monitor (Turn motor shaft by hand) ---")
    print("Claiming GPIO 21 with PULL_UP. Rotate the motor shaft slowly now...")
    print("Press Ctrl+C to exit.\n")

    lgpio.gpio_claim_input(h, PIN_ENCODER, lgpio.SET_PULL_UP)
    
    edge_count = 0
    def on_edge(*_):
        global edge_count
        edge_count += 1

    cb = lgpio.callback(h, PIN_ENCODER, lgpio.BOTH_EDGES, on_edge)

    last_lvl = lgpio.gpio_read(h, PIN_ENCODER)
    start_time = time.time()

    try:
        while True:
            lvl = lgpio.gpio_read(h, PIN_ENCODER)
            print(f"\r  Current Pin Level: {lvl} | Total Edges Detected: {edge_count:<5d}", end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n\nDiagnostic finished.")
    finally:
        cb.cancel()
        lgpio.gpio_free(h, PIN_ENCODER)
        lgpio.gpiochip_close(h)

except Exception as e:
    print(f"[ERROR] {e}")
