#!/usr/bin/env python3
"""
Phone Speaker Dispatcher for Nova Robot (Raspberry Pi 5 -> Phone App).
=============================================================================
Dispatches text from the Raspberry Pi to the LUKU Live Phone App so it is
voiced out loud over the speaker connected to the phone.

Routing strategy:
  1. Direct Local LAN (Port 8888): If PHONE_IP is configured or reachable,
     posts directly to http://<phone_ip>:8888/speak for near-zero latency.
  2. Cloud / Bridge Site Queue: Posts to <BRIDGE_URL>/api/phone_speech
     (or /api/speak), which the phone app polls continuously and voices out loud.

Usage:
  # In Python:
  from robot.bridge.phone_speaker import speak_to_phone, speak_to_phone_async
  speak_to_phone("Hello Yohanna! Nova is ready.")

  # From CLI:
  python3 phone_speaker.py "Hello Yohanna!"
  python3 phone_speaker.py --ip 10.205.77.50 "Direct LAN message"
=============================================================================
"""

import sys
import os
import json
import time
import argparse
import threading
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional, Dict, Any

# Ensure project root is in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from robot.bridge.config import DEFAULT_BRIDGE_URL
except ImportError:
    try:
        from bridge.config import DEFAULT_BRIDGE_URL
    except ImportError:
        DEFAULT_BRIDGE_URL = "http://localhost:3000"


def get_default_bridge_url() -> str:
    """Returns bridge site URL from environment or fallback default."""
    return (
        os.environ.get("BRIDGE_URL")
        or os.environ.get("ROBOT_BRIDGE_URL")
        or DEFAULT_BRIDGE_URL
    ).rstrip("/")


def get_phone_ip() -> Optional[str]:
    """Returns direct phone IP if configured in environment."""
    ip = os.environ.get("PHONE_IP") or os.environ.get("ROBOT_PHONE_IP")
    return ip.strip() if ip else None


def dispatch_direct_lan(
    text: str,
    phone_ip: str,
    port: int = 8888,
    timeout: float = 2.0
) -> bool:
    """
    Attempts to send text directly to the phone's local HTTP listener.
    """
    url = f"http://{phone_ip}:{port}/speak"
    payload = json.dumps({"text": text, "source": "pi_direct"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "NovaPi/1.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status in (200, 201, 202):
                print(f"[PhoneSpeaker] Direct LAN delivery to {phone_ip}:{port} successful.")
                return True
    except Exception as e:
        # Expected if phone is not running direct server or IP changed
        pass
    return False


def dispatch_to_bridge(
    text: str,
    bridge_url: Optional[str] = None,
    source: str = "pi",
    timeout: float = 4.0
) -> bool:
    """
    Pushes text packet to the Robot Bridge Site /api/phone_speech queue.
    The phone app continuously polls this queue and speaks it out loud.
    """
    base = (bridge_url or get_default_bridge_url()).rstrip("/")
    endpoint = f"{base}/api/phone_speech"
    payload = json.dumps({"text": text, "source": source}).encode("utf-8")

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "NovaPi/1.0"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success"):
                pkt_id = data.get("packet", {}).get("id", "N/A")
                print(f"[PhoneSpeaker] Queued on bridge site (ID: {pkt_id}) -> Phone will vocalize.")
                return True
            else:
                print(f"[PhoneSpeaker] Bridge returned error: {data.get('error')}")
    except urllib.error.HTTPError as e:
        print(f"[PhoneSpeaker] HTTP error from bridge ({e.code}): {e.reason}")
    except Exception as e:
        print(f"[PhoneSpeaker] Failed to connect to bridge at {base}: {e}")
    return False


def speak_to_phone(
    text: str,
    bridge_url: Optional[str] = None,
    phone_ip: Optional[str] = None,
    phone_port: int = 8888,
    timeout: float = 4.0
) -> bool:
    """
    Dispatches text to the phone app so it is voiced out loud over the speaker.
    Tries direct LAN first, then automatically falls back to Bridge Site queue.
    """
    clean_text = str(text or "").strip()
    if not clean_text:
        print("[PhoneSpeaker] Empty text provided; skipping.")
        return False

    target_ip = phone_ip or get_phone_ip()

    # 1. Direct LAN attempt if phone IP is provided
    if target_ip:
        if dispatch_direct_lan(clean_text, target_ip, port=phone_port, timeout=min(timeout, 2.0)):
            return True

    # 2. Bridge queue dispatch (primary / reliable fallback)
    return dispatch_to_bridge(clean_text, bridge_url=bridge_url, timeout=timeout)


def speak_to_phone_async(text: str, **kwargs) -> threading.Thread:
    """
    Non-blocking version: launches speech dispatch in a background thread.
    """
    t = threading.Thread(target=speak_to_phone, args=(text,), kwargs=kwargs, daemon=True)
    t.start()
    return t


def main():
    parser = argparse.ArgumentParser(description="Dispatch speech text from Pi to Phone App.")
    parser.add_argument("text", nargs="*", help="Text to speak out loud over phone speaker")
    parser.add_argument("--ip", default=None, help="Direct Phone IP (optional)")
    parser.add_argument("--port", type=int, default=8888, help="Phone direct port (default: 8888)")
    parser.add_argument("--bridge", default=None, help="Bridge site URL (default: from config/env)")

    args = parser.parse_args()
    message = " ".join(args.text).strip()

    if not message:
        message = "Hello! This is a test voice dispatch from the Raspberry Pi to your phone speaker."

    print(f"\n[PhoneSpeaker] Dispatching: \"{message}\"")
    ok = speak_to_phone(
        message,
        bridge_url=args.bridge,
        phone_ip=args.ip,
        phone_port=args.port
    )

    if ok:
        print("[PhoneSpeaker] Done. Spoken text dispatched to phone successfully.\n")
        sys.exit(0)
    else:
        print("[PhoneSpeaker] Warning: Failed to dispatch speech to phone.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
