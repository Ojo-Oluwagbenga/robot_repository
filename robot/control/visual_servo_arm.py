#!/usr/bin/env python3
"""
Autonomous Visual Servoing Arm Controller for LUKU Humanoid Robot.
=============================================================================
Controls the 6-DOF arm and gripper using closed-loop binocular stereo vision
(from track_hand_ball.py / HandBallGraspTracker):
  - Tracks target ball (configurable color) and index/thumb fingertips in 3D.
  - Dynamically learns and updates an empirical arm pose Jacobian (tracking how
    the hand moves in response to each motor) to make educated guesses on next moves.
  - Uncertainty gating: until the system is confident in a joint's directional effect,
    movements are strictly capped at small exploratory steps (100 pulses).
  - Table Level Collision Guard: ensures neither the wrist nor the elbow descends below
    the table plane level.
  - When the ball is positioned directly between index and thumb, clamps fingers
    (with breaker / brake profile), lifts arm, and verifies if the ball moved with the hand.
  - On successful pick: announces "Ball is now picked" through the LUKU app speaker.
  - On missed pick: releases fingers and re-attempts the approach.

Motor Configuration (from relaxed position beside torso):
  - Shoulder:
      * Front/Back Stepper : ESP 5, Motor 1 (Stepper A on GPIO 16/19)
      * Sideways (Top)     : ESP 1, Motor 1
  - Elbow:
      * Sideways (Humerus) : ESP 2, Motor 1
      * Front/Back         : ESP 1, Motor 3 (moved by sideways motor)
  - Wrist:
      * Ulna Rotation      : ESP 1, Motor 4 (palm facing body)
      * Palm Towards/Away  : ESP 1, Motor 2
  - Gripper:
      * Finger Clamping    : ESP 3, Motor 2 (Dir 0 = Close with breaker, Dir 1 = Open)
=============================================================================
"""

import sys
import os
import time
import math
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Ensure paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROBOT_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent if ROBOT_DIR.name == "robot" else ROBOT_DIR
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

logger = logging.getLogger("visual_servo_arm")

# Try imports
try:
    from robot.vision.hand_ball_tracker import HandBallGraspTracker, GraspResult
    from robot.hardware.camera import BinocularCamera
    from robot.bridge.phone_speaker import speak_to_phone
except (ImportError, ValueError):
    try:
        from vision.hand_ball_tracker import HandBallGraspTracker, GraspResult
        from hardware.camera import BinocularCamera
        from bridge.phone_speaker import speak_to_phone
    except (ImportError, ValueError):
        HandBallGraspTracker = None
        BinocularCamera = None
        speak_to_phone = lambda msg: print(f"[PHONE_SPEAKER] {msg}")

# Motor Dispatcher import
try:
    from robot.test_individual_motor import execute_multi_motor_test, run_pi_stepper
except ImportError:
    try:
        from test_individual_motor import execute_multi_motor_test, run_pi_stepper
    except ImportError:
        execute_multi_motor_test = None
        run_pi_stepper = None

# =============================================================================
# MOTOR CONFIGURATION CONSTANTS
# =============================================================================
# Format: (ESP_ID, MOTOR_ID)
JOINT_SHOULDER_FB = (5, 1)    # Stepper A (Pi GPIO 16/19)
JOINT_SHOULDER_SIDE = (1, 1)  # On top of FB motor
JOINT_ELBOW_SIDE = (2, 1)     # Humerus rotation
JOINT_ELBOW_FB = (1, 3)       # Flexion / extension
JOINT_WRIST_ROT = (1, 4)      # Ulna rotation
JOINT_WRIST_PITCH = (1, 2)    # Palm towards / away
JOINT_GRIPPER = (3, 2)        # Fingers
JOINT_HEAD_YAW = (1, 5)       # Head Yaw Left/Right (1-5-x-x)

MIN_NOTICEABLE_PULSES = 100   # Motors show noticeable motion starting from 100 pulses
CONFIDENT_MAX_PULSES = 200    # Only allowed when joint certainty score >= 0.7
STEP_INCREMENT = 30           # Increment step when movement produces no change

DIR_CW = 0
DIR_CCW = 1

# Head Yaw Directions & Limits
DIR_YAW_RIGHT = 0             # Rotate Right (+ pulses)
DIR_YAW_LEFT = 1              # Rotate Left (- pulses)
MAX_HEAD_SCAN_PULSES = 1000   # Scan range: [-1000, +1000] pulses from center
HEAD_SCAN_STEP = 200          # Pulses per scan step

