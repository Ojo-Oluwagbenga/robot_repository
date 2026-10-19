# robot_repository

Complete source code, firmware, AI vision models, cloud bridge, and companion mobile application for the **Nova Humanoid Robot**.

---

## 1. System Architecture

```text
       [ Gemini Live Voice ]
                 │
                 ▼ (Reads personality & context / GET /instruct)
    [ Robot Bridge Site (Next.js) ] ◄──► [ Google Firebase Realtime DB ]
                 ▲                                │
                 │ (Polls: /api/pi_instructions)  │ (Dispatches: /api/phone_speech)
                 │                                ▼
       [ Raspberry Pi 5 Brain ]       [ LUKU Live Phone App ]
                 │                                │
                 ▼ (UDP Broadcast: 5005)          ▼ (Android TTS)
   [ 4x ESP32 Motor Controllers ]      [ External Speaker Out Loud ]
                 │
                 ▼
     [ 16+ Joint Motors & Servos ]
```

---

## 2. Repository Structure

| Directory / File | Description |
|---|---|
| [`robot/`](robot/) | Core robot runtime: motor drivers, Wi-Fi UDP communication, kinematics, vision tracking, and bridge executor. |
| [`robot/bridge/`](robot/bridge/) | Bridge listener, code-to-action dispatcher, and phone speech client. |
| [`robot_bridge_site/`](robot_bridge_site/) | Next.js realtime cloud bridge hosted on Vercel connecting Gemini Live with Raspberry Pi. |
| [`luku/`](luku/) | Native Android voice companion app (LUKU Live) with direct LAN and cloud speech receivers. |
| [`esp32_wifi_controller/`](esp32_wifi_controller/) | ESP32 wireless motor driver firmware (Board #1–#3). |
| [`esp4_leg_controller/`](esp4_leg_controller/) | ESP32 Board #4 firmware specialized for biped leg actuators. |
| [`PROTOCOL.md`](PROTOCOL.md) | High-speed Wi-Fi UDP broadcast packet specification between Pi and ESPs. |

---

## 3. Getting Started

### A. Raspberry Pi Hardware Controller
```bash
# Start the Bridge listener and action executor
python3 robot/bridge_main.py
```

### B. Robot Bridge Site (Local or Vercel)
```bash
cd robot_bridge_site
npm install
npm run dev
```

### C. Build LUKU Live Android APK
```powershell
cd luku
.\build.ps1
```

---

## 4. Communication Protocol
See [`PROTOCOL.md`](PROTOCOL.md) for UDP packet structures, synchronization commands, and OTA updates.
