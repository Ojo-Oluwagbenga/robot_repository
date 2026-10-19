"""
Dedicated Wi-Fi Communication Logger for Raspberry Pi <-> ESP32 traffic.
Records all outgoing broadcast JSON packets and incoming ESP response JSON packets
with microsecond timestamps to logs/esp_comm.log and logs/robot.log.
"""

import os
import sys
import time
import json
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

ROBOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROBOT_DIR.parent
LOGS_DIR = PROJECT_ROOT / "logs"

DEFAULT_COMM_LOG = LOGS_DIR / "esp_comm.log"
DEFAULT_ROBOT_LOG = LOGS_DIR / "robot.log"


class WifiCommLogger:
    """Thread-safe logger dedicated to ESP32 Wi-Fi JSON packet traffic."""

    def __init__(self, comm_log_path: Optional[Path] = None, robot_log_path: Optional[Path] = None):
        self.comm_log_path = Path(comm_log_path or DEFAULT_COMM_LOG)
        self.robot_log_path = Path(robot_log_path or DEFAULT_ROBOT_LOG)
        self._lock = threading.Lock()

        # Ensure logs directory exists
        self.comm_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.robot_log_path.parent.mkdir(parents=True, exist_ok=True)

    def _format_time(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def log_tx(self, packet: Dict[str, Any], destination: str = "BROADCAST", port: int = 5005) -> None:
        """Log a packet transmitted from Raspberry Pi to ESPs."""
        timestamp = self._format_time()
        json_str = json.dumps(packet, separators=(', ', ': '))
        log_line = f"[{timestamp}] [TX -> {destination}:{port}] {json_str}\n"

        with self._lock:
            # Write to dedicated esp_comm.log
            try:
                with open(self.comm_log_path, "a", encoding="utf-8") as f:
                    f.write(log_line)
                    f.flush()
            except Exception as e:
                print(f"[Logger Error] Failed to write comm log: {e}", file=sys.stderr)

            # Also mirror to robot.log
            try:
                with open(self.robot_log_path, "a", encoding="utf-8") as f:
                    f.write(f"{timestamp} [INFO] [WifiComm] TX -> {json_str}\n")
                    f.flush()
            except Exception:
                pass

    def log_rx(self, packet: Dict[str, Any], source_ip: str = "Unknown", port: int = 5005) -> None:
        """Log a packet received by Raspberry Pi from an ESP."""
        timestamp = self._format_time()
        esp_id = packet.get("esp", "?")
        json_str = json.dumps(packet, separators=(', ', ': '))
        log_line = f"[{timestamp}] [RX <- ESP {esp_id} ({source_ip}:{port})] {json_str}\n"

        with self._lock:
            # Write to dedicated esp_comm.log
            try:
                with open(self.comm_log_path, "a", encoding="utf-8") as f:
                    f.write(log_line)
                    f.flush()
            except Exception as e:
                print(f"[Logger Error] Failed to write comm log: {e}", file=sys.stderr)

            # Also mirror to robot.log
            try:
                with open(self.robot_log_path, "a", encoding="utf-8") as f:
                    f.write(f"{timestamp} [INFO] [WifiComm] RX <- ESP {esp_id}: {json_str}\n")
                    f.flush()
            except Exception:
                pass


# Global singleton instance
wifi_comm_logger = WifiCommLogger()
