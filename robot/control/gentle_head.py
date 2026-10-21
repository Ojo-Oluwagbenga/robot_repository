#!/usr/bin/env python3
"""
Gentle Head Profiling & Ambient Life-like Movement Controller
=============================================================================
Controls Head Yaw (ESP 1, Motor 5) with smooth brake profile schedules:
  - When greeted or asked to introduce itself, gently turns its head back and forth
    (1-5-x-x) while vocalizing.
  - Ambient idle movement: randomly and gently looks left/right when no action or
    instruction is incoming, returning smoothly to center.
  - Non-blocking and yields immediately when new instructions or real work arrive.
=============================================================================
"""

import sys
import os
import time
import json
import random
import socket
import threading
import logging
from pathlib import Path
from typing import Optional, Dict, Any

# Ensure project paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

logger = logging.getLogger("gentle_head")

ESP_HEAD = 1
MOTOR_HEAD_YAW = 5
DEFAULT_UDP_PORT = 5005

DIR_RIGHT = 0
DIR_LEFT = 1


def send_profile_move(
    esp: int = ESP_HEAD,
    motor: int = MOTOR_HEAD_YAW,
    pulse: int = 150,
    direction: int = DIR_RIGHT,
    brake_count: int = 25,
    brake_ms: int = 6,
    timeout: float = 3.0,
    simulation: bool = False
) -> bool:
    """
    Transmits a profiled motor move packet over UDP (port 5005) to ESP32.
    Applies periodic micro-brakes for smooth, organic, non-jerky glide.
    """
    if simulation:
        print(f"  [SIM HEAD PROFILE] ESP #{esp} Motor {motor} -> {pulse} pulses Dir {direction} (Brakes: {brake_count}x{brake_ms}ms)")
        time.sleep(pulse * 0.002 + (brake_count * brake_ms * 0.001))
        return True

    # 1. Close power relay gate if needed
    try:
        from hardware.relay_gate import enable_motor_power_relay
        enable_motor_power_relay()
    except Exception:
        try:
            import subprocess
            subprocess.run(["pinctrl", "set", "26", "op", "dh"], capture_output=True, timeout=1)
        except Exception:
            pass

    # 2. Build profile packet
    packet = {
        "esp": esp,
        "runtime_motors": [
            {
                "motor": motor,
                "dir": direction,
                "pulse": pulse,
                "profile": {
                    "A": {
                        "duration_type": "pulse",
                        "start": 0,
                        "end": pulse,
                        "brake_count": brake_count,
                        "brake_ms": brake_ms
                    }
                }
            }
        ]
    }

    payload = json.dumps(packet).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)

    try:
        sock.sendto(payload, ("255.255.255.255", DEFAULT_UDP_PORT))
    except Exception as e:
        logger.warning(f"UDP broadcast failed: {e}")
        try:
            sock.sendto(payload, ("127.0.0.1", DEFAULT_UDP_PORT))
        except Exception:
            pass
    finally:
        sock.close()

    # Sleep estimated duration of smooth movement
    duration = (pulse * 0.003) + (brake_count * brake_ms * 0.001) + 0.1
    time.sleep(min(duration, timeout))
    return True


