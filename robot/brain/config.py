"""
Configuration settings for the Robot Brain module.
Defines Ollama connection endpoints, personality defaults, memory storage,
and web search behavior.
"""

import os
from pathlib import Path

# Base directory for the brain module
BRAIN_DIR = Path(__file__).resolve().parent
DATA_DIR = BRAIN_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Lightweight .env loader (checks robot/brain/.env and project root .env)
for env_candidate in [BRAIN_DIR / ".env", BRAIN_DIR.parent / ".env", BRAIN_DIR.parent.parent / ".env"]:
    if env_candidate.is_file():
        try:
            with open(env_candidate, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

# ==============================================================================
# 1. AI BACKEND & API CONFIGURATION
# ==============================================================================
# Active AI backend: 'ollama' (running on user's PC) or 'gemini' (cloud)
AI_BACKEND = os.environ.get("ROBOT_AI_BACKEND", "ollama").lower()

# Gemini API Configuration
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("ROBOT_GEMINI_MODEL", "gemini-flash-lite-latest")

# Ollama API Configuration (Primary & Fallback)
CANDIDATE_OLLAMA_URLS = [
    os.environ.get("ROBOT_OLLAMA_URL"),
    "http://10.35.208.172:11434",
    "http://127.0.0.1:11434",
    "http://DESKTOP-2CANEO2.local:11434",
    "http://10.206.152.172:11434",
    "http://10.82.240.172:11434",
]
CANDIDATE_OLLAMA_URLS = [u.rstrip("/") for u in CANDIDATE_OLLAMA_URLS if u]

PRIMARY_OLLAMA_URL = os.environ.get(
    "ROBOT_OLLAMA_URL", CANDIDATE_OLLAMA_URLS[0] if CANDIDATE_OLLAMA_URLS else "http://10.35.208.172:11434"
)
FALLBACK_OLLAMA_URL = os.environ.get(
    "ROBOT_OLLAMA_FALLBACK", "http://127.0.0.1:11434"
)
DEFAULT_MODEL = os.environ.get("ROBOT_OLLAMA_MODEL", "qwen2.5:0.5b")

REQUEST_TIMEOUT = float(os.environ.get("ROBOT_OLLAMA_TIMEOUT", "45.0"))
TEMPERATURE = 0.6
TOP_P = 0.9

NUM_THREADS = int(os.environ.get("ROBOT_NUM_THREADS", "4"))
NUM_CTX = int(os.environ.get("ROBOT_NUM_CTX", "512"))
MAX_OUTPUT_TOKENS = int(os.environ.get("ROBOT_MAX_OUTPUT_TOKENS", "120"))

# ==============================================================================
# 2. PERSONALITY & IDENTITY
# ==============================================================================
ROBOT_NAME = os.environ.get("ROBOT_NAME", "Nova")
ROBOT_CREATOR = os.environ.get("ROBOT_CREATOR", "Yohanna")
ROBOT_ROLE = "Humanoid Robot Companion & Assistant"
ROBOT_HARDWARE_SPECS = (
    "Raspberry Pi 5 brain board, 5 ESP32 motor controllers, 20 high-torque motors, "
    "synchronous 3-wire GPIO bus, connected mouthpiece audio ear, and speaker mouth."
)

# ==============================================================================
# 3. STRUCTURED MEMORY CONFIGURATION
# ==============================================================================
# Storage file for structured memory (JSON format)
MEMORY_FILE_PATH = DATA_DIR / "memory.json"

# Number of recent conversation turns (user + assistant pairs) in active LLM context
MAX_RECENT_TURNS = int(os.environ.get("ROBOT_MAX_RECENT_TURNS", "3"))

# Maximum number of stored historical dialogue turns before rolling summarization
MAX_HISTORY_ENTRIES = 50

# Maximum number of user facts to include in active prompt
MAX_PROFILE_FACTS = 6

# ==============================================================================
# 4. WEB SEARCH & LIVE DATA RETRIEVAL
# ==============================================================================
ENABLE_WEB_SEARCH = os.environ.get("ROBOT_ENABLE_WEB_SEARCH", "false").lower() in ("true", "1", "yes")
SEARCH_MAX_RESULTS = 2
SEARCH_TIMEOUT = 5.0

# Keywords indicating query strictly requires live / temporal information
LIVE_DATA_TRIGGERS = [
    "today", "yesterday", "tomorrow", "tonight", "current", "currently",
    "latest", "news", "weather", "temperature", "forecast", "price", "stock",
    "who is the current", "who won", "score", "president", "right now"
]

# Keywords indicating technical / factual queries that benefit from deep search
ADVANCED_SEARCH_TRIGGERS = [
    "specifications", "datasheet", "pinout", "documentation",
    "how does", "what is the difference", "algorithm", "research",
    "definition", "history of", "explain in detail"
]

# ==============================================================================
# 5. VOICE INPUT & SPEECH-TO-TEXT (STT) CONFIGURATION
# ==============================================================================
# Active STT engine: 'vosk' (lightweight local offline)
ROBOT_STT_ENGINE = os.environ.get("ROBOT_STT_ENGINE", "vosk").lower()

# Voice Activity Detection (RMS amplitude threshold for int16 audio)
VOICE_VAD_THRESHOLD = float(os.environ.get("ROBOT_VAD_THRESHOLD", "180.0"))
VOICE_MIN_SPEECH_DURATION = float(os.environ.get("ROBOT_MIN_SPEECH_DURATION", "0.3"))

# Silence duration in seconds to consider user speech finished (1.2s for prompt command turnaround)
VOICE_SILENCE_TIMEOUT = float(os.environ.get("ROBOT_VOICE_SILENCE", "1.2"))

# Audio capture format (16000Hz 16-bit mono)
AUDIO_SAMPLE_RATE = int(os.environ.get("ROBOT_AUDIO_SAMPLE_RATE", "16000"))
AUDIO_DEVICE_INDEX = os.environ.get("ROBOT_AUDIO_DEVICE")
ROBOT_PREFER_USB_MIC = os.environ.get("ROBOT_PREFER_USB_MIC", "true").lower() in ("true", "1", "yes")
ROBOT_MIC_KEYWORDS = ["usb", "ab13x", "headset", "microphone", "mic", "c-media", "respeaker", "array"]
AUDIO_CHANNELS = 1
AUDIO_DTYPE = "int16"

# Vosk model directory
VOSK_MODEL_PATH = os.environ.get(
    "ROBOT_VOSK_MODEL", "vosk-model-small-en-us-0.15"
)

# ==============================================================================
# 6. VOICE OUTPUT & SPEAKER CONFIGURATION
# ==============================================================================
# MOUTH SPEAKING DISABLED as requested
ROBOT_TTS_ENABLED = os.environ.get("ROBOT_TTS_ENABLED", "false").lower() in ("true", "1", "yes")
ROBOT_AUTO_CONNECT_BT = os.environ.get("ROBOT_AUTO_CONNECT_BT", "false").lower() in ("true", "1", "yes")

ROBOT_BT_MAC = os.environ.get("ROBOT_BT_MAC", "12:3F:2A:07:A1:F1")
ROBOT_BT_NAME = os.environ.get("ROBOT_BT_NAME", "BT SPEAKER")
ROBOT_PIPER_BIN = os.environ.get("ROBOT_PIPER_BIN", "/home/pi/piper/piper")
ROBOT_PIPER_MODEL = os.environ.get(
    "ROBOT_PIPER_MODEL", "/home/pi/robot_voice/en_US-ryan-medium.onnx"
)
ROBOT_TTS_RATE = int(os.environ.get("ROBOT_TTS_RATE", "22050"))
