# Nova Robot Voice Interface (Real-Time Gemini Streaming)

A real-time, bidirectional voice streaming web application for conversing with the **Nova Humanoid Robot Brain** using **Google Gemini** multimodal AI.

---

## Features

- **Hands-Free Live Conversation**: Dynamic voice activity detection (VAD) detects when you start and finish speaking, automatically sending audio without touching buttons.
- **Push-to-Talk Mode**: Optional hold-to-talk mode for loud environments.
- **60fps Holographic Visualizer**: Canvas-based bioluminescent audio visualizer with reactive frequency spectrum and glowing core.
- **Sentence-Level Streaming TTS**: Speaks responses sentence-by-sentence in real time as tokens arrive from Gemini, eliminating waiting time.
- **Full-Duplex Barge-In**: Immediately interrupts Nova if you start talking while she is responding.
- **Local Network Accessible**: Bound to `0.0.0.0:8000`, allowing any device on your Wi-Fi (phone, tablet, laptop, or Pi) to join the voice conversation.
- **Slide-Out Chat Transcript**: Collapsible conversation drawer with full turn history, token stats, and hybrid text chat input.

---

## Network Access URLs

When running, the server is accessible on:
* **Local PC**: [http://localhost:8000](http://localhost:8000)
* **Phone / Tablet on Wi-Fi**: `http://<YOUR_IP>:8000` (e.g. `http://10.216.150.172:8000`)

---

## How to Run

From the `Robot Code` workspace directory:

```bash
python robot_voice_web/run.py
```

Or run via Uvicorn directly:

```bash
uvicorn robot_voice_web.server:app --host 0.0.0.0 --port 8000
```
