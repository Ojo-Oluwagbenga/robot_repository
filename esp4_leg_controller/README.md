# ESP #4: Leg Driving Board Controller

Dedicated ESP32 firmware for driving the humanoid robot's 4 leg/base drive motors with single-pin encoder pulse tracking and Over-The-Air (OTA) flashing.

---

## 1. Hardware Pinout

| Motor | Channel / Function | Driver IN1 | Driver IN2 | Encoder Pulse Pin | ESP32 Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Motor 1** | `B1_front` (Board 1 Front) | **GPIO 25** | **GPIO 26** | **GPIO 15** | Single-pin pulse counter (Internal Pullup) |
| **Motor 2** | `B1_rear` (Board 1 Rear) | **GPIO 32** | **GPIO 33** | **GPIO 16** | Shifted to D16 (Internal Pullup) |
| **Motor 3** | `B2_front` (Board 2 Front) | **GPIO 14** | **GPIO 27** | **GPIO 34** | Input-only GPI pin (No internal pullup) |
| **Motor 4** | `B2_rear` (Board 2 Rear) | **GPIO 12** | **GPIO 13** | **GPIO 35** | Input-only GPI pin (No internal pullup) |
| **Status LED** | Visual Signalling / OTA | — | — | **GPIO 2** | Onboard Blue LED (Double-blinks on commands) |

> [!IMPORTANT]
> **ESP32 Hardware Boot Strapping Pin Caution:**
> * **GPIO 12 (Motor 4 IN1):** MTDI strapping pin. If driven HIGH externally during power-on/reset, flash voltage drops to 1.8V and the board will not boot. Ensure the motor driver input pulls low or stays neutral at boot.

---

## 2. First Run: Flashing via USB

1. Connect ESP32 to PC with a USB cable.
2. Open **Arduino IDE**.
3. Go to **File -> Open...** and select `esp4_leg_controller/esp4_leg_controller.ino`.
4. Under **Tools**:
   - **Board:** `ESP32 Dev Module`
   - **Upload Speed:** `921600` (or `115200`)
   - **Partition Scheme:** `Default 4MB with spiffs (1.2MB APP/1.5MB SPIFFS)` or `Minimal SPIFFS (1.9MB APP with OTA/190KB SPIFFS)`
   - **Port:** Select your ESP32 COM port.
5. Click **Upload**.
6. Open **Serial Monitor** at **115200 baud** to see:
   ```
   ==================================================
   ESP32 MOTOR CONTROLLER - BOARD ID: 4 (LEG DRIVING BOARD)
   Total Motors      : 4 (4 Encoded Channels)
   Communication Mode: WI-FI UDP JSON PACKET PROTOCOL (Port 5005)
   OTA Updates       : ENABLED (Port 3232 & Web Port 80)
   ==================================================
   [WIFI OK] Connected to el_Johannu!
   [WIFI OK] IP Address : 10.128.135.xxx
   [WIFI OK] Hostname   : esp32-robot-4.local
   ```

---

## 3. Subsequent Flashes: Over-The-Air (OTA)

Once the first USB flash is complete, **you do not need USB anymore**:

### Method A: WebOTA (Any Browser)
1. Open any web browser on your phone/PC connected to the same Wi-Fi.
2. Navigate to:
   ```
   http://esp32-robot-4.local/update
   ```
   (or `http://<ESP4_IP>/update`)
3. Choose the exported `.bin` file (`Sketch -> Export Compiled Binary` in Arduino IDE).
4. Click **Flash Firmware Over Wi-Fi**.

### Method B: Python Automated Script
In the project root:
```bash
python build_and_flash_ota.py --target esp4
```

---

## 4. Testing Motors via Python

Send UDP test commands using `test_individual_motor.py`:

```bash
# Format: <esp>-<motor>-<pulses>-<direction>

# Run Motor 1 (B1_front) for 50 pulses forward (CW)
python test_individual_motor.py 4-1-50-0

# Run Motor 2 (B1_rear) for 50 pulses reverse (CCW)
python test_individual_motor.py 4-2-50-1

# Run Motor 3 (B2_front) for 100 pulses forward
python test_individual_motor.py 4-3-100-0

# Run Motor 4 (B2_rear) for 100 pulses reverse
python test_individual_motor.py 4-4-100-1
```
