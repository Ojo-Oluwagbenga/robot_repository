"""
Cloud Bridge Listener for Raspberry Pi.
Polls the Cloud Bridge Site / Google Realtime Database on [ENTRY: pi_instructions].
When pending instructions arrive, stashes them into the local instruction file.
"""

import time
import requests
import threading
from typing import Optional, List, Dict, Any, Callable
from pathlib import Path

from .config import DEFAULT_BRIDGE_URL, DEFAULT_POLL_INTERVAL
from .storage import InstructionStorage


class CloudBridgeListener:
    """
    Listens to the bridge site's [ENTRY: pi_instructions] and stashes
    incoming instructions into the Pi's local instruction file.
    """

    def __init__(
        self,
        bridge_url: str = DEFAULT_BRIDGE_URL,
        storage: Optional[InstructionStorage] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        on_stashed_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.bridge_url = bridge_url.rstrip("/")
        self.storage = storage or InstructionStorage()
        self.poll_interval = poll_interval
        self.on_stashed_callback = on_stashed_callback

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_error_time = 0.0

    def fetch_pending_from_bridge(self) -> List[Dict[str, Any]]:
        """Fetch pending instructions from the cloud bridge site."""
        urls_to_try = [
            f"{self.bridge_url}/api/pi_instructions?pending=true",
            f"{self.bridge_url}/pi_instructions?pending=true",
        ]

        for url in urls_to_try:
            try:
                resp = requests.get(url, timeout=5)
                if resp.ok:
                    data = resp.json()
                    if isinstance(data, dict):
                        return data.get("instructions", [])
                    elif isinstance(data, list):
                        return data
            except Exception as e:
                now = time.time()
                # Rate limit error prints to once every 10 seconds
                if now - self._last_error_time > 10.0:
                    print(f"[BridgeListener] Connection warning ({url}): {e}")
                    self._last_error_time = now

        return []

    def poll_once(self) -> int:
        """
        Poll once: fetches pending instructions from cloud and stashes any new ones locally.
        Returns the count of newly stashed instructions.
        """
        pending = self.fetch_pending_from_bridge()
        if not pending:
            return 0

        stashed_count = 0
        for item in pending:
            inst_id = item.get("id")
            code = item.get("code")
            val = item.get("value", "")

            # Stash into local instruction file
            if self.storage.stash_instruction(item):
                stashed_count += 1
                total_local = self.storage.count()
                print(
                    f"\n[BridgeListener] >>> [ENTRY: pi_instructions] NEW PENDING DETECTED <<<\n"
                    f"  Instruction ID : {inst_id}\n"
                    f"  Command Code   : {code}\n"
                    f"  Value          : {val}\n"
                    f"  Action         : Stashed into local file '{self.storage.file_path.name}'\n"
                    f"  Queue Count    : {total_local} pending locally\n"
                )
                if self.on_stashed_callback:
                    try:
                        self.on_stashed_callback(item)
                    except Exception as cb_err:
                        print(f"[BridgeListener] Callback error: {cb_err}")

        return stashed_count

    def run_loop(self) -> None:
        """Continuous polling loop."""
        self._running = True
        print(f"[*] CloudBridgeListener started. Polling {self.bridge_url} every {self.poll_interval}s...")
        while self._running:
            try:
                self.poll_once()
            except Exception as e:
                print(f"[BridgeListener] Unexpected error in polling loop: {e}")

            time.sleep(self.poll_interval)

    def start_background(self) -> None:
        """Start polling in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="BridgeListenerThread")
        self._thread.start()

    def stop(self) -> None:
        """Stop background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
