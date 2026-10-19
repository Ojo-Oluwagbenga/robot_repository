"""
Structured logging system for the Raspberry Pi 5 Humanoid Robot Controller.
Supports hierarchical debug levels (0-3), console output, and file logging.
"""

import os
import sys
import logging
from typing import List, Optional

from config import DEBUG_LEVEL, LOG_TO_FILE, LOG_FILE_PATH


class RobotLogger:
    """
    Multi-level logger providing structured diagnostics for communication,
    packet inspection, feedback decoding, and hardware transitions.
    """

    def __init__(self, name: str = "RobotController", level: int = DEBUG_LEVEL):
        self.level = level
        self._logger = logging.getLogger(name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        # Clear existing handlers to prevent duplicate lines
        if self._logger.hasHandlers():
            self._logger.handlers.clear()

        # Console Formatter
        console_handler = logging.StreamHandler(sys.stdout)
        console_fmt = logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_fmt)
        self._logger.addHandler(console_handler)

        # File Handler (if configured)
        if LOG_TO_FILE:
            try:
                log_dir = os.path.dirname(LOG_FILE_PATH)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                file_handler = logging.FileHandler(LOG_FILE_PATH, encoding="utf-8")
                file_fmt = logging.Formatter(
                    fmt="%(asctime)s.%(msecs)03d [%(levelname)s] [%(name)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S"
                )
                file_handler.setFormatter(file_fmt)
                self._logger.addHandler(file_handler)
            except Exception as e:
                self._logger.warning(f"Failed to initialize file logger at {LOG_FILE_PATH}: {e}")

    def set_level(self, level: int) -> None:
        """Dynamically adjust the debug level (0 to 3)."""
        self.level = max(0, min(3, level))

    def error(self, msg: str) -> None:
        """Level 0+: Critical errors and hardware faults."""
        self._logger.error(f"[ERROR] {msg}")

    def warning(self, msg: str) -> None:
        """Level 0+: Non-fatal warnings."""
        self._logger.warning(f"[WARN] {msg}")

    def info(self, msg: str) -> None:
        """Level 1+: High-level operational events."""
        if self.level >= 1:
            self._logger.info(msg)

    def log_command(self, esp_id: int, cmd_type: str, motor_id: Optional[int] = None,
                    pulses: Optional[int] = None, direction: Optional[int] = None) -> None:
        """Level 1: Log command transmission at an operational level."""
        if self.level >= 1:
            dir_str = "CW" if direction == 0 else "CCW" if direction == 1 else "N/A"
            m_str = f"M{motor_id + 1}" if motor_id is not None else "ALL"
            p_str = f" pulses={pulses}" if pulses is not None else ""
            self._logger.info(f"CMD -> ESP:{esp_id} [{cmd_type}] {m_str}{p_str} dir={dir_str}")

    def log_tx_packet(self, esp_id: int, packet_type: str, bits: List[int]) -> None:
        """Level 2: Log complete transmitted packet structure."""
        if self.level >= 2:
            bit_str = "".join(str(b) for b in bits)
            lines = [
                "--- TX PACKET ---",
                f"ESP:    {esp_id}",
                f"TYPE:   {packet_type}",
                f"BITS:   {bit_str}",
                f"LENGTH: {len(bits)}"
            ]
            self._logger.debug("\n" + "\n".join(lines))

    def log_rx_feedback(self, esp_id: int, motor_id: int, raw_bits: List[int],
                        magnitude: int, sign_bit: int, net_pulses: int) -> None:
        """Level 1 & 2: Log received feedback packet details."""
        if self.level == 1:
            sign_str = "+" if net_pulses >= 0 else ""
            self._logger.info(f"FEEDBACK <- ESP:{esp_id} M{motor_id + 1}: net={sign_str}{net_pulses} pulses")
        elif self.level >= 2:
            bit_str = "".join(str(b) for b in raw_bits)
            sign_name = "POSITIVE" if sign_bit == 1 else "NEGATIVE"
            sign_sym = "+" if net_pulses >= 0 else ""
            lines = [
                "--- RX FEEDBACK ---",
                f"ESP:       {esp_id}",
                f"MOTOR:     {motor_id + 1} (ID: {motor_id:02b})",
                f"RAW:       {bit_str}",
                f"MAGNITUDE: {magnitude}",
                f"SIGN:      {sign_name} ({sign_bit})",
                f"NET:       {sign_sym}{net_pulses}"
            ]
            self._logger.debug("\n" + "\n".join(lines))

    def trace(self, msg: str) -> None:
        """Level 3: Low-level bit trace and timing debug."""
        if self.level >= 3:
            self._logger.debug(f"[TRACE] {msg}")


# Global instance
_default_logger: Optional[RobotLogger] = None


def setup_robot_logger(level: int = DEBUG_LEVEL) -> RobotLogger:
    """Initialize and return the singleton logger instance."""
    global _default_logger
    _default_logger = RobotLogger(level=level)
    return _default_logger


def get_logger() -> RobotLogger:
    """Retrieve the global robot logger instance."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_robot_logger()
    return _default_logger
