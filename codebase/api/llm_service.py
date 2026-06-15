"""
api/llm_service.py  —  Context Builder + LLM Response Generation

Builds the token-budgeted context window and calls Ollama for generation.
Handles:
  - Max 8 chunks, max 3 per document
  - Citation attachment
  - Low confidence → clarification question (no LLM call)
  - Streaming via generator for SSE
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional

import httpx
import tiktoken

from configs.settings import get_settings
from retrieval.hybrid_engine import RetrievalResult, RetrievedChunk

logger = logging.getLogger(__name__)
settings = get_settings()

_tokenizer = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_tokenizer.encode(text))


@dataclass
class Citation:
    source_file: str
    section_title: str
    page_number: int
    chunk_id: str
    score: float


@dataclass
class LLMResponse:
    answer: str
    citations: List[Citation]
    confidence: float
    chunks_used: int
    low_confidence: bool
    clarification_question: Optional[str] = None


# ── System prompt ──────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """You are the Enterprise Knowledge Copilot, an intelligent assistant for internal enterprise queries.

RULES:
1. Answer ONLY from the provided context chunks. Do not use outside knowledge.
2. If the context doesn't contain enough information, say so clearly.
3. Always cite your sources using [Source N] notation.
4. Be concise and professional. Use bullet points for step-by-step instructions.
5. For IT issues, include actionable steps when available.
6. Never guess or hallucinate. If unsure, say "Based on available documentation..."

