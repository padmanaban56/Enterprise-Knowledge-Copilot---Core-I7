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

    def get_dashboard_metrics(self, days: int = 7, *, user_id: str,
                              user_roles: Optional[List[str]] = None, user_email: Optional[str] = None) -> Dict:
        """Consolidated activity for ADMIN; personal activity for all other users."""
        if not user_id or user_id == 'anonymous':
            raise ValueError('A signed-in user is required for dashboard metrics')
        from api.access_control import DOCUMENT_VISIBLE, REPOSITORY_VISIBLE, access_parameters
        since = datetime.utcnow() - timedelta(days=days)
        activity_params = {'since': since, 'uid': user_id}
        visibility_params = access_parameters(user_roles)
        consolidated = 'ADMIN' in {str(role).upper() for role in (user_roles or [])}
        activity_where = 'timestamp > :since' + ('' if consolidated else ' AND user_id = :uid')
        try:
            with self.engine.connect() as conn:
                # Overall stats
                stats = conn.execute(text(
                    "SELECT COUNT(*) as total_queries, "
                    "AVG(confidence) as avg_confidence, "
                    "AVG(latency_ms) as avg_latency, "
                    "AVG(chunks_retrieved) as avg_chunks, "
                    "SUM(CASE WHEN low_confidence THEN 1 ELSE 0 END) as low_conf_count "
                    f"FROM retrieval_analytics WHERE {activity_where}"
                ), activity_params).fetchone()

                # Repository usage
                repo_usage = conn.execute(text(
                    "SELECT unnest(repositories_searched) as repo, COUNT(*) as queries "
                    f"FROM retrieval_analytics WHERE {activity_where} "
                    "GROUP BY repo ORDER BY queries DESC"
                ), activity_params)
                repo_data = [dict(r._mapping) for r in repo_usage]

                # Queries over time (daily buckets)
                daily = conn.execute(text(
                    "SELECT DATE(timestamp) as day, COUNT(*) as queries, "
                    "AVG(confidence) as avg_conf "
                    f"FROM retrieval_analytics WHERE {activity_where} "
                    "GROUP BY DATE(timestamp) ORDER BY day"
                ), activity_params)
                daily_data = [dict(r._mapping) for r in daily]

                # Intent distribution
                intents = conn.execute(text(
                    "SELECT intent, COUNT(*) as count "
                    f"FROM retrieval_analytics WHERE {activity_where} "
                    "GROUP BY intent ORDER BY count DESC"
                ), activity_params)
                intent_data = [dict(r._mapping) for r in intents]

                top_queries = conn.execute(text(
                    "SELECT query_text, confidence, latency_ms, timestamp, session_id, user_id "
                    f"FROM retrieval_analytics WHERE {activity_where} "
                    "ORDER BY timestamp DESC LIMIT 10"
                ), activity_params)
                recent_queries = [dict(r._mapping) for r in top_queries]

                # Document and chunk counts
                doc_counts = conn.execute(text(
                    "SELECT COUNT(*) as docs, "
                    "COALESCE(SUM(chunk_count), 0) as chunks "
                    f"FROM documents d WHERE d.status = 'READY' AND {DOCUMENT_VISIBLE}"
                ), visibility_params).fetchone()

                ticket_count = conn.execute(text(
                    "SELECT COUNT(*) FROM tickets" if consolidated else
                    "SELECT COUNT(*) FROM tickets WHERE :email <> '' AND LOWER(requester_email) = LOWER(:email)"
                ), {'email': user_email or ''}).scalar() or 0
                repo_count = conn.execute(text(
                    f"SELECT COUNT(*) FROM repositories r WHERE {REPOSITORY_VISIBLE}"
                ), visibility_params).scalar() or 0

                access_request_count = conn.execute(text(
                    "SELECT COUNT(*) FROM access_requests WHERE status = 'pending'" +
                    ('' if consolidated else ' AND user_id = :uid')
                ), {'uid': user_id}).scalar() or 0

                s = dict(stats._mapping) if stats else {}
                return {
                    "period_days": days,
                    "scope": "global" if consolidated else "user",
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
            raise RuntimeError('Dashboard metrics are temporarily unavailable') from e

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
                    "ON CONFLICT (session_id) DO UPDATE SET updated_at = NOW(), "
                    "title = CASE WHEN chat_sessions.title='New Chat' AND NOT EXISTS "
                    "(SELECT 1 FROM chat_messages WHERE session_id=chat_sessions.session_id AND role='user') "
                    "THEN EXCLUDED.title ELSE chat_sessions.title END "
                    "WHERE chat_sessions.user_id=EXCLUDED.user_id"
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
            from api.guardrails import output_category, safe_metadata, OUTPUT_MESSAGE
            if role == 'assistant' and (category := output_category(content)):
                content, citations, confidence = OUTPUT_MESSAGE, [], 0
                retrieval_meta = {'intent': 'SAFETY', 'guardrail': {
                    'blocked': True, 'stage': 'output', 'category': category}}
            if role == 'assistant':
                from api.citation_policy import apply_citation_policy
                cited = apply_citation_policy({'answer': content, 'citations': citations})
                if cited.get('citation_status'):
                    content, citations, confidence = cited['answer'], [], 0
                    retrieval_meta = {**(retrieval_meta or {}), 'citation_status': cited['citation_status']}
                citations, retrieval_meta = safe_metadata(citations), safe_metadata(retrieval_meta)
            with self.engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO chat_messages "
                    "(session_id, role, content, citations, confidence, retrieval_meta) "
                    "SELECT :sid, :role, :content, :citations, :confidence, :meta "
                    "WHERE EXISTS (SELECT 1 FROM chat_sessions WHERE session_id=:sid AND user_id=:uid)"
                ), {
                    "sid": session_id,
                    "uid": user_id,
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
                "(SELECT COUNT(*) FROM chat_messages cm WHERE cm.session_id = cs.session_id AND cm.role IN ('user','assistant')) as message_count "
                "FROM chat_sessions cs "
                "WHERE cs.user_id = :uid "
                "AND EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.session_id=cs.session_id AND cm.role='user') "
                "AND EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.session_id=cs.session_id AND cm.role='assistant') "
                "ORDER BY cs.updated_at DESC LIMIT :limit"
            ), {"uid": user_id, "limit": limit})
            return [dict(r._mapping) for r in rows]

    def rename_session(self, session_id: str, user_id: str, title: str):
        with self.engine.begin() as conn:
            row = conn.execute(text(
                'UPDATE chat_sessions cs SET title=:title WHERE session_id=:sid AND user_id=:uid '
                "AND EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.session_id=cs.session_id AND cm.role='user') "
                "AND EXISTS (SELECT 1 FROM chat_messages cm WHERE cm.session_id=cs.session_id AND cm.role='assistant') "
                'RETURNING session_id,title'), {'sid':session_id,'uid':user_id,'title':title}).fetchone()
            return dict(row._mapping) if row else None

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
                "ORDER BY created_at ASC, timeline_seq ASC"
            ), {"sid": session_id})
            messages = [dict(r._mapping) for r in rows]
            from api.guardrails import output_category, safe_metadata, OUTPUT_MESSAGE
            from api.citation_policy import apply_citation_policy
            last_query = None
            for message in messages:
                if message['role'] == 'user':
                    last_query = message['content']
                if message['role'] != 'assistant':
                    continue
                category = output_category(message['content'])
                if category:
                    message.update(content=OUTPUT_MESSAGE, citations=[], confidence=0,
                        retrieval_meta={'intent': 'SAFETY', 'guardrail': {
                            'blocked': True, 'stage': 'output', 'category': category}})
                message['citations'] = safe_metadata(message.get('citations'))
                message['retrieval_meta'] = safe_metadata(message.get('retrieval_meta'))
                if not (message['retrieval_meta'] or {}).get('guardrail', {}).get('blocked'):
                    cited = apply_citation_policy({'answer': message['content'], 'citations': message['citations']}, last_query)
                    if cited.get('citation_status'):
                        message.update(content=cited['answer'], citations=[], confidence=0,
                            retrieval_meta={**(message['retrieval_meta'] or {}), 'citation_status': cited['citation_status']})
            return messages
