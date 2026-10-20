#!/usr/bin/env python3
"""
Per-Motor Brake Profile Controller & Hardware Tester for Raspberry Pi 5 -> ESP32.
=============================================================================
Defines per-motor brake profile schedules, validates configuration,
transmits profiles over Wi-Fi UDP (port 5005) to ESP32 motor controllers,
monitors real-time done/jam feedback, and supports pushing firmware over OTA.
=============================================================================
"""

import sys
import os
import time
import json
import socket
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

# ==============================================================================
# RUNTIME CONFIG (Hardcoded block, editable before each run)
# ==============================================================================
CONFIG = {
  "runtime_motors": [
    {
      "motor": 5, "dir": 1, "pulse": 1000,
      "profile": {
        "A": {"duration_type": "pulse", "start": 0,   "end": 100, "brake_count": 30, "brake_ms": 5},
        "B": {"duration_type": "time",  "start": 300, "end": 400, "brake_count": 40, "brake_ms": 3}
      }
    },
    {"motor": 2, "dir": 0, "pulse": 800, "profile": {}}
  ]
}

DEFAULT_UDP_PORT = 5005
DEFAULT_OTA_PORT = 80
DEFAULT_ESP_HOSTNAMES = {
    1: "esp32-robot-1.local",
    2: "esp32-robot-2.local",
    3: "esp32-robot-3.local"
}


