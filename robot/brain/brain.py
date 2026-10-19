"""
Robot Brain Core Controller.
Orchestrates cognition, persona system prompts, structured memory retention,
real-time web search with Ollama filtering, and audio I/O interfaces (mouth and ear).
"""

import logging
from typing import Any, Callable, Dict, List, Optional

try:
    from robot.brain.config import (
        AI_BACKEND,
        DEFAULT_MODEL,
        ENABLE_WEB_SEARCH,
        GEMINI_API_KEY,
        GEMINI_MODEL,
        MAX_RECENT_TURNS,
        PRIMARY_OLLAMA_URL,
        ROBOT_NAME,
        ROBOT_STT_ENGINE,
    )
    from robot.brain.ear import RobotEar
    from robot.brain.gemini_client import GeminiClient
    from robot.brain.memory import StructuredMemory
    from robot.brain.mouth import RobotMouth
    from robot.brain.ollama_client import OllamaClient
    from robot.brain.persona import RobotPersona
    from robot.brain.search import WebSearcher
except ImportError:
    from brain.config import (
        AI_BACKEND,
        DEFAULT_MODEL,
        ENABLE_WEB_SEARCH,
        GEMINI_API_KEY,
        GEMINI_MODEL,
        MAX_RECENT_TURNS,
        PRIMARY_OLLAMA_URL,
        ROBOT_NAME,
        ROBOT_STT_ENGINE,
    )
    from brain.ear import RobotEar
    from brain.gemini_client import GeminiClient
    from brain.memory import StructuredMemory
    from brain.mouth import RobotMouth
    from brain.ollama_client import OllamaClient
    from brain.persona import RobotPersona
    from brain.search import WebSearcher

logger = logging.getLogger("robot.brain")


