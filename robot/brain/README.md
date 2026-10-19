# Robot Brain: Cognitive, Memory & Speech System

This module handles the cognitive reasoning, structured memory, real-time web browsing, personality synthesis, and audio interfaces (mouth speaker and ear mouthpiece) for the humanoid robot.

---

## 1. Architecture Overview

```
robot/brain/
├── config.py          # Central configurations (Ollama host, model, timeouts, memory limits)
├── persona.py         # Robot persona, embodiment awareness, and system prompt generator
├── memory.py          # Structured persistent memory (user profile, episodic turns, JSON store)
├── search.py          # Real-time web browsing (DuckDuckGo, Wikipedia) with Ollama filtering
├── ollama_client.py   # Robust Ollama HTTP client with primary and fallback endpoints
├── mouth.py           # Connected speaker / TTS interface (ready for phase 2)
├── ear.py             # Connected mouthpiece / STT interface (ready for phase 2)
├── brain.py           # Master cognitive coordinator (RobotBrain class)
├── interactive.py     # Terminal chat interface with real-time token streaming
└── __main__.py        # Module entrypoint for: python -m robot.brain
```

---

## 2. Key Capabilities

### A. Central Neural Ollama Integration
- Reaches your PC via primary URL: `http://DESKTOP-2CANEO2.local:11434`
- Automatically detects and falls back to `http://127.0.0.1:11434` if running locally or if mDNS hostname lookup fails.
- Configured for the fast `llama3.2:1b` model (or any model set in `ROBOT_OLLAMA_MODEL`).
- Supports both `/api/chat` (multi-turn conversation with memory) and `/api/generate`.
- Real-time token streaming for zero-latency interactive feedback.

### B. Robot Personality & Embodiment
- Default Persona: **Nova** (warm, witty, technically sharp, loyal companion to Yohanna).
- Physical embodiment awareness: understands it is a physical robot running on a Raspberry Pi 5 with 5 ESP32 motor controllers, 20 motors, a connected ear mouthpiece, and a connected mouth speaker.
- Speech-optimized responses: avoids giant markdown tables or walls of code unless requested, ensuring answers sound natural when read aloud.

### C. Structured Memory
- **Semantic Profile Facts**: Remembers user identity, preferences, ongoing tasks, and calibration details (stored persistently in `robot/brain/data/memory.json`).
- **Episodic Dialogue History**: Retains recent turns in active LLM context and compresses older turns into rolling summaries to prevent context window overflow.
- **Dynamic Learning**: Automatically extracts facts from phrases like *"Remember that I am testing the leg motor"* or *"My favorite sensor is the IMU"*.

### D. Real-Time Web Browsing & Ollama Fact Filtering
- Automatically detects questions needing live data (weather, news, current time/dates, scores, prices, status).
- Queries real-time search backends (DuckDuckGo Search + Wikipedia fallback).
- Uses Ollama to filter out ads and noise, synthesizing only 1–3 verified factual sentences for the conversation.

### E. Mouth & Ear Interfaces (Prepared for Hardware Connection)
- `RobotMouth`: Text-to-Speech abstraction for the connected speaker.
- `RobotEar`: Speech-to-Text abstraction for the connected mouthpiece/microphone.
- Modular stubs ready for hardware wiring and voice models.

---

## 3. How to Run and Test

You can test the brain directly in the terminal before connecting the speaker and microphone:

```bash
# 1. Hands-Free Voice Mode (Vosk + sounddevice microphone input):
python robot/brain/voice.py
# or
python -m robot.brain --voice

# 2. Terminal Text Chat Mode:
python -m robot.brain
# or
python robot/brain/interactive.py
```

### Interactive Terminal Commands
While chatting, you can use built-in slash commands:
- `/memory` - Inspect all remembered facts about you and conversation history.
- `/search <topic>` - Manually test live internet search and Ollama filtering.
- `/clear` - Clear conversation turns while keeping learned profile facts.
- `/reset` - Completely wipe all persistent memory.
- `/status` - Check Ollama connection health and diagnostic metrics.
- `exit` or `quit` - End the chat session.

---

## 4. Environment Variables (Optional)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `ROBOT_OLLAMA_URL` | `http://DESKTOP-2CANEO2.local:11434` | Primary host URL for PC Ollama |
| `ROBOT_OLLAMA_FALLBACK` | `http://127.0.0.1:11434` | Fallback endpoint |
| `ROBOT_OLLAMA_MODEL` | `llama3.2:1b` | Target LLM model |
| `ROBOT_OLLAMA_TIMEOUT` | `45.0` | Timeout in seconds |
| `ROBOT_NAME` | `Nova` | Robot's persona name |
| `ROBOT_CREATOR` | `Yohanna` | Companion & creator name |
