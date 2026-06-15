"""
api/repository_service.py  —  Knowledge Repository Management

Handles:
  - Repository CRUD and stats
  - Repository routing (map query → likely repositories)
  - Document-to-repository assignment
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Repository keyword routing ────────────────────────────────────────────────
# Maps query keywords → repository name
# Used for repository-aware retrieval routing
_REPO_ROUTING: Dict[str, List[str]] = {
    "HR": [
        "leave", "annual leave", "sick leave", "vacation", "payroll", "salary",
        "onboarding", "offboarding", "hiring", "recruit", "employee", "benefits",
        "performance review", "appraisal", "training", "handbook", "grievance",
        "resignation", "notice period", "carry forward", "maternity", "paternity",
    ],
    "Finance": [
        "invoice", "budget", "expense", "reimbursement", "payment", "tax",
        "purchase order", "po", "accounts payable", "accounts receivable",
        "financial", "billing", "cost", "vendor payment", "audit", "fiscal",
        "travel claim", "allowance", "procurement",
    ],
    "IT": [
        "vpn", "network", "firewall", "dns", "dhcp", "wifi", "internet",
        "laptop", "printer", "server", "access", "password", "reset",
        "ticket", "incident", "outage", "troubleshoot", "install", "software",
        "hardware", "antivirus", "email", "outlook", "teams", "remote",
    ],
    "Engineering": [
        "kubernetes", "docker", "k8s", "deploy", "deployment", "pipeline",
        "cicd", "ci/cd", "gitlab", "github", "git", "api", "microservice",
        "kafka", "redis", "postgres", "database", "monitoring", "grafana",
        "prometheus", "terraform", "ansible", "databricks", "airflow",
        "architecture", "backend", "frontend", "devops", "infrastructure",
    ],
    "Projects": [
        "project", "milestone", "sprint", "scrum", "agile", "roadmap",
        "stakeholder", "delivery", "timeline", "risk", "dependency",
        "kickoff", "status update", "project plan", "charter",
    ],
    "External": [
        "documentation", "public docs", "vendor", "third party",
        "kubernetes docs", "gitlab handbook", "docker docs",
    ],
}

# Department name → Repository name mapping
_DEPT_TO_REPO = {
    "HR": "HR",
    "Finance": "Finance",
    "IT": "IT",
    "Engineering": "Engineering",
    "Projects": "Projects",
    "Operations": "IT",
    "Unknown": None,
}


class RepositoryService:
    """Repository management and query routing."""

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url or settings.postgres_url)
        self._repo_cache: Optional[List[Dict]] = None

    # ── Repository routing ────────────────────────────────────────────────────
    def route_query(self, query: str, departments: List[str] = None) -> List[str]:
        """
        Determine which repositories are most relevant for a query.
        Returns ordered list of repository names (most relevant first).
        """
        lower = query.lower()
        scores: Dict[str, int] = {}

        # Score by keyword matches
        for repo, keywords in _REPO_ROUTING.items():
            score = sum(1 for kw in keywords if kw in lower)
            if score > 0:
                scores[repo] = score

        # Boost from department extraction
        if departments:
            for dept in departments:
                mapped = _DEPT_TO_REPO.get(dept)
                if mapped:
                    scores[mapped] = scores.get(mapped, 0) + 3  # strong boost

        if not scores:
            return []  # no specific routing → search all

        # Return sorted by score, top 2 repositories
        sorted_repos = sorted(scores, key=scores.get, reverse=True)
        return sorted_repos[:2]

    def map_department_to_repo(self, department: str) -> Optional[str]:
        return _DEPT_TO_REPO.get(department)

    # ── Query expansion ────────────────────────────────────────────────────────
    def expand_query(self, query: str, repositories: List[str]) -> List[str]:
        """
        Generate 2-4 expanded query variants using rule-based synonym expansion.
        No LLM call — fast, deterministic, CPU-friendly.
        """
        lower = query.lower()
        expanded = [query]  # original always first

        # Domain-specific expansions
        expansions_map = {
            "vpn": ["vpn access", "remote access", "virtual private network", "vpn connection"],
            "leave": ["annual leave", "leave policy", "time off", "vacation days"],
            "password": ["password reset", "account access", "credential reset"],
            "invoice": ["invoice processing", "invoice approval", "billing document"],
            "deploy": ["deployment process", "release process", "ci/cd pipeline"],
            "onboard": ["employee onboarding", "new hire process", "joining process"],
            "expense": ["expense claim", "expense reimbursement", "travel expense"],
            "access": ["access request", "permission request", "role access"],
            "incident": ["incident response", "incident management", "outage handling"],
            "performance": ["performance review", "appraisal process", "evaluation"],
        }

        for key, synonyms in expansions_map.items():
            if key in lower:
                expanded.extend(synonyms[:3])
                break

        # Add repository-context prefix if single repo
        if len(repositories) == 1:
            repo = repositories[0].lower()
            if repo not in lower:
                expanded.append(f"{repo} {query}")

        # Deduplicate while preserving order
        seen = set()
        result = []
        for q in expanded:
            if q not in seen:
                seen.add(q)
                result.append(q)

        return result[:5]  # max 5 queries

    # ── Repository CRUD ────────────────────────────────────────────────────────
    def get_all(self) -> List[Dict]:
        """Get all repositories with stats.

        Document/chunk counts are computed live from the `documents` table
        (LEFT JOIN) rather than read from the cached `repositories
        .document_count` / `chunk_count` columns — those columns are only
        refreshed when `_persist_document()` runs after an ingest, so they
        can go stale (e.g. bulk/legacy ingests, or rows backfilled directly
        in the DB). Computing live keeps Repositories, Dashboard, and the
        Chat repo picker in sync with what's actually in `documents`.
        """
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT r.repository_id, r.name, r.display_name, r.description, "
                "r.color, r.icon, "
                "COUNT(DISTINCT d.doc_id) AS document_count, "
                "COALESCE(SUM(d.chunk_count), 0) AS chunk_count, "
                "r.last_updated "
                "FROM repositories r "
                "LEFT JOIN documents d ON d.repository_id = r.repository_id "
                "GROUP BY r.repository_id, r.name, r.display_name, r.description, "
                "r.color, r.icon, r.last_updated "
                "ORDER BY r.name"
            ))
            return [dict(r._mapping) for r in rows]

    def get_by_name(self, name: str) -> Optional[Dict]:
        with self.engine.connect() as conn:
            row = conn.execute(text(
                "SELECT * FROM repositories WHERE name = :name"
            ), {"name": name}).fetchone()
            return dict(row._mapping) if row else None

    def get_id_by_name(self, name: str) -> Optional[str]:
        repo = self.get_by_name(name)
        return str(repo["repository_id"]) if repo else None

    def refresh_stats(self):
        """Recompute document/chunk counts for all repositories.

        Inlined rather than calling the `refresh_repository_stats()` Postgres
        function (migration_v2.sql) — that function may not exist on DBs
        bootstrapped without the v2 migration, which would raise
        `UndefinedFunction`. `get_all()` no longer depends on these cached
        columns (it computes counts live), but this is kept for callers that
        still want the cached columns updated.
        """
        with self.engine.connect() as conn:
            conn.execute(text(
                "UPDATE repositories r "
                "SET document_count = (SELECT COUNT(*) FROM documents d WHERE d.repository_id = r.repository_id), "
                "    chunk_count    = (SELECT COALESCE(SUM(d.chunk_count), 0) FROM documents d WHERE d.repository_id = r.repository_id), "
                "    last_updated   = NOW()"
            ))
            conn.commit()

    def get_documents(self, repository_name: str, limit: int = 50) -> List[Dict]:
        """Get documents belonging to a repository."""
        with self.engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT d.doc_id, d.title, d.source_file, d.doc_type, "
                "d.doc_origin, d.chunk_count, d.access_roles, d.ingested_at "
                "FROM documents d "
                "JOIN repositories r ON d.repository_id = r.repository_id "
                "WHERE r.name = :name "
                "ORDER BY d.ingested_at DESC "
                "LIMIT :limit"
            ), {"name": repository_name, "limit": limit})
            return [dict(r._mapping) for r in rows]
