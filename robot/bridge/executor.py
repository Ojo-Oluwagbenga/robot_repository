"""
Local Instruction Executor for Raspberry Pi.
Reads instructions from the local instruction file, looks up the defined
action file in code_mappings.py, executes it, and removes the instruction
from the local file when execution finishes completely.
"""

import sys
import os
import json
import time
import subprocess
import threading
import requests
from pathlib import Path
from typing import Optional, Dict, Any, Callable

from .config import (
    DEFAULT_BRIDGE_URL,
    DEFAULT_EXECUTOR_INTERVAL,
    ACTION_TIMEOUT_SECONDS,
    AUTO_ACK_CLOUD,
)
from .storage import InstructionStorage
from .code_mappings import resolve_action_file


class LocalInstructionExecutor:
    """
    Monitors the local instruction file on the Pi, runs the user-defined
    script for each received code, and removes the instruction upon completion.
    """

    def __init__(
        self,
        storage: Optional[InstructionStorage] = None,
        bridge_url: str = DEFAULT_BRIDGE_URL,
        interval: float = DEFAULT_EXECUTOR_INTERVAL,
        timeout_seconds: int = ACTION_TIMEOUT_SECONDS,
        on_completed_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.storage = storage or InstructionStorage()
        self.bridge_url = bridge_url.rstrip("/")
        self.interval = interval
        self.timeout_seconds = timeout_seconds
        self.on_completed_callback = on_completed_callback

        self._running = False
        self._thread: Optional[threading.Thread] = None

    def acknowledge_to_cloud(self, inst_id: str, status: str = "executed") -> bool:
        """Mark instruction as executed on the cloud bridge site."""
        if not AUTO_ACK_CLOUD or not self.bridge_url:
            return True

        urls = [
            f"{self.bridge_url}/api/pi_instructions?ack={inst_id}&status={status}",
            f"{self.bridge_url}/pi_instructions?ack={inst_id}&status={status}",
        ]
        for url in urls:
            try:
                resp = requests.get(url, timeout=4)
                if resp.ok:
                    return True
            except Exception:
                pass
        return False

    def execute_instruction(self, instruction: Dict[str, Any]) -> bool:
        """
        Execute an instruction by running its defined file from code_mappings.py.
        Returns True if the target file ran completely and succeeded (exit code 0).
        """
        inst_id = str(instruction.get("id", ""))
        code = str(instruction.get("code", "")).strip()
        val = str(instruction.get("value", "")).strip()

        print("=" * 68)
        print(f"[*] READING LOCAL INSTRUCTION QUEUE")
        print(f"    ID       : {inst_id}")
        print(f"    Code     : {code}")
        print(f"    Value    : {val}")
        print(f"    Timestamp: {instruction.get('timestamp')}")
        print("-" * 68)

        # 1. Look up the file defined to run for this code
        target_file = resolve_action_file(code)
        if not target_file:
            print(f"[!] [Executor] No action file defined or found for code '{code}'.")
            return False

        print(f"[+] [Executor] Matched action file: {target_file}")
        print(f"[*] [Executor] Spawning process...")

        # 2. Prepare environment and CLI arguments for the target file
        env = os.environ.copy()
        env["ROBOT_CODE"] = code
        env["ROBOT_VALUE"] = val
        env["ROBOT_INSTRUCTION_ID"] = inst_id

        cmd = [
            sys.executable,
            str(target_file),
            code,
            val,
            inst_id,
            json.dumps(instruction),
        ]

        # 3. Run target file and wait for it to complete
        start_time = time.time()
        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            elapsed = time.time() - start_time

            # Print standard output from the action file
            if result.stdout.strip():
                print(f"--- Action Output ({target_file.name}) ---")
                print(result.stdout.strip())
                print("------------------------------------------")

            if result.stderr.strip():
                print(f"[!] Action Stderr:\n{result.stderr.strip()}")

            if result.returncode == 0:
                print(f"[OK] [Executor] Action file ran completely in {elapsed:.2f}s (Exit code 0).")
                return True
            else:
                print(f"[FAIL] [Executor] Action file failed with exit code {result.returncode}.")
                return False

        except subprocess.TimeoutExpired:
            print(f"[ERROR] [Executor] Action file timed out after {self.timeout_seconds}s.")
            return False
        except Exception as e:
            print(f"[ERROR] [Executor] Failed to launch action process: {e}")
            return False

    def process_next(self) -> bool:
        """
        Pulls next instruction from local file, runs its mapped action file,
        and removes it from the local instruction file once finished.
        """
        instruction = self.storage.get_next_instruction()
        if not instruction:
            return False

        inst_id = str(instruction.get("id", ""))
        success = self.execute_instruction(instruction)

        if success:
            # Code ran completely -> remove from local instruction file!
            removed = self.storage.remove_instruction(inst_id)
            remaining = self.storage.count()
            if removed:
                print(
                    f"\n[+] [Executor] Instruction '{inst_id}' COMPLETED AND REMOVED from local file.\n"
                    f"    Remaining in queue: {remaining} instructions\n"
                )
            else:
                print(f"[!] Warning: Could not remove {inst_id} from local file.")

            # Acknowledge to cloud bridge site
            if self.acknowledge_to_cloud(inst_id, "executed"):
                print(f"[+] [Cloud] Acknowledged '{inst_id}' as 'executed' on bridge site.\n")

            if self.on_completed_callback:
                try:
                    self.on_completed_callback(instruction)
                except Exception:
                    pass
            return True
        else:
            # If failed, still remove after retry or remove with warning to prevent deadlocking queue
            print(f"[!] [Executor] Retrying or discarding failed instruction '{inst_id}'...")
            self.storage.remove_instruction(inst_id)
            self.acknowledge_to_cloud(inst_id, "failed")
            return False

    def run_loop(self) -> None:
        """Continuous execution loop."""
        self._running = True
        print(f"[*] LocalInstructionExecutor started. Checking local file every {self.interval}s...")
        while self._running:
            try:
                # If there are items in the queue, process them immediately
                has_work = self.process_next()
                if not has_work:
                    time.sleep(self.interval)
            except Exception as e:
                print(f"[Executor] Unexpected error in execution loop: {e}")
                time.sleep(self.interval)

    def start_background(self) -> None:
        """Start executor in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="BridgeExecutorThread")
        self._thread.start()

    def stop(self) -> None:
        """Stop background execution thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
