"""
Interactive Terminal Interface for Robot Brain.
Allows direct conversational testing with text input, streaming responses,
live web search inspection, and structured memory management.

Usage:
    python -m robot.brain
    # or
    python robot/brain/interactive.py
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure both project root and robot directory are in sys.path for Pi & PC environments
CURRENT_DIR = Path(__file__).resolve().parent           # .../robot/brain
ROBOT_DIR = CURRENT_DIR.parent                         # .../robot
PROJECT_ROOT = ROBOT_DIR.parent                        # .../Robot Code or .../Robot

for path in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from robot.brain.brain import RobotBrain
    from robot.brain.config import (
        DEFAULT_MODEL,
        PRIMARY_OLLAMA_URL,
        ROBOT_NAME,
    )
except ImportError:
    from brain.brain import RobotBrain
    from brain.config import (
        DEFAULT_MODEL,
        PRIMARY_OLLAMA_URL,
        ROBOT_NAME,
    )


def print_banner(brain: RobotBrain) -> None:
    """Display startup diagnostics and commands banner."""
    status = brain.get_status()
    backend = status.get("ai_backend", "gemini").upper()
    model = status.get("model") or status.get("active_model", "")
    online_str = "ONLINE (Connected)" if status.get("active_online") else "OFFLINE / PENDING KEY"

    print("\n" + "=" * 65)
    print(f"[*] ROBOT BRAIN INTERFACE: {status['name'].upper()}")
    print("=" * 65)
    print(f"  - AI Backend      : {backend}")
    print(f"  - Active Model    : {model}")
    print(f"  - Status          : {online_str}")
    if status.get("ollama_host") and backend == "OLLAMA":
        print(f"  - Ollama Endpoint : {status['ollama_host']}")
    print(f"  - Profile Facts   : {status['profile_facts_count']} remembered")
    print(f"  - History Turns   : {status['memory_turns']} turns recorded")
    print(f"  - Real-Time Web   : {'Enabled' if status['web_search_enabled'] else 'Disabled'}")
    print("-" * 65)
    print("Commands:")
    print("  /memory         - View all structured facts and memory summary")
    print("  /search <query> - Test real-time web search and Ollama filtering")
    print("  /clear          - Clear current conversation turns")
    print("  /reset          - Wipe all persistent memory completely")
    print("  /voice          - Switch to hands-free microphone voice input")
    print("  /help           - Show this help menu")
    print("  exit / quit     - End session")
    print("=" * 65)
    print("\nSpeak or type to your robot below:")


def handle_command(cmd: str, brain: RobotBrain) -> bool:
    """
    Handle special slash commands.
    Returns True if a command was handled, False otherwise.
    """
    parts = cmd.strip().split(maxsplit=1)
    keyword = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if keyword in ["exit", "quit", "/exit", "/quit"]:
        print(f"\n[{brain.persona.name}] Goodbye Yohanna! See you soon.")
        sys.exit(0)

    if keyword in ["/help", "help"]:
        print("\nAvailable Commands:")
        print("  /memory         - Display remembered user facts & conversation summary")
        print("  /search <query> - Manually test web browsing & fact filtering")
        print("  /clear          - Clear conversation dialogue turns")
        print("  /reset          - Wipe entire memory including user profile")
        print("  /voice          - Switch to hands-free microphone voice mode")
        print("  exit / quit     - Exit the chat\n")
        return True

    if keyword in ["/voice", "voice"]:
        try:
            from robot.brain.voice import run_voice_loop
        except ImportError:
            from brain.voice import run_voice_loop
        run_voice_loop(brain)
        return True

    if keyword == "/status":
        st = brain.get_status()
        print("\n--- BRAIN STATUS ---")
        for k, v in st.items():
            print(f"  {k}: {v}")
        print("--------------------\n")
        return True

    if keyword == "/memory":
        print("\n--- STRUCTURED MEMORY ---")
        print("Profile Facts Remembered:")
        facts = brain.memory.get_profile_facts()
        if facts:
            for k, v in facts.items():
                print(f"  - {k}: {v}")
        else:
            print("  (No facts stored yet)")

        if brain.memory.discussion_summary:
            print("\nDiscussion Summary:")
            print(f"  {brain.memory.discussion_summary}")

        print(f"\nTotal Recorded Dialogue Turns: {len(brain.memory.history)}")
        print("-------------------------\n")
        return True

    if keyword == "/clear":
        brain.clear_conversation(wipe_profile=False)
        print("\n[Brain] Conversation dialogue history cleared. Profile facts retained.\n")
        return True

    if keyword == "/reset":
        confirm = input("Are you sure you want to wipe ALL persistent memory? (y/N): ").strip().lower()
        if confirm == "y":
            brain.clear_conversation(wipe_profile=True)
            print("\n[Brain] All persistent memory has been wiped clean.\n")
        else:
            print("\n[Brain] Reset cancelled.\n")
        return True

    if keyword == "/search":
        if not arg:
            print("\nUsage: /search <topic or question>\n")
            return True
        print(f"\n[Web Search] Browsing internet for: '{arg}'...")
        filtered_result = brain.searcher.get_live_data_and_filter(arg)
        if filtered_result:
            print("\n[Live Web Finding (Filtered by Ollama)]:")
            print(filtered_result)
        else:
            print("[Web Search] No relevant results found.")
        print()
        return True

    return False


def run_interactive_loop(brain: Optional[RobotBrain] = None) -> None:
    """Run the main conversational loop in the terminal."""
    if brain is None:
        brain = RobotBrain()

    print_banner(brain)

    while True:
        try:
            user_input = input(f"\nYou > ").strip()
            if not user_input:
                continue

            # Check if input is a slash command
            if user_input.startswith("/") or user_input.lower() in ["exit", "quit", "help"]:
                if handle_command(user_input, brain):
                    continue

            print(f"\n{brain.persona.name} > ", end="", flush=True)

            # Stream response in real-time
            def on_chunk(token: str) -> None:
                print(token, end="", flush=True)

            brain.think_and_respond(
                user_input=user_input,
                stream=True,
                on_chunk=on_chunk,
            )
            print()  # Newline after streaming completes

        except (KeyboardInterrupt, EOFError):
            print(f"\n\n[{brain.persona.name}] Shutting down brain session. Take care!")
            break
        except Exception as e:
            print(f"\n[Error] An unexpected error occurred: {e}\n")


if __name__ == "__main__":
    if "--voice" in sys.argv:
        try:
            from robot.brain.voice import run_voice_loop
        except ImportError:
            from brain.voice import run_voice_loop
        run_voice_loop()
    else:
        run_interactive_loop()
