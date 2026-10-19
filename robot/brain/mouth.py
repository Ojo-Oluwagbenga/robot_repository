"""
Robot Mouth Interface (Piper TTS & Bluetooth Speaker).
Handles text-to-speech synthesis using Piper neural TTS and audio output
via PipeWire (pw-play) to a trusted Bluetooth speaker or default audio sink.
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    from robot.brain.config import (
        ROBOT_AUTO_CONNECT_BT,
        ROBOT_BT_MAC,
        ROBOT_BT_NAME,
        ROBOT_PIPER_BIN,
        ROBOT_PIPER_MODEL,
        ROBOT_TTS_ENABLED,
        ROBOT_TTS_RATE,
    )
except ImportError:
    try:
        from brain.config import (
            ROBOT_AUTO_CONNECT_BT,
            ROBOT_BT_MAC,
            ROBOT_BT_NAME,
            ROBOT_PIPER_BIN,
            ROBOT_PIPER_MODEL,
            ROBOT_TTS_ENABLED,
            ROBOT_TTS_RATE,
        )
    except ImportError:
        ROBOT_BT_MAC = "12:3F:2A:07:A1:F1"
        ROBOT_BT_NAME = "BT SPEAKER"
        ROBOT_PIPER_BIN = "/home/pi/piper/piper"
        ROBOT_PIPER_MODEL = "/home/pi/robot_voice/en_US-ryan-medium.onnx"
        ROBOT_TTS_RATE = 22050
        ROBOT_AUTO_CONNECT_BT = True
        ROBOT_TTS_ENABLED = True

logger = logging.getLogger("robot.brain.mouth")


def clean_text_for_speech(text: str) -> str:
    """
    Sanitize text for clean vocal pronunciation:
    - Removes code blocks, URLs, and markdown markup (*, #, _, `, etc.)
    - Expands contractions and normalizes whitespace
    """
    if not text:
        return ""

    # Remove markdown fenced code blocks entirely
    cleaned = re.sub(r"```[\s\S]*?```", " [code block omitted] ", text)
    # Remove inline code backticks
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    # Convert markdown links [Label](url) -> Label
    cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
    # Remove raw URLs
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    # Remove markdown headers (#, ##, etc.)
    cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
    # Remove markdown bold/italics markers
    cleaned = re.sub(r"[*_]{1,3}", "", cleaned)
    # Remove bullet markers (- or * or • at start of line)
    cleaned = re.sub(r"^[\s*•-]+\s*", "", cleaned, flags=re.MULTILINE)
    # Replace multiple newlines or spaces with single space
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned


class RobotMouth:
    """
    Controls humanoid robot vocal output:
    1. Connects to trusted Bluetooth speaker via bluetoothctl / wpctl.
    2. Synthesizes voice locally using Piper neural ONNX models.
    3. Streams raw audio to PipeWire (pw-play) or ALSA (aplay).
    """

    def __init__(
        self,
        bt_mac: str = ROBOT_BT_MAC,
        sink_name: str = ROBOT_BT_NAME,
        piper_bin: str = ROBOT_PIPER_BIN,
        piper_model: str = ROBOT_PIPER_MODEL,
        rate: int = ROBOT_TTS_RATE,
        enabled: bool = ROBOT_TTS_ENABLED,
    ):
        self.bt_mac = bt_mac
        self.sink_name = sink_name
        self.piper_bin = str(Path(piper_bin).expanduser())
        self.piper_model = str(Path(piper_model).expanduser())
        self.rate = rate
        self.enabled = enabled

        self.is_bluetooth_connected = False
        self.active_sink_id: Optional[str] = None
        self._current_player_proc: Optional[subprocess.Popen] = None
        self._current_piper_proc: Optional[subprocess.Popen] = None

        logger.info(
            f"RobotMouth initialized: Piper='{self.piper_bin}', Model='{self.piper_model}', "
            f"BT='{self.bt_mac}' ({self.sink_name})"
        )

    def is_available(self) -> bool:
        """Check if Piper engine executable and voice model exist on the filesystem."""
        return os.path.isfile(self.piper_bin) and os.path.isfile(self.piper_model)

    def connect_bluetooth_speaker(self, timeout: int = 15) -> bool:
        """
        Reconnect to the trusted BT speaker and set it as the default audio sink.
        Uses bluetoothctl and wpctl.
        """
        if not self.enabled:
            return False

        if not shutil.which("bluetoothctl"):
            logger.debug("bluetoothctl not found on this system. Skipping BT connection.")
            return False

        print(f"Connecting to Bluetooth speaker ({self.bt_mac})...")
        try:
            subprocess.run(
                ["bluetoothctl", "power", "on"],
                capture_output=True,
                text=True,
                timeout=10,
            )

            result = subprocess.run(
                ["bluetoothctl", "connect", self.bt_mac],
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if "Connection successful" in result.stdout:
                print("Bluetooth speaker connected successfully.")
                self.is_bluetooth_connected = True
            elif "already connected" in result.stdout.lower():
                print("Bluetooth speaker is already connected.")
                self.is_bluetooth_connected = True
            else:
                print(f"Warning: connect may not have succeeded: {result.stdout.strip()}")

            time.sleep(2)

            # Discover sink ID with wpctl
            if shutil.which("wpctl"):
                status_res = subprocess.run(
                    ["wpctl", "status"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                status_out = status_res.stdout

                sink_id = None
                in_sinks = False
                for line in status_out.splitlines():
                    clean = line.strip()
                    if "Sinks:" in clean:
                        in_sinks = True
                        continue
                    if in_sinks and any(hdr in clean for hdr in ["Sources:", "Filters:", "Streams:", "Devices:"]):
                        in_sinks = False
                        continue
                    if in_sinks and (self.sink_name in line or self.bt_mac.replace(":", "-").lower() in line.lower()):
                        for token in clean.split():
                            num = token.lstrip("*").rstrip(".:")
                            if num.isdigit():
                                sink_id = num
                                break
                        if sink_id:
                            break

                if sink_id:
                    subprocess.run(["wpctl", "set-default", sink_id], check=True)
                    self.active_sink_id = sink_id
                    print(f"BT SPEAKER (sink {sink_id}) set as default audio output.")
                    self._restore_mic_source(status_out)
                    return True
                else:
                    print("Speaker connected, but audio sink was not found yet. Using current default output.")
                    self._restore_mic_source(status_out)
                    return self.is_bluetooth_connected

            return self.is_bluetooth_connected
        except subprocess.TimeoutExpired:
            print("Bluetooth connect timed out. Continuing with current default audio output.")
            return False
        except Exception as e:
            print(f"Bluetooth connect notice: {e}. Continuing with current default audio output.")
            return False

    def _restore_mic_source(self, status_out: str) -> None:
        """Prevent Bluetooth speaker from stealing the default recording microphone."""
        in_sources = False
        mic_candidates = []
        is_bluez_default = False

        for line in status_out.splitlines():
            clean = line.strip()
            if "*" in clean and ("bluez" in clean.lower() or "speaker" in clean.lower()):
                is_bluez_default = True
            if "Sources:" in clean:
                in_sources = True
                continue
            if in_sources and any(hdr in clean for hdr in ["Filters:", "Streams:", "Settings:"]):
                in_sources = False
                continue
            if in_sources and clean and ("." in clean or "bluez" in clean):
                num = None
                for token in clean.split():
                    t = token.lstrip("*").rstrip(".:")
                    if t.isdigit():
                        num = t
                        break
                if num:
                    if "bluez" not in clean.lower() and "monitor" not in clean.lower():
                        clean_name = re.sub(r'^[│\s*0-9.:\-]+', '', clean)
                        clean_name = re.sub(r'\[vol:.*?\]', '', clean_name).strip()
                        mic_candidates.append((num, clean_name or clean))

        if is_bluez_default and mic_candidates:
            real_mic_id, real_mic_name = mic_candidates[0]
            try:
                subprocess.run(["wpctl", "set-default", real_mic_id], check=True)
                print(f"Physical microphone '{real_mic_name}' (source {real_mic_id}) restored as default recording input.")
            except Exception as e:
                logger.debug(f"Could not restore mic source: {e}")

    def speak(self, text: str, block: bool = True) -> None:
        """
        Synthesize speech with Piper and output to PipeWire or ALSA speaker.
        Safe against shell injection and supports raw streaming.
        """
        if not self.enabled or not text:
            return

        clean_text = clean_text_for_speech(text)
        if not clean_text:
            return

        # If speech target is phone or local Piper engine is not available, dispatch to phone speaker
        speech_target = os.environ.get("ROBOT_SPEECH_TARGET", "phone").lower()
        if speech_target == "phone" or not self.is_available():
            try:
                from robot.bridge.phone_speaker import speak_to_phone
                print(f"\n[Mouth -> Phone Speaker]: {clean_text}")
                speak_to_phone(clean_text)
                return
            except Exception as e:
                logger.debug(f"Could not dispatch to phone speaker: {e}")

        # Check if local Piper engine is ready
        if not self.is_available():
            logger.debug(f"[Mouth Preview] {clean_text}")
            return

        print(f"\n[Speaking (Ryan)]: {clean_text}")

        # Choose audio player: PipeWire (pw-play) or ALSA (aplay)
        if shutil.which("pw-play"):
            player_cmd = [
                "pw-play",
                "--raw",
                "--rate", str(self.rate),
                "--channels", "1",
                "--format", "s16",
                "-",
            ]
        elif shutil.which("aplay"):
            player_cmd = [
                "aplay",
                "-q",
                "-r", str(self.rate),
                "-f", "S16_LE",
                "-c", "1",
                "-",
            ]
        else:
            print("Audio Error: Neither 'pw-play' nor 'aplay' audio player found in PATH.")
            return

        # Prepare environment to restrict ONNX / OpenMP to a single CPU thread.
        # This prevents 4-core 100% CPU current surges that trip the Raspberry Pi PMIC under-voltage halt (Red LED).
        piper_env = os.environ.copy()
        piper_env["OMP_NUM_THREADS"] = "1"
        piper_env["OPENBLAS_NUM_THREADS"] = "1"
        piper_env["MKL_NUM_THREADS"] = "1"
        piper_env["VECLIB_MAXIMUM_THREADS"] = "1"
        piper_env["NUMEXPR_NUM_THREADS"] = "1"

        piper_cmd = [
            self.piper_bin,
            "--model", self.piper_model,
            "--output_raw",
        ]
        # Use nice and taskset to constrain CPU frequency and core usage
        if shutil.which("nice"):
            piper_cmd = ["nice", "-n", "10"] + piper_cmd
        if shutil.which("taskset"):
            piper_cmd = ["taskset", "-c", "0"] + piper_cmd

        try:
            # Piped process execution without shell=True to avoid quoting issues
            piper_proc = subprocess.Popen(
                piper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=piper_env,
            )
            player_proc = subprocess.Popen(
                player_cmd,
                stdin=piper_proc.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Allow piper_proc to receive a SIGPIPE if player_proc exits
            if piper_proc.stdout:
                piper_proc.stdout.close()

            self._current_piper_proc = piper_proc
            self._current_player_proc = player_proc

            # Feed text directly to Piper's standard input
            piper_proc.communicate(input=clean_text.encode("utf-8"))

            if block:
                # Calculate safe timeout proportional to speech length (~10 chars/sec + 10s buffer)
                max_wait = max(12, int(len(clean_text) / 10) + 10)
                try:
                    player_proc.wait(timeout=max_wait)
                except subprocess.TimeoutExpired:
                    logger.warning("[Mouth] Audio player exceeded timeout; terminating.")
                    player_proc.terminate()

        except Exception as e:
            print(f"Audio Error: {e}")
        finally:
            self._current_piper_proc = None
            self._current_player_proc = None

    def stop(self) -> None:
        """Interrupt and kill any active speech output processes."""
        if self._current_player_proc and self._current_player_proc.poll() is None:
            try:
                self._current_player_proc.terminate()
            except Exception:
                pass
        if self._current_piper_proc and self._current_piper_proc.poll() is None:
            try:
                self._current_piper_proc.terminate()
            except Exception:
                pass
        logger.debug("[Mouth] Speech output halted.")


if __name__ == "__main__":
    mouth = RobotMouth()
    mouth.connect_bluetooth_speaker()

    if len(sys.argv) > 1:
        message = " ".join(sys.argv[1:])
        mouth.speak(message)
    else:
        mouth.speak("Bluetooth system is now fully operational.")
        if sys.stdin.isatty():
            print("\n--- Robot Mouth Interactive Test (Ctrl+C to exit) ---")
            while True:
                try:
                    line = input("What should I say next: ").strip()
                    if line.lower() in ("exit", "quit"):
                        break
                    if line:
                        mouth.speak(line)
                except (KeyboardInterrupt, EOFError):
                    print("\nExiting mouth test.")
                    break
