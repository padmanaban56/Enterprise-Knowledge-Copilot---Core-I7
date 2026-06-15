"""
api/analytics_service.py  —  Retrieval Analytics & Dashboard Metrics

Tracks every query and builds the admin analytics dashboard:
  - Query volume over time
  - Average confidence and latency
  - Repository usage distribution
  - Low confidence rate
  - Top queries
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AnalyticsService:
    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)

    def log_query(
        self,
        query_text: str,
        intent: str,
        repository_names: List[str],
        expanded_queries: List[str],
        chunks_retrieved: int,
        confidence: float,
        latency_ms: int,
        reranker_top_score: float,
        low_confidence: bool,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """Persist query analytics record."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "INSERT INTO retrieval_analytics "
                    "(query_text, intent, repositories_searched, expanded_queries, "
                    "chunks_retrieved, confidence, latency_ms, reranker_top_score, "
                    "low_confidence, user_id, session_id) "
                    "VALUES (:q, :intent, :repos, :expanded, :chunks, :conf, "
                    ":latency, :top_score, :low_conf, :uid, :sid)"
                ), {
                    "q": query_text[:500],
                    "intent": intent,
                    "repos": repository_names,
                    "expanded": expanded_queries,
                    "chunks": chunks_retrieved,
                    "conf": confidence,
                    "latency": latency_ms,
                    "top_score": reranker_top_score,
                    "low_conf": low_confidence,
                    "uid": user_id,
                    "sid": session_id,
                })
                conn.commit()
        except Exception as e:
            logger.warning(f"Analytics log failed: {e}")

    def get_dashboard_metrics(self, days: int = 7, user_id: Optional[str] = None) -> Dict:
        """Aggregate metrics for the dashboard."""
        since = datetime.utcnow() - timedelta(days=days)
        try:
            with self.engine.connect() as conn:
                # Overall stats
                stats = conn.execute(text(
                    "SELECT COUNT(*) as total_queries, "
                    "AVG(confidence) as avg_confidence, "
                    "AVG(latency_ms) as avg_latency, "
                    "AVG(chunks_retrieved) as avg_chunks, "
                    "SUM(CASE WHEN low_confidence THEN 1 ELSE 0 END) as low_conf_count "
                    "FROM retrieval_analytics WHERE timestamp > :since"
                ), {"since": since}).fetchone()

                # Repository usage
                repo_usage = conn.execute(text(
                    "SELECT unnest(repositories_searched) as repo, COUNT(*) as queries "
                    "FROM retrieval_analytics WHERE timestamp > :since "
                    "GROUP BY repo ORDER BY queries DESC"
                ), {"since": since})
                repo_data = [dict(r._mapping) for r in repo_usage]

                # Queries over time (daily buckets)
                daily = conn.execute(text(
                    "SELECT DATE(timestamp) as day, COUNT(*) as queries, "
                    "AVG(confidence) as avg_conf "
                    "FROM retrieval_analytics WHERE timestamp > :since "
                    "GROUP BY DATE(timestamp) ORDER BY day"
                ), {"since": since})
                daily_data = [dict(r._mapping) for r in daily]

                # Intent distribution
                intents = conn.execute(text(
                    "SELECT intent, COUNT(*) as count "
                    "FROM retrieval_analytics WHERE timestamp > :since "
                    "GROUP BY intent ORDER BY count DESC"
                ), {"since": since})
                intent_data = [dict(r._mapping) for r in intents]

                # Top queries (scoped to user if provided)
                if user_id:
                    top_queries = conn.execute(text(
                        "SELECT query_text, confidence, latency_ms, timestamp, session_id "
                        "FROM retrieval_analytics WHERE timestamp > :since AND user_id = :uid "
                        "ORDER BY timestamp DESC LIMIT 10"
                    ), {"since": since, "uid": user_id})
                else:
                    top_queries = conn.execute(text(
                        "SELECT query_text, confidence, latency_ms, timestamp, session_id "
                        "FROM retrieval_analytics WHERE timestamp > :since "
                        "ORDER BY timestamp DESC LIMIT 10"
                    ), {"since": since})
                recent_queries = [dict(r._mapping) for r in top_queries]

                # Document and chunk counts
                doc_counts = conn.execute(text(
                    "SELECT COUNT(*) as docs, "
                    "COALESCE(SUM(chunk_count), 0) as chunks "
                    "FROM documents WHERE status = 'READY'"
                )).fetchone()

                ticket_count = conn.execute(text("SELECT COUNT(*) FROM tickets")).scalar() or 0
                repo_count = conn.execute(text("SELECT COUNT(*) FROM repositories")).scalar() or 0

                # Access request count (pending) — useful for admin home card
                try:
                    access_request_count = conn.execute(
                        text("SELECT COUNT(*) FROM access_requests WHERE status = 'pending'")
                    ).scalar() or 0
                except Exception:
                    access_request_count = 0

                s = dict(stats._mapping) if stats else {}
                return {
                    "period_days": days,
                    "total_queries": int(s.get("total_queries") or 0),
                    "avg_confidence": round(float(s.get("avg_confidence") or 0), 3),
                    "avg_latency_ms": round(float(s.get("avg_latency") or 0)),
                    "avg_chunks": round(float(s.get("avg_chunks") or 0), 1),
                    "low_confidence_count": int(s.get("low_conf_count") or 0),
                    "low_confidence_rate": round(
                        int(s.get("low_conf_count") or 0) / max(int(s.get("total_queries") or 1), 1), 3
                    ),
                    "repository_usage": repo_data,
                    "daily_queries": daily_data,
                    "intent_distribution": intent_data,
                    "recent_queries": recent_queries,
                    "total_documents": int(doc_counts.docs if doc_counts else 0),
                    "total_chunks": int(doc_counts.chunks if doc_counts else 0),
                    "total_tickets": ticket_count,
                    "total_repositories": repo_count,
                    "access_request_count": access_request_count,
                }
        except Exception as e:
            logger.error(f"Analytics fetch failed: {e}")
            return {
                "error": str(e),
                "total_queries": 0, "avg_confidence": 0,
                "avg_latency_ms": 0, "total_documents": 0,
                "total_chunks": 0, "total_tickets": 0, "total_repositories": 6,
            }

    def get_evaluation_metrics(self) -> Dict:
        """
        Compute retrieval evaluation metrics from analytics history.
        Precision@5, MRR, Hit Rate, Average Confidence, Average Latency.
        """
        try:
            with self.engine.connect() as conn:
                # Use confidence > 0.5 as proxy for "relevant" (no ground truth)
                rows = conn.execute(text(
                    "SELECT confidence, chunks_retrieved, latency_ms, low_confidence "
                    "FROM retrieval_analytics ORDER BY timestamp DESC LIMIT 500"
                ))
                records = [dict(r._mapping) for r in rows]

            if not records:
                return {"message": "No analytics data yet. Run some queries first."}

            confidences = [r["confidence"] or 0 for r in records]
            latencies = [r["latency_ms"] or 0 for r in records]
            low_conf = [r["low_confidence"] for r in records]

            n = len(records)
            hit_count = sum(1 for c in confidences if c >= 0.5)

            return {
                "total_evaluated": n,
                "hit_rate": round(hit_count / n, 3),
                "avg_confidence": round(sum(confidences) / n, 3),
                "avg_latency_ms": round(sum(latencies) / n),
                "low_confidence_rate": round(sum(low_conf) / n, 3),
                "p95_latency_ms": round(sorted(latencies)[int(n * 0.95)]),
                "confidence_distribution": {
                    "high_gte_075": sum(1 for c in confidences if c >= 0.75),
                    "medium_050_075": sum(1 for c in confidences if 0.50 <= c < 0.75),
                    "low_035_050": sum(1 for c in confidences if 0.35 <= c < 0.50),
                    "very_low_lt_035": sum(1 for c in confidences if c < 0.35),
                },
            }
        except Exception as e:
            return {"error": str(e)}


