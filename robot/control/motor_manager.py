"""
High-level Motor Manager for the humanoid robot.
Coordinates normal motor movements, degree-to-pulse conversions, command interruption,
feedback accounting, and lock-on operations across all 20 motors.
Designed for clean integration with future ROS 2 nodes.
"""

from typing import Dict, Optional, Tuple, Callable, List
import threading
import time

from config import (
    DEFAULT_PULSES_PER_DEGREE,
    DIRECTION_CW,
    DIRECTION_CCW,
    VALID_ESP_IDS,
    VALID_MOTOR_IDS,
    TOTAL_MOTORS,
    NUM_ESPS,
    MOTORS_PER_ESP
)
from communication.transmitter import BusTransmitter
from communication.receiver import FeedbackReceiver
from communication.feedback import FeedbackPacket, FeedbackQueue
from control.lock_manager import LockManager
from debug.logger import get_logger


class MotorState:
    """Tracks state and position accounting for a single motor."""
    def __init__(self, esp_id: int, motor_id: int):
        self.esp_id = esp_id
        self.motor_id = motor_id
        self.mode: str = "IDLE"  # "IDLE", "MOVING_NORMAL", "LOCKED_ON"
        self.absolute_position: int = 0
        self.last_execution_net_pulses: int = 0
        self.target_pulses: int = 0
        self.current_direction: int = DIRECTION_CW
        self.last_update_time: float = time.time()

    def update_from_feedback(self, packet: FeedbackPacket) -> None:
        """Update absolute and execution counters from verified ESP feedback."""
        self.last_execution_net_pulses = packet.net_pulses
        self.absolute_position += packet.net_pulses
        self.last_update_time = packet.timestamp
        if self.mode != "LOCKED_ON":
            self.mode = "IDLE"


