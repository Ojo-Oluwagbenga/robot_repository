"""
Gemini AI Client for Robot Brain.
Provides high-speed conversational reasoning and streaming responses using the Google Gemini API.
Uses direct HTTP REST with SSE streaming for zero-dependency portability across PC and Raspberry Pi.
"""

import base64
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional
import requests

try:
    from robot.brain.config import (
        GEMINI_API_KEY,
        GEMINI_MODEL,
        REQUEST_TIMEOUT,
        TEMPERATURE,
    )
except ImportError:
    from brain.config import (
        GEMINI_API_KEY,
        GEMINI_MODEL,
        REQUEST_TIMEOUT,
        TEMPERATURE,
    )

logger = logging.getLogger("robot.brain.gemini")


class GeminiClient:
    """HTTP client for interacting with the Google Gemini API."""

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    FALLBACK_MODELS = [
        "gemini-flash-lite-latest",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemini-3.1-flash-lite",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = GEMINI_MODEL,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self.api_key = api_key or GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        self.timeout = timeout
        self._online_cached: Optional[bool] = None

        # Build candidate fallback chain starting with requested model
        models = [self.model] + [m for m in self.FALLBACK_MODELS if m != self.model]
        self.candidate_models = models

    def set_api_key(self, api_key: str) -> None:
        """Update the active Gemini API key."""
        self.api_key = api_key.strip()
        self._online_cached = None

    def is_online(self) -> bool:
        """Check whether the Gemini API key is configured and reachable."""
        if not self.api_key:
            return False
        if self._online_cached is True:
            return True

        for m in self.candidate_models:
            url = f"{self.BASE_URL}/{m}?key={self.api_key}"
            try:
                resp = requests.get(url, timeout=4.0)
                if resp.status_code == 200:
                    self.model = m
                    self._online_cached = True
                    return True
            except Exception:
                continue
        return False

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: float = TEMPERATURE,
    ) -> str:
        """Single-turn text generation."""
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY is not set. Please provide your Gemini API key.")

        url = f"{self.BASE_URL}/{self.model}:generateContent?key={self.api_key}"
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if system:
            payload["systemInstruction"] = {
                "parts": [{"text": system}]
            }

        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            return ""

    def chat(
        self,
        messages: List[Dict[str, str]],
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        temperature: float = TEMPERATURE,
    ) -> str:
        """
        Multi-turn chat interaction.
        Accepts standard messages list [{'role': 'system'|'user'|'assistant', 'content': '...'}]
        Supports real-time token streaming via Server-Sent Events (SSE).
        """
        if not self.api_key:
            raise ValueError(
                "GEMINI_API_KEY is not set. Please set the GEMINI_API_KEY environment variable "
                "or configure it in robot/brain/config.py."
            )

        # 1. Separate system instructions from conversational turns
        system_instruction_parts: List[Dict[str, str]] = []
        contents: List[Dict[str, Any]] = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not content.strip():
                continue

            if role == "system":
                system_instruction_parts.append({"text": content})
            elif role in ("assistant", "model"):
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})

        # Ensure we have at least one user content
        if not contents:
            contents.append({"role": "user", "parts": [{"text": "Hello"}]})

        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
            },
        }
        if system_instruction_parts:
            payload["systemInstruction"] = {"parts": system_instruction_parts}

        headers = {"Content-Type": "application/json"}
        last_error = None

        # Try candidate models in order (e.g. gemini-3.8-flash -> gemini-flash-latest -> gemini-3.1-flash-lite)
        for cand_model in self.candidate_models:
            try:
                # 2. Streaming execution via Server-Sent Events (alt=sse)
                if stream:
                    url = f"{self.BASE_URL}/{cand_model}:streamGenerateContent?key={self.api_key}&alt=sse"
                    resp = requests.post(
                        url, json=payload, headers=headers, stream=True, timeout=self.timeout
                    )
                    if resp.status_code in (429, 500, 503, 404):
                        logger.debug(f"Gemini model '{cand_model}' returned {resp.status_code}. Trying fallback model...")
                        continue
                    resp.raise_for_status()

                    full_response: List[str] = []
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        if line.startswith("data:"):
                            json_str = line[len("data:"):].strip()
                            if not json_str or json_str == "[DONE]":
                                continue
                            try:
                                chunk_obj = json.loads(json_str)
                                candidates = chunk_obj.get("candidates", [])
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts", [])
                                    for p in parts:
                                        token = p.get("text", "")
                                        if token:
                                            full_response.append(token)
                                            if on_chunk:
                                                on_chunk(token)
                            except json.JSONDecodeError:
                                continue

                    if full_response:
                        self.model = cand_model
                        return "".join(full_response).strip()

                # 3. Non-streaming execution
                else:
                    url = f"{self.BASE_URL}/{cand_model}:generateContent?key={self.api_key}"
                    resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                    if resp.status_code in (429, 500, 503, 404):
                        logger.debug(f"Gemini model '{cand_model}' returned {resp.status_code}. Trying fallback model...")
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    self.model = cand_model
                    if on_chunk:
                        on_chunk(text)
                    return text

            except Exception as e:
                last_error = e
                continue

        if last_error:
            raise last_error
        return ""

    def transcribe_audio(self, wav_bytes: bytes) -> str:
        """
        Transcribe speech from raw WAV audio bytes using Gemini Multimodal Audio.
        Extremely accurate for any accent, background noise, or conversational phrasing.
        Automatically retries across candidate models and strips noise/timestamp artifacts.
        """
        if not self.api_key or not wav_bytes:
            return ""

        b64_data = base64.b64encode(wav_bytes).decode("utf-8")
        payload: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": "audio/wav", "data": b64_data}},
                        {
                            "text": (
                                "Transcribe the spoken words in this audio clip verbatim. "
                                "Return ONLY the transcribed words with natural punctuation and capitalization. "
                                "Do NOT include timestamps, speaker labels, or commentary. "
                                "If the audio is silence or unintelligible noise, return nothing."
                            )
                        },
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
            },
        }

        headers = {"Content-Type": "application/json"}
        for cand_model in self.candidate_models:
            url = f"{self.BASE_URL}/{cand_model}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
                if resp.status_code in (429, 500, 503, 404):
                    continue
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            raw_text = parts[0].get("text", "").strip()
                            text = re.sub(r"^```[\w]*\n?", "", raw_text)
                            text = re.sub(r"\n?```$", "", text).strip().strip("\"'")

                            # Discard timestamp hallucinations (e.g. "0:00", "00:00", "[00:00]")
                            if re.match(r"^\[?\d{1,2}:\d{2}\]?$", text):
                                return ""

                            # Discard typical silence / noise hallucinations
                            IGNORED = {
                                "silence", "[silence]", "no speech", "none", "blank audio",
                                "unintelligible", "[unintelligible]", "thank you for watching",
                                "please subscribe", "subtitles by", "you", ".", ""
                            }
                            if text.lower().strip(".,!?;: ") in IGNORED or len(text.strip(".,!?;: ")) <= 1:
                                return ""

                            return text
            except Exception as e:
                logger.debug(f"Gemini transcription attempt with {cand_model} failed: {e}")
                continue

        return ""

