"""
Wi-Fi UDP Broadcast Communication Bus for Raspberry Pi 5 -> ESP32 Controllers.
Broadcasts JSON motor commands and receives real-time JSON encoder feedback.
Replaces the legacy 3-wire physical clock/data bus with wireless packets.
"""

import sys
import time
import json
import socket
import threading
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path

# Ensure robot dir is in sys.path
ROBOT_DIR = Path(__file__).resolve().parent.parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

from debug.wifi_logger import wifi_comm_logger
from debug.logger import get_logger

DEFAULT_ESP_IPS: Dict[int, str] = {
    1: "172.28.29.170",
    2: "172.28.29.196",
    3: "172.28.29.119",
}


class WifiBus:
    """
    Manages UDP socket broadcast transmission and reception of JSON packets
    between Raspberry Pi 5 and the 4 ESP32 motor controller boards.
    """

    def __init__(
        self,
        port: int = 5005,
        broadcast_ip: str = "255.255.255.255",
        bind_ip: str = "0.0.0.0",
        on_feedback_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.port = port
        self.broadcast_ip = broadcast_ip
        self.bind_ip = bind_ip
        self.on_feedback_callback = on_feedback_callback
        self.logger = get_logger()

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Instruction ID -> received response packet
        self._pending_responses: Dict[str, Dict[str, Any]] = {}
        self._response_events: Dict[str, threading.Event] = {}

        # Known ESP status: esp_id -> {"ip": ..., "last_seen": ..., ...}
        self.discovered_esps: Dict[int, Dict[str, Any]] = {}

        # Initialize UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Some platforms require SO_REUSEPORT
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except Exception:
                    pass

            self.sock.bind((self.bind_ip, self.port))
            self.sock.settimeout(0.5)
            self.logger.info(f"WifiBus UDP socket bound to {self.bind_ip}:{self.port} (Broadcast: {self.broadcast_ip})")
        except Exception as e:
            self.logger.warning(f"Failed to bind UDP socket to port {self.port}: {e}")

        # Start background listener thread
        self.start_listener()

    def start_listener(self) -> None:
        """Start background listener thread for incoming ESP response packets."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._listener_loop, daemon=True, name="WifiBusListener")
        self._thread.start()

    def stop_listener(self) -> None:
        """Stop background listener thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _listener_loop(self) -> None:
        """Continuous loop listening for incoming JSON packets from ESP32s."""
        while self._running:
            try:
                data, (addr, port) = self.sock.recvfrom(4096)
                if not data:
                    continue

                raw_text = data.decode("utf-8", errors="ignore").strip()
                try:
                    packet = json.loads(raw_text)
                except Exception:
                    continue

                # Ignore loopback echo of our own outgoing command packets
                # ESP responses contain 'totalpulse' or 'status' or 'uptime_ms'
                is_esp_packet = ("totalpulse" in packet) or ("status" in packet) or ("uptime_ms" in packet) or ("total_pulse" in packet)
                if not is_esp_packet:
                    continue

                # 1. Log incoming JSON packet from ESP to esp_comm.log and robot.log
                wifi_comm_logger.log_rx(packet, source_ip=addr, port=port)

                # 2. Track discovered ESP
                esp_id = packet.get("esp")
                if esp_id:
                    self.discovered_esps[esp_id] = {
                        "ip": addr,
                        "port": port,
                        "last_seen": time.time(),
                        "packet": packet,
                    }

                # 3. Match pending instruction response
                inst_id = str(packet.get("instructionID") or packet.get("instruction_id") or "")
                if inst_id:
                    with self._lock:
                        self._pending_responses[inst_id] = packet
                        if inst_id in self._response_events:
                            self._response_events[inst_id].set()

                # 4. Trigger callback if registered
                if self.on_feedback_callback:
                    try:
                        self.on_feedback_callback(packet)
                    except Exception as cb_err:
                        self.logger.error(f"Error in WifiBus feedback callback: {cb_err}")

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    time.sleep(0.05)

    def broadcast_command(
        self,
        esp: int,
        motor: int,
        pulse: int,
        dir: int = 0,
        instruction_id: Optional[str] = None,
        cmd: str = "MOVE",
    ) -> str:
        """
        Broadcast a motor command JSON packet to the ESP32 boards.
        Format: {"esp": 1, "motor": 2, "pulse": 300, "dir": 0, "instructionID": "12DE4"}
        """
        if not instruction_id:
            instruction_id = f"inst_{int(time.time() * 1000) % 1000000:06d}"

        payload: Dict[str, Any] = {
            "esp": int(esp),
            "motor": int(motor),
            "pulse": int(pulse),
            "dir": int(dir),
            "instructionID": str(instruction_id),
            "cmd": str(cmd),
        }

        with self._lock:
            # Register event for response tracking
            self._response_events[instruction_id] = threading.Event()

        # Send via UDP broadcast
        data_bytes = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(data_bytes, (self.broadcast_ip, self.port))
        except Exception as e:
            self.logger.error(f"Failed to broadcast JSON packet: {e}")

        # Also send direct unicast to target ESP if IP is known (bypasses AP broadcast filtering)
        target_ip = self.discovered_esps.get(esp, {}).get("ip") or DEFAULT_ESP_IPS.get(esp)
        if target_ip:
            try:
                self.sock.sendto(data_bytes, (target_ip, self.port))
            except Exception:
                pass

        # Log outgoing packet
        wifi_comm_logger.log_tx(payload, destination="BROADCAST", port=self.port)
        return instruction_id

    def broadcast_multi_command(
        self,
        esp: int,
        moves: List[Dict[str, Any]],
        instruction_id: Optional[str] = None,
    ) -> str:
        """
        Broadcast a multi-motor command JSON packet to execute multiple motors simultaneously.
        Format:
        {
          "esp": 1,
          "cmd": "MOVE",
          "instructionID": "batch_123456",
          "moves": [
            {"motor": 5, "pulse": 300, "dir": 0, "instructionID": "batch_123456_m5"},
            {"motor": 6, "pulse": 250, "dir": 1, "instructionID": "batch_123456_m6"}
          ]
        }
        """
        if not instruction_id:
            instruction_id = f"multi_{int(time.time() * 1000) % 1000000:06d}"

        formatted_moves = []
        with self._lock:
            # Register event for overall batch
            self._response_events[instruction_id] = threading.Event()

            for idx, m in enumerate(moves):
                motor_num = int(m.get("motor", 1))
                pulse_cnt = int(m.get("pulse", 0))
                direction = int(m.get("dir", 0))
                sub_id = str(m.get("instructionID") or m.get("instruction_id") or f"{instruction_id}_m{motor_num}_{idx}")
                self._response_events[sub_id] = threading.Event()

                formatted_moves.append({
                    "motor": motor_num,
                    "pulse": pulse_cnt,
                    "dir": direction,
                    "instructionID": sub_id,
                })

        payload: Dict[str, Any] = {
            "esp": int(esp),
            "cmd": "MOVE",
            "instructionID": str(instruction_id),
            "moves": formatted_moves,
        }

        # Send via UDP broadcast
        data_bytes = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(data_bytes, (self.broadcast_ip, self.port))
        except Exception as e:
            self.logger.error(f"Failed to broadcast multi-move JSON packet: {e}")

        # Also send direct unicast to target ESP if IP is known
        target_ip = self.discovered_esps.get(esp, {}).get("ip") or DEFAULT_ESP_IPS.get(esp)
        if target_ip:
            try:
                self.sock.sendto(data_bytes, (target_ip, self.port))
            except Exception:
                pass

        # Log outgoing packet
        wifi_comm_logger.log_tx(payload, destination="BROADCAST", port=self.port)
        return instruction_id

    def wait_for_multi_response(
        self,
        instruction_ids: List[str],
        timeout: float = 6.0,
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Wait for multiple instructions to finish (in parallel, within total timeout).
        Returns a dict of instruction_id -> response packet (or None on timeout).
        """
        deadline = time.time() + timeout
        results: Dict[str, Optional[Dict[str, Any]]] = {}
        for inst_id in instruction_ids:
            remaining = max(0.05, deadline - time.time())
            results[inst_id] = self.wait_for_response(inst_id, timeout=remaining)
        return results

    def send_stop(self, instruction_id: Optional[str] = None) -> str:
        """Emergency stop broadcast across all ESPs (esp: 0)."""
        inst_id = instruction_id or f"stop_{int(time.time() * 1000) % 1000000:06d}"
        payload = {"esp": 0, "cmd": "STOP", "instructionID": inst_id}
        data_bytes = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(data_bytes, (self.broadcast_ip, self.port))
        except Exception as e:
            self.logger.error(f"Failed to broadcast STOP packet: {e}")
        wifi_comm_logger.log_tx(payload, destination="BROADCAST", port=self.port)
        return inst_id

    def send_ping(self, esp: int = 0, instruction_id: Optional[str] = None) -> str:
        """Ping/status inquiry broadcast."""
        inst_id = instruction_id or f"ping_{int(time.time() * 1000) % 1000000:06d}"
        payload = {"esp": int(esp), "cmd": "STATUS", "instructionID": inst_id}
        data_bytes = json.dumps(payload).encode("utf-8")
        try:
            self.sock.sendto(data_bytes, (self.broadcast_ip, self.port))
        except Exception as e:
            self.logger.error(f"Failed to broadcast PING packet: {e}")
        wifi_comm_logger.log_tx(payload, destination="BROADCAST", port=self.port)
        return inst_id

    def wait_for_response(self, instruction_id: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """Wait until the ESP responds with the matching instructionID."""
        with self._lock:
            evt = self._response_events.get(instruction_id)

        if not evt:
            return None

        finished = evt.wait(timeout=timeout)
        with self._lock:
            resp = self._pending_responses.pop(instruction_id, None)
            self._response_events.pop(instruction_id, None)

        return resp if finished else None

    def enable_motor_power(self) -> bool:
        """Close relay gate on GPIO 26 so VM enters motor drivers."""
        try:
            from hardware.relay_gate import enable_motor_power_relay
            return enable_motor_power_relay()
        except Exception:
            return False

    def disable_motor_power(self) -> bool:
        """Open relay gate on GPIO 26 to cut VM to motor drivers."""
        try:
            from hardware.relay_gate import disable_motor_power_relay
            return disable_motor_power_relay()
        except Exception:
            return False

    def is_motor_power_enabled(self) -> bool:
        """Check if motor power relay is enabled."""
        try:
            from hardware.relay_gate import is_relay_enabled
            return is_relay_enabled()
        except Exception:
            return False

    def close(self) -> None:
        """Close socket and stop listener."""
        self.stop_listener()
        try:
            self.sock.close()
        except Exception:
            pass


# Global singleton instance for the robot controller
_GLOBAL_WIFI_BUS: Optional[WifiBus] = None


def get_wifi_bus(port: int = 5005) -> WifiBus:
    global _GLOBAL_WIFI_BUS
    if _GLOBAL_WIFI_BUS is None:
        _GLOBAL_WIFI_BUS = WifiBus(port=port)
    return _GLOBAL_WIFI_BUS
