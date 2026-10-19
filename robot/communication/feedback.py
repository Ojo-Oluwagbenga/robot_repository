"""
Feedback data model and thread-safe queue management for the robot.
"""

import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable
import queue
import threading


@dataclass
class FeedbackPacket:
    """Represents a structured, decoded 17-bit feedback event from an ESP32."""
    esp_id: int
    motor_id: int
    magnitude: int
    sign: int
    net_pulses: int
    is_overflow: bool = False
    timestamp: float = field(default_factory=time.time)
    raw_bits: List[int] = field(default_factory=list)

    @property
    def global_motor_index(self) -> int:
        """Global 0-indexed motor number (0 to 19)."""
        return (self.esp_id - 1) * 4 + self.motor_id

    def to_dict(self) -> Dict[str, object]:
        """Dictionary representation suitable for ROS 2 serialization or JSON logging."""
        return {
            "esp_id": self.esp_id,
            "motor_id": self.motor_id,
            "global_motor_index": self.global_motor_index,
            "magnitude": self.magnitude,
            "sign": "positive" if self.sign == 1 else "negative",
            "net_pulses": self.net_pulses,
            "is_overflow": self.is_overflow,
            "timestamp": self.timestamp,
            "raw_bits": "".join(str(b) for b in self.raw_bits)
        }

    def __str__(self) -> str:
        sign_str = "+" if self.net_pulses >= 0 else ""
        overflow_warn = " [OVERFLOW]" if self.is_overflow else ""
        return (f"ESP:{self.esp_id} Motor:{self.motor_id + 1} "
                f"Net:{sign_str}{self.net_pulses} pulses{overflow_warn} (t={self.timestamp:.3f})")


class FeedbackQueue:
    """
    Thread-safe feedback storage and notification mechanism.
    Provides per-ESP and global feedback retrieval.
    """

    def __init__(self, maxsize: int = 1000):
        self._queues: Dict[int, queue.Queue] = {esp: queue.Queue(maxsize=maxsize) for esp in range(1, 6)}
        self._global_queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._callbacks: List[Callable[[FeedbackPacket], None]] = []
        self._lock = threading.Lock()
        self._latest_by_motor: Dict[Tuple[int, int], FeedbackPacket] = {}

    def push(self, packet: FeedbackPacket) -> None:
        """Store a received feedback packet and notify registered listeners."""
        with self._lock:
            # Store in per-ESP queue
            if packet.esp_id in self._queues:
                try:
                    self._queues[packet.esp_id].put_nowait(packet)
                except queue.Full:
                    self._queues[packet.esp_id].get_nowait()
                    self._queues[packet.esp_id].put_nowait(packet)

            # Store in global queue
            try:
                self._global_queue.put_nowait(packet)
            except queue.Full:
                self._global_queue.get_nowait()
                self._global_queue.put_nowait(packet)

            # Update latest position cache
            self._latest_by_motor[(packet.esp_id, packet.motor_id)] = packet

            # Execute callbacks (e.g. ROS 2 publishers or state estimators)
            callbacks = list(self._callbacks)

        for cb in callbacks:
            try:
                cb(packet)
            except Exception as e:
                pass

    def pop_esp(self, esp_id: int, block: bool = False, timeout: Optional[float] = None) -> Optional[FeedbackPacket]:
        """Pop the oldest feedback packet for a specific ESP32."""
        if esp_id not in self._queues:
            return None
        try:
            return self._queues[esp_id].get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def pop_global(self, block: bool = False, timeout: Optional[float] = None) -> Optional[FeedbackPacket]:
        """Pop the oldest feedback packet across any ESP32."""
        try:
            return self._global_queue.get(block=block, timeout=timeout)
        except queue.Empty:
            return None

    def get_latest(self, esp_id: int, motor_id: int) -> Optional[FeedbackPacket]:
        """Get the most recent feedback packet for a specific motor without popping."""
        with self._lock:
            return self._latest_by_motor.get((esp_id, motor_id))

    def register_callback(self, callback: Callable[[FeedbackPacket], None]) -> None:
        """Register a callback function to be called on every incoming feedback packet."""
        with self._lock:
            self._callbacks.append(callback)

    def clear(self) -> None:
        """Clear all stored queues."""
        with self._lock:
            for q in self._queues.values():
                while not q.empty():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        break
            while not self._global_queue.empty():
                try:
                    self._global_queue.get_nowait()
                except queue.Empty:
                    break
            self._latest_by_motor.clear()
