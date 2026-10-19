#!/usr/bin/env python3
"""
Lightweight Conversation Listener for Physical Gestures (Pick, Nod, Wave).
- Uses lightweight offline Vosk STT for real-time speech recognition
- Uses Ollama AI running on your PC for intelligent instruction extraction
- Mouth speaking is completely disabled (silent physical action execution)
- Listens to continuous conversation and triggers PICK, NOD, or WAVE
"""

import sys
import os
import re
import time
from pathlib import Path

# Ensure project root is in sys.path
ROBOT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from robot.brain.config import (
    PRIMARY_OLLAMA_URL,
    DEFAULT_MODEL,
    VOICE_SILENCE_TIMEOUT,
    VOSK_MODEL_PATH
)
from robot.brain.ear import RobotEar, AUDIO_DEPS_AVAILABLE
from robot.brain.ollama_client import OllamaClient
from robot.control.gestures import GestureController


from robot.control.similarity import find_most_similar_instruction


def extract_instruction_from_conversation(text: str, ollama_client: OllamaClient):
    """
    Finds the most similar known robot instruction for any incoming user speech.
    Uses multi-metric string, keyword, and token similarity, backed by PC Ollama AI.
    """
    best_inst, score, all_scores, explanation = find_most_similar_instruction(
        text=text,
        ollama_client=ollama_client,
        threshold=0.40
    )
    if best_inst:
        scores_summary = " ".join([f"{k}:{v:.2f}" for k, v in all_scores.items()])
        return best_inst, f"{explanation} [{scores_summary}]"

    return "NONE", explanation


def run_gesture_listener():
    print("=" * 68)
    print("      NOVA LIGHTWEIGHT CONVERSATION LISTENER (GESTURES)")
    print("=" * 68)
    print("  * STT Engine    : Lightweight Vosk (Offline, Low CPU/RAM)")
    print("  * AI Brain      : Ollama on PC (http://10.35.208.172:11434)")
    print("  * Mouth / Audio : DISABLED (Silent physical movement execution)")
    print("  * Actions       : [PICK] | [NOD] | [WAVE]")
    print("=" * 68)

    # 1. Initialize PC Ollama client
    ollama = OllamaClient()
    ollama_status = "CONNECTED" if ollama.is_online() else "OFFLINE"
    print(f"[*] PC Ollama AI Status: {ollama_status} ({ollama.active_url or ollama.primary_url})")

    # 2. Initialize Hardware / Simulation Gesture Controller
    gestures = GestureController()

    # 3. Initialize Vosk Ear
    print(f"[*] Initializing lightweight Vosk speech engine...")
    ear = RobotEar(
        stt_engine="vosk",
        model_path=VOSK_MODEL_PATH,
        silence_timeout=VOICE_SILENCE_TIMEOUT,
    )

    if not ear._initialize_engine():
        print("[!] Could not start Vosk ear engine.")
        if not AUDIO_DEPS_AVAILABLE:
            print("[!] Audio dependencies missing. Install: pip install sounddevice vosk")
        sys.exit(1)

    print(f"[+] Vosk Model Loaded: {ear.resolved_model_path}")
    print(f"[+] Microphone Input : {ear.device_name}")

    if not ear.start_listening():
        print("[!] Could not start microphone stream.")
        sys.exit(1)

    print("\n[READY] Listening to conversation... Speak naturally into microphone.")
    print("        Mention 'pick', 'nod', or 'wave' to see action executed.\n")

    try:
        while True:
            # Partial hearing feedback callback
            def on_partial(txt: str):
                print(f"\r[Hearing]: {txt} ...", end="", flush=True)

            user_speech = ear.listen(
                silence_timeout=VOICE_SILENCE_TIMEOUT,
                on_partial=on_partial,
            )

            if not user_speech or not user_speech.strip():
                continue

            clean_input = user_speech.strip()
            # Clear hearing preview line
            print(f"\r[Heard]: \"{clean_input}\"                               ")

            # Check exit
            if clean_input.lower() in ["exit", "quit", "stop listening"]:
                print("\n[*] Stopping listener.")
                break

            # Extract instruction
            action, reason = extract_instruction_from_conversation(clean_input, ollama)

            if action in ["PICK", "NOD", "WAVE"]:
                print(f"[Action Detected]: >>> {action} <<< ({reason})")
                # Execute physical hardware action
                gestures.execute(action, details=clean_input)
            else:
                print(f"[Conversation]: No motion instruction (monitoring...)")

            print("\nListening... Speak now:")

    except (KeyboardInterrupt, EOFError):
        print("\n\n[*] Listener terminated by user.")
    finally:
        ear.stop_listening()


if __name__ == "__main__":
    run_gesture_listener()
