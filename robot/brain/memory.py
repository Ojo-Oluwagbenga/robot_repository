"""
Structured Memory System for Robot Brain.
Maintains persistent user profile facts, episodic dialogue history,
and discussion summaries across sessions.
"""

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from robot.brain.config import (
        MAX_HISTORY_ENTRIES,
        MAX_PROFILE_FACTS,
        MAX_RECENT_TURNS,
        MEMORY_FILE_PATH,
        ROBOT_CREATOR,
    )
except ImportError:
    from brain.config import (
        MAX_HISTORY_ENTRIES,
        MAX_PROFILE_FACTS,
        MAX_RECENT_TURNS,
        MEMORY_FILE_PATH,
        ROBOT_CREATOR,
    )

logger = logging.getLogger("robot.brain.memory")


class StructuredMemory:
    """Manages persistent structured memory, profile facts, and dialogue context."""

    def __init__(self, storage_path: Path = MEMORY_FILE_PATH):
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory structures
        self.user_profile: Dict[str, str] = {
            "name": ROBOT_CREATOR,
            "role": "Creator & Roboticist",
            "active_project": "Humanoid robot with Raspberry Pi 5 and 5 ESP32 controllers",
        }
        self.history: List[Dict[str, Any]] = []
        self.discussion_summary: str = ""
        self.topics_discussed: List[str] = []

        # Load existing data if file exists
        self.load()

    def load(self) -> bool:
        """Load persistent memory from disk."""
        if not self.storage_path.exists():
            logger.info(f"Memory file not found. Initialized new memory at {self.storage_path}")
            self.save()
            return False

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.user_profile.update(data.get("user_profile", {}))
            self.history = data.get("history", [])
            self.discussion_summary = data.get("discussion_summary", "")
            self.topics_discussed = data.get("topics_discussed", [])
            logger.info(f"Loaded memory: {len(self.history)} history turns, {len(self.user_profile)} profile facts.")
            return True
        except Exception as e:
            logger.error(f"Failed to load memory file from {self.storage_path}: {e}")
            return False

    def save(self) -> bool:
        """Atomically persist memory to disk."""
        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "user_profile": self.user_profile,
            "discussion_summary": self.discussion_summary,
            "topics_discussed": self.topics_discussed[-20:],
            "history": self.history[-MAX_HISTORY_ENTRIES:],
        }

        try:
            # Atomic write via temporary file
            temp_dir = self.storage_path.parent
            with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
                json.dump(data, tf, indent=2, ensure_ascii=False)
                temp_name = tf.name

            # Replace original file atomically
            os.replace(temp_name, self.storage_path)
            return True
        except Exception as e:
            logger.error(f"Failed to save memory to {self.storage_path}: {e}")
            return False

    def add_turn(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Record a single dialogue turn in episodic history."""
        entry = {
            "role": role,
            "content": content.strip(),
            "timestamp": datetime.now().isoformat(),
        }
        if metadata:
            entry["metadata"] = metadata

        self.history.append(entry)

        # Trigger automatic extraction when user speaks
        if role == "user":
            self.extract_facts_from_user_text(content)

        # Rolling trim & summary maintenance if history grows large
        if len(self.history) > MAX_HISTORY_ENTRIES:
            self._compress_older_history()

        self.save()

    def get_recent_messages(self, limit: int = MAX_RECENT_TURNS) -> List[Dict[str, str]]:
        """
        Get recent dialogue turns formatted as Ollama chat messages:
        [{'role': 'user'|'assistant', 'content': '...'}]
        """
        recent = self.history[-limit:]
        return [{"role": item["role"], "content": item["content"]} for item in recent]

    def get_profile_facts(self) -> Dict[str, str]:
        """Return the dictionary of remembered facts about the user."""
        return dict(list(self.user_profile.items())[:MAX_PROFILE_FACTS])

    def set_fact(self, key: str, value: str) -> None:
        """Explicitly set or update a remembered fact."""
        self.user_profile[key.strip().lower()] = value.strip()
        self.save()

    def remove_fact(self, key: str) -> bool:
        """Remove a fact from profile memory."""
        k = key.strip().lower()
        if k in self.user_profile:
            del self.user_profile[k]
            self.save()
            return True
        return False

    def extract_facts_from_user_text(self, text: str) -> List[Tuple[str, str]]:
        """
        Extract facts from user utterances using conversational regex heuristics.
        Catches explicit memory commands and personal disclosures.
        """
        extracted = []
        lower = text.strip()

        # 1. Explicit remember command: "remember that I like coffee" / "remember: my name is X"
        rem_match = re.search(r"\bremember\s+(?:that\s+)?(.+)", lower, re.IGNORECASE)
        if rem_match:
            fact_body = rem_match.group(1).rstrip(".!?")
            key = f"remembered_fact_{len(self.user_profile) + 1}"
            self.user_profile[key] = fact_body
            extracted.append((key, fact_body))

        # 2. Name disclosure: "my name is Yohanna" / "call me Alex"
        name_match = re.search(r"\bmy name is\s+([A-Za-z]+)", text, re.IGNORECASE)
        if name_match:
            name = name_match.group(1).capitalize()
            self.user_profile["name"] = name
            extracted.append(("name", name))

        callme_match = re.search(r"\bcall me\s+([A-Za-z]+)", text, re.IGNORECASE)
        if callme_match:
            name = callme_match.group(1).capitalize()
            self.user_profile["name"] = name
            extracted.append(("name", name))

        # 3. Preferences: "my favorite X is Y" / "i like X" / "i love X"
        fav_match = re.search(r"\bmy favorite\s+([a-zA-Z\s]+?)\s+is\s+(.+)", text, re.IGNORECASE)
        if fav_match:
            item = fav_match.group(1).strip().lower()
            val = fav_match.group(2).strip().rstrip(".!?")
            key = f"favorite_{item}"
            self.user_profile[key] = val
            extracted.append((key, val))

        # 4. Project updates: "i am working on X" / "i'm building X"
        proj_match = re.search(r"\bi(?:'m| am)\s+(?:working on|building)\s+(.+)", text, re.IGNORECASE)
        if proj_match:
            proj = proj_match.group(1).strip().rstrip(".!?")
            self.user_profile["current_focus"] = proj
            extracted.append(("current_focus", proj))

        if extracted:
            self.save()
            logger.info(f"Learned facts from user input: {extracted}")

        return extracted

    def search_history(self, keyword: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search past dialogue turns matching a keyword."""
        kw = keyword.lower()
        matches = [h for h in self.history if kw in h["content"].lower()]
        return matches[-limit:]

    def _compress_older_history(self) -> None:
        """
        Compress older turns into discussion summary points
        to prevent memory explosion while preserving context.
        """
        overflow_count = len(self.history) - MAX_HISTORY_ENTRIES
        if overflow_count <= 0:
            return

        to_compress = self.history[:overflow_count]
        self.history = self.history[overflow_count:]

        # Create quick summary bullets
        summary_snippets = []
        for turn in to_compress:
            role = turn.get("role", "unknown")
            snippet = turn.get("content", "")[:70]
            summary_snippets.append(f"{role}: {snippet}...")

        addition = "\n".join(summary_snippets)
        if self.discussion_summary:
            self.discussion_summary += "\n" + addition
        else:
            self.discussion_summary = addition

        # Keep summary under reasonable size
        if len(self.discussion_summary) > 2000:
            self.discussion_summary = self.discussion_summary[-2000:]

    def clear(self, keep_profile: bool = True) -> None:
        """Clear memory. If keep_profile is True, retains user profile facts."""
        self.history.clear()
        self.discussion_summary = ""
        self.topics_discussed.clear()
        if not keep_profile:
            self.user_profile = {"name": ROBOT_CREATOR}
        self.save()
        logger.info("Memory cleared.")
