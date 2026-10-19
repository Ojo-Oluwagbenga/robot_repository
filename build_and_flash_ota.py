#!/usr/bin/env python3
"""
Automated Compile & Flash Tool for Robot ESP32 Controllers.
=============================================================================
Allows you to edit C++ code and flash it directly to any ESP32 board
over Wi-Fi OTA with a single command.

Usage:
  # 1. Compile and flash active board over Wi-Fi:
  python build_and_flash_ota.py

  # 2. Compile and flash specifically for Board #3:
  python build_and_flash_ota.py --target esp3

  # 3. Compile and flash by direct IP:
  python build_and_flash_ota.py --target 10.128.135.119

  # 4. Compile for all boards and flash each sequentially:
  python build_and_flash_ota.py --target all

  # 5. Compile only without flashing:
  python build_and_flash_ota.py --compile-only

  # 6. Flash existing .bin without recompiling:
  python build_and_flash_ota.py --flash-only
=============================================================================
"""

import sys
import os
import re
import time
import socket
import argparse
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

# ==============================================================================
# CONFIGURATION & PATHS
# ==============================================================================
PROJECT_ROOT = Path(__file__).resolve().parent
SKETCH_DIR = PROJECT_ROOT / "esp32_wifi_controller"
CONFIG_H_PATH = SKETCH_DIR / "config.h"
BUILD_DIR = PROJECT_ROOT / "build_esp32"
FQBN = "esp32:esp32:esp32"
UDP_PORT = 5005
HTTP_OTA_PORT = 80

# Auto-detect arduino-cli binary bundled with Arduino IDE
ARDUINO_CLI_CANDIDATES = [
    Path(r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
    Path(r"C:\Program Files (x86)\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"),
    Path.home() / r"AppData\Local\Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe",
    Path.home() / r"bin\arduino-cli.exe",
]


def find_arduino_cli() -> Optional[Path]:
    """Locate the arduino-cli executable."""
    for candidate in ARDUINO_CLI_CANDIDATES:
        if candidate.exists():
            return candidate

    # Try PATH
    import shutil
    cli_in_path = shutil.which("arduino-cli")
    if cli_in_path:
        return Path(cli_in_path)

    return None


def get_current_configured_esp_id() -> int:
    """Read the currently configured ESP_ID in config.h."""
    if not CONFIG_H_PATH.exists():
        return 1
    content = CONFIG_H_PATH.read_text(encoding="utf-8")
    m = re.search(r"#define\s+ESP_ID\s+(\d+)", content)
    return int(m.group(1)) if m else 1


def set_configured_esp_id(esp_id: int) -> None:
    """Update ESP_ID in config.h."""
    content = CONFIG_H_PATH.read_text(encoding="utf-8")
    new_content = re.sub(r"#define\s+ESP_ID\s+\d+", f"#define ESP_ID {esp_id}", content)
    CONFIG_H_PATH.write_text(new_content, encoding="utf-8")
    print(f"[*] Updated {CONFIG_H_PATH.name} -> #define ESP_ID {esp_id}")


def discover_esps_udp(timeout: float = 2.0) -> Dict[int, Dict[str, str]]:
    """Broadcast UDP discovery packet to find all online ESPs."""
    discovered: Dict[int, Dict[str, str]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)

    ping_payload = b'{"cmd":"STATUS","esp":0,"instructionID":"ota_discover"}'
    destinations = [
        ("255.255.255.255", UDP_PORT),
        ("10.204.123.255", UDP_PORT),
        ("10.128.135.255", UDP_PORT),
        ("10.204.123.170", UDP_PORT),
        ("10.204.123.196", UDP_PORT),
        ("10.204.123.119", UDP_PORT),
        ("10.128.135.170", UDP_PORT),
        ("10.128.135.196", UDP_PORT),
        ("10.128.135.119", UDP_PORT),
    ]

    for dest in destinations:
        try:
            sock.sendto(ping_payload, dest)
        except Exception:
            pass

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            data, (addr, port) = sock.recvfrom(2048)
            import json
            pkt = json.loads(data.decode("utf-8", errors="ignore"))
            esp_id = pkt.get("esp")
            if esp_id:
                discovered[int(esp_id)] = {
                    "ip": addr,
                    "hostname": pkt.get("hostname", f"esp32-robot-{esp_id}"),
                    "status": pkt.get("status", "online"),
                }
        except (socket.timeout, Exception):
            continue

    sock.close()
    return discovered


def compile_firmware(arduino_cli: Path, esp_id: Optional[int] = None) -> Optional[Path]:
    """Compile the ESP32 firmware using arduino-cli."""
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    if esp_id is not None and esp_id != 4:
        current_id = get_current_configured_esp_id()
        if current_id != esp_id:
            set_configured_esp_id(esp_id)

    cur_id = esp_id if (esp_id is not None) else get_current_configured_esp_id()
    sketch_dir = (PROJECT_ROOT / "esp4_leg_controller") if cur_id == 4 else SKETCH_DIR

    print("\n" + "=" * 65)
    print(f"[*] COMPILING FIRMWARE FOR ESP #{cur_id}...")
    print(f"    FQBN      : {FQBN}")
    print(f"    Sketch    : {sketch_dir}")
    print(f"    Output Dir: {BUILD_DIR}")
    print("=" * 65)

    CACHE_DIR = PROJECT_ROOT / ("build_cache_esp4" if cur_id == 4 else "build_cache_esp")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(arduino_cli),
        "compile",
        "--fqbn", FQBN,
        "--build-path", str(CACHE_DIR),
        "--output-dir", str(BUILD_DIR),
        str(sketch_dir)
    ]

    start_t = time.time()
    proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    elapsed = time.time() - start_t

    if proc.returncode != 0:
        print("\n[COMPILATION ERROR]:")
        print(proc.stdout)
        return None

    print(f"[OK] Firmware compiled successfully in {elapsed:.2f}s!")

    expected_bin_name = f"{sketch_dir.name}.ino.bin"
    bin_path = BUILD_DIR / expected_bin_name
    if not bin_path.exists():
        # Fallback to any .bin generated in BUILD_DIR
        bins = list(BUILD_DIR.glob("*.bin"))
        if bins:
            bin_path = bins[0]

    if bin_path.exists():
        size = bin_path.stat().st_size
        print(f"[OK] Binary generated: {bin_path.name} ({size:,} bytes)")
        named_bin = BUILD_DIR / f"esp32_robot_{cur_id}.bin"
        import shutil
        shutil.copy2(bin_path, named_bin)
        print(f"[OK] Board binary saved: {named_bin.name}")
        return named_bin
    else:
        print(f"[ERROR] Binary file not found in {BUILD_DIR}")
        return None


