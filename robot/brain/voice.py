"""
Voice Interaction Interface for Robot Brain.
Provides hands-free conversational operation using lightweight Vosk Speech-to-Text (STT)
and Ollama AI on PC, listening for simple physical instructions (PICK, NOD, WAVE)
from conversation, with mouth speaking disabled.

Usage:
    python robot/brain/voice.py
    # or
    python -m robot.brain.voice
"""

import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure project root and robot directory are in sys.path
CURRENT_DIR = Path(__file__).resolve().parent           # .../robot/brain
ROBOT_DIR = CURRENT_DIR.parent                         # .../robot
PROJECT_ROOT = ROBOT_DIR.parent                        # .../Robot Code

for path in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from robot.brain.brain import RobotBrain
    from robot.brain.config import (
        DEFAULT_MODEL,
        PRIMARY_OLLAMA_URL,
        ROBOT_AUTO_CONNECT_BT,
        ROBOT_NAME,
        ROBOT_STT_ENGINE,
        ROBOT_TTS_ENABLED,
        VOICE_SILENCE_TIMEOUT,
        VOSK_MODEL_PATH,
    )
    from robot.brain.ear import AUDIO_DEPS_AVAILABLE, AUDIO_DEPS_ERROR
    from robot.control.gestures import GestureController
except ImportError:
    from brain.brain import RobotBrain
    from brain.config import (
        DEFAULT_MODEL,
        PRIMARY_OLLAMA_URL,
        ROBOT_AUTO_CONNECT_BT,
        ROBOT_NAME,
        ROBOT_STT_ENGINE,
        ROBOT_TTS_ENABLED,
        VOICE_SILENCE_TIMEOUT,
        VOSK_MODEL_PATH,
    )
    from brain.ear import AUDIO_DEPS_AVAILABLE, AUDIO_DEPS_ERROR
    from control.gestures import GestureController


try:
    from robot.control.similarity import find_most_similar_instruction
except ImportError:
    from control.similarity import find_most_similar_instruction


def extract_gesture_intent(text: str, ollama_client=None) -> Optional[str]:
    """Finds the most similar known robot instruction for any incoming speech."""
    best_inst, score, all_scores, explanation = find_most_similar_instruction(
        text=text,
        ollama_client=ollama_client,
        threshold=0.40
    )
    return best_inst


def print_voice_banner(brain: RobotBrain, silence_timeout: float) -> None:
    """Display startup voice interface status."""
    status = brain.get_status()
    backend_name = status.get("ai_backend", "ollama").upper()
    active_model = status.get("active_model", "")
    online_str = "ONLINE (Connected to PC)" if status.get("active_online") else "OFFLINE"
    ear_ready = brain.ear.is_available()

    print("\n" + "=" * 65)
    print(f"[*] ROBOT VOICE INTERFACE: {status['name'].upper()}")
    print("=" * 65)
    if ear_ready:
        dev_desc = brain.ear.device_name
        if brain.ear.device is not None:
            dev_desc += f" (Device #{brain.ear.device})"
        input_desc = f"{dev_desc} [16kHz Mono]"
    else:
        input_desc = "Awaiting Setup"

    stt_desc = f"Lightweight Vosk ({brain.ear.resolved_model_path or brain.ear.requested_model})"
    output_desc = "DISABLED (Silent Action Execution)" if not brain.mouth.enabled else "Piper Neural TTS"

    print(f"  - Speech Input  : {input_desc}")
    print(f"  - STT Engine    : {stt_desc}")
    print(f"  - Speech Output : {output_desc}")
    print(f"  - Silence Wait  : {silence_timeout}s")
    print(f"  - AI Backend    : {backend_name} ({active_model}) [{online_str}]")
    print(f"  - Gestures      : [PICK] | [NOD] | [WAVE]")
    print("-" * 65)
    print("Instructions:")
    print("  * Speak naturally into your microphone.")
    print("  * Mention 'pick', 'nod', or 'wave' during conversation.")
    print(f"  * {brain.persona.name} will silently execute physical motor actions.")
    print("  * Press Ctrl+C at any time to exit.")
    print("=" * 65 + "\n")