def gentle_head_turn_and_return(
    amplitude: int = 150,
    cycles: int = 1,
    brake_ms: int = 6,
    simulation: bool = False
) -> bool:
    """
    Gently turns head back and forth using brake profiling:
      1. Turn right (amplitude pulses)
      2. Turn left across center (amplitude * 2 pulses)
      3. Turn right back to center (amplitude pulses)
    """
    print(f"[*] Executing gentle head oscillation ({amplitude} pulses, {cycles} cycle(s))...")
    for c in range(cycles):
        brakes = max(10, amplitude // 6)
        # Turn Right
        send_profile_move(
            esp=ESP_HEAD,
            motor=MOTOR_HEAD_YAW,
            pulse=amplitude,
            direction=DIR_RIGHT,
            brake_count=brakes,
            brake_ms=brake_ms,
            simulation=simulation
        )
        time.sleep(0.08)

        # Turn Left across center
        sweep_left = amplitude * 2
        brakes_sweep = max(15, sweep_left // 6)
        send_profile_move(
            esp=ESP_HEAD,
            motor=MOTOR_HEAD_YAW,
            pulse=sweep_left,
            direction=DIR_LEFT,
            brake_count=brakes_sweep,
            brake_ms=brake_ms,
            simulation=simulation
        )
        time.sleep(0.08)

        # Return Right to center
        send_profile_move(
            esp=ESP_HEAD,
            motor=MOTOR_HEAD_YAW,
            pulse=amplitude,
            direction=DIR_RIGHT,
            brake_count=brakes,
            brake_ms=brake_ms,
            simulation=simulation
        )
        time.sleep(0.05)

    print("[*] Gentle head oscillation completed and returned to center.")
    return True


def gentle_head_random_idle_glance(simulation: bool = False) -> bool:
    """
    Performs a subtle, organic glance to one side and returns to center:
    Used for life-like ambient behavior when robot is idle.
    """
    amplitude = random.randint(70, 130)
    initial_dir = random.choice([DIR_RIGHT, DIR_LEFT])
    return_dir = DIR_LEFT if initial_dir == DIR_RIGHT else DIR_RIGHT
    dir_name = "RIGHT" if initial_dir == DIR_RIGHT else "LEFT"

    print(f"[*] [Ambient Idle] Gentle head glance {dir_name} ({amplitude} pulses)...")
    brakes = max(8, amplitude // 6)

    # 1. Glance away
    send_profile_move(
        esp=ESP_HEAD,
        motor=MOTOR_HEAD_YAW,
        pulse=amplitude,
        direction=initial_dir,
        brake_count=brakes,
        brake_ms=7,
        simulation=simulation
    )

    # Pause at glance target for an organic pause
    time.sleep(random.uniform(0.4, 0.9))

    # 2. Smoothly return to center
    send_profile_move(
        esp=ESP_HEAD,
        motor=MOTOR_HEAD_YAW,
        pulse=amplitude,
        direction=return_dir,
        brake_count=brakes,
        brake_ms=7,
        simulation=simulation
    )
    print("[*] [Ambient Idle] Returned to center.")
    return True


class IdleHeadManager:
    """
    Background ambient manager that triggers subtle random head movements
    when no action or instruction has been incoming for a configurable idle window.
    Immediately pauses and yields whenever an instruction arrives or active work occurs.
    """

    _instance = None
    _lock = threading.Lock()

    def __init__(self, min_idle_sec: float = 14.0, max_idle_sec: float = 30.0, simulation: bool = False):
        self.min_idle_sec = min_idle_sec
        self.max_idle_sec = max_idle_sec
        self.simulation = simulation
        self.last_activity_time = time.time()
        self.next_idle_interval = random.uniform(self.min_idle_sec, self.max_idle_sec)
        self.is_active_busy = False
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def get_instance(cls, simulation: bool = False) -> "IdleHeadManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(simulation=simulation)
            return cls._instance

    def notify_activity(self):
        """Notifies the manager that an action or command is executing."""
        self.last_activity_time = time.time()
        self.next_idle_interval = random.uniform(self.min_idle_sec, self.max_idle_sec)

    def set_busy(self, busy: bool):
        """Marks the robot as busy executing a physical instruction."""
        self.is_active_busy = busy
        self.notify_activity()

    def _ambient_loop(self):
        logger.info("[IdleHeadManager] Ambient idle movement loop started.")
        while self._running:
            time.sleep(1.0)
            if not self._running:
                break

            now = time.time()
            if self.is_active_busy:
                continue

            idle_duration = now - self.last_activity_time
            if idle_duration >= self.next_idle_interval:
                # Execute subtle glance
                try:
                    gentle_head_random_idle_glance(simulation=self.simulation)
                except Exception as e:
                    logger.warning(f"Error during ambient idle glance: {e}")

                self.last_activity_time = time.time()
                self.next_idle_interval = random.uniform(self.min_idle_sec, self.max_idle_sec)

    def start(self):
        """Starts the ambient idle manager thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._ambient_loop, daemon=True, name="IdleHeadThread")
        self._thread.start()
        print("[*] Ambient Idle Head Behavior active (random glance every 14-30s when idle).")

    def stop(self):
        """Stops the ambient idle manager."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Gentle Head Movement & Ambient Idle Controller")
    parser.add_argument("--amplitude", type=int, default=150, help="Movement amplitude in pulses")
    parser.add_argument("--cycles", type=int, default=1, help="Oscillation cycles")
    parser.add_argument("--idle-demo", action="store_true", help="Demo ambient idle glances")
    parser.add_argument("--sim", action="store_true", help="Simulation mode")
    args = parser.parse_args()

    if args.idle_demo:
        print("[*] Demonstrating ambient idle glances. Press Ctrl+C to stop.")
        for _ in range(3):
            gentle_head_random_idle_glance(simulation=args.sim)
            time.sleep(2.0)
    else:
        gentle_head_turn_and_return(amplitude=args.amplitude, cycles=args.cycles, simulation=args.sim)


if __name__ == "__main__":
    main()
