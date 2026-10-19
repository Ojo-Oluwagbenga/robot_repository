"""
Unit and integration tests for the Robot Brain module.
"""

import os
import tempfile
from pathlib import Path
import pytest

from robot.brain.config import (
    DEFAULT_MODEL,
    PRIMARY_OLLAMA_URL,
    ROBOT_NAME,
)
from robot.brain.ear import RobotEar
from robot.brain.memory import StructuredMemory
from robot.brain.mouth import RobotMouth
from robot.brain.ollama_client import OllamaClient
from robot.brain.persona import RobotPersona
from robot.brain.search import WebSearcher
from robot.brain.brain import RobotBrain


def test_structured_memory_persistence():
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_path = Path(tmp_dir) / "test_memory.json"
        mem = StructuredMemory(storage_path=storage_path)

        # 1. Test fact extraction
        mem.add_turn("user", "Hello! Remember that I am testing the leg motor calibration.")
        assert "testing the leg motor calibration" in str(mem.user_profile)

        mem.add_turn("assistant", "Noted! I will remember that you are testing leg motor calibration.")
        assert len(mem.history) == 2

        # 2. Test disk persistence and reload
        mem2 = StructuredMemory(storage_path=storage_path)
        assert len(mem2.history) == 2
        assert "testing the leg motor calibration" in str(mem2.user_profile)

        # 3. Test recent messages format
        recent = mem2.get_recent_messages()
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[1]["role"] == "assistant"


def test_web_search_query_detection():
    searcher = WebSearcher()

    # Live data queries
    assert searcher.is_live_or_advanced_query("What is the latest news today?") is True
    assert searcher.is_live_or_advanced_query("Current temperature in Tokyo") is True
    assert searcher.is_live_or_advanced_query("What is the price of Bitcoin right now?") is True
    assert searcher.is_live_or_advanced_query("Who is the current president of France?") is True

    # Standard conversational queries
    assert searcher.is_live_or_advanced_query("Hello there, how are you feeling?") is False
    assert searcher.is_live_or_advanced_query("What is two plus two?") is False


def test_persona_system_prompt_builder():
    persona = RobotPersona(name="Nova", creator="Yohanna")
    user_facts = {"favorite_tool": "oscilloscope", "name": "Yohanna"}
    prompt = persona.build_system_prompt(
        user_facts=user_facts,
        discussion_summary="Discussed motor wiring yesterday.",
        web_context="Live Fact: Mars rover Curiosity is active.",
    )

    assert "Nova" in prompt
    assert "Yohanna" in prompt
    assert "oscilloscope" in prompt
    assert "Live Fact: Mars rover Curiosity is active." in prompt
    assert "Discussed motor wiring yesterday." in prompt
    assert "SYNTAX & PHONETIC VALIDATION" in prompt
    assert "Active Listening Echo" in prompt or "Always confirm" in prompt


def test_ollama_client_connectivity():
    client = OllamaClient()
    assert client.active_url is not None


def test_gemini_client_initialization():
    from robot.brain.gemini_client import GeminiClient
    client = GeminiClient(api_key="test_dummy_key", model="gemini-2.5-flash")
    assert client.api_key == "test_dummy_key"
    assert client.model == "gemini-2.5-flash"
    # Without valid key, is_online will be False
    assert client.is_online() is False


def test_robot_mouth_and_ear_interfaces():
    mouth = RobotMouth()
    mouth.speak("Testing speech synthesis.")
    assert mouth.is_active is False

    ear = RobotEar()
    assert ear.samplerate == 16000
    assert ear.listen() is None
    ear.start_ambient_listen()
    assert ear.is_listening is True
    ear.stop_ambient_listen()
    assert ear.is_listening is False

