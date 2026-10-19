#!/usr/bin/env python3
"""
Raspberry Pi 5 Motor & Encoder Hardware Test Script
====================================================
Hardware Pinout:
  - Motor IN1:        BCM GPIO 16  (Physical Pin 36)
  - Motor IN2:        BCM GPIO 20  (Physical Pin 38)
  - Encoder Pin:      BCM GPIO 21  (Physical Pin 40)
  - Relay Gateway:    BCM GPIO 26  (Physical Pin 37 - Motor Power Gate)
  - Common GND:       GND          (Physical Pin 39 or Pin 6, 14, 20, 25, 30, 34)

Features:
  - Automatically turns ON the relay gateway before motor tests and turns it OFF afterwards.
  - Tests Forward (CW) and Reverse (CCW) motor directions.
  - Monitors and counts encoder pulses in real-time via hardware edge interrupts.
  - Includes safe shutdown on Ctrl+C (brakes motor and turns off relay gateway).
  - Uses native Raspberry Pi 5 `lgpio` with automatic fallback to `gpiozero` / `RPi.GPIO`.
"""

import os
import sys
import glob
import time
import threading

# Add system dist-packages path for Raspberry Pi OS virtual environments
for sp in ["/usr/lib/python3/dist-packages"] + glob.glob("/usr/lib/python3*/dist-packages"):
    if sp not in sys.path and os.path.exists(sp):
        sys.path.append(sp)

# ==============================================================================
# PIN DEFINITIONS (BCM Numbering)
# ==============================================================================
PIN_IN1 = 16          # Motor IN1 (Physical Pin 36)
PIN_IN2 = 20          # Motor IN2 (Physical Pin 38)
PIN_ENCODER = 21      # Encoder signal (Physical Pin 40)
PIN_RELAY = 26        # Relay / Motor Power Gateway (Physical Pin 37)

DEFAULT_RUN_TIME = 2.5  # Seconds to run in each direction during auto test

# ==============================================================================
# GPIO BACKEND INITIALIZATION (lgpio for Pi 5, with fallbacks)
# ==============================================================================
BACKEND = None
lgpio_handle = None
pulse_counter = 0
pulse_lock = threading.Lock()

try:
    import lgpio
    for chip in [0, 4]:
        try:
            cand = lgpio.gpiochip_open(chip)
            lgpio.gpio_claim_output(cand, PIN_IN1, 0)
            lgpio.gpio_free(cand, PIN_IN1)
            lgpio_handle = cand
            BACKEND = "lgpio"
            print(f"[OK] Using lgpio on /dev/gpiochip{chip} (Raspberry Pi 5 RP1 Controller)")
            break
        except Exception:
            continue
    if BACKEND is None and lgpio_handle is None:
        raise RuntimeError("Could not open gpiochip 0 or 4 with lgpio")
except Exception:
    try:
        from gpiozero import DigitalOutputDevice, DigitalInputDevice
        BACKEND = "gpiozero"
        print("[OK] Using gpiozero backend")
    except ImportError:
        try:
            import RPi.GPIO as GPIO
            BACKEND = "RPi.GPIO"
            print("[OK] Using RPi.GPIO backend")
        except ImportError:
            print("[ERROR] No supported GPIO library found.")
            print("Please install lgpio: sudo apt install python3-lgpio")
            sys.exit(1)