# Table height safety parameters (in camera pixel coordinates: row 0 is top, higher y is lower down)
DEFAULT_TABLE_ROW_LIMIT = 600   # Maximum pixel Y: beyond this is table collision zone
TABLE_SAFETY_MARGIN_PX = 25     # Stop 25 pixels before table surface


class JointModel:
    """Tracks empirical kinematic influence and confidence score of each motor."""

    def __init__(self, name: str, default_axis: Tuple[float, float, float]):
        self.name = name
        # Estimated (dX, dY, dZ) displacement per 100 pulses in direction 0 (CW)
        self.vector_d0 = list(default_axis)
        # Certainty score: 0.0 (untested) to 1.0 (highly certain)
        self.certainty: float = 0.20
        self.samples_collected: int = 0

    def update_observation(self, dir_moved: int, pulses: int, actual_disp: Tuple[float, float, float]):
        """Updates internal model using observed physical hand displacement."""
        if pulses < 50:
            return

        scale = 100.0 / float(pulses)
        norm_disp = [actual_disp[0] * scale, actual_disp[1] * scale, actual_disp[2] * scale]
        if dir_moved == DIR_CCW:
            # Reverse sign for CCW
            norm_disp = [-v for v in norm_disp]

        mag = math.sqrt(norm_disp[0] ** 2 + norm_disp[1] ** 2 + norm_disp[2] ** 2)
        if mag > 3.0:  # Noticeable physical motion detected
            # Exponential moving average
            alpha = 0.35
            for i in range(3):
                self.vector_d0[i] = (1.0 - alpha) * self.vector_d0[i] + alpha * norm_disp[i]
            # Increase certainty score
            self.certainty = min(1.0, self.certainty + 0.20)
            self.samples_collected += 1
            logger.info(f"[JointModel: {self.name}] Updated vector: {self.vector_d0}, Certainty: {self.certainty:.2f}")
        else:
            # Low or no movement detected; lower certainty slightly
            self.certainty = max(0.10, self.certainty - 0.15)
            logger.info(f"[JointModel: {self.name}] Negligible motion ({mag:.1f}px); Certainty reduced to {self.certainty:.2f}")


