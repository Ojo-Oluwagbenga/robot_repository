"""
Configuration settings for the Raspberry Pi 5 Humanoid Robot Controller.
All GPIO pins use BCM (Broadcom) numbering.
"""

from typing import Dict, List

# ==============================================================================
# 1. RASPBERRY PI 5 GPIO PIN ASSIGNMENTS (BCM Numbering)
# ==============================================================================
# Shared 3-wire synchronous bus to all 5 ESP32 boards in parallel
PIN_CLOCK: int = 17       # Output: 50 kHz Bus Clock (Physical Pin 11)
PIN_DATA: int = 27        # Output: Serial Command Data & Lock-on Run/Brake (Physical Pin 13)
PIN_SET: int = 22         # Output: Framing (Physical Pin 15)

# Motor Power System Enable (Active HIGH once all pins and systems are ready)
# Physical Pin 37 (BCM 26) activates the motor power relay so motors never run when pins float
PIN_POWER_ENABLE: int = 26

# Dedicated feedback input pins from each ESP32 board
# Pin mapping: ESP ID (1 to 5) -> Raspberry Pi BCM GPIO Pin
FEEDBACK_PINS: Dict[int, int] = {
    1: 23,  # FEEDBACK_1 from ESP32 #1 -> BCM 23
    2: 4,   # FEEDBACK_2 from ESP32 #2 -> BCM 4
    3: 24,  # FEEDBACK_3 from ESP32 #3 (Nod Up/Down motor) -> BCM 24
}

# ==============================================================================
# DIRECT RASPBERRY PI 5 MOTOR PINS
# ==============================================================================
# Right Shoulder (Stepper Motor)
PIN_SHOULDER_RIGHT_DIR: int = 6   # Direction pin (BCM 6)
PIN_SHOULDER_RIGHT_STEP: int = 5  # Step pulse pin (BCM 5)

# Left Shoulder (Stepper Motor)
PIN_SHOULDER_LEFT_DIR: int = 9    # Direction pin (BCM 9)
PIN_SHOULDER_LEFT_STEP: int = 10  # Step pulse pin (BCM 10)

# Turn Head Sideways (Encoded DC Motor: Pan Left / Right)
PIN_HEAD_PAN_IN1: int = 20        # IN1 (BCM 20)
PIN_HEAD_PAN_IN2: int = 16        # IN2 (BCM 16)
HEAD_PAN_MS_PER_DEG: int = 100    # Timer mode: 1 degree = 100ms of run time

# ESP32 Time-Based Pulse Protocol: 1 pulse = 10ms of run time
ESP_MS_PER_PULSE: int = 10

# Preferred hardware gpiochip on Raspberry Pi 5 (RP1 southbridge controller: gpiochip0 or gpiochip4)
DEFAULT_GPIOCHIP: int = 0

# List of all valid ESP IDs (ESP 1, ESP 2, ESP 3, ESP 4)
VALID_ESP_IDS: List[int] = [1, 2, 3, 4]

# Broadcast ID for clearing locks
BROADCAST_CLEAR_LOCK_ID: int = 0

# Valid Motor IDs per ESP32 (0-indexed)
# ESP 1 & ESP 2: Motors 1..6 (indices 0..5)
# ESP 3: Motors 1..2 (indices 0..1, limit switches on pins 26 and 25)
# ESP 4: Motors 1..4 (indices 0..3, leg driving board with encoders)
VALID_MOTOR_IDS: List[int] = [0, 1, 2, 3, 4, 5]
MOTORS_PER_ESP: Dict[int, int] = {1: 6, 2: 6, 3: 2, 4: 4}

# Total hardware counts
NUM_ESPS: int = 4
TOTAL_MOTORS: int = 18  # 6 on ESP1 + 6 on ESP2 + 2 on ESP3 + 4 on ESP4

# ==============================================================================
# 2. BUS TIMING SPECIFICATIONS
# ==============================================================================
# Bus clock frequency: 50 kHz -> Period = 20 microseconds
CLOCK_FREQ_HZ: int = 50_000
CLOCK_PERIOD_NS: int = 20_000       # 20 microseconds in nanoseconds
CLOCK_HALF_PERIOD_NS: int = 10_000  # 10 microseconds HIGH, 10 microseconds LOW

# Setup time before active clock edge and after frame edges (nanoseconds)
DATA_SETUP_TIME_NS: int = 20_000

# Inter-packet pause time (microseconds)
INTER_PACKET_DELAY_US: int = 50

# Maximum feedback read timeout (milliseconds)
FEEDBACK_TIMEOUT_MS: int = 100

# ==============================================================================
# 3. MOTOR CONVERSION PARAMETERS
# ==============================================================================
# Encoder pulses per mechanical degree of output shaft rotation
# Configurable based on motor gearbox and encoder PPR (e.g. 1440 pulses / 360 deg = 4.0)
DEFAULT_PULSES_PER_DEGREE: float = 4.0

# Direction conventions
DIRECTION_CW: int = 0   # Clockwise / Forward
DIRECTION_CCW: int = 1  # Counter-Clockwise / Reverse

# Maximum command pulse magnitude (15-bit unsigned limit for 21-bit normal command: 2^15 - 1 = 32767)
MAX_PULSE_COUNT: int = 32767

# Maximum feedback pulse magnitude (14-bit unsigned limit: 2^14 - 1 = 16383)
MAX_FEEDBACK_MAGNITUDE: int = 16383

# ==============================================================================
# 4. DEBUGGING AND LOGGING
# ==============================================================================
# Debug Levels:
# 0: Minimal output (Errors and warnings only)
# 1: Normal operational output (Commands dispatched and feedback received)
# 2: Packet-level debugging (Decoded packets, lengths, and fields)
# 3: Low-level bit trace (Raw bit arrays, high-frequency bus transitions)
DEBUG_LEVEL: int = 1

# Optional file logging
LOG_TO_FILE: bool = True
LOG_FILE_PATH: str = "logs/robot.log"

# ==============================================================================
# 5. WI-FI JSON PACKET COMMUNICATION PROTOCOL (ESP32 <-> RASPBERRY PI 5)
# ==============================================================================
# Default communication mode: "WIFI" (replaces 3-wire synchronous bus)
# Available modes: "WIFI", "SIMULATION", "GPIO_WIRE"
COMMUNICATION_MODE: str = "WIFI"

WIFI_UDP_PORT: int = 5005
WIFI_BROADCAST_IP: str = "255.255.255.255"

# Dedicated bidirectional communication log file
WIFI_COMM_LOG_PATH: str = "logs/esp_comm.log"

# ==============================================================================
# 6. BINOCULAR STEREO VISION CAMERA CONFIGURATION
# ==============================================================================
CAMERA_DEVICE_INDEX: int = 0             # /dev/video0 (UVC video stream)
CAMERA_FOURCC: str = "MJPG"              # Motion JPEG for high performance
CAMERA_DEFAULT_WIDTH: int = 1280         # Total side-by-side width (640 per eye)
CAMERA_DEFAULT_HEIGHT: int = 480         # Frame height (480)
CAMERA_HD_WIDTH: int = 2560              # 720p HD side-by-side width (1280 per eye)
CAMERA_HD_HEIGHT: int = 720              # 720p HD height
CAMERA_FPS: int = 30                     # Target frame rate
CAMERA_WARMUP_FRAMES: int = 5            # Number of frames to settle auto-exposure
CAMERA_SAVE_DIR: str = "captures"        # Output directory for saved images
CAMERA_WEB_PORT: int = 8080              # Web viewer port

