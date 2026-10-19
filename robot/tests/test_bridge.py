"""
Unit tests for Nova Robot Bridge Subsystem.
Tests:
- Storage stash, FIFO read, deduplication, and removal
- Code-to-file mapping resolution
- LocalInstructionExecutor process execution and queue removal upon completion
- CloudBridgeListener pending fetch and stash
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure robot folder is in sys.path
TEST_DIR = Path(__file__).resolve().parent
ROBOT_DIR = TEST_DIR.parent
PROJECT_ROOT = ROBOT_DIR.parent
for p in [str(PROJECT_ROOT), str(ROBOT_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from bridge.storage import InstructionStorage
from bridge.code_mappings import resolve_action_file, CODE_TO_FILE
from bridge.executor import LocalInstructionExecutor
from bridge.listener import CloudBridgeListener


class TestBridgeStorage(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.inst_file = Path(self.temp_dir.name) / "test_instructions.json"
        self.hist_file = Path(self.temp_dir.name) / "test_history.json"
        self.storage = InstructionStorage(file_path=self.inst_file, history_path=self.hist_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_empty_storage(self):
        self.assertEqual(self.storage.load_instructions(), [])
        self.assertEqual(self.storage.count(), 0)
        self.assertIsNone(self.storage.get_next_instruction())

    def test_stash_and_deduplicate(self):
        item1 = {"id": "inst_101", "code": "NOD_UP", "value": "30"}
        # First stash should succeed
        added = self.storage.stash_instruction(item1)
        self.assertTrue(added)
        self.assertEqual(self.storage.count(), 1)

        # Duplicate stash of same ID should be rejected
        added_again = self.storage.stash_instruction(item1)
        self.assertFalse(added_again)
        self.assertEqual(self.storage.count(), 1)

        # Different item should succeed
        item2 = {"id": "inst_102", "code": "WAVE", "value": "2"}
        self.assertTrue(self.storage.stash_instruction(item2))
        self.assertEqual(self.storage.count(), 2)

    def test_fifo_ordering(self):
        item1 = {"id": "inst_1", "code": "NOD", "value": "1"}
        item2 = {"id": "inst_2", "code": "WAVE", "value": "2"}
        self.storage.stash_instruction(item1)
        self.storage.stash_instruction(item2)

        next_item = self.storage.get_next_instruction()
        self.assertEqual(next_item["id"], "inst_1")

    def test_remove_instruction(self):
        item1 = {"id": "inst_1", "code": "NOD", "value": "1"}
        item2 = {"id": "inst_2", "code": "WAVE", "value": "2"}
        self.storage.stash_instruction(item1)
        self.storage.stash_instruction(item2)

        # Remove first instruction
        removed = self.storage.remove_instruction("inst_1")
        self.assertTrue(removed)
        self.assertEqual(self.storage.count(), 1)

        # Remaining item should now be inst_2
        remaining = self.storage.get_next_instruction()
        self.assertEqual(remaining["id"], "inst_2")

        # Removing non-existent returns False
        self.assertFalse(self.storage.remove_instruction("non_existent"))


class TestCodeMappings(unittest.TestCase):
    def test_exact_matches(self):
        nod_file = resolve_action_file("NOD")
        self.assertIsNotNone(nod_file)
        self.assertTrue(nod_file.name.endswith(".py"))

        wave_file = resolve_action_file("WAVE")
        self.assertIsNotNone(wave_file)
        self.assertEqual(wave_file.name, "wave.py")

    def test_case_insensitivity(self):
        nod_up = resolve_action_file("nod_up")
        self.assertIsNotNone(nod_up)
        self.assertEqual(nod_up.name, "nod_up.py")

    def test_motor_prefix_matching(self):
        m2_move = resolve_action_file("M2_20")
        self.assertIsNotNone(m2_move)
        self.assertEqual(m2_move.name, "motor_move.py")

        n1_move = resolve_action_file("N1_-30")
        self.assertIsNotNone(n1_move)
        self.assertEqual(n1_move.name, "motor_move.py")

    def test_fallback_to_default(self):
        unknown = resolve_action_file("RANDOM_CUSTOM_CODE_123")
        self.assertIsNotNone(unknown)
        self.assertEqual(unknown.name, "default_action.py")


class TestExecutorAndListener(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.inst_file = Path(self.temp_dir.name) / "test_instructions.json"
        self.hist_file = Path(self.temp_dir.name) / "test_history.json"
        self.storage = InstructionStorage(file_path=self.inst_file, history_path=self.hist_file)

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("requests.get")
    def test_listener_stashes_from_cloud(self, mock_get):
        # Mock cloud bridge returning 1 pending instruction
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "instructions": [
                {
                    "id": "inst_test_01",
                    "code": "NOD_UP",
                    "value": "30",
                    "status": "pending",
                }
            ]
        }
        mock_get.return_value = mock_resp

        listener = CloudBridgeListener(
            bridge_url="http://testbridge.local",
            storage=self.storage,
        )

        stashed_count = listener.poll_once()
        self.assertEqual(stashed_count, 1)
        self.assertEqual(self.storage.count(), 1)

        queued = self.storage.get_next_instruction()
        self.assertEqual(queued["id"], "inst_test_01")
        self.assertEqual(queued["code"], "NOD_UP")

    @patch("requests.get")
    def test_executor_runs_and_removes_from_file(self, mock_get):
        # Mock cloud acknowledgment
        mock_ack_resp = MagicMock()
        mock_ack_resp.ok = True
        mock_get.return_value = mock_ack_resp

        # Create a tiny mock action script that exits with 0
        test_script = Path(self.temp_dir.name) / "mock_action.py"
        with open(test_script, "w") as f:
            f.write("import sys; print('Mock action completed'); sys.exit(0)\n")

        # Stash an instruction
        self.storage.stash_instruction({
            "id": "inst_run_01",
            "code": "TEST_ACTION",
            "value": "10",
        })
        self.assertEqual(self.storage.count(), 1)

        # Point CODE_TO_FILE for TEST_ACTION to our test script
        with patch.dict("bridge.code_mappings.CODE_TO_FILE", {"TEST_ACTION": str(test_script)}):
            executor = LocalInstructionExecutor(
                storage=self.storage,
                bridge_url="http://testbridge.local",
            )

            # Process the item
            handled = executor.process_next()
            self.assertTrue(handled)

            # The instruction MUST have been removed from the local instruction file!
            self.assertEqual(self.storage.count(), 0)
            self.assertIsNone(self.storage.get_next_instruction())


if __name__ == "__main__":
    unittest.main()
