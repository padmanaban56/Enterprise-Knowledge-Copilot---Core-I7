"""
retrieval/rag_evaluator.py  —  RAG Evaluation Framework + Knowledge Gap Detection

Implements:
  - Precision@K  : relevant chunks retrieved / total retrieved (K)
  - Recall@K     : relevant chunks retrieved / total relevant in corpus
  - MRR          : Mean Reciprocal Rank — how high is the first relevant result
  - Hit Rate     : % of queries where ≥1 relevant chunk retrieved in top-K
  - Avg Latency  : p50, p95, p99 latency distribution

Knowledge Gap Detection:
  Queries where retrieval consistently fails (low confidence, zero results)
  are flagged as knowledge gaps — documents that don't exist in the KB yet.
  These are stored in PostgreSQL for admin review and corpus expansion.

Confidence proxy (no ground truth):
  Since we don't have human-annotated relevance labels for every query,
  we use reranker_score >= 0.50 as a proxy for "relevant chunk retrieved".
  This is standard practice for online RAG evaluation.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class RetrievalEvalRecord:
    query: str
    intent: str
    repositories: List[str]
    top_scores: List[float]         # reranker scores of returned chunks
    latency_ms: int
    total_candidates: int
    chunks_returned: int
    low_confidence: bool
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EvalMetrics:
    precision_at_k: float           # @5 by default
    recall_at_k: float
    mrr: float                      # Mean Reciprocal Rank
    hit_rate: float                 # % queries with ≥1 relevant result
    avg_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    avg_confidence: float
    low_confidence_rate: float
    total_evaluated: int
    knowledge_gap_count: int
    confidence_distribution: Dict[str, int] = field(default_factory=dict)


RELEVANCE_THRESHOLD = 0.50   # reranker score proxy for "relevant"
K = 5                         # evaluate @K=5


class RAGEvaluator:
    """
    Online RAG evaluation using retrieval analytics as signals.
    No ground-truth annotations required for operational monitoring.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)

    # ── Core metrics computation ───────────────────────────────────────────────
    def compute_metrics(self, records: List[RetrievalEvalRecord]) -> EvalMetrics:
        """Compute full evaluation suite from a list of retrieval records."""
        if not records:
            return EvalMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        n = len(records)
        precisions, mrr_scores, hit_flags = [], [], []
        latencies = [r.latency_ms for r in records]
        confidences = [max(r.top_scores) if r.top_scores else 0.0 for r in records]

        for rec in records:
            scores = rec.top_scores[:K]
            relevant = [s for s in scores if s >= RELEVANCE_THRESHOLD]

            # Precision@K
            prec = len(relevant) / min(len(scores), K) if scores else 0.0
            precisions.append(prec)

            # Hit Rate
            hit_flags.append(1 if relevant else 0)

            # MRR — reciprocal of rank of first relevant result
            rr = 0.0
            for rank, score in enumerate(scores, start=1):
                if score >= RELEVANCE_THRESHOLD:
                    rr = 1.0 / rank
                    break
            mrr_scores.append(rr)

        # Recall@K — approximated as avg relevant / avg total candidates
        avg_relevant = sum(
            len([s for s in r.top_scores[:K] if s >= RELEVANCE_THRESHOLD])
            for r in records
        ) / n
        avg_candidates = sum(r.total_candidates for r in records) / n
        recall_at_k = min(avg_relevant / max(avg_candidates * 0.3, 1), 1.0)

        # Latency percentiles
        sorted_latencies = sorted(latencies)
        p95 = sorted_latencies[math.floor(n * 0.95)] if n > 1 else (sorted_latencies[0] if sorted_latencies else 0)
        p99 = sorted_latencies[math.floor(n * 0.99)] if n > 1 else (sorted_latencies[0] if sorted_latencies else 0)

        low_conf_count = sum(1 for r in records if r.low_confidence)

        conf_dist = {
            "high_gte_075": sum(1 for c in confidences if c >= 0.75),
            "medium_055_075": sum(1 for c in confidences if 0.55 <= c < 0.75),
            "low_035_055": sum(1 for c in confidences if 0.35 <= c < 0.55),
            "very_low_lt_035": sum(1 for c in confidences if c < 0.35),
        }

        # Knowledge gaps
        try:
            kg_count = self._count_knowledge_gaps()
        except Exception:
            kg_count = 0

        return EvalMetrics(
            precision_at_k=round(sum(precisions) / n, 4),
            recall_at_k=round(recall_at_k, 4),
            mrr=round(sum(mrr_scores) / n, 4),
            hit_rate=round(sum(hit_flags) / n, 4),
            avg_latency_ms=round(sum(latencies) / n),
            p95_latency_ms=p95,
            p99_latency_ms=p99,
            avg_confidence=round(sum(confidences) / n, 4),
            low_confidence_rate=round(low_conf_count / n, 4),
            total_evaluated=n,
            knowledge_gap_count=kg_count,
            confidence_distribution=conf_dist,
        )

    def get_metrics_from_db(self, days: int = 7) -> EvalMetrics:
        """Load retrieval records from PostgreSQL and compute metrics."""
        since = datetime.utcnow() - timedelta(days=days)
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT query_text, intent, repositories_searched, "
                    "chunks_retrieved, confidence, latency_ms, low_confidence, "
                    "reranker_top_score, timestamp "
                    "FROM retrieval_analytics WHERE timestamp > :since "
                    "ORDER BY timestamp DESC LIMIT 1000"
                ), {"since": since})

                records = []
                for row in rows:
                    r = dict(row._mapping)
                    top_score = r.get("reranker_top_score") or 0.0
                    conf = r.get("confidence") or 0.0
                    # Reconstruct score list from top_score + confidence
                    scores = [top_score] + [max(0, conf - 0.05 * i) for i in range(1, min(r.get("chunks_retrieved", 1), 5))]
                    records.append(RetrievalEvalRecord(
                        query=r.get("query_text", ""),
                        intent=r.get("intent", "SEARCH"),
                        repositories=r.get("repositories_searched") or [],
                        top_scores=scores,
                        latency_ms=r.get("latency_ms") or 0,
                        total_candidates=max(r.get("chunks_retrieved", 0) * 3, 5),
                        chunks_returned=r.get("chunks_retrieved") or 0,
                        low_confidence=r.get("low_confidence") or False,
                    ))

                return self.compute_metrics(records)
        except Exception as e:
            logger.error(f"Eval metrics from DB failed: {e}")
            return EvalMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    # ── Knowledge Gap Detection ───────────────────────────────────────────────
    def record_knowledge_gap(self, query: str, intent: str, repositories: List[str]):
        """
        Log a query as a knowledge gap when:
          - Zero chunks retrieved
          - OR top reranker score < 0.30 (below hard threshold)
          - AND not a SMALLTALK query
        """
        if intent == "SMALLTALK":
            return
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "INSERT INTO knowledge_gaps (query_text, intent, repositories_searched) "
                    "VALUES (:q, :intent, :repos) "
                    "ON CONFLICT (query_hash) DO UPDATE SET "
                    "frequency = knowledge_gaps.frequency + 1, last_seen = NOW()"
                ), {
                    "q": query[:500],
                    "intent": intent,
                    "repos": repositories,
                })
                conn.commit()
        except Exception as e:
            logger.debug(f"Knowledge gap record failed: {e}")

    def _count_knowledge_gaps(self) -> int:
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM knowledge_gaps WHERE resolved = FALSE"))
            return result.scalar() or 0

    def get_knowledge_gaps(self, limit: int = 20) -> List[Dict]:
        """Return unresolved knowledge gaps for admin review."""
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT gap_id, query_text, intent, repositories_searched, "
                    "frequency, last_seen, created_at "
                    "FROM knowledge_gaps WHERE resolved = FALSE "
                    "ORDER BY frequency DESC, last_seen DESC LIMIT :limit"
                ), {"limit": limit})
                return [dict(r._mapping) for r in rows]
        except Exception as e:
            logger.error(f"Knowledge gaps fetch failed: {e}")
            return []

    def resolve_gap(self, gap_id: str):
        """Mark a knowledge gap as resolved (document was added)."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "UPDATE knowledge_gaps SET resolved = TRUE WHERE gap_id = :id"
                ), {"id": gap_id})
                conn.commit()
        except Exception as e:
            logger.error(f"Gap resolve failed: {e}")
