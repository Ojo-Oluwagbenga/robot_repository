# Raspberry Pi 5 Humanoid Robot Controller

This software is the master controller for a distributed humanoid robot system. It controls 20 motors across 5 ESP32 motor-controller boards via a custom 50 kHz synchronous bus, with dedicated feedback return lines.

---

## 1. Hardware Architecture

- **1x Raspberry Pi 5** (Master)
- **5x ESP32 Motor Controller Boards** (Slaves, IDs `001` through `101`)
- **20x DC Motors with Quadrature Encoders** (4 motors per ESP32)
- **Shared Master-to-Slave Synchronous Bus (3 Wires)**:
  - `CLOCK` (50 kHz)
  - `DATA` (Serial commands & Lock-on Run/Brake control)
  - `SET` (Framing control: LOW = Receive, HIGH = Stop & Interpret)
- **Dedicated Slave-to-Master Feedback Lines (5 Wires)**:
  - `FEEDBACK_1` through `FEEDBACK_5`
- **Common Ground**: All devices share an electrical ground reference. Cable lengths < 30 cm.

---

## 2. Raspberry Pi 5 GPIO Connections (BCM Pinout)

Pin assignments are centrally configured in [config.py](file:///robot/config.py):

| Signal | Direction | Pi 5 BCM Pin | Physical Pin | Target |
| :--- | :--- | :--- | :--- | :--- |
| **CLOCK** | Output | **GPIO 17** | Pin 11 | ESP32 GPIO 17 (All 5 ESPs in parallel) |
| **DATA** | Output | **GPIO 27** | Pin 13 | ESP32 GPIO 16 (All 5 ESPs in parallel) |
| **SET** | Output | **GPIO 22** | Pin 15 | ESP32 GPIO 4 (All 5 ESPs in parallel) |
| **MOTOR_ENABLE** | Output | **GPIO 26** | Pin 37 | Motor power gate / relay (Active HIGH) |
| **FEEDBACK_1** | Input (Pull-down) | **GPIO 23** | Pin 16 | ESP32 #1 GPIO 15 |
| **FEEDBACK_2** | Input (Pull-down) | **GPIO 4**  | Pin 7  | ESP32 #2 GPIO 15 |
| **FEEDBACK_3** | Input (Pull-down) | **GPIO 24** | Pin 18 | ESP32 #3 GPIO 15 |
| **FEEDBACK_4** | Input (Pull-down) | **GPIO 5**  | Pin 29 | ESP32 #4 GPIO 15 |
| **FEEDBACK_5** | Input (Pull-down) | **GPIO 6**  | Pin 31 | ESP32 #5 GPIO 15 |
| **GND** | Ground | **GND** | Pin 6, 9, 14, 20, etc. | ESP32 GND (All boards) [MANDATORY] |

---

## 3. Pi GPIO Technology & 50 kHz Clock Generation

### The Raspberry Pi 5 RP1 Architecture
On Raspberry Pi 5, GPIO peripherals are handled by the proprietary **RP1 I/O controller chip** connected via PCIe, replacing the legacy BCM2835 GPIO registers.
- Legacy `RPi.GPIO` **does not work** on Raspberry Pi 5.
- The official kernel driver library for Pi 5 is **`lgpio`** (or `libgpiod` v2).

### Achieving Deterministic 50 kHz Timing in Python
A 50 kHz clock requires a **20 µs period (10 µs HIGH, 10 µs LOW)**.
- Standard Python `time.sleep(10e-6)` on Linux yields time slices back to the kernel scheduler, introducing 50 µs to 150 µs of unpredictable jitter.
- To guarantee deterministic 10 µs clock phases, our HAL ([hardware/gpio_bus.py](file:///robot/hardware/gpio_bus.py)) utilizes a calibrated nanosecond busy-wait:
  ```python
  def delay_ns(nanoseconds: int):
      start = time.perf_counter_ns()
      while (time.perf_counter_ns() - start) < nanoseconds:
          pass
  ```
  On the Cortex-A76 cores of the Raspberry Pi 5, `time.perf_counter_ns()` provides sub-50ns resolution with rock-solid timing accuracy.

---

## 4. Software Architecture

```
robot/
├── main.py                     # Entry point & interactive CLI
├── config.py                   # Pinouts, timing, and tuning constants
├── requirements.txt            # Dependencies
├── PROTOCOL.md                 # Complete bus and bit-level spec
├── communication/
│   ├── protocol.py             # 20-bit, 6-bit, and 17-bit bitstream packers
│   ├── transmitter.py          # 50 kHz falling-edge bus transmitter
│   ├── receiver.py             # 17-bit synchronous feedback reader
│   └── feedback.py             # Thread-safe FeedbackQueue & data models
├── control/
│   ├── motor_manager.py        # 20-motor coordinator (Normal, Degrees, ROS 2 ready)
│   └── lock_manager.py         # Global single-lock invariant and RUN/BRAKE driver
├── hardware/
│   └── gpio_bus.py             # lgpio driver & SimulationBus
├── debug/
│   └── logger.py               # 4-tier structured logging (Levels 0-3)
└── tests/
    ├── test_protocol.py        # Protocol packing/unpacking tests
    ├── test_feedback.py        # Net pulse & sign/magnitude tests
    └── test_lock_manager.py    # Lock-on transitions & arbitration tests
```

### ROS 2 Compatibility
The low-level motor communication system has zero dependencies on ROS 2. However, [control/motor_manager.py](file:///robot/control/motor_manager.py) and [communication/feedback.py](file:///robot/communication/feedback.py) expose clean, non-blocking APIs and callbacks. To integrate ROS 2 later, simply create an `rclpy.node.Node` that wraps `MotorManager`.

---

## 5. Installation

On the Raspberry Pi 5:

```bash
# 1. Update package lists
sudo apt-get update

# 2. Install lgpio system library for Raspberry Pi 5
sudo apt-get install -y python3-lgpio python3-pip

# 3. Clone / copy the robot folder and enter it
cd robot

# 4. Install test requirements
pip3 install -r requirements.txt
```

---

## 6. Running the Program

### Hardware Mode (On Raspberry Pi 5)
```bash
python3 main.py
```

### Simulation Mode (On PC or Pi without hardware)
```bash
python3 main.py --simulation
```

### Automated Demo Mode
```bash
python3 main.py --simulation --demo
```

### Debug Level Configuration
```bash
# Debug levels: 0 (Errors), 1 (Commands/Feedback), 2 (Packet details), 3 (Bit trace)
python3 main.py --simulation --debug 2
```

---

## 7. Interactive CLI Commands

When running `python3 main.py`, the program automatically starts with a **Guided Motor Test Wizard**:
1. Enter **ESP Code** (1 to 5)
2. Enter **Motor Code** (1 to 4)
3. Enter **Pulse Count** (e.g. 500)
4. Enter **Direction** (0 for CW / Forward, 1 for CCW / Reverse)

After testing, you can test another motor or enter the interactive command console:

```
robot> test                  # Relaunch the Guided Motor Test Wizard (ESP, Motor, Pulse, Dir)
robot> norm 1 1 500 0        # Send Normal move: ESP 1, Motor 1, 500 pulses, CW (0)
robot> queue 1 1 500 0       # Store move in ESP memory
robot> go                    # Broadcast 10111: execute all queued moves
robot> deg 3 2 90.0 1        # Move ESP 3, Motor 2 by 90.0 degrees, CCW (1)
robot> lock 2 1 0            # Lock-on mode: ESP 2, Motor 1, CW
robot> run                   # Drive DATA HIGH (Runs locked motor)
robot> brake                 # Drive DATA LOW (Brakes locked motor)
robot> clear                 # Broadcast 000000 (Clear all locks)
robot> simfb 1 1 500         # Inject simulated feedback (+500 pulses) in simulation mode
robot> status                # Print all 20 motor encoder positions
robot> debug 3               # Switch to bit-level trace logging
robot> exit                  # Clean shutdown
```

---

## 8. Unit Testing

Execute the complete automated test suite:
```bash
python3 -m unittest discover -s tests
```

All 16 unit tests verify:
1. 3-bit ESP ID binary encoding (`001` through `101`).
2. 2-bit Motor ID binary encoding (`00` through `11`).
3. 20-bit normal packet construction and field alignment.
4. 6-bit lock-on packet construction (`001101`).
5. `000000` clear-lock packet generation.
6. Rejection of invalid packet lengths (5, 7, 19, 21 bits).
7. 17-bit feedback magnitude and sign parsing (e.g. `+100 +100 +100 -20 -10 = +270`, `-500 +100 -50 = -450`).
8. Single-lock global arbitration and real-time run/brake signaling.

---

## 9. Cognitive Brain, Memory & Speech System

The robot features an intelligent cognitive subsystem located in [`robot/brain/`](file:///robot/brain/README.md):
- **Ollama Neural Brain**: Communicates with the central PC (`http://DESKTOP-2CANEO2.local:11434`, model: `llama3.2:1b`).
- **Structured Persistent Memory**: Remembers user facts, tasks, and discussion summaries in [`robot/brain/data/memory.json`](file:///robot/brain/data/memory.json).
- **Personality & Embodiment**: Aware of its 20 motors, 5 ESP32 boards, Pi 5 brain, and speaks concisely for voice output.
- **Real-Time Web Browsing**: Fetches live data and uses Ollama to distill factual answers.
- **Mouth & Ear Interfaces**: Pre-wired for connected speaker and mouthpiece audio.

To test the brain interactively with text input:
```bash
python -m robot.brain
```
