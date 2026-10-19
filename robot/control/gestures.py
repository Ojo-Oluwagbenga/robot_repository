"""
Gestures and Action Controller for Humanoid Robot.
Executes physical motions for:
- NOD: Head / Neck pitch downward and return
- WAVE: Arm raise and greeting oscillation
- PICK: Arm lower, gripper grasp, and arm lift
Supports both live Raspberry Pi 5 GPIO hardware and software simulation mode.
"""

import time
import logging
from typing import Optional, Any

import sys
from pathlib import Path

# Ensure robot directory is in sys.path for internal imports
ROBOT_DIR = Path(__file__).resolve().parent.parent
if str(ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROBOT_DIR))

logger = logging.getLogger("robot.gestures")

class GestureController:
    """Executes physical robot gestures over the 3-wire motor bus."""

    def __init__(self, robot_system: Optional[Any] = None):
        self.robot = robot_system
        if self.robot is None:
            try:
                from robot.main import RobotSystem
                import os
                # Auto-detect simulation if not on Linux or if RP1 GPIO unavailable
                is_simulation = (os.name == "nt")
                self.robot = RobotSystem(simulation=is_simulation)
            except Exception as e:
                logger.warning(f"Could not initialize RobotSystem: {e}. Running in log mode.")
                self.robot = None

    def execute(self, action: str, details: str = "") -> bool:
        """Dispatch gesture by name: 'NOD', 'WAVE', or 'PICK'."""
        act = (action or "").upper().strip()
        print("\n" + "=" * 65)
        print(f"[*] >>> EXECUTING ROBOT ACTION: [{act}] <<<")
        if details:
            print(f"    Context: {details}")
        print("=" * 65)

        if act == "NOD":
            return self.nod()
        elif act == "WAVE":
            return self.wave()
        elif act == "PICK":
            return self.pick()
        else:
            print(f"[!] Unrecognized action '{act}'")
            return False

    def nod(self, cycles: int = 1) -> bool:
        """Nod head/neck up 10 pulses (100ms) and return (ESP 3 Motor 1)."""
        print("[Gesture: NOD] Tilting head/neck up 10 pulses (100ms) and returning...")
        if self.robot and hasattr(self.robot, "motor_manager"):
            try:
                # ESP 3, Motor 0 = Neck Pitch (Nod - N1)
                # 1. Nod Up (direction=0 / CW, 10 pulses = 100ms run)
                self.robot.motor_manager.move_pulses(esp_id=3, motor_id=0, pulse_count=10, direction=0)
                time.sleep(0.20)
                # 2. Return Down (direction=1 / CCW, 10 pulses = 100ms run)
                self.robot.motor_manager.move_pulses(esp_id=3, motor_id=0, pulse_count=10, direction=1)
                time.sleep(0.15)
                print("[Gesture: NOD] Completed.")
                return True
            except Exception as e:
                print(f"[!] Error executing NOD: {e}")
        else:
            time.sleep(0.35)
            print("[Gesture: NOD] (Simulated) Neck pitch 10 pulses up (100ms) and return executed.")
            return True
        return False

    def wave(self, cycles: int = 2) -> bool:
        """Raise right arm and oscillate greeting wave."""
        print(f"[Gesture: WAVE] Raising right arm and cycling {cycles} greeting waves...")
        if self.robot and hasattr(self.robot, "motor_manager"):
            try:
                # ESP 2 = Arm & Shoulder
                # 1. Raise arm forward/up
                self.robot.motor_manager.move_pulses(esp_id=2, motor_id=0, pulse_count=350, direction=0)
                time.sleep(0.3)
                # 2. Wave wrist/forearm back and forth
                for c in range(cycles):
                    self.robot.motor_manager.move_pulses(esp_id=2, motor_id=1, pulse_count=180, direction=1)
                    time.sleep(0.25)
                    self.robot.motor_manager.move_pulses(esp_id=2, motor_id=1, pulse_count=180, direction=0)
                    time.sleep(0.25)
                # 3. Lower arm back to resting
                self.robot.motor_manager.move_pulses(esp_id=2, motor_id=0, pulse_count=350, direction=1)
                time.sleep(0.3)
                print("[Gesture: WAVE] Completed.")
                return True
            except Exception as e:
                print(f"[!] Error executing WAVE: {e}")
        else:
            time.sleep(0.6)
            print(f"[Gesture: WAVE] (Simulated) Arm raised, {cycles} wave oscillations executed, arm lowered.")
            return True
        return False

    def pick(self) -> bool:
        """Lower arm, close gripper, and lift object up."""
        print("[Gesture: PICK] Lowering arm to target, closing gripper, lifting object...")
        if self.robot and hasattr(self.robot, "motor_manager"):
            try:
                # ESP 2 / 3: Arm Reach, Shoulder Pitch, Gripper
                # 1. Reach arm downward toward object (ESP 2 Motor 0, 300 pulses)
                self.robot.motor_manager.move_pulses(esp_id=2, motor_id=0, pulse_count=300, direction=1)
                time.sleep(0.4)
                # 2. Close gripper fingers to grasp object (ESP 2 Motor 2, 250 pulses)
                self.robot.motor_manager.move_pulses(esp_id=2, motor_id=2, pulse_count=250, direction=0)
                time.sleep(0.4)
                # 3. Lift arm back up holding object (ESP 2 Motor 0, 300 pulses)
                self.robot.motor_manager.move_pulses(esp_id=2, motor_id=0, pulse_count=300, direction=0)
                time.sleep(0.3)
                print("[Gesture: PICK] Object grasped and lifted.")
                return True
            except Exception as e:
                print(f"[!] Error executing PICK: {e}")
        else:
            time.sleep(0.7)
            print("[Gesture: PICK] (Simulated) Arm reached down, gripper closed, object lifted.")
            return True
        return False