class ChatHistoryService:
    """
    Persists chat sessions and messages per user (chat_sessions / chat_messages
    tables — already defined in db_schema.sql, just never written to before).

    All reads are scoped to a user_id so users only ever see their own
    conversation history.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)

    def ensure_session(self, session_id: str, user_id: Optional[str], first_query: str) -> None:
        """
        Create the chat_sessions row if it doesn't exist yet (titled from the
        first query), and bump updated_at on every turn. No-op for anonymous
        users (user_id is None) since chat_sessions.user_id has no NOT NULL
        constraint but we don't want to clutter history with anonymous rows
        across the table — anonymous sessions are simply not persisted.
        """
        if not user_id:
            return
        try:
            title = (first_query or "New Chat").strip()[:255]
            with self.engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO chat_sessions (session_id, user_id, title) "
                    "VALUES (:sid, :uid, :title) "
                    "ON CONFLICT (session_id) DO UPDATE SET updated_at = NOW()"
                ), {"sid": session_id, "uid": user_id, "title": title})
        except Exception as e:
            logger.warning(f"ensure_session failed: {e}")

    def add_message(
        self,
        session_id: str,
        user_id: Optional[str],
        role: str,
        content: str,
        citations: Optional[list] = None,
        confidence: Optional[float] = None,
        retrieval_meta: Optional[dict] = None,
    ) -> None:
        """Insert one chat message. No-op for anonymous users."""
        if not user_id:
            return
        try:
            import json
            with self.engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO chat_messages "
                    "(session_id, role, content, citations, confidence, retrieval_meta) "
                    "VALUES (:sid, :role, :content, :citations, :confidence, :meta)"
                ), {
                    "sid": session_id,
                    "role": role,
                    "content": content,
                    "citations": json.dumps(citations or []),
                    "confidence": confidence,
                    "meta": json.dumps(retrieval_meta or {}),
                })
        except Exception as e:
            logger.warning(f"add_message failed: {e}")

    def list_sessions(self, user_id: str, limit: int = 50) -> List[Dict]:
        """List a user's chat sessions, most recently updated first."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT cs.session_id, cs.title, cs.created_at, cs.updated_at, "
                "(SELECT COUNT(*) FROM chat_messages cm WHERE cm.session_id = cs.session_id) as message_count "
                "FROM chat_sessions cs "
                "WHERE cs.user_id = :uid "
                "ORDER BY cs.updated_at DESC LIMIT :limit"
            ), {"uid": user_id, "limit": limit})
            return [dict(r._mapping) for r in rows]

    def get_session_messages(self, session_id: str, user_id: str) -> Optional[List[Dict]]:
        """
        Return all messages for a session, but only if it belongs to user_id.
        Returns None if the session doesn't exist or belongs to someone else
        (caller should treat as 403/404).
        """
        with self.engine.connect() as conn:
            owner = conn.execute(text(
                "SELECT user_id FROM chat_sessions WHERE session_id = :sid"
            ), {"sid": session_id}).fetchone()
            if owner is None or str(owner.user_id) != str(user_id):
                return None

            rows = conn.execute(text(
                "SELECT message_id, role, content, citations, confidence, "
                "retrieval_meta, created_at "
                "FROM chat_messages WHERE session_id = :sid "
                "ORDER BY created_at ASC"
            ), {"sid": session_id})
            return [dict(r._mapping) for r in rows]