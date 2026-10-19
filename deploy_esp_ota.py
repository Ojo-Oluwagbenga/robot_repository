#!/usr/bin/env python3
"""
Over-The-Air (OTA) Firmware Deployment Tool for Robot ESP32 Controllers.
=============================================================================
Allows flashing any of the 4 ESP32 motor boards over Wi-Fi from this PC
without connecting USB cables.

Usage:
  # 1. Scan network and list all 3 ESP32 boards:
  python deploy_esp_ota.py --scan

  # 2. Ping all ESPs and verify connection:
  python deploy_esp_ota.py --ping

  # 3. Flash a compiled .bin firmware to a specific board:
  python deploy_esp_ota.py --target esp1 --bin firmware.bin
  python deploy_esp_ota.py --target esp3 --bin firmware.bin
  python deploy_esp_ota.py --target 10.128.135.119 --bin firmware.bin

  # 4. Flash all 3 ESP32 boards:
  python deploy_esp_ota.py --target all --bin firmware.bin
=============================================================================
"""

import sys
import os
import time
import json
import socket
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

# Default ESP Hostnames & Configuration
DEFAULT_UDP_PORT = 5005
DEFAULT_HTTP_PORT = 80
DEFAULT_OTA_PORT = 3232

DEFAULT_ESP_TARGETS = {
    1: {"name": "ESP #1 (Right Arm / Shoulder)", "hostname": "esp32-robot-1.local"},
    2: {"name": "ESP #2 (Left Arm / Shoulder)",  "hostname": "esp32-robot-2.local"},
    3: {"name": "ESP #3 (Head / Limit Switches)","hostname": "esp32-robot-3.local"},
    4: {"name": "ESP #4 (Leg Driving Board)",    "hostname": "esp32-robot-4.local"},
}


def discover_esps_udp(timeout: float = 2.0, port: int = DEFAULT_UDP_PORT) -> Dict[int, Dict[str, str]]:
    """Broadcast a discovery ping over UDP and gather responses from all active ESPs."""
    discovered: Dict[int, Dict[str, str]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)

    ping_payload = json.dumps({"esp": 0, "cmd": "STATUS", "instructionID": "OTA_DISCOVERY"}).encode("utf-8")

    destinations = [
        ("255.255.255.255", port),
        ("10.204.123.255", port),
        ("10.128.135.255", port),
        ("<broadcast>", port),
        ("10.204.123.170", port),
        ("10.204.123.196", port),
        ("10.204.123.119", port),
        ("10.128.135.170", port),
        ("10.128.135.196", port),
        ("10.128.135.119", port),
    ]
    for dest in destinations:
        try:
            sock.sendto(ping_payload, dest)
        except Exception:
            pass

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            data, (addr, _) = sock.recvfrom(2048)
            try:
                pkt = json.loads(data.decode("utf-8"))
                esp_id = pkt.get("esp")
                if esp_id:
                    discovered[int(esp_id)] = {
                        "id": str(esp_id),
                        "ip": addr,
                        "hostname": pkt.get("hostname", f"esp32-robot-{esp_id}"),
                        "status": pkt.get("status", "online"),
                        "uptime_ms": str(pkt.get("uptime_ms", 0)),
                        "ota": str(pkt.get("ota", "ready")),
                    }
            except Exception:
                pass
        except socket.timeout:
            continue
        except Exception:
            break

    sock.close()
    return discovered


def resolve_esp_ip(target: str) -> Optional[str]:
    """Resolve target ('esp1', '1', 'esp32-robot-1.local', or IP) to an IP address."""
    target_clean = target.strip().lower()

    # Check numeric (1..3)
    if target_clean in ["1", "2", "3", "esp1", "esp2", "esp3"]:
        num = target_clean.replace("esp", "")
        # First try quick UDP discovery
        active = discover_esps_udp(timeout=1.0)
        if int(num) in active:
            return active[int(num)]["ip"]

        # Fallback to mDNS resolution
        hostname = f"esp32-robot-{num}.local"
        try:
            return socket.gethostbyname(hostname)
        except Exception:
            pass

    # Check if target is already an IP address
    parts = target_clean.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return target_clean

    # Try hostname resolution
    try:
        return socket.gethostbyname(target)
    except Exception:
        return None


def flash_firmware_http(ip: str, bin_path: Path, port: int = DEFAULT_HTTP_PORT) -> bool:
    """Flash a firmware .bin file to the ESP32 via HTTP Web OTA (/update endpoint)."""
    if not requests:
        print("[!] Python 'requests' library required for HTTP OTA. Run: pip install requests")
        return False

    if not bin_path.exists():
        print(f"[ERROR] Firmware binary not found at: {bin_path}")
        return False

    file_size = bin_path.stat().st_size
    url = f"http://{ip}:{port}/update"

    print(f"\n[*] Preparing OTA deployment:")
    print(f"    Target IP    : {ip}:{port}")
    print(f"    Binary File  : {bin_path.name} ({file_size:,} bytes)")
    print(f"    Endpoint     : {url}")
    print(f"[*] Uploading firmware over Wi-Fi...")

    start_time = time.time()
    try:
        with open(bin_path, "rb") as f:
            files = {"update": (bin_path.name, f, "application/octet-stream")}
            response = requests.post(url, files=files, timeout=45)

        elapsed = time.time() - start_time
        if response.status_code == 200:
            print(f"[SUCCESS] Firmware flashed successfully in {elapsed:.2f}s!")
            print(f"          Response: {response.text.strip()}")
            print(f"          ESP32 is rebooting into the new firmware...")
            return True
        else:
            print(f"[FAILED] HTTP {response.status_code}: {response.text.strip()}")
            return False

    except requests.exceptions.Timeout:
        # Sometimes reboot happens before response finishes
        print(f"[?] Request timed out after upload. ESP32 may already be rebooting.")
        return True
    except Exception as e:
        print(f"[ERROR] OTA upload failed: {e}")
        return False


