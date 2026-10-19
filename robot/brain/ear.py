"""
Robot Ear Interface (Connected Mouthpiece / Microphone).
Provides real-time speech-to-text audio capture using Vosk and sounddevice,
with intelligent silence detection (end-of-utterance), live streaming partial transcription,
and graceful fallback to text/terminal input.
"""

import collections
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import numpy as np

# Ensure project root and robot directory are in sys.path
CURRENT_DIR = Path(__file__).resolve().parent           # .../robot/brain
ROBOT_DIR = CURRENT_DIR.parent                         # .../robot
PROJECT_ROOT = ROBOT_DIR.parent                        # .../Robot Code

for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from robot.brain.config import (
        AUDIO_CHANNELS,
        AUDIO_DEVICE_INDEX,
        AUDIO_DTYPE,
        AUDIO_SAMPLE_RATE,
        GEMINI_API_KEY,
        ROBOT_MIC_KEYWORDS,
        ROBOT_PREFER_USB_MIC,
        ROBOT_STT_ENGINE,
        VOICE_MIN_SPEECH_DURATION,
        VOICE_SILENCE_TIMEOUT,
        VOICE_VAD_THRESHOLD,
        VOSK_MODEL_PATH,
    )
except ImportError:
    from brain.config import (
        AUDIO_CHANNELS,
        AUDIO_DEVICE_INDEX,
        AUDIO_DTYPE,
        AUDIO_SAMPLE_RATE,
        GEMINI_API_KEY,
        ROBOT_MIC_KEYWORDS,
        ROBOT_PREFER_USB_MIC,
        ROBOT_STT_ENGINE,
        VOICE_MIN_SPEECH_DURATION,
        VOICE_SILENCE_TIMEOUT,
        VOICE_VAD_THRESHOLD,
        VOSK_MODEL_PATH,
    )

logger = logging.getLogger("robot.brain.ear")

AUDIO_DEPS_ERROR: Optional[str] = None
try:
    import sounddevice as sd
    AUDIO_DEPS_AVAILABLE = True
except Exception as e:
    AUDIO_DEPS_AVAILABLE = False
    AUDIO_DEPS_ERROR = str(e)
    logger.warning(f"Voice audio dependencies unavailable ({e}). RobotEar running in stub/fallback mode.")

# Vosk is optional (only needed if explicitly running in offline vosk mode)
VOSK_AVAILABLE = False
try:
    from vosk import KaldiRecognizer, Model
    VOSK_AVAILABLE = True
except Exception:
    KaldiRecognizer, Model = None, None


def ensure_physical_mic_source() -> Optional[Tuple[str, str]]:
    """
    Ensure PipeWire default recording source points to physical USB mic (not Bluetooth speaker).
    Returns (source_id, source_name) if set, or None.
    """
    if not shutil.which("wpctl"):
        return None
    try:
        res = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=5)
        status_out = res.stdout
    except Exception:
        return None

    in_sources = False
    source_candidates = []
    is_bluez_default = False

    for line in status_out.splitlines():
        clean = line.strip()
        if "*" in clean and ("bluez" in clean.lower() or "speaker" in clean.lower()):
            is_bluez_default = True
        if "Sources:" in clean:
            in_sources = True
            continue
        if in_sources and any(hdr in clean for hdr in ["Filters:", "Streams:", "Settings:"]):
            in_sources = False
            continue
        if in_sources and clean and ("." in clean or "bluez" in clean):
            num = None
            for token in clean.split():
                t = token.lstrip("*").rstrip(".:")
                if t.isdigit():
                    num = t
                    break
            if num and "bluez" not in clean.lower() and "monitor" not in clean.lower():
                import re
                clean_name = re.sub(r'^[│\s*0-9.:\-]+', '', clean)
                clean_name = re.sub(r'\[vol:.*?\]', '', clean_name).strip()
                source_candidates.append((num, clean_name or clean))

    has_physical_default = False
    for line in status_out.splitlines():
        clean = line.strip()
        if "Sources:" in clean:
            in_sources = True
            continue
        if in_sources and ("Filters:" in clean or "Streams:" in clean):
            break
        if in_sources and "*" in clean and "bluez" not in clean.lower() and "monitor" not in clean.lower():
            has_physical_default = True

    if (is_bluez_default or not has_physical_default) and source_candidates:
        mic_id, mic_name = source_candidates[0]
        try:
            subprocess.run(["wpctl", "set-default", mic_id], check=True, timeout=5)
            logger.info(f"Set PipeWire default recording source to ID {mic_id} ({mic_name})")
            return mic_id, mic_name
        except Exception as e:
            logger.warning(f"Could not set default source to {mic_id}: {e}")
    elif source_candidates:
        return source_candidates[0]

    return None