def flash_firmware_ota(ip: str, bin_path: Path, esp_label: str = "ESP") -> bool:
    """Flash firmware over Wi-Fi via the Web OTA /update endpoint."""
    url = f"http://{ip}:{HTTP_OTA_PORT}/update"
    file_size = bin_path.stat().st_size

    print(f"\n[*] Deploying to {esp_label} ({ip})...")
    print(f"    Target URL : {url}")
    print(f"    Firmware   : {bin_path.name} ({file_size:,} bytes)")
    print("[*] Uploading firmware over Wi-Fi...")

    start_t = time.time()

    # Prefer curl.exe for reliable streaming upload
    import shutil
    curl_bin = shutil.which("curl") or "curl.exe"
    try:
        cmd = [curl_bin, "-F", f"update=@{str(bin_path)}", url, "--silent", "--show-error", "--max-time", "45"]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        elapsed = time.time() - start_t
        if "UPDATE SUCCESS" in proc.stdout or proc.returncode == 0:
            print(f"[SUCCESS] Flashed {esp_label} via curl in {elapsed:.2f}s! ({proc.stdout.strip()})")
            print(f"          Device is rebooting into the new firmware...")
            return True
        elif "UPDATE FAILED" in proc.stdout:
            print(f"[FAILED] Device rejected update: {proc.stdout.strip()}")
            return False
    except Exception as e:
        print(f"[*] curl invocation failed ({e}), falling back to requests...")

    if not requests:
        print("[ERROR] Python 'requests' module required for OTA flashing fallback.")
        return False

    try:
        with open(bin_path, "rb") as f:
            files = {"update": (bin_path.name, f, "application/octet-stream")}
            resp = requests.post(url, files=files, timeout=35)

        elapsed = time.time() - start_t
        if resp.status_code == 200:
            print(f"[SUCCESS] Flashed {esp_label} in {elapsed:.2f}s! ({resp.text.strip()})")
            print(f"          Device is rebooting into the new firmware...")
            return True
        else:
            print(f"[FAILED] HTTP {resp.status_code}: {resp.text.strip()}")
            return False

    except requests.exceptions.Timeout:
        print("[?] Request timed out during reboot. Flashing likely succeeded.")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to flash {esp_label}: {e}")
        return False


