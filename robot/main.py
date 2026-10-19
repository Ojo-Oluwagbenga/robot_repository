#!/usr/bin/env python3
"""
Main entry point for the Raspberry Pi 5 Humanoid Robot Controller.
Orchestrates hardware abstraction, communication bus, feedback reception,
motor coordination, and lock management.

Usage:
  python3 main.py                     # Run on Raspberry Pi 5 with hardware GPIO
  python3 main.py --simulation        # Run in software simulation mode (No GPIO needed)
  python3 main.py --debug 2           # Run with detailed packet debugging (Levels 0-3)
  python3 main.py --demo              # Run automated multi-motor demo sequence
"""

import os
import sys
import time
import argparse
import signal
import re
from typing import Optional, Dict, Any

from config import DEBUG_LEVEL, DIRECTION_CW, DIRECTION_CCW
from debug.logger import setup_robot_logger, get_logger
from hardware.gpio_bus import create_bus, SimulationBus
from communication.feedback import FeedbackQueue, FeedbackPacket
from communication.transmitter import BusTransmitter
from communication.receiver import FeedbackReceiver
from communication.protocol import encode_feedback_packet
from control.lock_manager import LockManager
from control.motor_manager import MotorManager

# ==============================================================================
# MOTOR MAPPINGS (M2, N1 & N2)
# ==============================================================================
# M2: Motor 2               -> ESP 1, Motor 4 (motor_id = 3)
# N1: Neck Up / Down (Nod)  -> ESP 3, Motor 1 (motor_id = 0)
# N2: Neck Turn (Left/Right)-> ESP 3, Motor 2 (motor_id = 1)
ROBOT_MOTORS: Dict[str, Dict[str, Any]] = {
    "M2": {
        "esp_id": 1,
        "motor_id": 3,
        "name": "Motor 2 [ESP 1, Motor 4]",
    },
    "N1": {
        "esp_id": 3,
        "motor_id": 0,
        "name": "Neck Up / Down (Nod) [ESP 3, Motor 1]",
    },
    "N2": {
        "esp_id": 3,
        "motor_id": 1,
        "name": "Neck Left / Right (Turn) [ESP 3, Motor 2]",
    },
    "B1_F": {
        "esp_id": 4,
        "motor_id": 0,
        "name": "Board 1 Front Drive [ESP 4, Motor 1]",
    },
    "B1_R": {
        "esp_id": 4,
        "motor_id": 1,
        "name": "Board 1 Rear Drive [ESP 4, Motor 2]",
    },
    "B2_F": {
        "esp_id": 4,
        "motor_id": 2,
        "name": "Board 2 Front Drive [ESP 4, Motor 3]",
    },
    "B2_R": {
        "esp_id": 4,
        "motor_id": 3,
        "name": "Board 2 Rear Drive [ESP 4, Motor 4]",
    },
}
NECK_MOTORS = ROBOT_MOTORS