def run_voice_loop(
    brain: Optional[RobotBrain] = None,
    silence_timeout: float = VOICE_SILENCE_TIMEOUT,
    model_path: Optional[str] = None,
    audio_device: Optional[Any] = None,
    stt_engine: str = ROBOT_STT_ENGINE,
    connect_bt: bool = ROBOT_AUTO_CONNECT_BT,
) -> None:
    """
    Run continuous hands-free voice loop.
    Listens for user speech via lightweight Vosk -> extracts PICK, NOD, WAVE -> triggers physical gesture.
    """
    if brain is None:
        brain = RobotBrain(audio_device=audio_device, stt_engine=stt_engine)
    elif audio_device is not None:
        brain.ear.requested_device = audio_device

    # Ensure mouth is disabled for silent action execution
    brain.mouth.enabled = False

    # Initialize ear engine for voice mode
    if model_path:
        brain.ear.requested_model = model_path
    brain.ear._initialize_engine()

    # Initialize gesture controller
    gestures = GestureController()

    print_voice_banner(brain, silence_timeout)

    if not brain.ear.is_available():
        if not AUDIO_DEPS_AVAILABLE:
            print("[!] Python audio packages are missing or incomplete.")
            print(f"    Details: {AUDIO_DEPS_ERROR}")
        else:
            print(f"[!] Vosk speech model not detected at: '{brain.ear.requested_model}'")
        return

    # Start audio stream
    if not brain.ear.start_listening():
        print("[!] Could not open audio input stream. Check your microphone connection.")
        return

    print("Listening to conversation... Speak now!\n")

    try:
        while True:
            # Live visual partial transcription callback
            def on_partial(partial_text: str):
                print(f"\r[Hearing]: {partial_text} ...", end="", flush=True)

            user_speech = brain.ear.listen(
                silence_timeout=silence_timeout,
                on_partial=on_partial,
            )

            if not user_speech or not user_speech.strip():
                continue

            clean_input = user_speech.strip()
            clean_word = clean_input.lower().strip(".,!?;: ")

            # Filter out throat clearing, breaths, and hesitation fillers
            IGNORED_FILLERS = {
                "hm", "hmm", "hmmm", "um", "umm", "uh", "uhh", "m", "mm", "mmm",
                "ah", "ahh", "eh", "huh", "shh", "oh", "er", "ha", "no"
            }
            if clean_word in IGNORED_FILLERS or len(clean_word) <= 1:
                continue

            # Clear hearing preview line and print final user speech
            print(f"\r\nYou (Voice) > \"{clean_input}\"")

            # Check exit phrases
            if clean_input.lower() in ["exit", "quit", "goodbye nova", "shut down"]:
                print(f"\n[{brain.persona.name}] Goodbye! Exiting voice listener.")
                break

            # 1. Check for physical gesture action (pick, nod, wave)
            action = extract_gesture_intent(clean_input, brain.ollama)
            if action:
                print(f"[Gesture Detected]: >>> {action} <<< from conversation.")
                gestures.execute(action, details=clean_input)
            else:
                print(f"[{brain.persona.name}] Heard dialogue (No physical gesture requested).")

            print("\nListening... Speak now:")

    except (KeyboardInterrupt, EOFError):
        print(f"\n\n[{brain.persona.name}] Voice session closed.")
    finally:
        brain.ear.stop_listening()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Nova Robot Lightweight Voice & Gesture Listener")
    parser.add_argument("--engine", type=str, default="vosk", choices=["gemini", "vosk"], help="STT engine to use")
    parser.add_argument("--model", type=str, default=None, help="Path to Vosk speech model directory")
    parser.add_argument("--silence", type=float, default=VOICE_SILENCE_TIMEOUT, help="Silence timeout in seconds")
    parser.add_argument("--mic", "--device", dest="mic", type=str, default=None, help="Microphone index or keyword")
    args = parser.parse_args()

    brain_instance = RobotBrain(audio_device=args.mic, stt_engine=args.engine)
    brain_instance.mouth.enabled = False

    run_voice_loop(
        brain=brain_instance,
        silence_timeout=args.silence,
        model_path=args.model,
        audio_device=args.mic,
        stt_engine=args.engine,
        connect_bt=False,
    )