class MotorManager:
    """
    Central motor controller providing a clean interface for robotics applications
    and ROS 2 nodes. Manages 20 motors across 5 ESP32 boards.
    """

    def __init__(
        self,
        transmitter: BusTransmitter,
        receiver: FeedbackReceiver,
        lock_manager: LockManager,
        pulses_per_degree: float = DEFAULT_PULSES_PER_DEGREE
    ):
        self.transmitter = transmitter
        self.receiver = receiver
        self.lock_manager = lock_manager
        self.pulses_per_degree = pulses_per_degree
        self.logger = get_logger()
        self._lock = threading.Lock()

        # Initialize tracking state for all 20 motors: (esp_id, motor_id) -> MotorState
        self.motors: Dict[Tuple[int, int], MotorState] = {}
        for esp_id in VALID_ESP_IDS:
            for motor_id in VALID_MOTOR_IDS:
                self.motors[(esp_id, motor_id)] = MotorState(esp_id, motor_id)

        # Register feedback callback to automatically update positions
        self.receiver.queue.register_callback(self._on_feedback_received)

    def _on_feedback_received(self, packet: FeedbackPacket) -> None:
        """Internal callback invoked whenever a 17-bit feedback packet is decoded."""
        key = (packet.esp_id, packet.motor_id)
        with self._lock:
            if key in self.motors:
                self.motors[key].update_from_feedback(packet)

    def queue_move(
        self,
        esp_id: int,
        motor_id: int,
        pulse_count: int,
        direction: int = DIRECTION_CW,
        instruction_id: Optional[str] = None
    ) -> None:
        """
        Queue a normal motor movement on an ESP.
        Dispatches via Wi-Fi UDP JSON packet broadcast.
        """
        if esp_id not in VALID_ESP_IDS:
            raise ValueError(f"Invalid ESP ID {esp_id}.")
        if motor_id not in VALID_MOTOR_IDS:
            raise ValueError(f"Invalid Motor ID {motor_id}.")

        with self._lock:
            motor = self.motors[(esp_id, motor_id)]

            # Check if this motor currently has lock-on
            if self.lock_manager.is_locked(esp_id, motor_id):
                self.logger.info(f"Target motor ESP:{esp_id} M{motor_id + 1} is in lock-on. Clearing lock before normal move.")
                self.lock_manager.clear_all_locks()

            # Mark state as queued
            motor.mode = "QUEUED_NORMAL"
            motor.target_pulses = pulse_count
            motor.current_direction = direction

        # Send command over Wi-Fi UDP JSON packet bus (and synchronous bus)
        self.transmitter.send_normal_command(
            esp_id=esp_id,
            motor_id=motor_id,
            pulse_count=pulse_count,
            direction=direction,
            instruction_id=instruction_id
        )

    def go_ahead(self) -> None:
        """
        Broadcast 10111 (Go Ahead) to all ESP32 boards.
        Triggers simultaneous execution of all stored normal movements across all ESPs.
        """
        with self._lock:
            for m in self.motors.values():
                if m.mode == "QUEUED_NORMAL":
                    m.mode = "MOVING_NORMAL"
        self.transmitter.send_go_ahead()

    def clear_queue(self) -> None:
        """
        Broadcast 10100 (Clear Queue) to all ESP32 boards.
        Discards all queued movements stored in ESP memory without executing them.
        """
        with self._lock:
            for m in self.motors.values():
                if m.mode == "QUEUED_NORMAL":
                    m.mode = "IDLE"
        self.transmitter.send_clear_queue()

    def move_pulses(self, esp_id: int, motor_id: int, pulse_count: int, direction: int = DIRECTION_CW) -> None:
        """
        Execute a normal motor movement by pulse count.
        Queues the 21-bit command in ESP memory and triggers immediate execution via 10111.
        """
        self.queue_move(esp_id, motor_id, pulse_count, direction)
        self.go_ahead()

    def move_simultaneous(
        self,
        moves: List[Tuple[int, int, int, int]],
        wait: bool = True,
        timeout: float = 6.0,
    ) -> List[str]:
        """
        Execute multiple motor movements simultaneously across one or more ESP32 controllers.
        Each move is a tuple: (esp_id, motor_id, pulse_count, direction).
        Dispatches all moves concurrently via Wi-Fi UDP JSON multi-command packets (or rapid batching)
        and optionally waits for all motors to complete execution.
        Returns a list of instruction IDs.
        """
        if not moves:
            return []

        # Group moves by ESP ID
        by_esp: Dict[int, List[Tuple[int, int, int]]] = {}
        with self._lock:
            for (esp_id, motor_id, pulse_count, direction) in moves:
                if (esp_id, motor_id) in self.motors:
                    m = self.motors[(esp_id, motor_id)]
                    m.mode = "MOVING_NORMAL"
                    m.target_pulses = pulse_count
                    m.current_direction = direction
                by_esp.setdefault(esp_id, []).append((motor_id, pulse_count, direction))

        instruction_ids: List[str] = []
        try:
            from communication.wifi_bus import get_wifi_bus
            wifi_bus = get_wifi_bus()
        except Exception:
            wifi_bus = None

        batch_id_prefix = f"sim_{int(time.time() * 1000) % 1000000:06d}"

        for esp_id, esp_moves in by_esp.items():
            esp_batch_id = f"{batch_id_prefix}_esp{esp_id}"
            formatted_moves = []
            for (m_id, pulses, direction) in esp_moves:
                sub_id = f"{esp_batch_id}_m{m_id + 1}"
                instruction_ids.append(sub_id)
                formatted_moves.append({
                    "motor": m_id + 1 if m_id < 6 else m_id,
                    "pulse": pulses,
                    "dir": direction,
                    "instructionID": sub_id
                })

            if wifi_bus and hasattr(wifi_bus, "broadcast_multi_command"):
                wifi_bus.broadcast_multi_command(
                    esp=esp_id,
                    moves=formatted_moves,
                    instruction_id=esp_batch_id
                )
            else:
                # Fallback to queuing and go_ahead
                for (m_id, pulses, direction) in esp_moves:
                    self.transmitter.send_normal_command(
                        esp_id=esp_id,
                        motor_id=m_id,
                        pulse_count=pulses,
                        direction=direction,
                        instruction_id=f"{esp_batch_id}_m{m_id + 1}"
                    )
                self.transmitter.send_go_ahead()

        if wait:
            if wifi_bus and hasattr(wifi_bus, "wait_for_multi_response") and instruction_ids:
                wifi_bus.wait_for_multi_response(instruction_ids, timeout=timeout)
            else:
                deadline = time.time() + timeout
                for (esp_id, motor_id, _, _) in moves:
                    rem = max(0.05, deadline - time.time())
                    self.wait_for_motor_idle(esp_id, motor_id, timeout=rem)

        return instruction_ids

    def wait_for_motor_idle(self, esp_id: int, motor_id: int, timeout: float = 3.0) -> bool:
        """Wait until the specified motor finishes moving or timeout expires."""
        deadline = time.time() + timeout
        time.sleep(0.05)
        while time.time() < deadline:
            with self._lock:
                state = self.motors.get((esp_id, motor_id))
                if state and state.mode != "MOVING_NORMAL":
                    return True
            time.sleep(0.02)
        return False

    def move_degrees(self, esp_id: int, motor_id: int, degrees: float, direction: int = DIRECTION_CW) -> None:
        """Execute a normal movement specified in degrees, converted using pulses_per_degree."""
        pulse_count = int(round(abs(degrees) * self.pulses_per_degree))
        self.move_pulses(esp_id, motor_id, pulse_count, direction)

    def lock_motor(self, esp_id: int, motor_id: int, direction: int = DIRECTION_CW) -> None:
        """
        Place a specific motor into Lock-On mode.
        Enforces single global lock invariant.
        """
        with self._lock:
            for m in self.motors.values():
                if m.mode == "LOCKED_ON":
                    m.mode = "IDLE"
            self.motors[(esp_id, motor_id)].mode = "LOCKED_ON"

        self.lock_manager.lock_motor(esp_id, motor_id, direction)

    def run_locked_motor(self) -> None:
        """Command the active lock-on motor to accelerate and run."""
        self.lock_manager.run()

    def brake_locked_motor(self) -> None:
        """Command the active lock-on motor to brake and stop."""
        self.lock_manager.brake()

    def clear_all_locks(self) -> None:
        """Broadcast 000000 to clear all locks."""
        with self._lock:
            for m in self.motors.values():
                if m.mode == "LOCKED_ON":
                    m.mode = "IDLE"
        self.lock_manager.clear_all_locks()

    def get_motor_state(self, esp_id: int, motor_id: int) -> Dict[str, object]:
        """Return the current tracked state of a specific motor."""
        with self._lock:
            m = self.motors[(esp_id, motor_id)]
            return {
                "esp_id": m.esp_id,
                "motor_id": m.motor_id,
                "mode": m.mode,
                "absolute_position": m.absolute_position,
                "last_execution_net_pulses": m.last_execution_net_pulses,
                "target_pulses": m.target_pulses,
                "current_direction": "CW" if m.current_direction == DIRECTION_CW else "CCW",
                "last_update_time": m.last_update_time
            }

    def get_all_positions(self) -> Dict[str, int]:
        """Return a mapping of all 20 motors to their absolute encoder pulse positions."""
        with self._lock:
            return {
                f"ESP{esp}_M{motor + 1}": self.motors[(esp_id, motor_id)].absolute_position
                for (esp_id, motor_id) in sorted(self.motors.keys())
                for esp, motor in [(esp_id, motor_id)]
            }