def find_best_input_device(
    requested_device: Optional[Any] = None,
    prefer_usb: bool = ROBOT_PREFER_USB_MIC,
    keywords: Optional[List[str]] = None,
) -> Tuple[Optional[int], str]:
    """
    Auto-detect and prioritize dedicated USB / physical microphones over Bluetooth sinks.
    Ensures PipeWire routing to physical mic and checks for 16kHz sample rate compatibility.
    Returns (device_index, device_name).
    """
    if not AUDIO_DEPS_AVAILABLE:
        return None, "Audio dependencies unavailable"

    # Step 1: Ensure PipeWire default source routes to physical USB microphone
    pipewire_mic = ensure_physical_mic_source()

    try:
        devices = sd.query_devices()
    except Exception as e:
        return None, f"Query error: {e}"

    def supports_16k(dev_idx: int) -> bool:
        try:
            sd.check_input_settings(device=dev_idx, samplerate=AUDIO_SAMPLE_RATE, channels=1, dtype=AUDIO_DTYPE)
            return True
        except Exception:
            return False

    # 1. If explicit device index or name is provided
    if requested_device is not None and str(requested_device).strip() and str(requested_device).lower() not in ("none", "auto"):
        req_str = str(requested_device).strip()
        try:
            dev_idx = int(req_str)
            if supports_16k(dev_idx):
                dev_info = sd.query_devices(dev_idx, "input")
                return dev_idx, dev_info.get("name", f"Device #{dev_idx}")
            else:
                logger.warning(f"Requested device #{dev_idx} does not support {AUDIO_SAMPLE_RATE}Hz. Falling back to default.")
        except Exception:
            for idx, d in enumerate(devices):
                if d.get("max_input_channels", 0) > 0 and req_str.lower() in d.get("name", "").lower():
                    if supports_16k(idx):
                        return idx, d["name"]

    # 2. On Linux PipeWire, 'default' or 'pipewire' bridges to the physical mic with automatic resampling
    mic_label = f"USB Mic [{pipewire_mic[1]}]" if pipewire_mic else "Default System Input"
    for pref_name in ["default", "pipewire"]:
        for idx, d in enumerate(devices):
            if d.get("name", "").lower() == pref_name and d.get("max_input_channels", 0) > 0:
                if supports_16k(idx):
                    return idx, mic_label

    # 3. Search other input devices supporting 16kHz mono
    usb_keywords = keywords or ROBOT_MIC_KEYWORDS
    excluded_keywords = ["bluez", "bluetooth", "speaker", "sink", "monitor"]
    candidates = []
    for idx, d in enumerate(devices):
        name = d.get("name", "").lower()
        channels = d.get("max_input_channels", 0)
        if channels <= 0 or any(ex in name for ex in excluded_keywords):
            continue
        if not supports_16k(idx):
            continue
        score = sum(3 for kw in usb_keywords if kw in name)
        candidates.append((score, idx, d["name"]))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1], candidates[0][2]

    # 4. Fallback to default input
    try:
        def_idx = sd.default.device[0]
        if def_idx is not None and def_idx >= 0 and supports_16k(def_idx):
            d_info = sd.query_devices(def_idx, "input")
            return def_idx, d_info.get("name", f"Default #{def_idx}")
    except Exception:
        pass

    return None, mic_label


