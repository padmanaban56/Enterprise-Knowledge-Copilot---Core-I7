"""
retrieval/hyde_service.py  —  Hypothetical Document Embedding (HyDE)

LLD Step 7 of Query Understanding Pipeline.

HyDE improves dense retrieval for short or vague queries by:
  1. Asking the LLM to generate a HYPOTHETICAL ideal document passage
  2. Embedding THAT passage instead of the raw query
  3. The hypothetical passage lives in document-space, not query-space
     → much closer to real document embeddings → better recall

Triggers:
  - Query < 5 tokens (too short for good embedding)
  - Dense top score < 0.55 (first-pass retrieval is weak)
  - Always on for SUMMARIZE intent (user wants document-level match)

CPU-safe: uses Ollama phi3:mini locally, async, 8s timeout.
Falls back gracefully to original query if Ollama unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Optional

import httpx

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# HyDE prompt — instructs LLM to write a realistic document passage
_HYDE_PROMPT = """You are a document retrieval assistant. Given a user query, write a SHORT (3-5 sentence) passage from an internal enterprise document that would PERFECTLY answer this query.

Write the passage as if it appears in the actual document. Use specific, factual language. Do NOT mention the query itself.

Query: {query}

Document passage:"""


class HyDEService:
    """
    Generates hypothetical document passages for improved dense retrieval.
    Caches results in-memory (LRU-style, max 200 entries) to avoid redundant LLM calls.
    """

    def __init__(self):
        self._cache: dict[str, str] = {}
        self._max_cache = 200

    def _cache_key(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    async def generate(
        self,
        query: str,
        intent: str = "SEARCH",
        token_count: int = 0,
        first_pass_score: float = 1.0,
        force: bool = False,
    ) -> tuple[str, bool]:
        """
        Generate a hypothetical document passage.

        P5: the BROADENED trigger conditions (weak confidence, score below
        threshold, OR ambiguous query — not just "short query") are evaluated
        by HybridRetrievalEngine.retrieve() BEFORE calling this method, which
        passes `force=True` once it has already decided HyDE should run.
        The internal `should_hyde` check below remains as a sane default for
        any OTHER caller (e.g. ad-hoc sub-query HyDE) that invokes this
        service directly without pre-evaluating ambiguity.

        Returns (passage, was_used):
          passage  : the hypothetical text (or original query if skipped/failed)
          was_used : True if HyDE was actually invoked
        """
        # ── Decide whether to invoke HyDE ─────────────────────────────────────
        should_hyde = force or (
            token_count < 5
            or first_pass_score < 0.55
            or intent == "SUMMARIZE"
        )
        if not should_hyde:
            return query, False

        # ── Check cache ───────────────────────────────────────────────────────
        key = self._cache_key(query)
        if key in self._cache:
            logger.debug(f"HyDE cache hit for: {query[:50]}")
            return self._cache[key], True

        # ── Call Ollama ───────────────────────────────────────────────────────
        prompt = _HYDE_PROMPT.format(query=query)
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 150,
                            "stop": ["\n\n", "Query:"],
                        },
                    },
                )
                data = resp.json()
                passage = data.get("response", "").strip()

                # Clean up any leaked prompt fragments
                passage = re.sub(r'^(Document passage:|Passage:)\s*', '', passage, flags=re.IGNORECASE)
                passage = passage.strip()

                if len(passage) < 20:
                    logger.debug("HyDE returned too short, using original query")
                    return query, False

                # Cache with LRU eviction
                if len(self._cache) >= self._max_cache:
                    # Evict oldest key
                    oldest = next(iter(self._cache))
                    del self._cache[oldest]
                self._cache[key] = passage

                logger.info(f"HyDE generated ({len(passage)} chars) for: {query[:50]}")
                return passage, True

        except Exception as e:
            logger.debug(f"HyDE skipped (Ollama unavailable): {e}")
            return query, False

    async def generate_for_sub_queries(
        self, sub_queries: list[str]
    ) -> list[tuple[str, bool]]:
        """Generate HyDE passages for multiple sub-queries in parallel."""
        tasks = [self.generate(q) for q in sub_queries]
        return await asyncio.gather(*tasks)