FORMAT:
- Lead with the direct answer
- Use bullet points for lists/steps
- Add citations inline: "According to [Source 1]..."
- End with: "📎 Sources: [list source files]"
"""

_CLARIFICATION_TEMPLATES = [
    "I couldn't find confident information for that query. Could you be more specific? For example:\n- Which department or system are you asking about?\n- Is this related to a specific process or tool?\n- Do you have a ticket or incident number?",
    "I found some partial matches but need more context. Could you clarify:\n- What specific problem are you trying to solve?\n- What system or service is involved?\n- Is this urgent or blocking you?",
]


def build_context(
    chunks: List[RetrievedChunk],
    max_tokens: int = 4096,
    max_per_doc: int = 3,
) -> tuple[str, List[Citation]]:
    """
    Build LLM context from retrieved chunks.
    Rules: max 8 chunks, max 3 per document, token budget.
    Returns (context_text, citations).
    """
    doc_count: Dict[str, int] = defaultdict(int)
    selected: List[RetrievedChunk] = []
    used_tokens = 0
    citations: List[Citation] = []

    for chunk in sorted(chunks, key=lambda c: c.final_score, reverse=True):
        if len(selected) >= 8:
            break
        if doc_count[chunk.source_file] >= max_per_doc:
            continue
        chunk_tokens = count_tokens(chunk.content)
        if used_tokens + chunk_tokens > max_tokens:
            continue

        selected.append(chunk)
        doc_count[chunk.source_file] += 1
        used_tokens += chunk_tokens

    # Build context string with source labels
    context_parts = []
    for i, chunk in enumerate(selected, start=1):
        source_label = (
            f"[Source {i}] {chunk.source_file.split('/')[-1]} "
            f"| {chunk.section_title} | Page {chunk.page_number}"
        )
        context_parts.append(f"{source_label}\n{chunk.content}")
        citations.append(Citation(
            source_file=chunk.source_file,
            section_title=chunk.section_title,
            page_number=chunk.page_number,
            chunk_id=chunk.chunk_id,
            score=round(chunk.final_score, 4),
        ))

    return "\n\n---\n\n".join(context_parts), citations


class LLMService:
    """Ollama-backed LLM service with streaming support."""

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model

    async def generate(
        self,
        query: str,
        retrieval_result: RetrievalResult,
        chat_history: Optional[List[Dict]] = None,
    ) -> LLMResponse:
        """Non-streaming generation."""
        chunks = list(retrieval_result.chunks)

        # Low confidence → clarification, no LLM call
        if retrieval_result.low_confidence or len(chunks) < 1:
            q_idx = hash(query) % len(_CLARIFICATION_TEMPLATES)
            return LLMResponse(
                answer=_CLARIFICATION_TEMPLATES[q_idx],
                citations=[],
                confidence=retrieval_result.confidence,
                chunks_used=0,
                low_confidence=True,
                clarification_question=_CLARIFICATION_TEMPLATES[q_idx],
            )

        context, citations = build_context(chunks)
        prompt = self._build_prompt(query, context, chat_history)

        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": _SYSTEM_PROMPT,
                        "stream": False,
                        "options": {
                            "temperature": 0.1,   # low temp = factual, consistent
                            "top_p": 0.9,
                            "num_predict": 1024,
                        },
                    },
                )
                data = resp.json()
                answer = data.get("response", "").strip()

        except Exception as e:
            logger.error(f"Ollama error: {e}")
            answer = (
                "I encountered an error generating a response. "
                "Please check that Ollama is running: `ollama serve`\n"
                f"Model required: `ollama pull {self.model}`"
            )

        return LLMResponse(
            answer=answer,
            citations=citations,
            confidence=retrieval_result.confidence,
            chunks_used=len(citations),
            low_confidence=retrieval_result.low_confidence,
        )

    async def stream_generate(
        self,
        query: str,
        retrieval_result: RetrievalResult,
        chat_history: Optional[List[Dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming generation. Yields SSE-compatible chunks.
        Usage: async for token in llm.stream_generate(...): send_sse(token)
        """
        chunks = list(retrieval_result.chunks)

        if retrieval_result.low_confidence or len(chunks) < 1:
            q_idx = hash(query) % len(_CLARIFICATION_TEMPLATES)
            yield json.dumps({"type": "clarification", "text": _CLARIFICATION_TEMPLATES[q_idx]})
            return

        context, citations = build_context(chunks)
        prompt = self._build_prompt(query, context, chat_history)

        # Yield citations metadata first
        yield json.dumps({
            "type": "citations",
            "citations": [
                {
                    "source": c.source_file.split("/")[-1],
                    "section": c.section_title,
                    "page": c.page_number,
                    "score": c.score,
                }
                for c in citations
            ],
            "confidence": round(retrieval_result.confidence, 3),
        })

        # Stream tokens
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": _SYSTEM_PROMPT,
                        "stream": True,
                        "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 1024},
                    },
                ) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            data = json.loads(line)
                            token = data.get("response", "")
                            if token:
                                yield json.dumps({"type": "token", "text": token})
                            if data.get("done"):
                                yield json.dumps({"type": "done", "confidence": round(retrieval_result.confidence, 3)})
                                break

        except Exception as e:
            logger.error(f"Ollama streaming error: {e}")
            yield json.dumps({"type": "error", "text": "LLM unavailable. Is Ollama running?"})

    def _build_prompt(
        self,
        query: str,
        context: str,
        chat_history: Optional[List[Dict]] = None,
    ) -> str:
        """Build the full prompt with optional chat history."""
        history_text = ""
        if chat_history:
            last_turns = chat_history[-4:]  # last 2 exchanges
            history_parts = []
            for msg in last_turns:
                role = "User" if msg["role"] == "user" else "Assistant"
                history_parts.append(f"{role}: {msg['content'][:300]}")
            history_text = "\n".join(history_parts) + "\n\n"

        return (
            f"{history_text}"
            f"CONTEXT FROM KNOWLEDGE BASE:\n"
            f"{context}\n\n"
            f"---\n"
            f"USER QUESTION: {query}\n\n"
            f"Answer based only on the context above. Include [Source N] citations."
        )

    async def generate_from_context(
        self,
        query: str,
        context: str,
        citations: List[Dict],
        is_low_confidence: bool,
        chat_history: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Called by the new pipeline after Context Builder has assembled the window.
        context : pre-built context text with [Source N] labels
        citations: list of citation dicts from BuiltContext
        """
        if is_low_confidence or not context.strip():
            from api.llm_service import _CLARIFICATION_TEMPLATES
            import random
            msg = random.choice(_CLARIFICATION_TEMPLATES)
            return {"answer": msg, "low_confidence": True}

        prompt = self._build_prompt(query, context, chat_history)
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": _SYSTEM_PROMPT,
                        "stream": False,
                        "options": {"temperature": 0.1, "top_p": 0.9, "num_predict": 1024},
                    },
                )
                data = resp.json()
                answer = data.get("response", "").strip()
        except httpx.TimeoutException:
            logger.error("Ollama request timed out after 180s")
            answer = (
                "The model took too long to respond (over 180s). This usually "
                "means the CPU is under heavy load or the prompt is very long. "
                "Try a shorter/more specific question, or switch to a smaller "
                f"model than `{self.model}`."
            )
        except Exception as e:
            logger.error(f"Ollama error: {e}")
            answer = (
                "LLM unavailable. Ensure Ollama is running: `ollama serve`\n"
                f"Model required: `ollama pull {self.model}`"
            )
        return {"answer": answer, "low_confidence": False}

    async def check_ollama(self) -> bool:
        """Health check for Ollama service."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                models = [m["name"] for m in r.json().get("models", [])]
                return any(self.model.split(":")[0] in m for m in models)
        except Exception:
            return False