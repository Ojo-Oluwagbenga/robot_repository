"""
Web Search and Real-Time Knowledge Retrieval Module.
Detects live or advanced queries, performs online web searches via DuckDuckGo / Wikipedia,
and uses Ollama to filter and synthesize precise factual answers.
"""

import logging
import re
import urllib.parse
from typing import Any, Callable, Dict, List, Optional
import requests

try:
    from robot.brain.config import (
        ADVANCED_SEARCH_TRIGGERS,
        ENABLE_WEB_SEARCH,
        LIVE_DATA_TRIGGERS,
        SEARCH_MAX_RESULTS,
        SEARCH_TIMEOUT,
    )
except ImportError:
    from brain.config import (
        ADVANCED_SEARCH_TRIGGERS,
        ENABLE_WEB_SEARCH,
        LIVE_DATA_TRIGGERS,
        SEARCH_MAX_RESULTS,
        SEARCH_TIMEOUT,
    )

logger = logging.getLogger("robot.brain.search")


class WebSearcher:
    """Manages real-time internet search and Ollama-based information synthesis."""

    def __init__(self, ollama_filter_fn: Optional[Callable[[str, str], str]] = None):
        """
        Args:
            ollama_filter_fn: Optional callback taking (user_query, raw_snippets)
                              and returning an Ollama-filtered summary string.
        """
        self.ollama_filter_fn = ollama_filter_fn

    def is_live_or_advanced_query(self, query: str) -> bool:
        """
        Determine if the user query requires real-time web data or advanced external facts.
        """
        if not ENABLE_WEB_SEARCH:
            return False

        lower = query.lower().strip()

        # Exclude casual conversational chit-chat from triggering heavy web search
        chitchat_patterns = [
            r"\bhow are you\b", r"\bhow do you feel\b", r"\bwhat are you doing\b",
            r"\bwho are you\b", r"\btell me a\b", r"\bare you (?:happy|sad|ok|fine|alive)\b",
            r"\bhello\b", r"\bhi\b", r"\bhey\b", r"\bgood (?:morning|afternoon|evening|night)\b"
        ]
        for p in chitchat_patterns:
            if re.search(p, lower):
                return False

        # Check explicit live data trigger phrases
        for trigger in LIVE_DATA_TRIGGERS:
            # Word boundary search for precision
            if re.search(r"\b" + re.escape(trigger) + r"\b", lower):
                return True

        # Check advanced knowledge triggers
        for trigger in ADVANCED_SEARCH_TRIGGERS:
            if re.search(r"\b" + re.escape(trigger) + r"\b", lower):
                return True

        # Check question patterns typically requiring live factual verification
        patterns = [
            r"who is the (?:current|new|latest)\b",
            r"what is the (?:latest|current|score|price|status|weather|time)\b",
            r"how much does .+ cost\b",
            r"when (?:is|was) .+ (?:released|announced|launched)\b",
        ]
        for pattern in patterns:
            if re.search(pattern, lower):
                return True

        return False

    def search_duckduckgo(self, query: str, max_results: int = SEARCH_MAX_RESULTS) -> List[Dict[str, str]]:
        """Perform search using the ddgs library."""
        results = []
        try:
            from ddgs import DDGS
            with DDGS(timeout=int(SEARCH_TIMEOUT)) as ddgs:
                ddg_gen = ddgs.text(query, max_results=max_results)
                for item in ddg_gen:
                    results.append({
                        "title": item.get("title", ""),
                        "snippet": item.get("body", ""),
                        "url": item.get("href", ""),
                    })
        except Exception as e:
            logger.warning(f"DuckDuckGo search failed for query '{query}': {e}")
        return results

    def search_wikipedia(self, query: str) -> Optional[Dict[str, str]]:
        """Query Wikipedia REST API for clean encyclopedic summaries."""
        try:
            # Clean common question prefixes to get the entity
            clean_term = re.sub(
                r"^(?:who is|what is|tell me about|explain)\s+", "", query, flags=re.IGNORECASE
            ).strip().rstrip("?.!")
            if not clean_term:
                return None

            encoded = urllib.parse.quote(clean_term)
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}"
            headers = {"User-Agent": "HumanoidRobotBrain/1.0 (Embedded Pi5 Robot)"}
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                extract = data.get("extract", "")
                if extract:
                    return {
                        "title": data.get("title", clean_term),
                        "snippet": extract,
                        "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    }
        except Exception as e:
            logger.debug(f"Wikipedia lookup error for '{query}': {e}")
        return None

    def search(self, query: str) -> List[Dict[str, str]]:
        """
        Execute multi-backend search (DuckDuckGo with Wikipedia fallback).
        """
        # 1. Try DuckDuckGo first for real-time coverage
        results = self.search_duckduckgo(query)
        if results:
            return results

        # 2. Try Wikipedia if DuckDuckGo yielded nothing or is unavailable
        wiki_res = self.search_wikipedia(query)
        if wiki_res:
            return [wiki_res]

        return []

    def get_live_data_and_filter(self, query: str) -> Optional[str]:
        """
        Searches the web for the query, compiles snippets, and uses Ollama
        to extract and synthesize the right answer.
        """
        raw_results = self.search(query)
        if not raw_results:
            logger.info(f"No search results found for query: {query}")
            return None

        # Build raw snippets text
        snippet_blocks = []
        for i, res in enumerate(raw_results, 1):
            snippet_blocks.append(
                f"[{i}] {res['title']}: {res['snippet']} (Source: {res['url']})"
            )
        raw_text = "\n\n".join(snippet_blocks)

        # If an Ollama filter function is provided, use it to distill the facts
        if self.ollama_filter_fn:
            try:
                logger.info(f"Filtering {len(raw_results)} web search results using Ollama...")
                filtered = self.ollama_filter_fn(query, raw_text)
                if filtered and len(filtered.strip()) > 10:
                    return filtered.strip()
            except Exception as e:
                logger.error(f"Error during Ollama web result filtering: {e}")

        # Fallback to direct raw snippets if filtering fails
        return raw_text
