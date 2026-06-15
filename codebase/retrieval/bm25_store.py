"""
retrieval/bm25_store.py  —  BM25 sparse retrieval (in-memory with Redis persistence)

BM25 captures exact matches for:
  - IT terms: Kubernetes, Docker, nginx, PostgreSQL
  - Ticket IDs: INC-1234, JIRA-567
  - Commands and error codes
  - Acronyms that dense embeddings may miss

Phase 1: loads into memory at startup, persists corpus to Redis for restarts.
"""
from __future__ import annotations

import json
import logging
import pickle
import re
from typing import Dict, List, Optional

import redis
from rank_bm25 import BM25Okapi

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# BM25 params from LLD spec: b=0.75, k1=1.2
BM25_B = 0.75
BM25_K1 = 1.2

# Simple English stopwords (lightweight, no NLTK needed)
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "to", "was",
    "were", "will", "with", "this", "have", "been", "they", "what", "when",
    "where", "which", "who", "how", "but", "not", "can", "if", "than", "then",
    "some", "more", "also", "into", "your", "our", "we", "their", "about",
}


def _tokenize(text: str) -> List[str]:
    """
    BM25 tokenizer: lowercase, split on non-alphanumeric, remove stopwords.
    Preserves technical terms, commands, IDs intact.
    """
    text = text.lower()
    tokens = re.findall(r'[a-z0-9][\w\-\.]*', text)
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


class BM25Store:
    """
    In-memory BM25 index.
    corpus_meta: list of {chunk_id, doc_id, doc_origin, priority_tier, ...}
    """

    REDIS_KEY = "bm25:corpus"

    def __init__(self):
        self._corpus_meta: List[Dict] = []
        self._tokenized_corpus: List[List[str]] = []
        self._bm25: Optional[BM25Okapi] = None

        # Redis for persistence
        try:
            self._redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                socket_connect_timeout=2,
            )
            self._redis.ping()
            self._redis_enabled = True
        except Exception:
            self._redis_enabled = False

        self._try_load_from_redis()

    def _try_load_from_redis(self):
        """Load previously built BM25 index from Redis on startup."""
        if not self._redis_enabled:
            return
        try:
            data = self._redis.get(self.REDIS_KEY)
            if data:
                saved = pickle.loads(data)
                self._corpus_meta = saved["meta"]
                self._tokenized_corpus = saved["tokens"]
                self._bm25 = BM25Okapi(
                    self._tokenized_corpus, k1=BM25_K1, b=BM25_B
                )
                logger.info(f"BM25 index loaded from Redis ({len(self._corpus_meta)} docs)")
        except Exception as e:
            logger.warning(f"BM25 Redis load failed: {e}")

    def add_documents(self, chunks: List[Dict]):
        """
        Add chunks to BM25 index.
        Each chunk dict must have: chunk_id, content, doc_id, doc_origin, priority_tier.
        P3: also carries repository / access_roles / project_id / uploaded_by /
        section_hierarchy / page_number / created_at so BM25 metadata stays in
        sync with the Qdrant payload for the same chunk.
        """
        for chunk in chunks:
            tokens = _tokenize(chunk.get("content", ""))
            self._tokenized_corpus.append(tokens)
            self._corpus_meta.append({
                "chunk_id": chunk["chunk_id"],
                "doc_id": chunk.get("doc_id", ""),
                "content": chunk.get("content", ""),
                "section_title": chunk.get("section_title", ""),
                "section_hierarchy": chunk.get("section_hierarchy", []),
                "page_number": chunk.get("page_number", 0),
                "doc_type": chunk.get("doc_type", ""),
                "department": chunk.get("department", ""),
                "doc_origin": chunk.get("doc_origin", "INTERNAL"),
                "priority_tier": chunk.get("priority_tier", 1),
                "source_file": chunk.get("source_file", ""),
                "repository": chunk.get("repository", ""),
                "access_roles": chunk.get("access_roles", []),
                "project_id": chunk.get("project_id", ""),
                "uploaded_by": chunk.get("uploaded_by", ""),
                "created_at": chunk.get("created_at", ""),
                "is_image_chunk": chunk.get("is_image_chunk", False),
                "image_path": chunk.get("image_path", ""),
            })

        # Rebuild BM25 index
        self._bm25 = BM25Okapi(self._tokenized_corpus, k1=BM25_K1, b=BM25_B)
        self._persist_to_redis()
        logger.info(f"BM25 index rebuilt: {len(self._corpus_meta)} total docs")

    def search(
        self,
        query: str,
        top_k: int = 20,
        doc_ids: Optional[List[str]] = None,
        repository: Optional[str] = None,
        department: Optional[str] = None,
    ) -> List[Dict]:
        """
        BM25 search.

        P4 (Document-Specific Retrieval): `doc_ids` / `repository` / `department`
        are HARD scope filters applied BEFORE scoring, mirroring the same scope
        used for the Qdrant dense/question passes at this retrieval level. This
        keeps BM25 candidates from a different document/repository/department
        out of the RRF fusion when the caller has explicitly scoped retrieval.

        Returns list of {chunk_id, score, payload} sorted by score desc.
        """
        if not self._bm25 or not self._corpus_meta:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        # Build the candidate index set, applying scope filters first so that
        # top_k is computed over the IN-SCOPE corpus only.
        if doc_ids or repository or department:
            doc_id_set = set(doc_ids) if doc_ids else None
            in_scope_idx = []
            for i, meta in enumerate(self._corpus_meta):
                if doc_id_set is not None and meta.get("doc_id") not in doc_id_set:
                    continue
                if repository is not None and meta.get("repository") != repository:
                    continue
                if department is not None and meta.get("department") != department:
                    continue
                in_scope_idx.append(i)
        else:
            in_scope_idx = range(len(self._corpus_meta))

        top_indices = sorted(in_scope_idx, key=lambda i: scores[i], reverse=True)[:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                meta = self._corpus_meta[idx]
                results.append({
                    "chunk_id": meta["chunk_id"],
                    "score": float(scores[idx]),
                    "payload": meta,
                })

        return results

    def _persist_to_redis(self):
        if not self._redis_enabled:
            return
        try:
            data = pickle.dumps({
                "meta": self._corpus_meta,
                "tokens": self._tokenized_corpus,
            })
            self._redis.set(self.REDIS_KEY, data)  # no TTL — persist across restarts
        except Exception as e:
            logger.warning(f"BM25 Redis persist failed: {e}")

    @property
    def doc_count(self) -> int:
        return len(self._corpus_meta)

    def clear(self):
        """Wipe the in-memory BM25 index and its Redis-persisted copy."""
        self._corpus_meta = []
        self._tokenized_corpus = []
        self._bm25 = None
        if self._redis_enabled:
            try:
                self._redis.delete(self.REDIS_KEY)
            except Exception as e:
                logger.warning(f"BM25 Redis clear failed: {e}")