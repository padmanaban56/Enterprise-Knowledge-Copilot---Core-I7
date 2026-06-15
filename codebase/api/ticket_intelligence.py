"""
api/ticket_intelligence.py  —  Ticket Intelligence Layer

Adds on top of existing TicketRetriever:
  - Known Issue Detection (cluster similar tickets by subject keywords)
  - Resolution Mining (find most common resolution for a symptom)
  - Resolution Frequency stats
  - Related ticket suggestions
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Dict, List, Optional

from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class TicketIntelligence:
    """
    Layered ticket analysis on top of PostgreSQL tickets table.
    Mines patterns from the existing 2000+ ticket corpus.
    """

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)

    def find_known_issue(self, query: str, category: str = None) -> Optional[Dict]:
        """
        Check if query matches a known recurring issue.
        Returns the most common resolution if found.
        """
        # Extract key symptom words from query
        symptoms = self._extract_symptoms(query)
        if not symptoms:
            return None

        with self.engine.connect() as conn:
            # Find tickets matching symptom keywords via full-text search
            sql = text(
                "SELECT ticket_id, subject, description, resolution, priority, category, status "
                "FROM tickets "
                "WHERE fts_vector @@ plainto_tsquery('english', :query) "
                + ("AND category = :cat " if category else "")
                + "ORDER BY ts_rank(fts_vector, plainto_tsquery('english', :query)) DESC "
                "LIMIT 20"
            )
            params = {"query": " ".join(symptoms)}
            if category:
                params["cat"] = category

            rows = [dict(r._mapping) for r in conn.execute(sql, params)]

        if not rows:
            return None

        # Mine most common resolution
        resolutions = [r["resolution"] for r in rows if r.get("resolution")]
        resolved_count = len(resolutions)
        total_count = len(rows)

        if not resolutions:
            return {
                "found": True,
                "ticket_count": total_count,
                "resolution_rate": 0.0,
                "common_resolution": None,
                "similar_tickets": rows[:5],
                "categories": self._count_categories(rows),
            }

        # Find most common resolution pattern
        common_resolution = self._find_common_resolution(resolutions)
        resolution_rate = resolved_count / total_count if total_count else 0

        return {
            "found": True,
            "ticket_count": total_count,
            "resolved_count": resolved_count,
            "resolution_rate": round(resolution_rate, 2),
            "common_resolution": common_resolution,
            "similar_tickets": rows[:5],
            "categories": self._count_categories(rows),
            "priorities": self._count_priorities(rows),
        }

    def get_resolution_stats(self, category: str) -> Dict:
        """Get resolution statistics for a ticket category."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT status, COUNT(*) as count "
                "FROM tickets WHERE category = :cat "
                "GROUP BY status"
            ), {"cat": category})
            status_counts = {r.status: r.count for r in rows}

            total = sum(status_counts.values())
            resolved = status_counts.get("resolved", 0) + status_counts.get("closed", 0)

            return {
                "category": category,
                "total": total,
                "resolved": resolved,
                "resolution_rate": round(resolved / total, 2) if total else 0,
                "status_breakdown": status_counts,
            }

    def search_tickets(
        self,
        query: str,
        category: str = None,
        priority: str = None,
        limit: int = 10,
    ) -> Dict:
        """
        Enhanced ticket search: FTS + intelligence overlay.
        Returns tickets + known issue context if found.
        """
        with self.engine.connect() as conn:
            base_sql = (
                "SELECT ticket_id, subject, description, priority, category, "
                "status, resolution, created_at, "
                "ts_rank(fts_vector, plainto_tsquery('english', :q)) as rank "
                "FROM tickets "
                "WHERE fts_vector @@ plainto_tsquery('english', :q) "
            )
            params = {"q": query, "limit": limit}
            filters = []
            if category:
                filters.append("AND category = :cat")
                params["cat"] = category
            if priority:
                filters.append("AND priority = :priority")
                params["priority"] = priority

            sql = text(base_sql + " ".join(filters) + " ORDER BY rank DESC LIMIT :limit")
            tickets = [dict(r._mapping) for r in conn.execute(sql, params)]

        # Overlay known issue intelligence
        known_issue = self.find_known_issue(query, category)

        return {
            "tickets": tickets,
            "total_found": len(tickets),
            "known_issue": known_issue,
            "query": query,
        }

    def get_recent_tickets(self, limit: int = 10, category: str = None) -> List[Dict]:
        """Get most recent tickets, optionally filtered by category."""
        with self.engine.connect() as conn:
            if category:
                rows = conn.execute(text(
                    "SELECT ticket_id, subject, priority, category, status, created_at "
                    "FROM tickets WHERE category = :cat ORDER BY created_at DESC LIMIT :limit"
                ), {"cat": category, "limit": limit})
            else:
                rows = conn.execute(text(
                    "SELECT ticket_id, subject, priority, category, status, created_at "
                    "FROM tickets ORDER BY created_at DESC LIMIT :limit"
                ), {"limit": limit})
            return [dict(r._mapping) for r in rows]

    def get_category_breakdown(self) -> List[Dict]:
        """Aggregate ticket counts by category for dashboard."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT category, COUNT(*) as total, "
                "SUM(CASE WHEN status IN ('resolved','closed') THEN 1 ELSE 0 END) as resolved "
                "FROM tickets GROUP BY category ORDER BY total DESC LIMIT 10"
            ))
            return [dict(r._mapping) for r in rows]

    def get_total_count(self) -> int:
        with self.engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM tickets"))
            return result.scalar() or 0

    # ── Private helpers ────────────────────────────────────────────────────────
    def _extract_symptoms(self, query: str) -> List[str]:
        """Extract meaningful symptom words from query for matching."""
        stop = {"the", "a", "an", "is", "are", "was", "were", "i", "my", "our",
                "can", "cant", "cannot", "not", "have", "has", "been", "for",
                "with", "about", "when", "how", "what", "why", "where", "please",
                "help", "issue", "problem", "error"}
        words = re.findall(r'\b[a-z]{3,}\b', query.lower())
        return [w for w in words if w not in stop][:8]

    def _find_common_resolution(self, resolutions: List[str]) -> str:
        """Find the most representative resolution text."""
        if not resolutions:
            return ""
        # Use the shortest non-trivial resolution as representative
        # (short = likely actionable summary)
        candidates = [r for r in resolutions if 20 < len(r) < 500]
        if not candidates:
            return resolutions[0][:300]
        # Return most common short resolution
        return sorted(candidates, key=len)[0]

    def _count_categories(self, tickets: List[Dict]) -> Dict[str, int]:
        return dict(Counter(t.get("category", "Unknown") for t in tickets))

    def _count_priorities(self, tickets: List[Dict]) -> Dict[str, int]:
        return dict(Counter(t.get("priority", "Unknown") for t in tickets))