# ==============================================================================
# BRAKE SCHEDULE COMPUTATION & VALIDATION
# ==============================================================================
def compute_brake_schedule(start: int, end: int, brake_count: int) -> List[int]:
    """
    Computes brake positions according to profile rules:
    - Window is split into brake_count equal slots.
    - Brake k fires at: position_k = start + k * (end - start) // brake_count, for k = 0 .. brake_count-1.
    - Window end is exclusive.
    - If brake_count > (end - start), brake once at every position and drop extras.
    """
    w = end - start
    if brake_count <= 0 or w <= 0:
        return []
    if brake_count > w:
        return [start + i for i in range(w)]
    return [start + (k * w) // brake_count for k in range(brake_count)]


def validate_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    Validates runtime motor configuration against rules:
    - pulse > 0
    - dir in (0, 1)
    - window start < end
    - brake_count >= 0
    - duration_type in ('pulse', 'time')
    """
    errors: List[str] = []
    if "runtime_motors" not in config or not isinstance(config["runtime_motors"], list):
        errors.append("Config missing 'runtime_motors' list")
        return False, errors

    for idx, m in enumerate(config["runtime_motors"]):
        m_id = m.get("motor")
        if not isinstance(m_id, int) or m_id < 1 or m_id > 6:
            errors.append(f"Motor[{idx}]: 'motor' must be an integer between 1 and 6 (got {m_id})")

        dir_val = m.get("dir")
        if dir_val not in (0, 1):
            errors.append(f"Motor[{m_id}]: 'dir' must be 0 (CW) or 1 (CCW) (got {dir_val})")

        pulse = m.get("pulse")
        if not isinstance(pulse, int) or pulse <= 0:
            errors.append(f"Motor[{m_id}]: 'pulse' must be an integer > 0 (got {pulse})")

        profile = m.get("profile", {})
        if not isinstance(profile, dict):
            errors.append(f"Motor[{m_id}]: 'profile' must be a dictionary")
            continue

        for sec_name, sec in profile.items():
            start = sec.get("start")
            end = sec.get("end")
            count = sec.get("brake_count")
            dtype = sec.get("duration_type")
            b_ms = sec.get("brake_ms", 0)

            if not isinstance(start, int) or start < 0:
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: 'start' must be integer >= 0")
            if not isinstance(end, int):
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: 'end' must be an integer")
            if isinstance(start, int) and isinstance(end, int) and start >= end:
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: window start ({start}) must be < end ({end})")

            if not isinstance(count, int) or count < 0:
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: 'brake_count' must be integer >= 0 (got {count})")

            if dtype not in ("pulse", "time"):
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: 'duration_type' must be 'pulse' or 'time' (got '{dtype}')")

            if not isinstance(b_ms, int) or b_ms < 0:
                errors.append(f"Motor[{m_id}] Sec[{sec_name}]: 'brake_ms' must be integer >= 0")

    return (len(errors) == 0), errors


# ==============================================================================
# TEST RUNNER & FEEDBACK MONITOR
# ==============================================================================
def run_test(config: Dict[str, Any] = CONFIG, dry_run: bool = True, timeout: float = 15.0) -> bool:
    """
    Executes profile test:
    1. Validates configuration.
    2. Prints computed brake schedule for each motor and section.
    3. If dry_run is False, transmits UDP packet to ESP32 and monitors done/jam/error feedback.
    """
    print("\n" + "=" * 78)
    print("      PER-MOTOR BRAKE PROFILE CONTROLLER & TEST RUNNER")
    print(f"      Mode: {'DRY RUN (Simulation & Schedule Validation)' if dry_run else 'LIVE EXECUTION (Transmitting to ESP32)'}")
    print("=" * 78)

    # 1. Validation
    valid, errors = validate_config(config)
    if not valid:
        print("\n[VALIDATION FAILED]")
        for err in errors:
            print(f"  [X] {err}")
        print("\nAborting test due to invalid configuration.\n")
        return False

    print("\n[OK] Config Validation: PASSED (all rules satisfied)")

    # 2. Print Computed Schedules
    print("\n" + "-" * 78)
    print(" COMPUTED BRAKE SCHEDULES PER MOTOR:")
    print("-" * 78)

    for m in config["runtime_motors"]:
        m_id = m["motor"]
        dir_val = m["dir"]
        dir_name = "0 (CW / Forward)" if dir_val == 0 else "1 (CCW / Reverse)"
        pulse = m["pulse"]
        profile = m.get("profile", {})

        print(f"\n> MOTOR {m_id}: Total Target Pulses = {pulse} | Direction = {dir_name}")
        if not profile:
            print("    Profile: Empty / Default -> Normal run, full drive, no braking until target.")
            continue

        for sec_name, sec in sorted(profile.items()):
            dtype = sec["duration_type"]
            start = sec["start"]
            end = sec["end"]
            count = sec["brake_count"]
            b_ms = sec.get("brake_ms", 0)

            positions = compute_brake_schedule(start, end, count)
            unit = "pulses" if dtype == "pulse" else "ms"

            print(f"    * Section '{sec_name}':")
            print(f"        Type        : {dtype.upper()} based ({unit})")
            print(f"        Window      : [{start}, {end}) (size: {end - start} {unit})")
            print(f"        Brake Count : {count} requested -> {len(positions)} scheduled")
            print(f"        Brake Hold  : {b_ms} ms per brake")
            print(f"        Schedule ({len(positions)} positions):")
            # Format nicely in rows of 10
            pos_strs = [str(p) for p in positions]
            for i in range(0, len(pos_strs), 10):
                chunk = ", ".join(pos_strs[i:i + 10])
                print(f"          [{i:02d}..{min(i + 9, len(pos_strs) - 1):02d}]: {chunk}")

    print("\n" + "-" * 78)

    if dry_run:
        print("[*] Dry-run complete. All schedules computed successfully.")
        print("    (Set dry_run=False or pass --run to broadcast to physical ESP32 boards)")
        print("=" * 78 + "\n")
        return True

    # 3. Live Execution: Broadcast over UDP and wait for feedback
    print(f"\n[*] Broadcasting profile packet to ESP32 over Wi-Fi (UDP port {DEFAULT_UDP_PORT})...")
    payload = json.dumps(config).encode("utf-8")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except Exception:
            pass

    try:
        sock.bind(("0.0.0.0", DEFAULT_UDP_PORT))
    except Exception as e:
        print(f"[!] Warning: Could not bind listening socket on port {DEFAULT_UDP_PORT}: {e}")

    sock.settimeout(0.5)

    # Broadcast to network
    broadcast_addrs = ["255.255.255.255", "<broadcast>"]
    for b_ip in broadcast_addrs:
        try:
            sock.sendto(payload, (b_ip, DEFAULT_UDP_PORT))
        except Exception:
            pass

    print("[OK] Packet dispatched! Awaiting real-time completion/jam feedback...")
    print(f"    Pending Motors: {[m['motor'] for m in config['runtime_motors']]}")
    print("-" * 78)

    pending_motors = {m["motor"] for m in config["runtime_motors"]}
    start_time = time.time()
    success = True

    while pending_motors and (time.time() - start_time < timeout):
        try:
            data, (addr, _) = sock.recvfrom(2048)
            text = data.decode("utf-8", errors="ignore").strip()
            pkt = json.loads(text)

            pkt_m = pkt.get("motor")
            if pkt_m in pending_motors:
                status = pkt.get("status")
                esp_id = pkt.get("esp", 1)

                if status == "done":
                    pulses = pkt.get("pulses")
                    r_time = pkt.get("running_time_ms")
                    print(f"\033[92m[DONE] Motor {pkt_m} (ESP #{esp_id} from {addr}):\033[0m")
                    print(f"       Pulses Counted  : {pulses}")
                    print(f"       Driven Time     : {r_time} ms")
                    pending_motors.remove(pkt_m)

                elif status == "error":
                    reason = pkt.get("reason", "unknown")
                    if reason == "jam":
                        pulses = pkt.get("pulses")
                        r_time = pkt.get("running_time_ms")
                        sec = pkt.get("section")
                        print(f"\033[91m[JAM ERROR] Motor {pkt_m} (ESP #{esp_id} from {addr}):\033[0m")
                        print(f"            Reason          : {reason}")
                        print(f"            Pulses Counted  : {pulses}")
                        print(f"            Driven Time     : {r_time} ms")
                        print(f"            Section         : {sec}")
                    else:
                        msg = pkt.get("message", "")
                        print(f"\033[91m[ERROR] Motor {pkt_m} (ESP #{esp_id} from {addr}): {reason} - {msg}\033[0m")
                    pending_motors.remove(pkt_m)
                    success = False

        except socket.timeout:
            continue
        except Exception:
            continue

    if pending_motors:
        print(f"\033[93m[!] Timeout: No response received for motors: {list(pending_motors)}\033[0m")
        success = False

    sock.close()
    print("=" * 78 + "\n")
    return success


# ==============================================================================
# OTA PUSH TOOL (Pi side only)
# ==============================================================================
def push_ota(target: str, bin_path: str, port: int = DEFAULT_OTA_PORT) -> bool:
    """
    Pushes a compiled firmware .bin file to the ESP32 over Wi-Fi OTA.
    Keeps existing OTA setup (hostname, password, partitions) unchanged.
    """
    if requests is None:
        print("[ERROR] 'requests' library required for OTA push. Run: pip install requests")
        return False

    p = Path(bin_path)
    if not p.exists():
        print(f"[ERROR] Firmware binary not found at: {p}")
        return False

    # Resolve target IP if board name given (e.g. 'esp1', 'esp2', 'esp3')
    target_clean = target.strip().lower()
    ip = target
    if target_clean in ("1", "esp1", "esp32-robot-1"):
        ip = "10.208.131.170"
    elif target_clean in ("2", "esp2", "esp32-robot-2"):
        ip = "10.208.131.196"
    elif target_clean in ("3", "esp3", "esp32-robot-3"):
        ip = "10.208.131.119"

    url = f"http://{ip}:{port}/update"
    file_size = p.stat().st_size

    print("\n" + "=" * 70)
    print("           ESP32 WI-FI OVER-THE-AIR (OTA) DEPLOYMENT")
    print("=" * 70)
    print(f"  Target IP    : {ip}:{port}")
    print(f"  Firmware Bin : {p.name} ({file_size:,} bytes)")
    print(f"  OTA Endpoint : {url}")
    print("[*] Uploading firmware to ESP32 over Wi-Fi...")

    start_time = time.time()
    try:
        with open(p, "rb") as f:
            files = {"update": (p.name, f, "application/octet-stream")}
            resp = requests.post(url, files=files, timeout=45)

        elapsed = time.time() - start_time
        if resp.status_code == 200:
            print(f"[SUCCESS] Firmware flashed in {elapsed:.2f}s! ESP32 rebooting into new firmware.\n")
            return True
        else:
            print(f"[FAILED] HTTP {resp.status_code}: {resp.text.strip()}\n")
            return False
    except requests.exceptions.Timeout:
        print("[?] Request timed out after upload; ESP32 is likely rebooting into new code.\n")
        return True
    except Exception as e:
        print(f"[ERROR] OTA push failed: {e}\n")
        return False


# ==============================================================================
# MAIN ENTRYPOINT
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(description="Per-Motor Brake Profile & OTA Controller")
    parser.add_argument("--run", action="store_true", help="Execute live test on physical ESP32 (default: dry_run=True)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Compute and print schedule only without transmitting")
    parser.add_argument("--ota", type=str, default=None, help="Path to .bin file to flash over OTA")
    parser.add_argument("--target", type=str, default="esp1", help="Target board for OTA (esp1, esp2, esp3, or IP)")
    args = parser.parse_args()

    if args.ota:
        push_ota(args.target, args.ota)
        return

    is_dry_run = not args.run
    run_test(CONFIG, dry_run=is_dry_run)


if __name__ == "__main__":
    main()