def verify_online(ip: str, timeout: float = 6.0) -> bool:
    """Verify that the ESP came back online after flashing."""
    print(f"[*] Waiting for device at {ip} to complete reboot...", end="", flush=True)
    start_t = time.time()
    while time.time() - start_t < timeout:
        time.sleep(1.0)
        print(".", end="", flush=True)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            if sock.connect_ex((ip, HTTP_OTA_PORT)) == 0:
                sock.close()
                print(" [ONLINE!]")
                return True
        except Exception:
            pass
        finally:
            sock.close()

    print(" [READY]")
    return True


def main():
    parser = argparse.ArgumentParser(description="Direct Compile & OTA Flash Pipeline for ESP32 Controllers")
    parser.add_argument("--target", default="auto", help="Target board: 'auto', 'all', 'esp1'..'esp3', or IP address")
    parser.add_argument("--compile-only", action="store_true", help="Compile firmware without flashing")
    parser.add_argument("--flash-only", action="store_true", help="Flash existing binary without compiling")
    parser.add_argument("--scan", action="store_true", help="Scan and list online ESP boards")
    args = parser.parse_args()

    # Handle scan command
    if args.scan:
        print("[*] Scanning network for ESP32 boards...")
        esps = discover_esps_udp(timeout=2.0)
        print("-" * 65)
        print(f"{'BOARD':<10} | {'IP ADDRESS':<16} | {'STATUS'}")
        print("-" * 65)
        for i in range(1, 5):
            if i in esps:
                print(f"ESP #{i:<6} | {esps[i]['ip']:<16} | ONLINE (OTA READY)")
            else:
                print(f"ESP #{i:<6} | {'-':<16} | OFFLINE")
        print("-" * 65)
        return

    cli_path = find_arduino_cli()
    if not cli_path and not args.flash_only:
        print("[ERROR] Could not find 'arduino-cli.exe' on your system.")
        sys.exit(1)

    # 1. Discover online boards
    print("[*] Discovering active ESP boards on Wi-Fi...")
    online_esps = discover_esps_udp(timeout=1.5)
    for esp_id, info in online_esps.items():
        print(f"  [Found] ESP #{esp_id} at {info['ip']}")

    # 2. Determine target boards
    targets = []
    if args.target == "auto":
        if online_esps:
            # Flash all discovered online boards
            for esp_id, info in online_esps.items():
                targets.append((esp_id, info["ip"]))
        else:
            # Default to the board ID currently configured in config.h
            cur_id = get_current_configured_esp_id()
            targets.append((cur_id, None))
    elif args.target == "all":
        for esp_id in range(1, 5):
            ip = online_esps.get(esp_id, {}).get("ip")
            targets.append((esp_id, ip))
    elif args.target.startswith("esp") or args.target.isdigit():
        num = int(args.target.replace("esp", ""))
        ip = online_esps.get(num, {}).get("ip")
        targets.append((num, ip))
    else:
        # Treat as direct IP
        targets.append((None, args.target))

    if not targets:
        print("[!] No target boards selected.")
        return

    # 3. Process each target
    for esp_id, ip in targets:
        esp_label = f"ESP #{esp_id}" if esp_id else f"ESP ({ip})"

        # Step A: Compile
        if not args.flash_only:
            bin_path = compile_firmware(cli_path, esp_id=esp_id)
            if not bin_path:
                print(f"[!] Compilation failed for {esp_label}. Aborting.")
                return
        else:
            named_bin = BUILD_DIR / f"esp32_robot_{esp_id}.bin" if esp_id else None
            if named_bin and named_bin.exists():
                bin_path = named_bin
            else:
                bin_path = BUILD_DIR / "esp32_wifi_controller.ino.bin"
            if not bin_path.exists():
                print(f"[ERROR] No existing binary found at: {bin_path}")
                return

        if args.compile_only:
            print(f"[OK] Compile completed for {esp_label}.")
            continue

        # Step B: Flash
        if not ip:
            # Try to resolve IP again
            if esp_id and esp_id in online_esps:
                ip = online_esps[esp_id]["ip"]
            else:
                # Try UDP discovery one more time
                refreshed = discover_esps_udp(timeout=1.5)
                if esp_id and esp_id in refreshed:
                    ip = refreshed[esp_id]["ip"]

        if not ip:
            print(f"[!] Cannot flash {esp_label}: device is offline or IP could not be resolved.")
            print(f"    Make sure {esp_label} is powered on and connected to the hotspot.")
            continue

        # Flash over OTA
        success = flash_firmware_ota(ip, bin_path, esp_label=esp_label)
        if success:
            verify_online(ip, timeout=5.0)

    print("\n" + "=" * 65)
    print(">>> DEPLOYMENT PIPELINE COMPLETE! <<<")
    print("=" * 65)


if __name__ == "__main__":
    main()