class RobotBrain:
    """The central cognitive orchestrator for the humanoid robot."""

    def __init__(
        self,
        ai_backend: str = AI_BACKEND,
        gemini_api_key: Optional[str] = None,
        gemini_model: str = GEMINI_MODEL,
        ollama_url: str = PRIMARY_OLLAMA_URL,
        ollama_model: str = DEFAULT_MODEL,
        robot_name: str = ROBOT_NAME,
        enable_search: bool = ENABLE_WEB_SEARCH,
        audio_device: Optional[Any] = None,
        stt_engine: str = ROBOT_STT_ENGINE,
    ):
        self.ai_backend = (ai_backend or "gemini").lower()

        # 1. Initialize Persona & Identity
        self.persona = RobotPersona(name=robot_name)

        # 2. Initialize Structured Memory
        self.memory = StructuredMemory()

        # 3. Initialize AI Clients (Gemini & Ollama)
        self.gemini = GeminiClient(api_key=gemini_api_key or GEMINI_API_KEY, model=gemini_model)
        self.ollama = OllamaClient(primary_url=ollama_url, model=ollama_model)

        # 4. Initialize Web Search Engine
        self.enable_search = enable_search
        self.searcher = WebSearcher(ollama_filter_fn=None)

        # 5. Initialize Mouth (Speaker) and Ear (Mouthpiece) Interfaces
        self.mouth = RobotMouth()
        self.ear = RobotEar(device=audio_device, gemini_client=self.gemini, stt_engine=stt_engine)

        active_model = self.gemini.model if self.ai_backend == "gemini" else self.ollama.model
        logger.info(
            f"RobotBrain initialized for '{self.persona.name}'. "
            f"Active Backend: {self.ai_backend.upper()} (Model: {active_model})"
        )

    def _filter_web_findings_with_ollama(self, query: str, raw_snippets: str) -> str:
        """
        Use Ollama to filter raw search snippets, extracting only precise facts
        directly addressing the user's question.
        """
        filter_prompt = (
            "You are a factual data filter for a humanoid robot. "
            f"Question: {query}\n\n"
            f"Raw Search Results:\n{raw_snippets}\n\n"
            "Task: Extract the verified facts that directly answer the question. "
            "Filter out advertising, irrelevant articles, and redundant text. "
            "Provide 1 to 3 concise, factual bullet points."
        )

        try:
            filtered = self.ollama.generate(
                prompt=filter_prompt,
                system="You are a precise data extractor. Be strictly factual and concise.",
            )
            return filtered.strip()
        except Exception as e:
            logger.warning(f"Ollama web filtering encountered an issue: {e}")
            return raw_snippets

    def think_and_respond(
        self,
        user_input: str,
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        force_web_search: bool = False,
    ) -> str:
        """
        Core reasoning pipeline:
        1. Parse and extract facts from user input into persistent memory.
        2. Check if real-time web search is required (or forced).
        3. Retrieve and filter live data if needed.
        4. Assemble system prompt (persona + memory facts + discussion summary + web data).
        5. Query Ollama with multi-turn conversational history.
        6. Persist dialogue turn and return response.
        """
        clean_input = user_input.strip()
        if not clean_input:
            return ""

        # Step 1: Record user input into memory (automatically extracts user facts)
        self.memory.add_turn("user", clean_input)

        # Step 2: Check for live or advanced data needs
        web_context: Optional[str] = None
        needs_search = force_web_search or (
            self.enable_search and self.searcher.is_live_or_advanced_query(clean_input)
        )

        if needs_search:
            logger.info(f"Query '{clean_input}' identified as requiring real-time web knowledge.")
            web_context = self.searcher.get_live_data_and_filter(clean_input)

        # Step 3: Build tailored system prompt from persona and memory
        system_prompt = self.persona.build_system_prompt(
            user_facts=self.memory.get_profile_facts(),
            discussion_summary=self.memory.discussion_summary,
            web_context=web_context,
        )

        # Step 4: Assemble conversation messages for Ollama /api/chat
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Include recent conversation turns for immediate multi-turn context
        recent_history = self.memory.get_recent_messages(limit=MAX_RECENT_TURNS)
        # Avoid duplicating the turn we just added
        if recent_history and recent_history[-1]["content"] == clean_input:
            messages.extend(recent_history)
        else:
            messages.extend(recent_history)
            messages.append({"role": "user", "content": clean_input})

        # Step 5: Query active AI Backend (Gemini or Ollama)
        reply = ""
        if self.ai_backend == "gemini":
            if not self.gemini.api_key:
                reply = (
                    "My Gemini AI system is activated, but my GEMINI_API_KEY is not set. "
                    "Please provide your Gemini API key or set the GEMINI_API_KEY environment variable."
                )
                if on_chunk and stream:
                    on_chunk(reply)
            else:
                try:
                    reply = self.gemini.chat(
                        messages=messages,
                        stream=stream,
                        on_chunk=on_chunk,
                    )
                except Exception as e:
                    logger.error(f"Error querying Gemini: {e}")
                    # Try falling back to Ollama if available
                    if self.ollama.is_online():
                        logger.info("Falling back to local Ollama...")
                        try:
                            reply = self.ollama.chat(
                                messages=messages,
                                stream=stream,
                                on_chunk=on_chunk,
                            )
                        except Exception as oe:
                            reply = f"I had trouble connecting to Gemini ({e}) and Ollama ({oe})."
                            if on_chunk and stream:
                                on_chunk(reply)
                    else:
                        reply = f"I encountered an issue reaching Gemini AI: {e}"
                        if on_chunk and stream:
                            on_chunk(reply)
        else:
            try:
                reply = self.ollama.chat(
                    messages=messages,
                    stream=stream,
                    on_chunk=on_chunk,
                )
            except Exception as e:
                logger.error(f"Error querying Ollama: {e}")
                target_url = self.ollama.active_url or self.ollama.primary_url
                reply = (
                    f"I'm having trouble connecting to my central neural computer right now. "
                    f"Please ensure Ollama is running on your PC (expected endpoint: {target_url}) "
                    f"and that port 11434 is accessible over your local network."
                )
                if on_chunk and stream:
                    on_chunk(reply)

        # Step 6: Record assistant response in structured memory
        self.memory.add_turn("assistant", reply)

        # Step 7: Send speech to mouth interface (previews speech / ready for hardware speaker)
        self.mouth.speak(reply)

        return reply

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic health and status metrics for the brain."""
        is_gemini = self.ai_backend == "gemini"
        active_model = self.gemini.model if is_gemini else self.ollama.model
        active_online = self.gemini.is_online() if is_gemini else self.ollama.is_online()
        return {
            "name": self.persona.name,
            "ai_backend": self.ai_backend,
            "model": active_model,
            "active_model": active_model,
            "active_online": active_online,
            "gemini_model": self.gemini.model,
            "gemini_online": self.gemini.is_online(),
            "ollama_host": self.ollama.active_url,
            "ollama_online": self.ollama.is_online(),
            "ollama_model": self.ollama.model,
            "memory_turns": len(self.memory.history),
            "profile_facts_count": len(self.memory.user_profile),
            "web_search_enabled": self.enable_search,
            "mouth_ready": self.mouth.is_available(),
            "bluetooth_connected": self.mouth.is_bluetooth_connected,
        }

    def clear_conversation(self, wipe_profile: bool = False) -> None:
        """Reset conversation context."""
        self.memory.clear(keep_profile=not wipe_profile)
        logger.info("Brain conversation context reset.")