class VisualServoArmController:
    """
    Closed-loop visual servo controller driving humanoid arm to track & grasp a ball.
    Features:
      - Empirical Jacobian arm pose learning.
      - Small-movement caution until joint behavior is certain.
      - Hard table plane collision protection.
      - Closed-loop grasp verification with vocal notification through LUKU phone speaker.
    """

    def __init__(
        self,
        ball_color: str = "red",
        camera_index: int = 0,
        camera_width: int = 2560,
        camera_height: int = 720,
        simulation: bool = False,
    ):
        self.ball_color = ball_color.lower().strip()
        self.camera_index = camera_index
        self.camera_width = camera_width
        self.camera_height = camera_height
        self.simulation = simulation or (execute_multi_motor_test is None)

        self.tracker: Optional[HandBallGraspTracker] = None
        self.cap = None

        # Table safety plane (calibrated or dynamically inferred from ball bottom)
        self.table_limit_y = DEFAULT_TABLE_ROW_LIMIT
        self.table_inferred = False

        # Empirical Joint Knowledge Base (dX, dY, dZ per 100 pulses CW)
        # X: +right, -left | Y: +downward (camera row), -upward | Z: +away, -closer
        self.joint_models: Dict[Tuple[int, int], JointModel] = {
            JOINT_SHOULDER_FB: JointModel("Shoulder FB (5-1)", default_axis=(0.0, 15.0, 10.0)),
            JOINT_SHOULDER_SIDE: JointModel("Shoulder Side (1-1)", default_axis=(20.0, 0.0, 0.0)),
            JOINT_ELBOW_SIDE: JointModel("Elbow Humerus (2-1)", default_axis=(18.0, 2.0, 0.0)),
            JOINT_ELBOW_FB: JointModel("Elbow Flexion (1-3)", default_axis=(0.0, 25.0, 5.0)),
            JOINT_WRIST_ROT: JointModel("Wrist Ulna Rot (1-4)", default_axis=(5.0, 0.0, 0.0)),
            JOINT_WRIST_PITCH: JointModel("Wrist Pitch (1-2)", default_axis=(0.0, 10.0, 0.0)),
        }

        # Arm & Head state
        self.current_pose_name = "RELAXED"
        self.current_head_yaw = 0  # Net yaw pulses from central home position (1-5-x-x)
        self._init_vision()

    def _init_vision(self):
        """Initializes the HandBallGraspTracker model."""
        if HandBallGraspTracker is None:
            logger.warning("HandBallGraspTracker class unavailable; running in mock vision mode.")
            return

        try:
            self.tracker = HandBallGraspTracker(ball_color=self.ball_color)
            logger.info(f"Initialized HandBallGraspTracker for '{self.ball_color}' ball.")
        except Exception as e:
            logger.error(f"Failed to load HandBallGraspTracker: {e}")
            self.tracker = None

    def open_camera(self) -> bool:
        """Opens binocular stereo camera capture."""
        if self.simulation:
            logger.info("[Sim] Camera simulation active.")
            return True

        if self.cap is not None and self.cap.isOpened():
            return True

        try:
            import cv2
            self.cap = cv2.VideoCapture(self.camera_index)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera_height)
            self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            if not self.cap.isOpened():
                logger.error(f"Cannot open camera device /dev/video{self.camera_index}")
                return False
            logger.info(f"Opened stereo camera /dev/video{self.camera_index} ({self.camera_width}x{self.camera_height})")
            return True
        except Exception as e:
            logger.error(f"Error opening camera: {e}")
            return False

    def close_camera(self):
        """Releases camera resource."""
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def read_stereo_frame(self) -> Tuple[Optional[Any], Optional[Any]]:
        """Reads one stereo frame, splitting horizontally into left and right eyes."""
        if self.simulation or self.cap is None:
            # Return synthetic frames for simulation
            import numpy as np
            h, w = 720, 1280
            dummy_left = np.zeros((h, w, 3), dtype=np.uint8)
            dummy_right = np.zeros((h, w, 3), dtype=np.uint8)
            return dummy_left, dummy_right

        ret, frame = self.cap.read()
        if not ret or frame is None:
            return None, None

        h, w = frame.shape[:2]
        half_w = w // 2
        left_eye = frame[:, :half_w].copy()
        right_eye = frame[:, half_w:].copy()
        return left_eye, right_eye

    def dispatch_moves(self, commands: List[Tuple[int, int, int, int]], timeout: float = 4.0) -> bool:
        """
        Executes single or simultaneous motor moves across ESP boards and Pi Stepper.
        commands: list of (esp, motor, pulse, dir)
        """
        if not commands:
            return True

        cmd_desc = " | ".join(f"{e}-{m}-{p}-{d}" for e, m, p, d in commands)
        logger.info(f"[ARM MOVE] Dispatched: {cmd_desc}")

        if self.simulation:
            print(f"  [SIMULATED ARM MOTION] Executed: {cmd_desc}")
            time.sleep(0.15)
            return True

        if execute_multi_motor_test is not None:
            try:
                return execute_multi_motor_test(commands, timeout=timeout)
            except Exception as e:
                logger.error(f"execute_multi_motor_test error: {e}")
                return False

        logger.warning(f"No motor dispatcher available for: {cmd_desc}")
        return False

    # =========================================================================
    # ARM POSE PRESETS
    # =========================================================================
    def move_to_table_ready_pose(self) -> bool:
        """
        Moves the arm from relaxed vertical pose beside torso into the natural
        human table-picking stance at chest level:
          - Elbow is above, wrist is down.
          - Forearm reaches forward and angles toward the table.
          - Fingers open ready to encompass the ball.
        """
        print("\n" + "=" * 65)
        print("[*] MOVING ARM TO TABLE READY PRE-GRASP STANCE...")
        print("    (Elbow above, wrist down, fingers open, chest-level table view)")
        print("=" * 65)

        # 1. Open gripper fingers first
        self.open_fingers(pulses=250)

        # 2. Simultaneous coordinated movement to table stance:
        #    - Stepper 5-1: Rotate shoulder forward (350 steps, CW/0)
        #    - Shoulder 1-1: Keep close to torso / slight outward (120 pulses, 0)
        #    - Elbow 1-3: Flex elbow forward/down (380 pulses, CW/0)
        #    - Wrist 1-4: Ulna rotation to level palm (150 pulses, 0)
        #    - Wrist 1-2: Pitch palm slightly down toward table (160 pulses, 0)
        moves = [
            (JOINT_SHOULDER_FB[0], JOINT_SHOULDER_FB[1], 350, DIR_CW),
            (JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], 380, DIR_CW),
            (JOINT_WRIST_ROT[0], JOINT_WRIST_ROT[1], 150, DIR_CW),
            (JOINT_WRIST_PITCH[0], JOINT_WRIST_PITCH[1], 160, DIR_CW),
        ]
        ok = self.dispatch_moves(moves, timeout=6.0)
        self.current_pose_name = "TABLE_READY"
        time.sleep(0.3)
        return ok

    def move_to_relaxed_pose(self) -> bool:
        """Returns arm back to relaxed vertical resting position beside torso."""
        print("[*] Returning arm to relaxed position beside torso...")
        moves = [
            (JOINT_WRIST_PITCH[0], JOINT_WRIST_PITCH[1], 160, DIR_CCW),
            (JOINT_WRIST_ROT[0], JOINT_WRIST_ROT[1], 150, DIR_CCW),
            (JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], 380, DIR_CCW),
            (JOINT_SHOULDER_FB[0], JOINT_SHOULDER_FB[1], 350, DIR_CCW),
        ]
        ok = self.dispatch_moves(moves, timeout=6.0)
        self.current_pose_name = "RELAXED"
        return ok

    def open_fingers(self, pulses: int = 250) -> bool:
        """Opens gripper fingers (Dir 1 = CCW / reverse)."""
        print(f"[*] Opening gripper fingers ({pulses} pulses)...")
        return self.dispatch_moves([(JOINT_GRIPPER[0], JOINT_GRIPPER[1], pulses, DIR_CCW)])

    def close_fingers_with_breaker(self, pulses: int = 350) -> bool:
        """
        Closes gripper fingers (Dir 0 = CW) using breaker / brake profile
        to clamp securely without jamming or burning out the motor.
        """
        print(f"[*] Closing gripper fingers with breaker clamp ({pulses} pulses)...")
        return self.dispatch_moves([(JOINT_GRIPPER[0], JOINT_GRIPPER[1], pulses, DIR_CW)])

    def lift_arm_up(self, pulses: int = 220) -> bool:
        """Lifts the hand up from the table surface to test pick success."""
        print(f"[*] Lifting arm upward ({pulses} pulses)...")
        moves = [
            (JOINT_SHOULDER_FB[0], JOINT_SHOULDER_FB[1], pulses, DIR_CCW),
            (JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], int(pulses * 0.8), DIR_CCW),
        ]
        return self.dispatch_moves(moves, timeout=4.0)

    # =========================================================================
    # HEAD YAW CONTROL & VISUAL SCANNING (1-5-x-x)
    # =========================================================================
    def move_head_yaw(self, pulses: int, direction: int) -> bool:
        """
        Moves head yaw motor 1-5-x-x and tracks net pulse position from center (home=0).
        direction: 0 = RIGHT (+ pulses), 1 = LEFT (- pulses)
        """
        cmd_dir_name = "RIGHT" if direction == DIR_YAW_RIGHT else "LEFT"
        print(f"[*] Moving head yaw: {pulses} pulses {cmd_dir_name} (1-5-{pulses}-{direction})...")
        ok = self.dispatch_moves([(JOINT_HEAD_YAW[0], JOINT_HEAD_YAW[1], pulses, direction)], timeout=3.0)
        if ok:
            if direction == DIR_YAW_RIGHT:
                self.current_head_yaw += pulses
            else:
                self.current_head_yaw -= pulses
            logger.info(f"Head yaw net position from center: {self.current_head_yaw} pulses")
        time.sleep(0.15)
        return ok

    def return_head_to_center(self) -> bool:
        """Returns head yaw (1-5-x-x) back to central home position (0 net pulses)."""
        if self.current_head_yaw == 0:
            print("[*] Head is at central home position.")
            return True

        pulses = abs(self.current_head_yaw)
        direction = DIR_YAW_LEFT if self.current_head_yaw > 0 else DIR_YAW_RIGHT
        dir_name = "LEFT" if direction == DIR_YAW_LEFT else "RIGHT"
        print(f"[*] Returning head to central home: {pulses} pulses {dir_name} (1-5-{pulses}-{direction})...")
        ok = self.dispatch_moves([(JOINT_HEAD_YAW[0], JOINT_HEAD_YAW[1], pulses, direction)], timeout=4.0)
        if ok:
            self.current_head_yaw = 0
            print("[*] Head cleanly centered at home (0 net pulses).")
        time.sleep(0.2)
        return ok

    def is_target_ball_in_frame(self, num_samples: int = 3) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Samples stereo camera frames to check if the target color ball is currently visible.
        Returns: (is_visible: bool, ball_info: dict or None)
        """
        if self.simulation:
            # In simulation, synthesize detection
            return True, {"x": 350.0, "y": 420.0, "z_cm": 35.0, "radius": 24.0, "live": True}

        for _ in range(num_samples):
            left_eye, right_eye = self.read_stereo_frame()
            if left_eye is not None and right_eye is not None and self.tracker is not None:
                res: GraspResult = self.tracker.process_stereo_frame(left_eye, right_eye)
                if res.ball and res.ball.get("live"):
                    return True, res.ball
            time.sleep(0.08)

        return False, None

    def scan_and_locate_ball(self) -> bool:
        """
        Initial sight check and head search scan within [-1000, +1000] pulses (1-5-x-x):
          1. If ball is in frame:
             Vocalizes: 'I can see the <color> ball, fetching it now.'
             Returns True immediately.
          2. If ball is NOT in frame:
             Vocalizes: 'I cannot see the <color> ball, but I will check around.'
             Rotates head yaw (1-5) within + or - 1000 pulses.
          3. When it sees the ball during scanning:
             Pauses head immediately at that angle.
             Vocalizes: 'I can see the <color> ball, fetching it now.'
             Returns True to begin picking.
          4. If after scanning the entire range it is still not found:
             Returns head to central home position.
             Vocalizes: 'The <color> ball is not in sight.'
             Returns False.
        """
        print("\n" + "=" * 65)
        print(f"[*] INITIAL SIGHT CHECK FOR {self.ball_color.upper()} BALL...")
        print("=" * 65)

        # 1. Check if ball is already in frame
        seen, ball_info = self.is_target_ball_in_frame(num_samples=4)
        if seen:
            msg = f"I can see the {self.ball_color} ball, fetching it now."
            print(f"[+] BALL IN SIGHT: {msg}")
            speak_to_phone(msg)
            return True

        # 2. Not in frame -> Announce head check
        msg_search = f"I cannot see the {self.ball_color} ball, but I will check around."
        print(f"[-] BALL NOT IN INITIAL FRAME: {msg_search}")
        speak_to_phone(msg_search)

        # 3. Rotate head within + or - 1000 pulses from center
        print(f"[*] Starting head scan with 1-5-x-x within [{-MAX_HEAD_SCAN_PULSES}, +{MAX_HEAD_SCAN_PULSES}] pulses...")

        # --- Phase A: Scan Right from center to +1000 pulses ---
        while self.current_head_yaw < MAX_HEAD_SCAN_PULSES:
            step = min(HEAD_SCAN_STEP, MAX_HEAD_SCAN_PULSES - self.current_head_yaw)
            self.move_head_yaw(pulses=step, direction=DIR_YAW_RIGHT)
            seen, ball_info = self.is_target_ball_in_frame(num_samples=3)
            if seen:
                print(f"\n[+] TARGET {self.ball_color.upper()} BALL SPOTTED DURING RIGHT SCAN AT YAW={self.current_head_yaw}!")
                print("    Pausing head motion immediately and commencing pick...")
                speak_to_phone(f"I can see the {self.ball_color} ball, fetching it now.")
                return True

        # --- Phase B: Scan Left from +1000 across center down to -1000 pulses ---
        print("[*] Reached right limit (+1000). Sweeping head left down to -1000 pulses...")
        while self.current_head_yaw > -MAX_HEAD_SCAN_PULSES:
            step = min(HEAD_SCAN_STEP, self.current_head_yaw - (-MAX_HEAD_SCAN_PULSES))
            self.move_head_yaw(pulses=step, direction=DIR_YAW_LEFT)
            seen, ball_info = self.is_target_ball_in_frame(num_samples=3)
            if seen:
                print(f"\n[+] TARGET {self.ball_color.upper()} BALL SPOTTED DURING LEFT SCAN AT YAW={self.current_head_yaw}!")
                print("    Pausing head motion immediately and commencing pick...")
                speak_to_phone(f"I can see the {self.ball_color} ball, fetching it now.")
                return True

        # --- Phase C: Not found after full scan -> Return home & report ---
        print("\n[!] Full head sweep completed: ball was not detected.")
        self.return_head_to_center()
        msg_not_found = f"The {self.ball_color} ball is not in sight."
        print(f"[-] {msg_not_found}")
        speak_to_phone(msg_not_found)
        return False

    # =========================================================================
    # TABLE HEIGHT SAFETY GUARD
    # =========================================================================
    def filter_downward_moves_at_table_level(
        self,
        moves: List[Tuple[int, int, int, int]],
        current_wrist_y: float
    ) -> List[Tuple[int, int, int, int]]:
        """
        Safety Guard: Ensures wrist and elbow NEVER go below the table level.
        If the wrist/hand is near or below the table plane boundary, blocks any
        commands that would drive the arm further downward.
        """
        is_near_table = current_wrist_y >= (self.table_limit_y - TABLE_SAFETY_MARGIN_PX)
        if not is_near_table:
            return moves

        filtered_moves = []
        for e, m, p, d in moves:
            # Check downward-driving commands:
            # Elbow FB (1-3) CW = extends downward
            # Shoulder FB (5-1) CW = rotates arm forward/down
            # Wrist Pitch (1-2) CW = tilts palm downward
            is_downward = False
            if (e, m) == JOINT_ELBOW_FB and d == DIR_CW:
                is_downward = True
            elif (e, m) == JOINT_SHOULDER_FB and d == DIR_CW:
                is_downward = True
            elif (e, m) == JOINT_WRIST_PITCH and d == DIR_CW:
                is_downward = True

            if is_downward:
                print(f"  \033[93m[TABLE GUARD] Blocked move {e}-{m}-{p}-{d}: Wrist at Y={current_wrist_y:.0f}px (Table Limit={self.table_limit_y:.0f}px) to prevent table collision!\033[0m")
            else:
                filtered_moves.append((e, m, p, d))

        return filtered_moves

    # =========================================================================
    # CLOSED-LOOP VISUAL SERVOING WITH ARM POSE ESTIMATION
    # =========================================================================
    def execute_visual_servo_pick(self, max_attempts: int = 3, max_servo_steps: int = 30) -> bool:
        """
        Main closed-loop visual servo and pick routine:
          1. Moves arm to table ready stance.
          2. Continuously tracks ball and index/thumb fingertips with binocular vision.
          3. Observes hand displacement from each move to guess arm pose and joint influence.
          4. Uncertainty management: keeps movements small (100 pulses) until certainty is established.
          5. Table collision guard: never allows wrist/elbow below table level.
          6. When ball is centered between index & thumb, closes gripper fingers (with breaker).
          7. Lifts arm. If ball moves with hand -> speaks 'Ball is now picked' and finishes.
          8. If pick missed -> opens fingers, readjusts, and retries.
        """
        print("\n" + "#" * 70)
        print(f"  >>> STARTING VISUAL SERVO PICK: {self.ball_color.upper()} BALL <<<")
        print("  * Arm Pose Guessing : Active via observed hand displacement")
        print("  * Movement Safety   : Conservative 100-pulse steps until confident")
        print(f"  * Table Guard       : Active (Upper Limit Y = {self.table_limit_y}px)")
        print("#" * 70)

        if not self.open_camera():
            print("[ERROR] Camera initialization failed.")
            return False

        # Step 0: Initial Sight Check & Head Search Scan if not in view
        ball_located = self.scan_and_locate_ball()
        if not ball_located:
            print(f"[!] {self.ball_color.capitalize()} ball not found. Pick procedure ended safely.")
            return False

        for attempt in range(1, max_attempts + 1):
            print(f"\n--- [ATTEMPT {attempt}/{max_attempts}] Initializing Stance & Approaching Ball ---")

            # Reset arm to ready stance
            self.move_to_table_ready_pose()
            time.sleep(0.5)

            grasped_target = False
            last_hand_pos: Optional[Tuple[float, float, float]] = None
            last_executed_joint: Optional[Tuple[Tuple[int, int], int, int]] = None

            for step in range(1, max_servo_steps + 1):
                # 1. Capture and analyze stereo vision
                left_eye, right_eye = self.read_stereo_frame()
                if left_eye is None or right_eye is None:
                    time.sleep(0.05)
                    continue

                if self.simulation:
                    # Synthetic visual servoing trajectory for simulation & CI testing
                    ball_x, ball_y, ball_z = 350.0, 420.0, 35.0
                    hx = 300.0 + min(step * 15.0, 50.0)
                    hy = 370.0 + min(step * 15.0, 50.0)
                    hz = 35.0
                    ball = {"x": ball_x, "y": ball_y, "z_cm": ball_z, "radius": 24.0, "live": True}
                    index = {"x": hx - 30.0, "y": hy, "live": True, "z_cm": hz}
                    thumb = {"x": hx + 30.0, "y": hy, "live": True, "z_cm": hz}
                    is_between = (step >= 3)
                    res = GraspResult(
                        is_in_between=is_between,
                        status="IN_BETWEEN" if is_between else "APPROACHING",
                        ball=ball,
                        index=index,
                        thumb=thumb,
                        alignment_score=95.0 if is_between else 40.0 + step * 15.0,
                    )
                elif self.tracker is None:
                    print("[Sim Vision] Tracker not loaded; simulating betweenness after 3 steps.")
                    if step >= 3:
                        grasped_target = True
                        break
                    time.sleep(0.2)
                    continue
                else:
                    res: GraspResult = self.tracker.process_stereo_frame(left_eye, right_eye)

                # Check if ball is visible
                ball = res.ball
                index = res.index
                thumb = res.thumb

                if not ball or not ball.get("live"):
                    print(f"  [Step {step:02d}] Looking for {self.ball_color} ball... (not in view)")
                    time.sleep(0.2)
                    continue

                ball_x, ball_y = ball["x"], ball["y"]
                ball_z = ball.get("z_cm")
                ball_radius = ball.get("radius", 20.0)

                # Infer table level dynamically from bottom of ball if on table
                if not self.table_inferred and ball_y > 300:
                    self.table_limit_y = ball_y + ball_radius + 15.0
                    self.table_inferred = True
                    logger.info(f"Dynamically inferred table plane limit: Y={self.table_limit_y:.1f}px")

                # Compute fingertip midpoint
                if index and thumb and index.get("live") and thumb.get("live"):
                    hx = (index["x"] + thumb["x"]) / 2.0
                    hy = (index["y"] + thumb["y"]) / 2.0
                    hz = ((index.get("z_cm") or 35.0) + (thumb.get("z_cm") or 35.0)) / 2.0
                else:
                    hx, hy, hz = (left_eye.shape[1] / 2.0, left_eye.shape[0] / 2.0, 35.0)

                current_hand_pos = (hx, hy, hz)

                # 2. Update Kinematic Pose Learning from previous move
                if last_hand_pos is not None and last_executed_joint is not None:
                    joint_key, dir_moved, pulses_moved = last_executed_joint
                    actual_displacement = (
                        current_hand_pos[0] - last_hand_pos[0],
                        current_hand_pos[1] - last_hand_pos[1],
                        current_hand_pos[2] - last_hand_pos[2],
                    )
                    model = self.joint_models.get(joint_key)
                    if model is not None:
                        model.update_observation(dir_moved, pulses_moved, actual_displacement)

                last_hand_pos = current_hand_pos

                print(
                    f"  [Step {step:02d}] State: {res.status:<10} | In-Between: {str(res.is_in_between):<5} | "
                    f"Hand: ({hx:.0f}, {hy:.0f}) | Ball: ({ball_x:.0f}, {ball_y:.0f}) | Align: {res.alignment_score:.0f}%"
                )

                # 3. Check if the ball is directly between index and thumb!
                if res.is_in_between or res.status in ("IN_BETWEEN", "GRASPED"):
                    print(f"\n[+] TARGET IN-BETWEEN REACHED! Alignment = {res.alignment_score:.1f}%")
                    grasped_target = True
                    break

                # 4. Formulate Next Servo Moves using Pose Guesses & Certainty Bounds
                dx = ball_x - hx
                dy = ball_y - hy
                dz = (ball_z - hz) if ball_z is not None else 0.0

                servo_moves: List[Tuple[int, int, int, int]] = []
                best_joint_key = None
                best_dir = DIR_CW
                best_step = MIN_NOTICEABLE_PULSES

                # A. Lateral Left/Right Correction
                if abs(dx) > 18:
                    side_model = self.joint_models[JOINT_SHOULDER_SIDE]
                    # Direction based on empirical vector sign
                    expected_sign = side_model.vector_d0[0]
                    target_dir = DIR_CW if (dx * expected_sign >= 0) else DIR_CCW

                    # Cautious step sizing: 100 pulses if uncertain, up to 160 pulses if certain
                    step_pulses = MIN_NOTICEABLE_PULSES
                    if side_model.certainty >= 0.70:
                        step_pulses = int(MIN_NOTICEABLE_PULSES + (CONFIDENT_MAX_PULSES - MIN_NOTICEABLE_PULSES) * (side_model.certainty - 0.7) / 0.3)

                    servo_moves.append((JOINT_SHOULDER_SIDE[0], JOINT_SHOULDER_SIDE[1], step_pulses, target_dir))
                    best_joint_key = JOINT_SHOULDER_SIDE
                    best_dir = target_dir
                    best_step = step_pulses

                # B. Vertical Up/Down & Depth Forward/Back Correction
                if abs(dy) > 18:
                    fb_model = self.joint_models[JOINT_ELBOW_FB]
                    expected_sign = fb_model.vector_d0[1]
                    target_dir = DIR_CW if (dy * expected_sign >= 0) else DIR_CCW

                    step_pulses = MIN_NOTICEABLE_PULSES
                    if fb_model.certainty >= 0.70:
                        step_pulses = int(MIN_NOTICEABLE_PULSES + (CONFIDENT_MAX_PULSES - MIN_NOTICEABLE_PULSES) * (fb_model.certainty - 0.7) / 0.3)

                    servo_moves.append((JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], step_pulses, target_dir))
                    if best_joint_key is None:
                        best_joint_key = JOINT_ELBOW_FB
                        best_dir = target_dir
                        best_step = step_pulses

                # If no move chosen (near target), micro-fine adjustment forward
                if not servo_moves:
                    servo_moves.append((JOINT_ELBOW_FB[0], JOINT_ELBOW_FB[1], MIN_NOTICEABLE_PULSES, DIR_CW))
                    best_joint_key = JOINT_ELBOW_FB
                    best_dir = DIR_CW
                    best_step = MIN_NOTICEABLE_PULSES

                # 5. Apply Table Level Safety Guard (prevents wrist/elbow going below table)
                safe_moves = self.filter_downward_moves_at_table_level(servo_moves, current_wrist_y=hy)
                if not safe_moves:
                    # If downward move was blocked, try pure horizontal or forward reach instead
                    print("  [TABLE GUARD] Re-orienting to horizontal search along table surface.")
                    safe_moves = [(JOINT_SHOULDER_SIDE[0], JOINT_SHOULDER_SIDE[1], MIN_NOTICEABLE_PULSES, DIR_CW if dx > 0 else DIR_CCW)]
                    best_joint_key = JOINT_SHOULDER_SIDE
                    best_dir = DIR_CW if dx > 0 else DIR_CCW
                    best_step = MIN_NOTICEABLE_PULSES

                # Record last joint executed for learning in next frame
                if best_joint_key is not None:
                    last_executed_joint = (best_joint_key, best_dir, best_step)

                # 6. Execute moves
                self.dispatch_moves(safe_moves, timeout=3.0)
                time.sleep(0.18)

            # -----------------------------------------------------------------
            # GRASP & LIFT EVALUATION PHASE
            # -----------------------------------------------------------------
            if grasped_target:
                print("\n[*] INITIATING GRASP CLAMP (with breaker)...")
                # 1. Close fingers with breaker
                self.close_fingers_with_breaker(pulses=350)
                time.sleep(0.5)

                # Record ball elevation before lift
                pre_lift_y = None
                left_eye, right_eye = self.read_stereo_frame()
                if left_eye is not None and self.tracker is not None:
                    pre_res = self.tracker.process_stereo_frame(left_eye, right_eye)
                    if pre_res.ball and pre_res.ball.get("live"):
                        pre_lift_y = pre_res.ball["y"]

                # 2. Lift arm up
                self.lift_arm_up(pulses=220)
                time.sleep(0.6)

                # 3. Vision Pick Verification
                pick_successful = False
                if self.simulation:
                    pick_successful = True
                else:
                    left_eye, right_eye = self.read_stereo_frame()
                    if left_eye is not None and self.tracker is not None:
                        post_res = self.tracker.process_stereo_frame(left_eye, right_eye)
                        post_ball = post_res.ball

                        if post_ball and post_ball.get("live"):
                            post_y = post_ball["y"]
                            # In image coordinates, moving UP means y decreases (lower pixel row)
                            elevated = (pre_lift_y is not None and post_y < pre_lift_y - 20) or (post_res.is_in_between)
                            if elevated:
                                pick_successful = True
                        elif post_res.is_pinched:
                            # Ball enclosed inside gripped fingers
                            pick_successful = True

                if pick_successful:
                    print("\n" + "=" * 70)
                    print("  >>> SUCCESS: BALL IS NOW PICKED! <<<")
                    print("=" * 70)
                    # Voice out loud through phone app speaker
                    speak_to_phone("Ball is now picked")
                    self.return_head_to_center()
                    return True
                else:
                    print("\n[!] Pick verification failed: ball did not move with hand!")
                    # Release fingers and try again
                    self.open_fingers(pulses=300)
                    time.sleep(0.4)
            else:
                print(f"[!] Target not reached within step limit in attempt {attempt}.")
                self.open_fingers(pulses=250)

        print("\n[!] Visual servo pick reached maximum attempts without success.")
        self.move_to_relaxed_pose()
        self.return_head_to_center()
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Autonomous Visual Servo Arm Controller")
    parser.add_argument("--ball-color", default="red", help="Target ball color (red, green, blue, yellow)")
    parser.add_argument("--attempts", type=int, default=3, help="Max pick attempts")
    parser.add_argument("--sim", action="store_true", help="Run in simulation mode")
    args = parser.parse_args()

    controller = VisualServoArmController(ball_color=args.ball_color, simulation=args.sim)
    controller.execute_visual_servo_pick(max_attempts=args.attempts)


if __name__ == "__main__":
    main()
