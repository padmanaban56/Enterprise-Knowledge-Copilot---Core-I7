"""
api/feedback_service.py  —  Feedback Learning Loop

Enterprise RAG systems must improve over time. This service:

1. Captures explicit user feedback (thumbs up/down + free text)
2. Captures implicit signals (follow-up questions = poor answer)
3. Identifies which chunks were CITED in good vs bad answers
4. Builds a feedback-weighted boost table that nudges retrieval
5. Surfaces patterns: queries that always get negative feedback

The boost table works by adding a small score multiplier to chunks
that have been cited in positively-rated answers. This is applied
in the Context Builder after reranking.

Storage: PostgreSQL feedback table (added in migration_v3.sql)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class FeedbackService:
    """
    Collects user feedback and computes chunk-level boost signals.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)
        self._boost_cache: Dict[str, float] = {}
        self._boost_cache_ts: Optional[datetime] = None

    def record_feedback(
        self,
        session_id: str,
        query_text: str,
        rating: int,               # 1=thumbs up, -1=thumbs down, 0=neutral
        comment: Optional[str],
        cited_chunk_ids: List[str],
        repositories_used: List[str],
        confidence: float,
    ) -> bool:
        """Persist user feedback and update chunk boost signals."""
        try:
            with self.engine.connect() as conn:
                conn.execute(text(
                    "INSERT INTO user_feedback "
                    "(session_id, query_text, rating, comment, cited_chunk_ids, "
                    "repositories_used, confidence, created_at) "
                    "VALUES (:sid, :q, :r, :c, :chunks, :repos, :conf, NOW())"
                ), {
                    "sid": session_id,
                    "q": query_text[:500],
                    "r": rating,
                    "c": comment,
                    "chunks": cited_chunk_ids,
                    "repos": repositories_used,
                    "conf": confidence,
                })
                conn.commit()

            # Update chunk boost signals asynchronously
            if cited_chunk_ids and rating != 0:
                self._update_chunk_boosts(cited_chunk_ids, rating)

            logger.info(f"Feedback recorded: rating={rating}, chunks={len(cited_chunk_ids)}")
            return True
        except Exception as e:
            logger.error(f"Feedback record failed: {e}")
            return False

    def _update_chunk_boosts(self, chunk_ids: List[str], rating: int):
        """
        Update per-chunk feedback boost scores.
        Positive rating: boost += 0.02 (max +0.10)
        Negative rating: boost -= 0.01 (min -0.05)
        """
        delta = 0.02 if rating > 0 else -0.01
        try:
            with self.engine.connect() as conn:
                for chunk_id in chunk_ids:
                    conn.execute(text(
                        "INSERT INTO chunk_feedback_boosts (chunk_id, boost_score, feedback_count) "
                        "VALUES (:cid, :delta, 1) "
                        "ON CONFLICT (chunk_id) DO UPDATE SET "
                        "boost_score = GREATEST(-0.05, LEAST(0.10, "
                        "  chunk_feedback_boosts.boost_score + :delta)), "
                        "feedback_count = chunk_feedback_boosts.feedback_count + 1, "
                        "updated_at = NOW()"
                    ), {"cid": chunk_id, "delta": delta})
                conn.commit()
            # Invalidate cache
            self._boost_cache_ts = None
        except Exception as e:
            logger.debug(f"Chunk boost update failed: {e}")

    def get_chunk_boost(self, chunk_id: str) -> float:
        """
        Get the learned feedback boost for a specific chunk.
        Returns 0.0 if no feedback data exists.
        Used by Context Builder to adjust final scores.
        """
        # Refresh cache every 5 minutes
        now = datetime.utcnow()
        cache_stale = (
            self._boost_cache_ts is None
            or (now - self._boost_cache_ts).seconds > 300
        )

        if cache_stale:
            self._refresh_boost_cache()

        return self._boost_cache.get(chunk_id, 0.0)

    def _refresh_boost_cache(self):
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT chunk_id, boost_score FROM chunk_feedback_boosts "
                    "WHERE ABS(boost_score) > 0.001"
                ))
                self._boost_cache = {r.chunk_id: r.boost_score for r in rows}
                self._boost_cache_ts = datetime.utcnow()
        except Exception as e:
            logger.debug(f"Boost cache refresh failed: {e}")

    def get_feedback_summary(self, days: int = 7) -> Dict:
        """Summary statistics for the feedback dashboard."""
        try:
            with self.engine.connect() as conn:
                stats = conn.execute(text(
                    "SELECT COUNT(*) as total, "
                    "SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as positive, "
                    "SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) as negative, "
                    "AVG(confidence) as avg_confidence "
                    "FROM user_feedback WHERE created_at > NOW() - :days * INTERVAL '1 day'"
                ), {"days": days}).fetchone()

                negative_queries = conn.execute(text(
                    "SELECT query_text, COUNT(*) as count "
                    "FROM user_feedback WHERE rating = -1 "
                    "AND created_at > NOW() - :days * INTERVAL '1 day' "
                    "GROUP BY query_text ORDER BY count DESC LIMIT 5"
                ), {"days": days})

                s = dict(stats._mapping) if stats else {}
                total = int(s.get("total") or 0)
                pos = int(s.get("positive") or 0)

                return {
                    "total_feedback": total,
                    "positive": pos,
                    "negative": int(s.get("negative") or 0),
                    "satisfaction_rate": round(pos / total, 3) if total else 0,
                    "avg_confidence_on_feedback": round(float(s.get("avg_confidence") or 0), 3),
                    "top_negative_queries": [
                        {"query": r.query_text, "count": r.count}
                        for r in negative_queries
                    ],
                }
        except Exception as e:
            logger.error(f"Feedback summary failed: {e}")
            return {"total_feedback": 0, "positive": 0, "negative": 0, "satisfaction_rate": 0}
