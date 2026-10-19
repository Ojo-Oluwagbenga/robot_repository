#!/usr/bin/env python3
"""
Interactive Wi-Fi JSON Packet Tester & Log Monitor.
=============================================================================
Allows sending motor commands and monitoring what the Pi is sending out
and what each ESP is sending in over the Wi-Fi network.

Usage:
  # 1. Send test command (e.g. ESP 1, Motor 2, 300 pulses CW):
  python test_wifi_comm.py --esp 1 --motor 2 --pulse 300 --dir 0

  # 2. Send emergency stop:
  python test_wifi_comm.py --stop

  # 3. Live monitor traffic on UDP port 5005:
  python test_wifi_comm.py --monitor

  # 4. View and tail the communication log file:
  python test_wifi_comm.py --log
=============================================================================
"""

import sys
import time
import json
import socket
import argparse
from pathlib import Path

# Add robot folder to sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR / "robot"
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

from debug.wifi_logger import wifi_comm_logger, DEFAULT_COMM_LOG
from communication.wifi_bus import get_wifi_bus


def monitor_live_udp(port: int = 5005):
    """Listen for and display all live UDP JSON packets."""
    print("\n" + "=" * 70)
    print(f"       LIVE WI-FI UDP PACKET MONITOR (PORT {port})")
    print("=" * 70)
    print(f"[*] Listening on 0.0.0.0:{port} (Press Ctrl+C to stop)...")
    print("-" * 70)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass
    sock.bind(("0.0.0.0", port))

    while True:
        try:
            data, (addr, p) = sock.recvfrom(4096)
            text = data.decode("utf-8", errors="ignore").strip()
            ts = time.strftime("%H:%M:%S")

            try:
                pkt = json.loads(text)
                if "totalpulse" in pkt:
                    print(f"[{ts}] \033[92m[RX <- ESP {pkt.get('esp')} ({addr})]\033[0m {text}")
                elif "pulse" in pkt:
                    print(f"[{ts}] \033[94m[TX -> BROADCAST ({addr})]\033[0m {text}")
                else:
                    print(f"[{ts}] [PACKET ({addr})] {text}")
            except Exception:
                print(f"[{ts}] [{addr}] {text}")

        except KeyboardInterrupt:
            print("\n[*] Monitor stopped.")
            break
        except Exception as e:
            print(f"[!] Error: {e}")
            time.sleep(0.1)


def show_log_file(tail_lines: int = 25):
    """Print the contents of logs/esp_comm.log."""
    log_path = DEFAULT_COMM_LOG
    print("\n" + "=" * 70)
    print(f"       COMMUNICATION LOG FILE: {log_path}")
    print("=" * 70)

    if not log_path.exists() or log_path.stat().st_size == 0:
        print(f"Log file '{log_path}' is currently empty. Run a command to generate entries.\n")
        return

    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    count = min(tail_lines, len(lines))
    print(f"Showing last {count} of {len(lines)} entries:\n")
    for line in lines[-count:]:
        sys.stdout.write(line)
    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Robot Wi-Fi JSON Packet Tester & Log Monitor")
    parser.add_argument("--esp", type=int, default=None, help="ESP ID (1 to 4)")
    parser.add_argument("--motor", type=int, default=1, help="Motor ID (1 to 4)")
    parser.add_argument("--pulse", type=int, default=300, help="Pulse count (default 300)")
    parser.add_argument("--dir", type=int, default=0, help="Direction (0=CW, 1=CCW)")
    parser.add_argument("--id", type=str, default=None, help="Instruction ID")
    parser.add_argument("--stop", action="store_true", help="Send emergency stop broadcast")
    parser.add_argument("--ping", action="store_true", help="Send ping/status broadcast")
    parser.add_argument("--monitor", action="store_true", help="Live stream UDP packets")
    parser.add_argument("--log", action="store_true", help="Display communication log file")
    args = parser.parse_args()

    if args.log:
        show_log_file()
        return

    if args.monitor:
        monitor_live_udp()
        return

    bus = get_wifi_bus()

    if args.stop:
        inst_id = bus.send_stop(instruction_id=args.id)
        print(f"[+] Emergency STOP broadcasted (ID: {inst_id})")
        time.sleep(0.5)
        show_log_file(tail_lines=5)
        return

    if args.ping:
        inst_id = bus.send_ping(esp=args.esp or 0, instruction_id=args.id)
        print(f"[+] PING broadcasted (ID: {inst_id})")
        time.sleep(0.5)
        show_log_file(tail_lines=5)
        return

    if args.esp is not None:
        inst_id = args.id or f"test_{int(time.time() * 1000) % 100000:05d}"
        print(f"\n[*] Broadcasting JSON Command:")
        print(f"    ESP          : {args.esp}")
        print(f"    Motor        : {args.motor}")
        print(f"    Pulses       : {args.pulse}")
        print(f"    Direction    : {args.dir} ({'CW' if args.dir == 0 else 'CCW'})")
        print(f"    InstructionID: {inst_id}")

        bus.broadcast_command(
            esp=args.esp,
            motor=args.motor,
            pulse=args.pulse,
            dir=args.dir,
            instruction_id=inst_id
        )

        print("[*] Command sent! Waiting up to 5s for ESP response packet...")
        resp = bus.wait_for_response(inst_id, timeout=5.0)
        if resp:
            print("\n\033[92m[RESPONSE RECEIVED FROM ESP]\033[0m")
            print(f"    ESP        : {resp.get('esp')}")
            print(f"    Motor      : {resp.get('motor')}")
            print(f"    TotalPulse : {resp.get('totalpulse')}")
            print(f"    Status     : {resp.get('status')}")
            print(f"    ID         : {resp.get('instructionID')}")
        else:
            print("[!] No response received within 5s (ESP may be offline or still moving).")

        show_log_file(tail_lines=5)
        return

    # If no arguments provided, show usage and recent log
    print("No command specified. Options:")
    print("  python test_wifi_comm.py --esp 1 --motor 2 --pulse 300 --dir 0")
    print("  python test_wifi_comm.py --stop")
    print("  python test_wifi_comm.py --monitor")
    print("  python test_wifi_comm.py --log")
    show_log_file(tail_lines=10)


if __name__ == "__main__":
    main()
