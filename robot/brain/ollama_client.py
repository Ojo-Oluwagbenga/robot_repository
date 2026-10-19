"""
Ollama HTTP Client for Robot Brain.
Handles robust communication with the Ollama server on the PC host,
with automatic fallback to localhost, endpoint health checks, and streaming support.
"""

import json
import logging
from typing import Any, Callable, Dict, Generator, List, Optional
import requests

try:
    from robot.brain.config import (
        CANDIDATE_OLLAMA_URLS,
        DEFAULT_MODEL,
        FALLBACK_OLLAMA_URL,
        MAX_OUTPUT_TOKENS,
        NUM_CTX,
        NUM_THREADS,
        PRIMARY_OLLAMA_URL,
        REQUEST_TIMEOUT,
        TEMPERATURE,
        TOP_P,
    )
except ImportError:
    from brain.config import (
        CANDIDATE_OLLAMA_URLS,
        DEFAULT_MODEL,
        FALLBACK_OLLAMA_URL,
        MAX_OUTPUT_TOKENS,
        NUM_CTX,
        NUM_THREADS,
        PRIMARY_OLLAMA_URL,
        REQUEST_TIMEOUT,
        TEMPERATURE,
        TOP_P,
    )

logger = logging.getLogger("robot.brain.ollama")


class OllamaClient:
    """Robust client for querying Ollama LLM on local network or localhost."""

    def __init__(
        self,
        primary_url: Optional[str] = None,
        fallback_url: Optional[str] = None,
        candidate_urls: Optional[List[str]] = None,
        model: str = DEFAULT_MODEL,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self.primary_url = (primary_url or PRIMARY_OLLAMA_URL).rstrip("/")
        self.fallback_url = (fallback_url or FALLBACK_OLLAMA_URL).rstrip("/")
        self.model = model
        self.timeout = timeout

        # Build prioritized list of candidate URLs
        base_candidates = list(candidate_urls or CANDIDATE_OLLAMA_URLS)
        if self.primary_url not in base_candidates:
            base_candidates.insert(0, self.primary_url)
        if self.fallback_url not in base_candidates:
            base_candidates.append(self.fallback_url)

        # Deduplicate while preserving priority order
        self.candidate_urls: List[str] = []
        for u in base_candidates:
            if u:
                clean_u = u.rstrip("/")
                if clean_u not in self.candidate_urls:
                    self.candidate_urls.append(clean_u)

        self.active_url: Optional[str] = None

        # Resolve working endpoint on initialization
        self._detect_active_endpoint()

    def _detect_active_endpoint(self) -> str:
        """
        Probe candidate URLs in priority order:
        1. Custom specified ROBOT_OLLAMA_URL / primary_url
        2. Direct PC Wi-Fi IP (e.g. 10.82.240.172:11434)
        3. Hostname mDNS (DESKTOP-2CANEO2.local:11434)
        4. Localhost loopback (127.0.0.1:11434)
        """
        for url in self.candidate_urls:
            try:
                resp = requests.get(f"{url}/api/tags", timeout=2.5)
                if resp.status_code == 200:
                    self.active_url = url
                    logger.info(f"Ollama connected successfully at {self.active_url}")
                    return url
            except Exception as e:
                logger.debug(f"Could not connect to Ollama at {url}: {e}")

        # Default to primary candidate if none responded
        self.active_url = self.candidate_urls[0] if self.candidate_urls else self.primary_url
        logger.warning(
            f"Ollama server not immediately detected on any candidate URL: {self.candidate_urls}. "
            f"Defaulting to {self.active_url}. Ensure Ollama is running with OLLAMA_HOST=0.0.0.0:11434."
        )
        return self.active_url

    def is_online(self) -> bool:
        """Check if Ollama server is currently reachable."""
        try:
            url = self.active_url or self.primary_url
            resp = requests.get(f"{url}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        stream: bool = False,
        _attempted_urls: Optional[List[str]] = None,
    ) -> str:
        """
        Send request to /api/generate (Direct match with user's requests.post snippet).
        """
        if _attempted_urls is None:
            _attempted_urls = []
        _attempted_urls.append(self.active_url)

        url = f"{self.active_url}/api/generate"
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "num_thread": NUM_THREADS,
                "num_ctx": NUM_CTX,
                "num_predict": MAX_OUTPUT_TOKENS,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
            },
        }
        if system:
            payload["system"] = system

        try:
            response = requests.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
        except requests.exceptions.RequestException as e:
            # Try remaining candidates if any
            for alt_url in self.candidate_urls:
                if alt_url not in _attempted_urls:
                    logger.warning(
                        f"Generate failed on {self.active_url}. Retrying candidate {alt_url}..."
                    )
                    self.active_url = alt_url
                    return self.generate(
                        prompt,
                        system=system,
                        model=model,
                        stream=stream,
                        _attempted_urls=_attempted_urls,
                    )
            logger.error(f"Ollama generate request failed across all candidates ({_attempted_urls}): {e}")
            raise

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        stream: bool = False,
        on_chunk: Optional[Callable[[str], None]] = None,
        _attempted_urls: Optional[List[str]] = None,
    ) -> str:
        """
        Send conversational messages to /api/chat.
        Supports real-time streaming with an optional on_chunk callback.
        """
        if _attempted_urls is None:
            _attempted_urls = []
        _attempted_urls.append(self.active_url)

        url = f"{self.active_url}/api/chat"
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "num_thread": NUM_THREADS,
                "num_ctx": NUM_CTX,
                "num_predict": MAX_OUTPUT_TOKENS,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
            },
        }

        try:
            if not stream:
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                return data.get("message", {}).get("content", "")

            # Streaming mode
            full_response = []
            with requests.post(url, json=payload, timeout=self.timeout, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                        token = chunk.get("message", {}).get("content", "")
                        if token:
                            full_response.append(token)
                            if on_chunk:
                                on_chunk(token)
                    except json.JSONDecodeError:
                        continue

            return "".join(full_response)

        except requests.exceptions.RequestException as e:
            # Try remaining candidates if any
            for alt_url in self.candidate_urls:
                if alt_url not in _attempted_urls:
                    logger.warning(
                        f"Chat request failed on {self.active_url}. Retrying candidate {alt_url}..."
                    )
                    self.active_url = alt_url
                    return self.chat(
                        messages,
                        model=model,
                        stream=stream,
                        on_chunk=on_chunk,
                        _attempted_urls=_attempted_urls,
                    )
            logger.error(f"Ollama chat request failed across all candidates ({_attempted_urls}): {e}")
            raise