def parse_and_execute_motor_command(robot: "RobotSystem", cmd_str: str) -> bool:
    """
    Parse and execute motor shortcut commands such as M2_20, M2_-20, N1_20, N2_20,
    or direct syntax like E1_M4_20.

    Format:
      M2_<pulses> -> Motor 2 (ESP 1, Motor 4)
      N1_<pulses> -> Neck Up/Down (ESP 3, Motor 1)
      N2_<pulses> -> Neck Left/Right (ESP 3, Motor 2)
      E<esp>_M<motor>_<pulses> -> Direct ESP/Motor move (e.g. E1_M4_20)
      Positive pulses (>0) -> Clockwise (CW / direction 0)
      Negative pulses (<0) -> Counter-Clockwise (CCW / direction 1)

    Returns:
      True if the command was recognized and handled (or had a format error), False otherwise.
    """
    clean = cmd_str.strip()

    # 1. Check named shortcuts (M2, N1, N2, etc.)
    import re
    shortcut_pattern = r'^(' + '|'.join(re.escape(k) for k in ROBOT_MOTORS.keys()) + r')[_\s:]*([+-]?\d+)$'
    match = re.match(shortcut_pattern, clean, re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        pulses_val = int(match.group(2))
        motor_info = ROBOT_MOTORS[code]
        esp_id = motor_info["esp_id"]
        motor_id = motor_info["motor_id"]
        desc = motor_info["name"]
    else:
        # Check if user typed just the motor code without pulses (e.g. "M2")
        if clean.upper() in ROBOT_MOTORS:
            print(f"[!] Please specify pulse count for {clean.upper()}, e.g. {clean.upper()}_20 or {clean.upper()}_-20")
            return True

        # 2. Check direct syntax: E<1-5>_M<1-4>_<pulses> or ESP<1-5>_M<1-4>_<pulses>
        direct_match = re.match(r'^(?:ESP|E)?([1-5])[_\s:]*M([1-4])[_\s:]*([+-]?\d+)$', clean, re.IGNORECASE)
        if direct_match:
            esp_id = int(direct_match.group(1))
            motor_num = int(direct_match.group(2))
            motor_id = motor_num - 1
            pulses_val = int(direct_match.group(3))
            code = f"E{esp_id}_M{motor_num}"
            desc = f"ESP {esp_id}, Motor {motor_num}"
        else:
            return False

    if pulses_val == 0:
        print("[!] Pulse count cannot be 0.")
        return True

    pulse_count = abs(pulses_val)
    if pulse_count > 32767:
        print(f"[!] Pulse count {pulse_count} exceeds protocol maximum of 32767.")
        return True

    direction = DIRECTION_CW if pulses_val > 0 else DIRECTION_CCW
    dir_name = "CW (0)" if direction == DIRECTION_CW else "CCW (1)"

    print(f"\n[MOTOR MOVE] {code} ({desc}): moving {pulse_count} pulses {dir_name} -> ESP {esp_id}, Motor {motor_id + 1}")
    try:
        # Verify relay motor power system is active before dispatching motion
        if not robot.bus.is_motor_power_enabled():
            robot.bus.enable_motor_power()
            time.sleep(0.05)

        robot.motor_manager.move_pulses(
            esp_id=esp_id,
            motor_id=motor_id,
            pulse_count=pulse_count,
            direction=direction
        )
        print(f"[OK] {code} movement ({pulse_count} pulses {dir_name}) dispatched successfully!")

        # Wait for movement to physically complete before any shutdown or subsequent command
        wait_timeout = max(1.5, pulse_count * 0.05 + 0.5)
        robot.motor_manager.wait_for_motor_idle(esp_id, motor_id, timeout=wait_timeout)
        print(f"[COMPLETE] {code} execution finished.\n")
    except Exception as e:
        print(f"[ERROR] Failed to execute {code} move: {e}\n")

    return True


parse_and_execute_neck_command = parse_and_execute_motor_command


class RobotSystem:
    """Encapsulates the complete robot control stack."""

    def __init__(self, simulation: bool = False, debug_level: int = DEBUG_LEVEL):
        # 1. Initialize Logger
        self.logger = setup_robot_logger(level=debug_level)
        self.logger.info("==================================================")
        self.logger.info("Starting Raspberry Pi 5 Humanoid Robot Controller")
        self.logger.info("==================================================")
        self.logger.info(f"Mode: {'SIMULATION' if simulation else 'HARDWARE (Pi 5 GPIO)'}")
        self.logger.info(f"Debug Level: {debug_level}")

        # 2. Initialize Hardware / Virtual Bus
        self.bus = create_bus(simulation=simulation)

        # 3. Initialize Wi-Fi Communication Bus (UDP Broadcast JSON Protocol)
        from communication.wifi_bus import get_wifi_bus
        self.wifi_bus = get_wifi_bus()
        self.logger.info("Wi-Fi Communication Bus active (UDP Broadcast JSON packets on port 5005).")

        # 4. Initialize Communication Layer
        self.feedback_queue = FeedbackQueue()
        self.transmitter = BusTransmitter(self.bus)
        self.receiver = FeedbackReceiver(self.bus, queue=self.feedback_queue)

        # 5. Initialize Control Layer
        self.lock_manager = LockManager(self.bus, self.transmitter)
        self.motor_manager = MotorManager(
            transmitter=self.transmitter,
            receiver=self.receiver,
            lock_manager=self.lock_manager
        )

        # 6. Start Background Feedback Thread
        self.receiver.start_background_listener(interval_sec=0.005)

        # 7. Activate Motor Power System (Physical Pin 37 / GPIO 26 goes HIGH)
        self.bus.enable_motor_power()
        self.logger.info("All systems initialized and ready. Wi-Fi JSON protocol active.")

    def shutdown(self) -> None:
        """Graceful shutdown of all subsystems."""
        self.logger.info("Shutting down robot system...")
        try:
            # First immediately cut motor power for physical safety!
            self.bus.disable_motor_power()
            if hasattr(self, "wifi_bus") and self.wifi_bus:
                self.wifi_bus.send_stop()
                self.wifi_bus.close()
            self.lock_manager.clear_all_locks()
            self.receiver.stop_background_listener()
            self.bus.close()
        except Exception as e:
            self.logger.error(f"Error during shutdown: {e}")
        self.logger.info("Robot system stopped cleanly.")


def run_interactive_cli(robot: RobotSystem) -> None:
    """Interactive command-line interface for motor pulse controls."""
    print("\n" + "=" * 60)
    print("        HUMANOID ROBOT: MOTOR CONTROLLER")
    print("=" * 60)
    print("  Hardware Mapping:")
    print("    M2   : Motor 2                   -> ESP 1, Motor 4")
    print("    N1   : Neck Nod  (Up / Down)    -> ESP 3, Motor 1")
    print("    N2   : Neck Turn (Left / Right) -> ESP 3, Motor 2")
    print("    B1_F : Board 1 Front Drive      -> ESP 4, Motor 1")
    print("    B1_R : Board 1 Rear Drive       -> ESP 4, Motor 2")
    print("    B2_F : Board 2 Front Drive      -> ESP 4, Motor 3")
    print("    B2_R : Board 2 Rear Drive       -> ESP 4, Motor 4")
    print("-" * 60)
    print("  Control Syntax (Signed Pulses):")
    print("    M2_<pulses>    (e.g. M2_20 for CW,     M2_-20 for CCW)")
    print("    N1_<pulses>    (e.g. N1_20 for CW/Up,  N1_-20 for CCW/Down)")
    print("    N2_<pulses>    (e.g. N2_20 for CW/Right, N2_-20 for CCW/Left)")
    print("    B1_F_<pulses>  (e.g. B1_F_50 for CW,   B1_F_-50 for CCW)")
    print("    E<1-5>_M<1-4>_<pulses> (e.g. E4_M1_50)")
    print("")
    print("  Commands:")
    print("    M2_20, N1_20, N2_-20  - Move motor by pulse count")
    print("    status                - View current motor encoder positions")
    print("    debug <0-3>           - Change logging level")
    print("    exit / quit           - Exit program")
    print("=" * 60 + "\n")

    while True:
        try:
            cmd_line = input("robot> ").strip()
            if not cmd_line:
                continue

            # First, check for M2_<pulses>, N1_<pulses>, N2_<pulses> command
            if parse_and_execute_motor_command(robot, cmd_line):
                continue

            parts = cmd_line.split()
            cmd = parts[0].lower()

            if cmd in ("exit", "quit"):
                break

            elif cmd == "status":
                positions = robot.motor_manager.get_all_positions()
                print("\nCurrent Motor Encoder Positions:")
                for k, v in positions.items():
                    print(f"  {k:12s}: {v:6d} pulses")
                print()

            elif cmd == "debug":
                if len(parts) < 2:
                    print("Usage: debug <0-3>")
                    continue
                lvl = int(parts[1])
                robot.logger.set_level(lvl)
                print(f"Debug level set to {lvl}")

            elif cmd in ("help", "?"):
                print("Available commands:")
                print("  M2_<pulses>  - Move M2 on ESP 1, Motor 4 (e.g. M2_20 for CW, M2_-20 for CCW)")
                print("  N1_<pulses>  - Move N1 on ESP 3, Motor 1 (e.g. N1_20 for CW, N1_-20 for CCW)")
                print("  N2_<pulses>  - Move N2 on ESP 3, Motor 2 (e.g. N2_20 for CW, N2_-20 for CCW)")
                print("  status       - View motor positions")
                print("  exit / quit  - Exit")

            else:
                print(f"[!] Unknown command '{cmd_line}'. Use M2_<pulses>, N1_<pulses>, or N2_<pulses> (e.g. M2_20, M2_-20).")

        except (KeyboardInterrupt, EOFError):
            break
        except Exception as e:
            print(f"Command Error: {e}")


def run_automated_demo(robot: RobotSystem) -> None:
    """Run an automated demonstration of normal commands, interruption, and lock-on control."""
    print("\n--- Running Automated Humanoid Robot Demo ---")
    logger = robot.logger

    # 1. Normal Command on ESP 1 Motor 1 (500 pulses CW)
    logger.info("[DEMO 1] Sending Normal Move: ESP 1, Motor 1, 500 pulses CW")
    robot.motor_manager.move_pulses(esp_id=1, motor_id=0, pulse_count=500, direction=DIRECTION_CW)
    time.sleep(0.05)

    # If simulation bus, inject normal completion feedback (+500 pulses)
    if isinstance(robot.bus, SimulationBus):
        robot.bus.queue_simulated_feedback(1, encode_feedback_packet(0, 500))
        time.sleep(0.02)

    # 2. Interruption Test on ESP 1 Motor 1
    # First command starts moving for 1000 pulses
    logger.info("[DEMO 2] Testing Interruption: ESP 1, Motor 1 starting 1000 pulses")
    robot.motor_manager.move_pulses(esp_id=1, motor_id=0, pulse_count=1000, direction=DIRECTION_CW)

    # Interrupted midway! Simulate that it only reached +350 pulses before new command arrives
    if isinstance(robot.bus, SimulationBus):
        robot.bus.queue_simulated_feedback(1, encode_feedback_packet(0, 350))

    time.sleep(0.02)
    logger.info("[DEMO 2] Sending new command to SAME motor (Interruption): ESP 1, Motor 1, 200 pulses CCW")
    robot.motor_manager.move_pulses(esp_id=1, motor_id=0, pulse_count=200, direction=DIRECTION_CCW)
    time.sleep(0.05)

    # 3. Lock-On Test: ESP 3 Motor 2
    logger.info("[DEMO 3] Testing Lock-On Mode: ESP 3, Motor 2, CW")
    robot.motor_manager.lock_motor(esp_id=3, motor_id=1, direction=DIRECTION_CW)
    time.sleep(0.05)

    logger.info("[DEMO 3] Toggling DATA HIGH (Run)")
    robot.motor_manager.run_locked_motor()
    time.sleep(0.05)

    logger.info("[DEMO 3] Toggling DATA LOW (Brake)")
    robot.motor_manager.brake_locked_motor()
    time.sleep(0.05)

    # 4. Global Lock Invariant Test: Switch lock to ESP 4 Motor 1
    logger.info("[DEMO 4] Global Invariant: Switching lock from ESP 3 to ESP 4 Motor 1")
    if isinstance(robot.bus, SimulationBus):
        # ESP 3 reports its accumulated lock-on movement (+120 pulses)
        robot.bus.queue_simulated_feedback(3, encode_feedback_packet(1, 120))

    robot.motor_manager.lock_motor(esp_id=4, motor_id=0, direction=DIRECTION_CCW)
    time.sleep(0.05)

    # 5. Clear All Locks (000000)
    logger.info("[DEMO 5] Broadcasting 000000 to clear all locks")
    if isinstance(robot.bus, SimulationBus):
        # ESP 4 reports its accumulated movement before clearing
        robot.bus.queue_simulated_feedback(4, encode_feedback_packet(0, -85))

    robot.motor_manager.clear_all_locks()
    time.sleep(0.05)

    logger.info("[DEMO COMPLETE] Demo finished successfully.\n")


def run_gestures_listener_mode(robot: RobotSystem) -> None:
    """Runs the lightweight Vosk ear and PC Ollama AI to execute physical gestures silently."""
    try:
        from robot.control.gestures import GestureController
        from robot.brain.ear import RobotEar
        from robot.brain.ollama_client import OllamaClient
        from robot.brain.config import VOICE_SILENCE_TIMEOUT, VOSK_MODEL_PATH
        from robot.listen_gestures import extract_instruction_from_conversation
    except ImportError:
        from control.gestures import GestureController
        from brain.ear import RobotEar
        from brain.ollama_client import OllamaClient
        from brain.config import VOICE_SILENCE_TIMEOUT, VOSK_MODEL_PATH
        from listen_gestures import extract_instruction_from_conversation

    gestures = GestureController(robot_system=robot)
    ollama = OllamaClient()

    print("\n" + "=" * 65)
    print("      NOVA LIGHTWEIGHT CONVERSATION LISTENER (GESTURES)")
    print("=" * 65)
    print("  * STT Engine    : Lightweight Vosk (Offline, Low CPU/RAM)")
    print(f"  * AI Brain      : Ollama on PC ({ollama.active_url or ollama.primary_url})")
    print("  * Mouth / Audio : DISABLED (Silent physical movement execution)")
    print("  * Actions       : [PICK] | [NOD] | [WAVE]")
    print("=" * 65)

    ear = RobotEar(stt_engine="vosk", model_path=VOSK_MODEL_PATH, silence_timeout=VOICE_SILENCE_TIMEOUT)
    if not ear._initialize_engine():
        print("[!] Could not start Vosk ear engine.")
        return

    if not ear.start_listening():
        print("[!] Could not start microphone stream.")
        return

    print("\n[READY] Listening to conversation... Speak naturally into microphone.")
    print("        Mention 'pick', 'nod', or 'wave' to see action executed.\n")

    try:
        while True:
            def on_partial(txt: str):
                print(f"\r[Hearing]: {txt} ...", end="", flush=True)

            user_speech = ear.listen(silence_timeout=VOICE_SILENCE_TIMEOUT, on_partial=on_partial)
            if not user_speech or not user_speech.strip():
                continue

            clean_input = user_speech.strip()
            print(f"\r[Heard]: \"{clean_input}\"                               ")

            if clean_input.lower() in ["exit", "quit", "stop listening"]:
                break

            action, reason = extract_instruction_from_conversation(clean_input, ollama)
            if action in ["PICK", "NOD", "WAVE"]:
                print(f"[Action Detected]: >>> {action} <<< ({reason})")
                gestures.execute(action, details=clean_input)
            else:
                print(f"[Conversation]: No motion instruction (monitoring...)")
            print("\nListening... Speak now:")
    except (KeyboardInterrupt, EOFError):
        print("\n\n[*] Listener stopped by user.")
    finally:
        ear.stop_listening()


def main() -> None:
    parser = argparse.ArgumentParser(description="Raspberry Pi 5 Humanoid Robot Controller")
    parser.add_argument("command", nargs="?", default=None, help="Optional direct motor command (e.g. M2_20, N1_20, N2_-20)")
    parser.add_argument("--cmd", type=str, help="Execute a single command (e.g. M2_20, N1_20, N2_-20) and exit")
    parser.add_argument("--simulation", action="store_true", help="Run in software simulation mode (no GPIO required)")
    parser.add_argument("--debug", type=int, default=DEBUG_LEVEL, choices=[0, 1, 2, 3], help="Set debug verbosity (0-3)")
    parser.add_argument("--demo", action="store_true", help="Run automated demo sequence and exit")
    parser.add_argument("--cli", action="store_true", help="Open interactive CLI console directly")
    parser.add_argument("--listen-gestures", action="store_true", help="Run Vosk + PC Ollama silent gesture listener (Pick, Nod, Wave)")
    parser.add_argument("--bridge", action="store_true", help="Run Cloud Bridge listener & local instruction queue executor")
    parser.add_argument("--bridge-url", type=str, default=None, help="Cloud bridge site URL (e.g. http://localhost:3000)")
    args = parser.parse_args()

    if args.bridge:
        from bridge_main import run_bridge_system
        from bridge.config import DEFAULT_BRIDGE_URL
        target_url = args.bridge_url or DEFAULT_BRIDGE_URL
        run_bridge_system(bridge_url=target_url)
        return

    robot = RobotSystem(simulation=args.simulation, debug_level=args.debug)

    def sig_handler(sig, frame):
        robot.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        target_cmd = args.cmd or args.command
        if target_cmd:
            if not parse_and_execute_motor_command(robot, target_cmd):
                print(f"[!] Unrecognized direct command: '{target_cmd}'. Format: M2_<pulses>, N1_<pulses>, or N2_<pulses> (e.g. M2_20 or M2_-20)")
            return

        if args.listen_gestures:
            run_gestures_listener_mode(robot)
        elif args.demo:
            run_automated_demo(robot)
        else:
            # Default entry: Interactive N1/N2 neck pulse CLI
            run_interactive_cli(robot)
    finally:
        robot.shutdown()


if __name__ == "__main__":
    main()