# ==============================================================================
# HARDWARE CONTROLLER ABSTRACTION
# ==============================================================================
class MotorHarness:
    def __init__(self):
        self.encoder_cb = None
        self.gz_in1 = None
        self.gz_in2 = None
        self.gz_relay = None
        self.gz_enc = None

        if BACKEND == "lgpio":
            # Claim outputs: motor lines LOW, relay gate HIGH (VM active)
            lgpio.gpio_claim_output(lgpio_handle, PIN_IN1, 0)
            lgpio.gpio_claim_output(lgpio_handle, PIN_IN2, 0)
            lgpio.gpio_claim_output(lgpio_handle, PIN_RELAY, 1)

            # Claim encoder input with pull-up
            try:
                lgpio.gpio_claim_input(lgpio_handle, PIN_ENCODER, lgpio.SET_PULL_UP)
            except Exception:
                lgpio.gpio_claim_input(lgpio_handle, PIN_ENCODER)

            # Register edge callback
            self.encoder_cb = lgpio.callback(lgpio_handle, PIN_ENCODER, lgpio.BOTH_EDGES, self._lgpio_edge_cb)

        elif BACKEND == "gpiozero":
            self.gz_in1 = DigitalOutputDevice(PIN_IN1, initial_value=False)
            self.gz_in2 = DigitalOutputDevice(PIN_IN2, initial_value=False)
            self.gz_relay = DigitalOutputDevice(PIN_RELAY, initial_value=True)
            self.gz_enc = DigitalInputDevice(PIN_ENCODER, pull_up=True)
            self.gz_enc.when_activated = self._increment_pulse
            self.gz_enc.when_deactivated = self._increment_pulse

        elif BACKEND == "RPi.GPIO":
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(PIN_IN1, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(PIN_IN2, GPIO.OUT, initial=GPIO.LOW)
            GPIO.setup(PIN_RELAY, GPIO.OUT, initial=GPIO.HIGH)
            GPIO.setup(PIN_ENCODER, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(PIN_ENCODER, GPIO.BOTH, callback=lambda ch: self._increment_pulse())

        # Ensure relay is activated
        self.set_relay(True)

    def _lgpio_edge_cb(self, chip, gpio, level, timestamp):
        self._increment_pulse()

    def _increment_pulse(self):
        global pulse_counter
        with pulse_lock:
            pulse_counter += 1

    def reset_pulse_count(self):
        global pulse_counter
        with pulse_lock:
            pulse_counter = 0

    def get_pulse_count(self) -> int:
        with pulse_lock:
            return pulse_counter

    def read_encoder_pin(self) -> int:
        if BACKEND == "lgpio":
            return lgpio.gpio_read(lgpio_handle, PIN_ENCODER)
        elif BACKEND == "gpiozero":
            return 1 if self.gz_enc.is_active else 0
        elif BACKEND == "RPi.GPIO":
            return GPIO.input(PIN_ENCODER)
        return 0

    def set_relay(self, state: bool):
        val = 1 if state else 0
        if BACKEND == "lgpio":
            lgpio.gpio_write(lgpio_handle, PIN_RELAY, val)
        elif BACKEND == "gpiozero":
            self.gz_relay.value = val
        elif BACKEND == "RPi.GPIO":
            GPIO.output(PIN_RELAY, val)

    def set_motor(self, in1: bool, in2: bool):
        val1 = 1 if in1 else 0
        val2 = 1 if in2 else 0
        if BACKEND == "lgpio":
            lgpio.gpio_write(lgpio_handle, PIN_IN1, val1)
            lgpio.gpio_write(lgpio_handle, PIN_IN2, val2)
        elif BACKEND == "gpiozero":
            self.gz_in1.value = val1
            self.gz_in2.value = val2
        elif BACKEND == "RPi.GPIO":
            GPIO.output(PIN_IN1, val1)
            GPIO.output(PIN_IN2, val2)

    def forward(self):
        """Turn motor Forward (IN1=HIGH, IN2=LOW)"""
        self.set_motor(True, False)

    def reverse(self):
        """Turn motor Reverse (IN1=LOW, IN2=HIGH)"""
        self.set_motor(False, True)

    def brake(self):
        """Active brake (IN1=LOW, IN2=LOW)"""
        self.set_motor(False, False)

    def cleanup(self):
        """Ensure everything is turned off and GPIO lines freed"""
        try:
            self.brake()
            self.set_relay(False)
        except Exception:
            pass

        if BACKEND == "lgpio" and lgpio_handle is not None:
            if self.encoder_cb:
                try:
                    self.encoder_cb.cancel()
                except Exception:
                    pass
            for pin in [PIN_IN1, PIN_IN2, PIN_RELAY, PIN_ENCODER]:
                try:
                    lgpio.gpio_free(lgpio_handle, pin)
                except Exception:
                    pass
            try:
                lgpio.gpiochip_close(lgpio_handle)
            except Exception:
                pass
        elif BACKEND == "gpiozero":
            for dev in [self.gz_in1, self.gz_in2, self.gz_relay, self.gz_enc]:
                if dev:
                    try:
                        dev.close()
                    except Exception:
                        pass
        elif BACKEND == "RPi.GPIO":
            GPIO.cleanup()


# ==============================================================================
# TEST RUNNERS
# ==============================================================================
def print_banner():
    print("\n" + "=" * 70)
    print("      RASPBERRY PI 5 MOTOR & ENCODER TEST HARNESS")
    print("=" * 70)
    print("PIN MAPPING:")
    print(f"  - Relay Gateway (Power Gate): BCM {PIN_RELAY:<2} | Physical Pin 37  [ACTIVE HIGH]")
    print(f"  - Motor IN1:                  BCM {PIN_IN1:<2} | Physical Pin 36  [OUTPUT]")
    print(f"  - Motor IN2:                  BCM {PIN_IN2:<2} | Physical Pin 38  [OUTPUT]")
    print(f"  - Encoder Input:              BCM {PIN_ENCODER:<2} | Physical Pin 40  [INPUT PULL-UP]")
    print("  - Common Ground:              GND    | Physical Pin 39 (or 6, 14, 20)")
    print("=" * 70 + "\n")


def run_motor_phase(harness: MotorHarness, name: str, forward_dir: bool, duration: float):
    print(f"\n>>> [TEST] Running Motor: {name.upper()} for {duration:.1f}s <<<")
    harness.reset_pulse_count()
    start_time = time.time()
    last_print = start_time
    last_pulses = 0

    if forward_dir:
        harness.forward()
    else:
        harness.reverse()

    try:
        while True:
            now = time.time()
            elapsed = now - start_time
            if elapsed >= duration:
                break

            # Live status update every 100ms
            if now - last_print >= 0.1:
                current_pulses = harness.get_pulse_count()
                rate = (current_pulses - last_pulses) / (now - last_print)
                last_pulses = current_pulses
                last_print = now
                pin_val = harness.read_encoder_pin()
                sys.stdout.write(
                    f"\r  [{name}] Time: {elapsed:4.1f}s / {duration:.1f}s | "
                    f"Pulses: {current_pulses:5d} | "
                    f"Speed: {rate:5.0f} p/s | Pin Level: {pin_val}  "
                )
                sys.stdout.flush()

            time.sleep(0.01)

    finally:
        harness.brake()

    final_pulses = harness.get_pulse_count()
    avg_rate = final_pulses / duration if duration > 0 else 0
    print(f"\r  [{name}] FINISHED: {duration:.1f}s | Total Pulses: {final_pulses} | Avg Rate: {avg_rate:.1f} pulses/sec")
    if final_pulses == 0:
        print(f"  [!] WARNING: 0 pulses detected on GPIO {PIN_ENCODER}!")
        print("      * Check if encoder VCC, GND, and signal wires are connected.")
        print(f"      * Current static pin read: {harness.read_encoder_pin()}")
    else:
        print(f"  [+] SUCCESS: Encoder successfully counted {final_pulses} pulses!")


def run_full_test(harness: MotorHarness, duration: float = DEFAULT_RUN_TIME):
    print("\n" + "#" * 60)
    print("           STARTING AUTOMATED MOTOR TEST CYCLE")
    print("#" * 60)

    # 1. Turn ON Relay Gateway
    print("\n[STEP 1] Activating Relay Gateway (GPIO 26 -> HIGH)...")
    harness.set_relay(True)
    time.sleep(0.15)  # Allow relay contacts to stabilize
    print("[OK] Relay Gateway is ON (Motor power rail energized).")

    # 2. Test Forward
    run_motor_phase(harness, "FORWARD (CW)", forward_dir=True, duration=duration)

    # 3. Pause
    print("\n[STEP 2] Pausing for 1.0s (Braked)...")
    time.sleep(1.0)

    # 4. Test Reverse
    run_motor_phase(harness, "REVERSE (CCW)", forward_dir=False, duration=duration)

    # 5. Brake and Turn OFF Relay Gateway
    print("\n[STEP 3] Braking motor and turning OFF Relay Gateway (GPIO 26 -> LOW)...")
    harness.brake()
    harness.set_relay(False)
    print("[OK] Relay Gateway is OFF (Motor power safely disconnected).")
    print("\n" + "=" * 60)
    print("                    TEST CYCLE COMPLETE")
    print("=" * 60 + "\n")


def live_encoder_monitor(harness: MotorHarness):
    print("\n>>> LIVE ENCODER MONITOR (Turn motor shaft by hand) <<<")
    print("Press Ctrl+C to return to menu.\n")
    harness.reset_pulse_count()
    last_count = -1
    try:
        while True:
            cnt = harness.get_pulse_count()
            lvl = harness.read_encoder_pin()
            if cnt != last_count:
                print(f"  Encoder Pin: Level={lvl} | Total Pulses Counted: {cnt}")
                last_count = cnt
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nExiting live monitor.")


def interactive_menu(harness: MotorHarness):
    while True:
        print("\n--- MOTOR TEST MENU ---")
        print("  1. Run Full Automated Test (Relay ON -> Forward -> Reverse -> Relay OFF)")
        print("  2. Run Forward Only (Relay ON)")
        print("  3. Run Reverse Only (Relay ON)")
        print("  4. Live Encoder Monitor (Rotate shaft by hand)")
        print("  5. Toggle Relay Gateway (Manual ON/OFF)")
        print("  6. Exit")

        choice = input("Select an option [1-6]: ").strip()
        if choice == "1":
            run_full_test(harness, DEFAULT_RUN_TIME)
        elif choice == "2":
            harness.set_relay(True)
            time.sleep(0.1)
            run_motor_phase(harness, "FORWARD (CW)", True, DEFAULT_RUN_TIME)
            harness.set_relay(False)
        elif choice == "3":
            harness.set_relay(True)
            time.sleep(0.1)
            run_motor_phase(harness, "REVERSE (CCW)", False, DEFAULT_RUN_TIME)
            harness.set_relay(False)
        elif choice == "4":
            live_encoder_monitor(harness)
        elif choice == "5":
            cur = input("Set Relay Gateway state (1=ON, 0=OFF): ").strip()
            harness.set_relay(cur == "1")
            print(f"Relay Gateway is now {'ON' if cur == '1' else 'OFF'}.")
        elif choice == "6" or choice.lower() == "q":
            break
        else:
            print("[!] Invalid option. Enter 1-6.")


def main():
    print_banner()
    harness = MotorHarness()

    try:
        # Check if run with --auto argument
        if len(sys.argv) > 1 and sys.argv[1] in ["--auto", "-a"]:
            run_full_test(harness, DEFAULT_RUN_TIME)
        else:
            # Run the automated test once first, then offer the menu
            run_full_test(harness, DEFAULT_RUN_TIME)
            interactive_menu(harness)

    except KeyboardInterrupt:
        print("\n[!] User interrupted test (Ctrl+C). Performing emergency shutdown...")
    finally:
        harness.cleanup()
        print("[SHUTDOWN] Motor braked, relay turned OFF, GPIO lines released.\n")


if __name__ == "__main__":
    main()
