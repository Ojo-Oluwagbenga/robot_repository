"""
Storage manager for local instruction queue on Raspberry Pi.
Handles thread-safe reading, stashing, and atomic removal of instructions.
"""

import os
import json
import time
import tempfile
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from .config import INSTRUCTIONS_FILE_PATH, PROCESSED_HISTORY_PATH

_STORAGE_LOCK = threading.Lock()


class InstructionStorage:
    """
    Manages reading, stashing, and removing instructions from the local instruction file.
    All disk operations are protected by an in-process lock and use atomic file replacing.
    """

    def __init__(
        self,
        file_path: Path = INSTRUCTIONS_FILE_PATH,
        history_path: Path = PROCESSED_HISTORY_PATH,
    ):
        self.file_path = Path(file_path)
        self.history_path = Path(history_path)
        self.stashed_ids: Set[str] = set()

        # Ensure directory exists
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file_exists()
        self._load_stashed_history()

    def _ensure_file_exists(self) -> None:
        """Create empty instructions JSON array if file does not exist."""
        if not self.file_path.exists() or self.file_path.stat().st_size == 0:
            self._write_raw([])

    def _load_stashed_history(self) -> None:
        """Load history of stashed IDs to prevent duplicates across restarts."""
        if self.history_path.exists():
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self.stashed_ids = set(data[-1000:])
            except Exception:
                self.stashed_ids = set()

    def _save_stashed_history(self) -> None:
        """Save history of stashed IDs."""
        try:
            ids_list = list(self.stashed_ids)[-1000:]
            with open(self.history_path, "w", encoding="utf-8") as f:
                json.dump(ids_list, f, indent=2)
        except Exception:
            pass

    def _read_raw(self) -> List[Dict[str, Any]]:
        """Read instruction list from local file."""
        if not self.file_path.exists():
            return []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return []
                data = json.loads(content)
                return data if isinstance(data, list) else []
        except Exception as e:
            print(f"[Storage] Warning: Failed to parse {self.file_path}: {e}")
            return []

    def _write_raw(self, items: List[Dict[str, Any]]) -> None:
        """Atomically write instruction list to disk using a temporary file."""
        dir_name = self.file_path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix="inst_tmp_", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(items, f, indent=2)
            # Atomic rename / replace
            if os.name == "nt" and self.file_path.exists():
                # On Windows, replace can fail if open, os.replace handles atomic overwrite in modern Python
                os.replace(tmp_path, str(self.file_path))
            else:
                os.replace(tmp_path, str(self.file_path))
        except Exception as e:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise e

    def load_instructions(self) -> List[Dict[str, Any]]:
        """Return all instructions currently queued in the local file."""
        with _STORAGE_LOCK:
            return self._read_raw()

    def stash_instruction(self, instruction: Dict[str, Any]) -> bool:
        """
        Stash an instruction from the cloud bridge to the local instruction file.
        Returns True if newly stashed, False if already present or duplicate.
        """
        inst_id = str(instruction.get("id", "")).strip()
        if not inst_id:
            return False

        with _STORAGE_LOCK:
            # Check if this ID is already in our stashed history
            if inst_id in self.stashed_ids:
                return False

            items = self._read_raw()
            # Double check against active list in file
            if any(str(i.get("id")) == inst_id for i in items):
                self.stashed_ids.add(inst_id)
                return False

            # Add timestamp for when it was stashed locally
            entry = dict(instruction)
            entry["stashed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            items.append(entry)
            self._write_raw(items)

            self.stashed_ids.add(inst_id)
            self._save_stashed_history()
            return True

    def get_next_instruction(self) -> Optional[Dict[str, Any]]:
        """Peek the next instruction in line to run (oldest first / FIFO)."""
        with _STORAGE_LOCK:
            items = self._read_raw()
            if items:
                return items[0]
            return None

    def remove_instruction(self, instruction_id: str) -> bool:
        """
        Remove an instruction from the local file when it runs completely.
        Returns True if removed, False if not found.
        """
        if not instruction_id:
            return False

        with _STORAGE_LOCK:
            items = self._read_raw()
            original_len = len(items)
            updated = [i for i in items if str(i.get("id")) != str(instruction_id)]
            if len(updated) != original_len:
                self._write_raw(updated)
                return True
            return False

    def clear(self) -> None:
        """Clear all instructions from the local file."""
        with _STORAGE_LOCK:
            self._write_raw([])

    def count(self) -> int:
        """Return count of instructions currently queued locally."""
        with _STORAGE_LOCK:
            return len(self._read_raw())
