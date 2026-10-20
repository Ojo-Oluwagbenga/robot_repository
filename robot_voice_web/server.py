"""
Nova Robot Brain - Real-Time Voice Streaming Web Server
FastAPI + WebSocket backend providing ultra-low-latency bidirectional voice conversation with Google Gemini.
Accessible across your local Wi-Fi network from any smartphone, tablet, PC, or Raspberry Pi.
"""

import asyncio
import io
import json
import logging
import os
import socket
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("robot.voice_web")

# Paths
WEB_DIR = Path(__file__).parent.resolve()
STATIC_DIR = WEB_DIR / "static"
PROJECT_ROOT = WEB_DIR.parent.resolve()
BRAIN_DIR = PROJECT_ROOT / "robot" / "brain"

# Add project root to sys.path so we can import robot modules
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from standard .env locations
_env_candidates = [
    BRAIN_DIR / ".env",
    PROJECT_ROOT / "robot" / ".env",
    PROJECT_ROOT / ".env",
    Path.cwd() / ".env",
    Path.home() / "projects" / "Robot" / ".env",
    Path.home() / ".env",
]
for _env_f in _env_candidates:
    if _env_f.is_file():
        try:
            with open(_env_f, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        except Exception:
            pass

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DEFAULT_MODEL = os.environ.get("ROBOT_GEMINI_MODEL", "gemini-flash-lite-latest")

# Import or configure Gemini client
try:
    from robot.brain.gemini_client import GeminiClient
    from robot.brain.persona import RobotPersona
    logger.info("Imported GeminiClient and RobotPersona from robot.brain successfully.")
except Exception as e:
    logger.warning(f"Could not import robot.brain modules directly ({e}). Using inline client.")
    GeminiClient = None
    RobotPersona = None

# Gemini Live Conversational Persona Prompt
NOVA_SYSTEM_PROMPT = (
    "You are Nova in live voice conversation mode. "
    "You speak in the direct, natural, fluid, and engaging conversational style of Gemini Live. "
    "Crucial Guidelines: "
    "1. Speak naturally like a real human in live conversation—warm, witty, concise, and direct. "
    "2. Keep responses brief: usually 1 to 2 sentences (at most 3 sentences), so the conversation flows naturally back and forth without monologues. "
    "3. NEVER use bullet points, numbered lists, markdown formatting, asterisks (*), emojis, or code blocks. Everything you say will be spoken aloud through the user's speaker. "
    "4. Do not use corporate filler phrases like 'Certainly!', 'I would be happy to help with that', or 'As an AI'. Just answer directly and conversationally. "
    "5. You have physical embodiment as Nova: a humanoid robot created by Yohanna, running on a Raspberry Pi 5 brain board controlling 20 high-torque motors across 5 ESP32 controllers. "
    "6. Frequently end with a natural thought or short question to keep the voice dialogue moving smoothly."
)

app = FastAPI(title="Nova Robot Voice Interface", version="1.0.0")

# CORS middleware for open network access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_network_ips() -> List[str]:
    """Retrieve all non-loopback IPv4 addresses of this host for network access."""
    ips = ["127.0.0.1"]
    try:
        host_name = socket.gethostname()
        for ip in socket.gethostbyname_ex(host_name)[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Fallback probe using external routing
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ext_ip = s.getsockname()[0]
        if ext_ip not in ips:
            ips.append(ext_ip)
        s.close()
    except Exception:
        pass
    return ips


class VoiceSession:
    """Manages conversational history and streaming LLM sessions."""

    def __init__(self, api_key: str = GEMINI_API_KEY, model: str = DEFAULT_MODEL):
        self.api_key = api_key
        self.model = model
        self.history: List[Dict[str, str]] = []
        if GeminiClient:
            self.gemini = GeminiClient(api_key=api_key, model=model)
        else:
            self.gemini = None

    def reset(self):
        self.history = []

    def get_messages(self, user_query: str) -> List[Dict[str, str]]:
        messages = [{"role": "system", "content": NOVA_SYSTEM_PROMPT}]
        # Include recent turns (up to 8 turns)
        messages.extend(self.history[-8:])
        messages.append({"role": "user", "content": user_query})
        return messages

    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe audio using Gemini Multimodal Audio in an async worker thread."""
        if not self.gemini:
            raise HTTPException(status_code=500, detail="GeminiClient not initialized.")
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(None, self.gemini.transcribe_audio, audio_bytes)
        return text.strip()

    async def stream_reply(self, user_query: str, token_queue: asyncio.Queue):
        """Streams tokens from Gemini into an asyncio queue."""
        messages = self.get_messages(user_query)

        def sync_on_chunk(token: str):
            asyncio.run_coroutine_threadsafe(token_queue.put(token), loop)

        loop = asyncio.get_running_loop()

        def run_chat():
            try:
                reply = self.gemini.chat(
                    messages=messages,
                    stream=True,
                    on_chunk=sync_on_chunk,
                )
                return reply
            except Exception as ex:
                logger.error(f"Chat streaming error: {ex}")
                raise

        try:
            full_reply = await loop.run_in_executor(None, run_chat)
            # Record turns in history
            self.history.append({"role": "user", "content": user_query})
            self.history.append({"role": "assistant", "content": full_reply})
            await token_queue.put(None)  # Signal completion
            return full_reply
        except Exception as e:
            await token_queue.put({"__error__": str(e)})
            await token_queue.put(None)
            return ""


# Shared session
default_session = VoiceSession()


@app.get("/api/status")
async def api_status():
    """Return status, active model, and accessible network IP URLs."""
    ips = get_network_ips()
    port = 8000
    urls = [f"http://{ip}:{port}" for ip in ips]
    return {
        "status": "online",
        "robot_name": "Nova",
        "model": default_session.model,
        "api_key_configured": bool(default_session.api_key),
        "network_ips": ips,
        "access_urls": urls,
    }


@app.post("/api/reset")
async def api_reset():
    """Reset conversational history."""
    default_session.reset()
    return {"status": "ok", "message": "Memory turns cleared."}


@app.post("/api/transcribe")
async def api_transcribe(file: UploadFile = File(...)):
    """Transcribe an uploaded WAV or WebM audio file using Gemini."""
    audio_data = await file.read()
    if not audio_data:
        raise HTTPException(status_code=400, detail="Empty audio file received.")
    try:
        text = await default_session.transcribe(audio_data)
        return {"transcript": text}
    except Exception as e:
        logger.error(f"Transcription API failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/voice")
async def websocket_voice_endpoint(websocket: WebSocket):
    """
    Bidirectional WebSocket connection for live voice conversation:
    - Client sends binary audio (WAV 16kHz mono) or JSON command.
    - Server transcribes speech with Gemini.
    - Non-blocking concurrent streaming allows instant user barge-in / interruption.
    """
    await websocket.accept()
    logger.info("WebSocket voice client connected.")

    session = VoiceSession()
    active_stream_task: Optional[asyncio.Task] = None

    async def stream_and_send(user_query: str):
        try:
            await websocket.send_json({
                "type": "status",
                "state": "thinking",
                "message": "Nova is thinking..."
            })

            token_queue: asyncio.Queue = asyncio.Queue()
            gen_task = asyncio.create_task(session.stream_reply(user_query, token_queue))

            while True:
                token = await token_queue.get()
                if token is None:
                    break
                if isinstance(token, dict) and "__error__" in token:
                    await websocket.send_json({
                        "type": "error",
                        "message": token["__error__"]
                    })
                    break

                await websocket.send_json({
                    "type": "token",
                    "token": token
                })

            full_reply = await gen_task
            await websocket.send_json({
                "type": "complete",
                "text": full_reply,
            })
            await websocket.send_json({
                "type": "status",
                "state": "speaking",
                "message": "Nova finished speaking."
            })
        except asyncio.CancelledError:
            logger.info("[Interrupt] Active response generation cancelled by user interruption.")
            await websocket.send_json({
                "type": "status",
                "state": "idle",
                "message": "Interrupted."
            })
        except Exception as e:
            logger.error(f"Error during streaming response: {e}")

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            user_text = ""

            # Case A: Binary audio payload (WAV audio chunk recorded from browser mic)
            if "bytes" in message and message["bytes"]:
                # If Nova was already streaming or thinking, cancel immediately on user speech!
                if active_stream_task and not active_stream_task.done():
                    active_stream_task.cancel()
                    active_stream_task = None

                audio_bytes = message["bytes"]
                logger.info(f"Received audio buffer: {len(audio_bytes)} bytes.")

                # Notify client that transcription started
                await websocket.send_json({
                    "type": "status",
                    "state": "transcribing",
                    "message": "Listening..."
                })

                try:
                    user_text = await session.transcribe(audio_bytes)
                except Exception as e:
                    logger.error(f"Audio transcription error: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Speech transcription failed: {e}"
                    })
                    continue

                if not user_text:
                    await websocket.send_json({
                        "type": "status",
                        "state": "idle",
                        "message": "No speech detected."
                    })
                    continue

                # Emit recognized user transcript to the client
                await websocket.send_json({
                    "type": "transcript",
                    "text": user_text,
                })

            # Case B: Text command JSON from client
            elif "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "chat")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                        continue
                    elif msg_type == "interrupt":
                        logger.info("Received interrupt command from client.")
                        if active_stream_task and not active_stream_task.done():
                            active_stream_task.cancel()
                            active_stream_task = None
                        await websocket.send_json({"type": "status", "state": "idle", "message": "Interrupted."})
                        continue
                    elif msg_type == "clear":
                        if active_stream_task and not active_stream_task.done():
                            active_stream_task.cancel()
                            active_stream_task = None
                        session.reset()
                        await websocket.send_json({"type": "status", "state": "idle", "message": "Conversation reset."})
                        continue
                    elif msg_type == "chat":
                        if active_stream_task and not active_stream_task.done():
                            active_stream_task.cancel()
                            active_stream_task = None
                        user_text = data.get("text", "").strip()
                        if not user_text:
                            continue
                        await websocket.send_json({"type": "transcript", "text": user_text})
                except json.JSONDecodeError:
                    if active_stream_task and not active_stream_task.done():
                        active_stream_task.cancel()
                        active_stream_task = None
                    user_text = message["text"].strip()
                    await websocket.send_json({"type": "transcript", "text": user_text})

            if not user_text:
                continue

            # Start streaming response as a concurrent task so the receive loop remains active for interruptions
            if active_stream_task and not active_stream_task.done():
                active_stream_task.cancel()
            active_stream_task = asyncio.create_task(stream_and_send(user_text))

    except WebSocketDisconnect:
        logger.info("WebSocket voice client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket session exception: {e}")
    finally:
        if active_stream_task and not active_stream_task.done():
            active_stream_task.cancel()
        try:
            await websocket.close()
        except Exception:
            pass


# Mount static assets
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run(host: str = "0.0.0.0", port: int = 8000):
    ips = get_network_ips()
    print("\n" + "=" * 65)
    print("   [*] NOVA ROBOT BRAIN: REAL-TIME VOICE WEB INTERFACE")
    print("=" * 65)
    print(f"  AI Model   : {DEFAULT_MODEL}")
    print(f"  Gemini API : {'Configured [OK]' if GEMINI_API_KEY else 'Missing Key'}")
    print("  Accessible over your network at:")
    for ip in ips:
        print(f"    --> http://{ip}:{port}")
    print("-" * 65)
    print("  Press Ctrl+C to stop the server.")
    print("=" * 65 + "\n", flush=True)

    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