class RobotEar:
    """
    Handles microphone audio capture and Speech-to-Text (STT).
    Defaults to Google Gemini Multimodal Cloud STT for ultra-high accuracy and zero Pi RAM overhead,
    with intelligent acoustic energy Voice Activity Detection (VAD) and optional Vosk offline fallback.
    """

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: Optional[Any] = AUDIO_DEVICE_INDEX,
        silence_timeout: float = VOICE_SILENCE_TIMEOUT,
        samplerate: int = AUDIO_SAMPLE_RATE,
        stt_engine: str = ROBOT_STT_ENGINE,
        gemini_client: Optional[Any] = None,
        vad_threshold: float = VOICE_VAD_THRESHOLD,
        auto_init: bool = False,
    ):
        self.stt_engine = (stt_engine or "gemini").lower()
        self.gemini_client = gemini_client
        self.vad_threshold = vad_threshold
        self.requested_model = model_path or VOSK_MODEL_PATH
        self.requested_device = device
        self.device: Optional[int] = None
        self.device_name: str = "Auto-Detecting"
        self.silence_timeout = silence_timeout
        self.samplerate = samplerate

        self.model: Optional[Any] = None
        self.recognizer: Optional[Any] = None
        self.stream: Optional[Any] = None
        self.audio_queue: queue.Queue = queue.Queue()

        self._is_active = False
        self._muted = False
        self.is_listening = False
        self.resolved_model_path: Optional[str] = None

        if auto_init and AUDIO_DEPS_AVAILABLE:
            self._initialize_engine()

    def _find_model_path(self, target: str) -> Optional[str]:
        """Search multiple common locations for the Vosk model directory."""
        def is_valid_vosk_dir(p: Path) -> bool:
            return p.is_dir() and ((p / "conf").exists() or (p / "am").exists() or (p / "model").exists() or (p / "graph").exists())

        # Prioritize lightweight small model to protect 2GB SBC memory
        target_candidates = ["vosk-model-small-en-us-0.15"]
        if target and target not in target_candidates and "lgraph" not in target:
            target_candidates.append(target)
        elif target and "lgraph" in target:
            target_candidates.append(target)

        for cand in target_candidates:
            locations = [
                Path(cand),
                Path.cwd() / cand,
                Path("/home/pi") / cand,
                Path.home() / cand,
                Path.home() / ".cache" / "vosk" / cand,
                Path.home() / ".cache" / "vosk",
                Path(__file__).resolve().parent / cand,
                Path(__file__).resolve().parent / "data" / cand,
            ]
            for p in locations:
                if is_valid_vosk_dir(p):
                    return str(p.resolve())
                if p.is_dir():
                    for sub in p.iterdir():
                        if is_valid_vosk_dir(sub):
                            return str(sub.resolve())
                    return str(p.resolve())

        search_roots = [
            Path("/home/pi"),
            Path.home(),
            Path.cwd(),
            Path.cwd().parent,
            Path(__file__).resolve().parent / "data",
        ]
        for root in search_roots:
            if root.exists():
                try:
                    for d in root.glob("*vosk*"):
                        if is_valid_vosk_dir(d):
                            return str(d.resolve())
                        if d.is_dir():
                            for sub in d.iterdir():
                                if is_valid_vosk_dir(sub):
                                    return str(sub.resolve())
                            return str(d.resolve())
                except Exception:
                    pass

        return None

    def _download_small_model(self, parent_dir: Path) -> bool:
        """Download and extract lightweight vosk-model-small-en-us-0.15 (40MB)."""
        import urllib.request
        import zipfile
        url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        zip_path = parent_dir / "vosk-model-small-en-us-0.15.zip"
        try:
            print(f"[*] Downloading lightweight Vosk speech model (~40MB)...", flush=True)
            urllib.request.urlretrieve(url, zip_path)
            print("[*] Extracting model...", flush=True)
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(parent_dir)
            if zip_path.exists():
                zip_path.unlink()
            print("[+] Vosk speech model installed successfully!", flush=True)
            return True
        except Exception as e:
            logger.warning(f"Could not auto-download Vosk model: {e}")
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:
                    pass
            return False

    def _initialize_engine(self) -> bool:
        """Initialize audio input device and STT engine (Gemini cloud or Vosk offline)."""
        if not AUDIO_DEPS_AVAILABLE:
            return False

        try:
            # 1. Resolve and initialize microphone device (PipeWire USB mic)
            self.samplerate = AUDIO_SAMPLE_RATE
            resolved_dev, dev_name = find_best_input_device(self.requested_device)
            self.device = resolved_dev
            self.device_name = dev_name
            print(f"[*] Microphone selected: '{dev_name}' (device index: {resolved_dev})")

            # 2. If Gemini STT engine is selected (default)
            if self.stt_engine == "gemini":
                if self.gemini_client is None:
                    try:
                        from robot.brain.gemini_client import GeminiClient
                        self.gemini_client = GeminiClient()
                    except ImportError:
                        from brain.gemini_client import GeminiClient
                        self.gemini_client = GeminiClient()

                self._is_active = True
                print(f"[*] Speech Input Ready: Gemini Multimodal Cloud STT via '{dev_name}'")
                logger.info(f"RobotEar initialized with Google Gemini Multimodal Cloud STT on '{dev_name}'.")
                return True

            # 3. If Vosk offline STT is explicitly requested
            if not VOSK_AVAILABLE:
                logger.warning("Vosk package is not installed. Switching RobotEar to Gemini STT.")
                self.stt_engine = "gemini"
                self._is_active = True
                return True

            resolved = self._find_model_path(self.requested_model)
            if not resolved:
                target_dir = Path("/home/pi") if Path("/home/pi").exists() else Path.home()
                if self._download_small_model(target_dir):
                    resolved = self._find_model_path(self.requested_model)

            if not resolved:
                try:
                    self.model = Model(model_name="vosk-model-small-en-us-0.15")
                    resolved = "vosk-cache:vosk-model-small-en-us-0.15"
                except Exception:
                    pass

            if not resolved:
                logger.warning("Vosk model not found locally. Switching RobotEar to Gemini STT.")
                self.stt_engine = "gemini"
                self._is_active = True
                return True

            if self.model is None:
                self.resolved_model_path = resolved
                self.model = Model(self.resolved_model_path)
            else:
                self.resolved_model_path = resolved

            self.recognizer = KaldiRecognizer(self.model, self.samplerate)
            self.recognizer.SetWords(True)
            self._is_active = True
            logger.info("RobotEar engine initialized with Vosk at 16000Hz.")
            return True

        except Exception as e:
            logger.error(f"RobotEar initialization failed: {e}")
            self._is_active = False
            return False

    def is_available(self) -> bool:
        """Check if microphone and STT engine are ready for speech input."""
        if not self._is_active:
            if AUDIO_DEPS_AVAILABLE:
                return self._initialize_engine()
            return False

        if self.stt_engine == "gemini":
            client = self.gemini_client
            api_key = getattr(client, "api_key", None) or os.environ.get("GEMINI_API_KEY", "")
            return bool(AUDIO_DEPS_AVAILABLE and api_key)

        return self.recognizer is not None

    def _audio_callback(self, indata, frames, time_info, status):
        """Sounddevice raw audio stream callback."""
        if status and logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"[Ear Stream Status] {status}")
        if not self._muted:
            self.audio_queue.put(bytes(indata))

    def start_listening(self) -> bool:
        """Start the background raw audio input stream."""
        if not self.is_available():
            if not self._initialize_engine():
                return False

        if self.stream is not None and self.stream.active:
            return True

        try:
            self.stream = sd.RawInputStream(
                samplerate=self.samplerate,
                dtype=AUDIO_DTYPE,
                channels=AUDIO_CHANNELS,
                callback=self._audio_callback,
                device=self.device,
            )
            self.stream.start()
            self.is_listening = True
            logger.info("Microphone stream started. Listening...")
            return True
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
            self.is_listening = False
            return False

    def stop_listening(self) -> None:
        """Stop and close the audio input stream."""
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.debug(f"Error closing audio stream: {e}")
            finally:
                self.stream = None
        self.is_listening = False
        self._flush_queue()
        logger.debug("Microphone stream stopped.")

    def mute(self) -> None:
        """Mute microphone queue while the robot is speaking to prevent acoustic feedback."""
        self._muted = True
        self._flush_queue()

    def unmute(self) -> None:
        """Unmute microphone queue when the robot is ready to listen again."""
        self._flush_queue()
        if self.recognizer:
            self.recognizer.Reset()
        self._muted = False

    def _flush_queue(self) -> None:
        """Discard all pending audio frames from the queue."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def _pcm_to_wav(self, frames: List[bytes]) -> bytes:
        """Convert raw PCM chunks to standard WAV format in memory."""
        import io
        import wave
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(AUDIO_CHANNELS)
            wf.setsampwidth(2)  # int16 = 2 bytes
            wf.setframerate(self.samplerate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()

    def listen(
        self,
        silence_timeout: Optional[float] = None,
        timeout: Optional[float] = None,
        on_partial: Optional[Callable[[str], None]] = None,
        gemini_client: Optional[Any] = None,
    ) -> Optional[str]:
        """
        Listen for a complete spoken utterance from the microphone.
        In Gemini mode (default): Uses high-speed RMS acoustic energy VAD to buffer speech,
        then transcribes using Google Gemini Multimodal Audio API.
        In Vosk mode: Uses KaldiRecognizer offline transcription.
        """
        if not self.is_available():
            if not self._initialize_engine():
                logger.debug("Voice STT engine unavailable. listen() returned None.")
                return None

        # Ensure audio stream is active
        if self.stream is None or not self.stream.active:
            if not self.start_listening():
                return None

        # Reset and flush previous sound
        self.unmute()

        # ==============================================================================
        # 1. GEMINI CLOUD MULTIMODAL AUDIO MODE (DEFAULT)
        # ==============================================================================
        if self.stt_engine == "gemini":
            client = gemini_client or self.gemini_client
            if client is None:
                try:
                    from robot.brain.gemini_client import GeminiClient
                    client = GeminiClient()
                except ImportError:
                    from brain.gemini_client import GeminiClient
                    client = GeminiClient()

            silence_dur = silence_timeout or self.silence_timeout
            speech_started = False
            speech_start_time = 0.0
            last_speech_time = 0.0
            listen_start = time.time()
            pre_buffer: collections.deque = collections.deque(maxlen=4)  # ~0.35s pre-speech buffer
            speech_frames: List[bytes] = []
            ambient_noise = 80.0

            while True:
                if timeout and not speech_started and (time.time() - listen_start > timeout):
                    return None

                try:
                    data = self.audio_queue.get(timeout=0.08)
                except queue.Empty:
                    if speech_started and (time.time() - last_speech_time >= silence_dur):
                        speech_dur = time.time() - speech_start_time
                        if speech_dur >= VOICE_MIN_SPEECH_DURATION and speech_frames:
                            if on_partial:
                                on_partial("Transcribing with Gemini...")
                            wav_data = self._pcm_to_wav(speech_frames)
                            text = client.transcribe_audio(wav_data)
                            if text and text.strip():
                                return text.strip()
                        speech_started = False
                        speech_frames = []
                    continue

                # Calculate RMS volume of incoming block
                audio_np = np.frombuffer(data, dtype=np.int16)
                rms = float(np.sqrt(np.mean(audio_np.astype(np.float32) ** 2)))

                if not speech_started:
                    # Update background ambient noise floor
                    ambient_noise = ambient_noise * 0.95 + min(rms, 300.0) * 0.05
                    dynamic_thresh = max(self.vad_threshold, ambient_noise * 2.2)

                    if rms > dynamic_thresh:
                        speech_started = True
                        speech_start_time = time.time()
                        last_speech_time = time.time()
                        speech_frames = list(pre_buffer)
                        speech_frames.append(data)
                        if on_partial:
                            on_partial("Listening...")
                    else:
                        pre_buffer.append(data)
                else:
                    speech_frames.append(data)
                    dynamic_thresh = max(self.vad_threshold, ambient_noise * 1.8)
                    if rms > dynamic_thresh:
                        last_speech_time = time.time()

                    # Prevent runaway buffering (>15 seconds)
                    if time.time() - speech_start_time > 15.0:
                        last_speech_time = 0.0

                    if time.time() - last_speech_time >= silence_dur:
                        speech_dur = time.time() - speech_start_time
                        if speech_dur >= VOICE_MIN_SPEECH_DURATION and speech_frames:
                            if on_partial:
                                on_partial("Transcribing with Gemini...")
                            wav_data = self._pcm_to_wav(speech_frames)
                            text = client.transcribe_audio(wav_data)
                            if text and text.strip():
                                return text.strip()
                        speech_started = False
                        speech_frames = []

        # ==============================================================================
        # 2. VOSK OFFLINE RECOGNIZER MODE (FALLBACK)
        # ==============================================================================
        silence_dur = silence_timeout or self.silence_timeout
        speech_started = False
        last_speech_time = 0.0
        last_partial_text = ""
        listen_start = time.time()
        pre_buffer = collections.deque(maxlen=6)
        speech_frames = []

        while True:
            if timeout and not speech_started and (time.time() - listen_start > timeout):
                return None

            try:
                data = self.audio_queue.get(timeout=0.08)
            except queue.Empty:
                if speech_started and (time.time() - last_speech_time >= silence_dur):
                    final_res = json.loads(self.recognizer.FinalResult())
                    raw_text = final_res.get("text", "").strip() or last_partial_text
                    self.recognizer.Reset()
                    if raw_text:
                        return raw_text
                continue

            if not speech_started:
                pre_buffer.append(data)
            else:
                speech_frames.append(data)

            if self.recognizer.AcceptWaveform(data):
                result = json.loads(self.recognizer.Result())
                raw_text = result.get("text", "").strip()
                self.recognizer.Reset()
                if raw_text:
                    return raw_text
            else:
                partial_obj = json.loads(self.recognizer.PartialResult())
                partial_text = partial_obj.get("partial", "").strip()
                if partial_text:
                    if not speech_started:
                        speech_started = True
                        speech_frames = list(pre_buffer)
                        speech_frames.append(data)
                    last_speech_time = time.time()
                    if on_partial and partial_text != last_partial_text:
                        on_partial(partial_text)
                    last_partial_text = partial_text
                elif speech_started:
                    if time.time() - last_speech_time >= silence_dur:
                        final_res = json.loads(self.recognizer.FinalResult())
                        raw_text = final_res.get("text", "").strip() or last_partial_text
                        self.recognizer.Reset()
                        if raw_text:
                            return raw_text
                        speech_started = False
                        speech_frames = []

    def start_ambient_listen(self) -> None:
        """Start ambient listening mode."""
        self.is_listening = True
        logger.debug("[Ear] Listening for ambient audio...")

    def stop_ambient_listen(self) -> None:
        """Stop ambient listening mode."""
        self.is_listening = False
        logger.debug("[Ear] Stopped ambient listening.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Standalone Robot Ear (Microphone / Vosk STT) Test")
    parser.add_argument("--model", type=str, default=None, help="Path to Vosk speech model directory")
    parser.add_argument("--silence", type=float, default=VOICE_SILENCE_TIMEOUT, help="Silence timeout in seconds")
    args = parser.parse_args()

    print("=" * 65)
    print("[*] ROBOT EAR: STANDALONE MICROPHONE & SPEECH TEST")
    print("=" * 65)

    ear = RobotEar(model_path=args.model, silence_timeout=args.silence)

    if not ear.is_available():
        if not AUDIO_DEPS_AVAILABLE:
            print("\n[!] Audio dependencies missing or failed to import:")
            print(f"    {AUDIO_DEPS_ERROR}")
            print("\nTo install audio dependencies on Raspberry Pi / Linux:")
            print("    sudo apt update && sudo apt install -y libportaudio2 python3-sounddevice")
            print("    pip3 install sounddevice vosk --break-system-packages\n")
        else:
            print(f"\n[!] Vosk model not found at '{ear.requested_model}'")
            print("    Ensure vosk-model-small-en-us-0.15 is unzipped in /home/pi or project root.")
            print(f"    Or pass --model <path_to_model>\n")
        sys.exit(1)

    print(f"  - Model Path    : {ear.resolved_model_path}")
    print(f"  - Sample Rate   : {ear.samplerate} Hz")
    print(f"  - Silence Wait  : {ear.silence_timeout}s")
    print("-" * 65)
    print("Instructions: Speak into your microphone. Press Ctrl+C to stop.")
    print("=" * 65 + "\n")

    def on_partial(text: str) -> None:
        print(f"\r[Hearing]: {text} ...", end="", flush=True)

    try:
        while True:
            result = ear.listen(on_partial=on_partial)
            if result:
                print(f"\r\n[Transcribed]: \"{result}\"\n")
    except KeyboardInterrupt:
        print("\n\n[*] Stopped listening. Ear test completed.")
    finally:
        ear.stop_listening()

