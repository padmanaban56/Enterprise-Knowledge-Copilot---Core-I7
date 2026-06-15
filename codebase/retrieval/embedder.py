"""
retrieval/embedder.py  —  Embedding service (CPU-optimized)

Uses BAAI/bge-small-en-v1.5 (384-dim) for Phase 1.
Swap to bge-large-en-v1.5 (1024-dim) if you upgrade RAM later.

BGE models require a special instruction prefix for queries:
  - Documents: no prefix needed
  - Queries:   prefix with "Represent this sentence for searching relevant passages: "
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import List, Optional

import numpy as np
import redis
import json
from sentence_transformers import SentenceTransformer

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# BGE instruction prefix for queries (improves retrieval by ~3-5%)
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingService:
    """
    Singleton embedding service with optional Redis caching.
    Loads model once at startup; subsequent calls are fast.
    """

    _instance: Optional["EmbeddingService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        logger.info(f"Loading embedding model: {settings.embedding_model}")
        self.model = SentenceTransformer(
            settings.embedding_model,
            device="cpu",
        )
        self.dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded. Dimension: {self.dim}")

        # Optional Redis cache
        try:
            self._redis = redis.Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                decode_responses=False,
                socket_connect_timeout=2,
            )
            self._redis.ping()
            self._cache_enabled = True
            logger.info("Redis embedding cache connected")
        except Exception:
            self._cache_enabled = False
            logger.warning("Redis unavailable — embedding cache disabled")

        self._initialized = True

    def _cache_key(self, text: str) -> str:
        h = hashlib.md5(text.encode()).hexdigest()
        return f"emb:{settings.embedding_model}:{h}"

    def embed_documents(self, texts: List[str]) -> np.ndarray:
        """
        Embed document chunks. No prefix for BGE document embeddings.
        Returns ndarray of shape (N, dim).
        """
        if not texts:
            return np.array([])
        return self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=len(texts) > 50,
            normalize_embeddings=True,  # cosine similarity via dot product
        )

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a user query with BGE instruction prefix.
        Checks Redis cache first (session-scoped, 1hr TTL).
        Returns ndarray of shape (dim,).
        """
        cache_key = self._cache_key(BGE_QUERY_PREFIX + query)
        if self._cache_enabled:
            cached = self._redis.get(cache_key)
            if cached:
                return np.frombuffer(cached, dtype=np.float32)

        vec = self.model.encode(
            BGE_QUERY_PREFIX + query,
            normalize_embeddings=True,
        )

        if self._cache_enabled:
            self._redis.setex(cache_key, 3600, vec.astype(np.float32).tobytes())

        return vec

    def embed_questions(self, questions: List[str]) -> np.ndarray:
        """
        Embed hypothetical questions (HyDE vectors) for a chunk.
        Used as secondary vector store per chunk.
        """
        return self.embed_documents(questions)


@lru_cache(maxsize=1)
def get_embedder() -> EmbeddingService:
    return EmbeddingService()