def run_scan() -> None:
    """Scan and print a formatted table of all active ESP boards."""
    print("\n" + "=" * 70)
    print("       ROBOT ESP32 WI-FI OVER-THE-AIR (OTA) SCANNER")
    print("=" * 70)
    print("[*] Broadcasting discovery request on UDP port 5005...")

    discovered = discover_esps_udp(timeout=2.5)

    print("-" * 70)
    print(f"{'BOARD':<10} | {'IP ADDRESS':<16} | {'HOSTNAME':<20} | {'STATUS':<10}")
    print("-" * 70)

    for esp_id in range(1, 4):
        if esp_id in discovered:
            info = discovered[esp_id]
            print(f"ESP #{esp_id:<5} | {info['ip']:<16} | {info['hostname']:<20} | \033[92mONLINE (OTA READY)\033[0m")
        else:
            # Try mDNS ping
            hostname = f"esp32-robot-{esp_id}.local"
            ip = None
            try:
                ip = socket.gethostbyname(hostname)
            except Exception:
                pass

            if ip:
                print(f"ESP #{esp_id:<5} | {ip:<16} | {hostname:<20} | \033[92mONLINE (mDNS)\033[0m")
            else:
                desc = DEFAULT_ESP_TARGETS.get(esp_id, {}).get("name", "")
                print(f"ESP #{esp_id:<5} | {'-':<16} | {hostname:<20} | \033[90mOFFLINE / UNREACHABLE\033[0m")

    print("=" * 70)
    print(f"Total discovered: {len(discovered)} of 3 ESP boards online.\n")


def run_ping() -> None:
    """Ping all ESPs via UDP broadcast and measure round-trip latency."""
    print("[*] Pinging all ESP32 boards on Wi-Fi...")
    start = time.time()
    discovered = discover_esps_udp(timeout=1.5)
    elapsed_ms = (time.time() - start) * 1000

    for esp_id, info in sorted(discovered.items()):
        print(f"  [PONG] ESP #{esp_id} at {info['ip']} -> Status: {info['status']} (RTT: {elapsed_ms:.1f}ms)")

    if not discovered:
        print("[!] No responses received. Ensure ESP32s are powered ON and connected to Wi-Fi.")


def main():
    parser = argparse.ArgumentParser(description="Over-The-Air (OTA) Deploy Tool for Robot ESPs")
    parser.add_argument("--scan", action="store_true", help="Scan local network for all 4 ESPs")
    parser.add_argument("--ping", action="store_true", help="Ping all ESPs via UDP broadcast")
    parser.add_argument("--target", type=str, default=None, help="Target board: 'esp1', 'esp2', 'esp3', 'esp4', 'all', or IP")
    parser.add_argument("--bin", type=str, default=None, help="Path to compiled firmware .bin file")
    parser.add_argument("--ip", type=str, default=None, help="Direct IP address override")
    parser.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT, help=f"HTTP OTA port (default {DEFAULT_HTTP_PORT})")
    args = parser.parse_args()

    if args.scan:
        run_scan()
        return

    if args.ping:
        run_ping()
        return

    if not args.target and not args.ip:
        run_scan()
        print("To deploy code to an ESP over Wi-Fi without USB:")
        print("  python deploy_esp_ota.py --target esp1 --bin path/to/firmware.bin")
        print("  python deploy_esp_ota.py --target all  --bin path/to/firmware.bin")
        return

    if not args.bin:
        print("[ERROR] Please specify the firmware binary file with --bin <path_to.bin>")
        sys.exit(1)

    bin_path = Path(args.bin).resolve()
    if not bin_path.exists():
        print(f"[ERROR] Firmware file does not exist: {bin_path}")
        sys.exit(1)

    # Handle flashing all ESPs
    if args.target and args.target.lower() == "all":
        discovered = discover_esps_udp(timeout=2.0)
        if not discovered:
            print("[ERROR] No ESP32 boards found on the network. Cannot flash.")
            sys.exit(1)

        print(f"[*] Flashing all {len(discovered)} discovered ESP boards...")
        for esp_id, info in sorted(discovered.items()):
            print(f"\n--- Flashing ESP #{esp_id} ({info['ip']}) ---")
            flash_firmware_http(info["ip"], bin_path, port=args.port)
        return

    # Handle single target
    target_ip = args.ip or resolve_esp_ip(args.target)
    if not target_ip:
        print(f"[ERROR] Could not resolve IP for target '{args.target}'. Run --scan to check connected boards.")
        sys.exit(1)

    success = flash_firmware_http(target_ip, bin_path, port=args.port)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
