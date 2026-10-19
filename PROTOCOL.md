# Robot Communication Protocol Specification

This document specifies the communication architecture linking the **Raspberry Pi 5** (Master controller) and the **4 ESP32 Motor Controller Boards** (Slaves controlling up to 4 motors each, 16 motors total).

---

## 1. System Architecture Overview

The system operates primarily in **Wi-Fi Mode** (`COMMUNICATION_MODE = "WIFI"` in `robot/config.py`). In this mode:
- **Transport**: UDP Broadcast over Wi-Fi on port `5005` (IP `255.255.255.255`).
- **Data Format**: Human-readable JSON packets.
- **OTA Updates**: Direct wireless flashing from PC/Pi via ArduinoOTA (port `3232`) or HTTP WebOTA (port `80` at `/update`).
- **Logging**: All transmitted commands (`TX`) and received responses (`RX`) are recorded with millisecond timestamps to [`logs/esp_comm.log`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/logs/esp_comm.log) and mirrored to [`logs/robot.log`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/logs/robot.log).

A physical 3-wire synchronous bus (`GPIO`) remains supported in `robot/config.py` (`COMMUNICATION_MODE = "GPIO"`) as a hardware fallback (documented in [Section 5](#5-legacy-3-wire-bus-protocol-hardware-fallback)).

---

## 2. Wi-Fi JSON Packet Protocol

### 2.1 Outgoing Commands (Pi / Master -> ESPs)

Commands are broadcasted by the Raspberry Pi over UDP port `5005`. Each ESP32 inspects the `esp` field and only acts on packets matching its own `ESP_ID` (or `esp: 0` for global broadcasts).

#### A. Motor Move Command
Transmitted when queuing or dispatching a motor motion:
```json
{
  "esp": 1,
  "motor": 2,
  "pulse": 300,
  "dir": 0,
  "instructionID": "12DE4",
  "cmd": "MOVE"
}
```

**Field Descriptions**:
| Field | Type | Description |
|---|---|---|
| `esp` | Integer (1–4, or 0) | Destination ESP controller ID (1 to 4). `0` targets all ESPs. |
| `motor` | Integer (1–4) | Target motor on the ESP board (1-indexed). |
| `pulse` | Integer (0–32767) | Total pulse count to rotate. |
| `dir` | Integer (0 or 1) | Direction: `0` = Clockwise (CW), `1` = Counter-Clockwise (CCW). |
| `instructionID` | String | Unique tracking ID for this instruction (e.g. `"12DE4"`). |
| `cmd` | String (optional) | `"MOVE"`, `"LOCK"`, or `"STOP"`. Defaults to `"MOVE"` when `pulse > 0`. |

#### B. Global Broadcast Commands
- **Execute / Go Ahead**:
  ```json
  {"esp": 0, "cmd": "GO_AHEAD", "instructionID": "sync_001"}
  ```
  Triggers immediate synchronized execution of queued moves across all ESPs.
- **Clear Queue**:
  ```json
  {"esp": 0, "cmd": "CLEAR_QUEUE", "instructionID": "clr_001"}
  ```
  Discards all buffered motor movements in memory.
- **Emergency Stop**:
  ```json
  {"esp": 0, "cmd": "STOP", "instructionID": "stop_001"}
  ```
  Immediately stops pulse generation on all active motors and engages active braking.
- **Heartbeat / Ping**:
  ```json
  {"esp": 0, "cmd": "STATUS", "instructionID": "ping_001"}
  ```
  Requests status broadcasts from all online ESPs.

---

### 2.2 Incoming Feedback & Telemetry (ESP -> Pi / Master)

When a motor finishes its pulse sequence or is interrupted, the ESP immediately sends a JSON response packet over UDP port `5005` back to the Pi:

#### A. Motion Completion Packet
```json
{
  "esp": 1,
  "motor": 2,
  "totalpulse": 305,
  "instructionID": "12DE4",
  "status": "completed"
}
```

**Field Descriptions**:
| Field | Type | Description |
|---|---|---|
| `esp` | Integer (1–4) | ID of the reporting ESP32 board. |
| `motor` | Integer (1–4) | Motor ID that completed the motion (1-indexed). |
| `totalpulse` | Integer | Signed net pulse count executed (+ for CW, - for CCW). |
| `instructionID` | String | Matches the `instructionID` from the original Pi command. |
| `status` | String | `"completed"` or `"interrupted"`. |

#### B. Board Online / Status Heartbeat
ESP32s broadcast periodic status packets every 30 seconds or upon receiving a ping:
```json
{
  "esp": 1,
  "hostname": "esp32-robot-1",
  "ip": "192.168.1.101",
  "status": "online",
  "uptime_ms": 34820,
  "ota": true
}
```

---

## 3. Dedicated Communication Logging

All outgoing (`TX`) and incoming (`RX`) packets are recorded by `robot/debug/wifi_logger.py` to:
- [`logs/esp_comm.log`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/logs/esp_comm.log) (Dedicated packet log)
- [`logs/robot.log`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/logs/robot.log) (System-wide log)

### Example Log Format:
```text
[2026-10-10 10:02:25.742] [TX -> BROADCAST:5005] {"esp": 1, "motor": 2, "pulse": 300, "dir": 0, "instructionID": "12DE4", "cmd": "MOVE"}
[2026-10-10 10:02:26.150] [RX <- ESP 1 (192.168.1.101:5005)] {"esp": 1, "motor": 2, "totalpulse": 305, "instructionID": "12DE4", "status": "completed"}
```

To live stream packets or inspect logs from your PC:
```bash
python test_wifi_comm.py --log          # View the last entries of logs/esp_comm.log
python test_wifi_comm.py --monitor      # Live stream all incoming and outgoing UDP packets
```

---

## 4. Over-The-Air (OTA) Wireless Firmware Flashing

No USB connection is required once the initial firmware is flashed onto each ESP32. You can update code on any ESP directly from your PC over Wi-Fi.

### 4.1 Prerequisites
1. Ensure `WIFI_SSID` and `WIFI_PASS` in [`esp32_wifi_controller/config.h`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/esp32_wifi_controller/config.h) match your Wi-Fi router / phone hotspot.
2. Compile your sketch to `.bin` format using the Arduino IDE (`Sketch -> Export Compiled Binary`) or PlatformIO (`pio run`).

### 4.2 Updating Firmware via PC Script (`deploy_esp_ota.py`)
Run the deployment tool from your PC terminal:
```bash
# 1. Scan the local network to find all online ESP32 boards
python deploy_esp_ota.py --scan

# 2. Flash compiled firmware to ESP #1
python deploy_esp_ota.py --target esp1 --bin path/to/firmware.bin

# 3. Flash to a specific IP address
python deploy_esp_ota.py --ip 192.168.1.101 --bin path/to/firmware.bin

# 4. Flash all 4 ESPs sequentially
python deploy_esp_ota.py --target all --bin path/to/firmware.bin
```

### 4.3 Updating Firmware via Web Browser (Zero-Installation)
Each ESP runs an embedded HTTP server on port 80:
1. Open your web browser on your PC or phone.
2. Navigate to: `http://esp32-robot-1.local/` or `http://<ESP_IP>/`
3. A web status page will load showing the board ID, uptime, and an OTA update form.
4. Select your compiled `.bin` file and click **"Upload & Flash Firmware"**.
5. The ESP will flash itself and reboot in ~5 seconds.

---

## 5. Legacy 3-Wire Bus Protocol (Hardware Fallback)

Used only when `COMMUNICATION_MODE = "GPIO"` in `robot/config.py`.

### 5.1 Signal Lines:
- `CLOCK`: 50 kHz square wave from Raspberry Pi (BCM 17).
- `DATA`: Serial command data stream (BCM 27).
- `SET`: Framing control (BCM 22). Active LOW enables shift-in; transition to HIGH latches packet.
- `MOTOR_ENABLE`: Active HIGH power gate (BCM 26).
- `FEEDBACK_1..5`: ESP32 GPIO 15 to Pi GPIO lines (BCM 23, 4, 24, 25, 6).

### 5.2 21-Bit Command Bit Layout:
```
Bit Index:  0  1  2  │  3  4  │  5  6  7  8  9 10 11 12 13 14 15 16 17 18 19 │  20
Field:     [ESP ID ] │ [M ID] │ [              Pulse Count                  ] │ [DIR]
Width:       3 bits  │ 2 bits │                   15 bits                     │ 1 bit
```

### 5.3 17-Bit Feedback Bit Layout:
```
Bit Index:  0  1  │  2  3  4  5  6  7  8  9 10 11 12 13 14 15 │   16
Field:     [M ID] │ [         Pulse Magnitude (14 bits)       ] │ [SIGN]
Width:     2 bits │                    14 bits                  │  1 bit
```
