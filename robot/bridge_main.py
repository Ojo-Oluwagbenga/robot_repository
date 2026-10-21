#!/usr/bin/env python3
"""
Nova Robot Bridge System - Main Entry Point.
=============================================================================
Orchestrates:
1. CloudBridgeListener : Listens to [ENTRY: pi_instructions] on the cloud site
                         and stashes incoming instructions into the local file.
2. LocalInstructionExecutor : Reads the local instruction file, looks up the
                         user-defined action file in code_mappings.py, executes
                         it, and removes the instruction upon full completion.
=============================================================================

Usage:
  python3 bridge_main.py                              # Default: https://robocode-kappa.vercel.app
  python3 bridge_main.py --clean                      # Clear local instruction queue on start
  python3 bridge_main.py --listener-only              # Only fetch & stash to local file
  python3 bridge_main.py --executor-only              # Only execute from local file
"""

import sys
import os
import time
import signal
import argparse
from pathlib import Path

# Ensure robot folder and parent directory are on sys.path
ROBOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from bridge.config import (
    DEFAULT_BRIDGE_URL,
    INSTRUCTIONS_FILE_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_EXECUTOR_INTERVAL,
)
from bridge.storage import InstructionStorage
from bridge.code_mappings import CODE_TO_FILE
from bridge.listener import CloudBridgeListener
from bridge.executor import LocalInstructionExecutor


def print_banner(bridge_url: str, storage: InstructionStorage):
    print("\n" + "=" * 72)
    print("        NOVA ROBOT BRIDGE SYSTEM — PI INSTRUCTION CONTROLLER")
    print("=" * 72)
    print(f"  * Cloud Bridge URL   : {bridge_url}")
    print(f"  * Cloud Queue Node   : [ENTRY: pi_instructions]")
    print(f"  * Relay Power Gate   : BCM GPIO 26 (Pin 37) [ACTIVE HIGH]")
    print(f"  * Local Storage File : {storage.file_path}")
    print(f"  * Pending Locally    : {storage.count()} instruction(s)")
    print("-" * 72)
    print("  * Defined Code-to-File Mappings (bridge/code_mappings.py):")
    for code, target in sorted(CODE_TO_FILE.items()):
        target_name = Path(target).name if target else "None"
        print(f"      [{code:12s}] -> {target_name}")
    print("=" * 72 + "\n")


def run_bridge_system(
    bridge_url: str = DEFAULT_BRIDGE_URL,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    executor_interval: float = DEFAULT_EXECUTOR_INTERVAL,
    clean_queue: bool = False,
    listener_only: bool = False,
    executor_only: bool = False,
):
    # Ensure motor power relay gate is activated on GPIO 26
    try:
        from hardware.relay_gate import enable_motor_power_relay
        relay_ok = enable_motor_power_relay()
        print(f"[*] Motor Power Relay (GPIO 26): {'ACTIVATED' if relay_ok else 'INITIALIZED'}")
    except Exception as e:
        print(f"[!] Relay activation notice: {e}")

    storage = InstructionStorage()

    if clean_queue:
        storage.clear()
        print(f"[*] Local instruction queue at {storage.file_path} cleared.")

    print_banner(bridge_url, storage)

    listener = None
    executor = None

    if not executor_only:
        listener = CloudBridgeListener(
            bridge_url=bridge_url,
            storage=storage,
            poll_interval=poll_interval,
        )

    if not listener_only:
        executor = LocalInstructionExecutor(
            storage=storage,
            bridge_url=bridge_url,
            interval=executor_interval,
        )

    # Signal handling for clean exit
    def shutdown(sig=None, frame=None):
        print("\n[*] Shutting down Robot Bridge System...")
        if listener:
            listener.stop()
        if executor:
            executor.stop()
        print("[*] Robot Bridge System stopped cleanly.")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Start services
    if listener_only and listener:
        print("[*] Running in LISTENER-ONLY mode (stashing to local file)...")
        listener.run_loop()
    elif executor_only and executor:
        print("[*] Running in EXECUTOR-ONLY mode (reading from local file)...")
        executor.run_loop()
    else:
        # Full mode: Listener in background thread, Executor in main loop
        print("[*] Starting dual pipeline: Cloud Listener (Stasher) + Local Executor...")
        listener.start_background()
        executor.run_loop()


def main():
    parser = argparse.ArgumentParser(description="Nova Robot Bridge System - Main Loop")
    parser.add_argument("bridge_url", nargs="?", default=DEFAULT_BRIDGE_URL, help="Cloud bridge site URL")
    parser.add_argument("--url", type=str, default=None, help="Override bridge site URL")
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="Cloud polling interval (s)")
    parser.add_argument("--exec-interval", type=float, default=DEFAULT_EXECUTOR_INTERVAL, help="Local executor interval (s)")
    parser.add_argument("--clean", action="store_true", help="Clear local instruction file on start")
    parser.add_argument("--listener-only", action="store_true", help="Only listen and stash to local file")
    parser.add_argument("--executor-only", action="store_true", help="Only read and execute from local file")
    args = parser.parse_args()

    target_url = args.url or args.bridge_url or DEFAULT_BRIDGE_URL

    run_bridge_system(
        bridge_url=target_url,
        poll_interval=args.poll_interval,
        executor_interval=args.exec_interval,
        clean_queue=args.clean,
        listener_only=args.listener_only,
        executor_only=args.executor_only,
    )


if __name__ == "__main__":
    main()
