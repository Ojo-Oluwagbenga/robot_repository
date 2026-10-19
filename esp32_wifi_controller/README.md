# ESP32 Wi-Fi & OTA Motor Controller

This folder contains the complete, self-contained firmware sketch for the **ESP32 Motor Controller Boards** operating over **Wi-Fi JSON broadcast** with **Over-The-Air (OTA)** firmware updates.

---

## 📁 Folder Contents

| File | Purpose |
|---|---|
| `esp32_wifi_controller.ino` | Main Arduino sketch: setup, main loop, JSON command dispatch, OTA handler |
| `config.h` | Configuration: `ESP_ID`, `WIFI_SSID`, `WIFI_PASS`, pin definitions, PWM parameters |
| `wifi_communication.h` / `.cpp` | UDP JSON receiver/transmitter, ArduinoOTA (port 3232), WebOTA (port 80) |
| `motor.h` / `.cpp` | Motor control logic, acceleration profiling, closed-loop timing, feedback triggers |
| `encoder.h` / `.cpp` | Hardware quadrature encoder decoding & automatic single-pin fallback |
| `protocol.h` / `.cpp` | Packet definitions and formatting utilities |
| `feedback.h` / `.cpp` | Feedback queuing and management |

---

## 🚀 Quick Setup & Flashing

### Step 1: Set Wi-Fi Credentials & Board ID
Open [`config.h`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/esp32_wifi_controller/config.h):
```cpp
#define ESP_ID          1             // Set to 1, 2, 3, or 4 for each board
#define WIFI_SSID       "RobotWiFi"   // Your Wi-Fi network or phone hotspot name
#define WIFI_PASS       "robot1234"   // Your Wi-Fi password
```

### Step 2: First-Time USB Flash (One-Time Only)
1. Open [`esp32_wifi_controller.ino`](file:///c:/Users/YOHANNA/Desktop/Projects/Robot%20Code/esp32_wifi_controller/esp32_wifi_controller.ino) in Arduino IDE.
2. Select Board: **ESP32 Dev Module**.
3. Select your ESP's COM port.
4. Click **Upload**.
5. Once uploaded, open Serial Monitor at **115200 baud** to verify:
   ```text
   [WIFI OK] Connected to RobotWiFi!
   [WIFI OK] IP Address : 192.168.x.x
   [WIFI OK] Hostname   : esp32-robot-1.local
   [WEB OTA] HTTP Web server listening on port 80 (/update)
   ```
6. Repeat for ESP #2, #3, and #4 (changing `#define ESP_ID` in `config.h` for each board).

---

## 📡 Wireless OTA Updates (No USB Cables Needed)

After the initial flash, **USB is never needed again**. You can deploy updates directly from your PC:

### Method A: From PC Command Line
```bash
# 1. Scan the network to discover all online ESPs
python deploy_esp_ota.py --scan

# 2. Flash compiled firmware to ESP 1 over Wi-Fi
python deploy_esp_ota.py --target esp1 --bin path/to/firmware.bin

# 3. Flash all 4 ESPs sequentially
python deploy_esp_ota.py --target all --bin path/to/firmware.bin
```

### Method B: From Web Browser
Open your browser on PC or phone and go to:
`http://esp32-robot-1.local/` (or `http://<ESP_IP>/`)
- Select your `.bin` file under **Firmware Update (OTA)**.
- Click **Upload & Flash Firmware**. The ESP flashes itself and reboots in ~5s.

---

## 💬 JSON Communication Protocol

### Commands Received (from Raspberry Pi or PC):
```json
{"esp": 1, "motor": 2, "pulse": 300, "dir": 0, "instructionID": "12DE4", "cmd": "MOVE"}
```

### Feedback Sent Back (to Raspberry Pi and Logger):
```json
{"esp": 1, "motor": 2, "totalpulse": 300, "instructionID": "12DE4", "status": "completed"}
```

### Testing Commands from PC:
```bash
python test_wifi_comm.py --esp 1 --motor 2 --pulse 300 --dir 0 --id 12DE4
python test_wifi_comm.py --log      # View traffic log
python test_wifi_comm.py --monitor  # Live stream UDP packets
```
