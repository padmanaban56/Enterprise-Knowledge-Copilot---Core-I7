"""
api/main.py  —  FastAPI Application

Endpoints:
  POST /chat            — main Q&A endpoint (streaming SSE)
  POST /chat/sync       — non-streaming for testing
  POST /ingest/file     — upload + ingest a single file
  POST /ingest/tickets  — ingest tickets.csv
  GET  /status          — system health check
  GET  /docs            — auto OpenAPI docs
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

import psycopg2
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from configs.settings import get_settings
from ingestion.idp_pipeline import (
    Chunk,
    classify_l1,
    chunk_document,
    enrich_chunk_async,
    process_docx,
    process_pdf,
    process_pptx,
)
from ingestion.job_tracker import JobCancelled, JobStatus, get_job_tracker
from retrieval.bm25_store import BM25Store
from retrieval.hybrid_engine import HybridRetrievalEngine, TicketRetriever, RetrievedChunk, _basename
from retrieval.query_understanding import QueryUnderstanding
from retrieval.vector_store import VectorStore
from api.llm_service import LLMService
# ── v2 services ───────────────────────────────────────────────────────────────
from api.repository_service import RepositoryService
from api.confidence_service import compute_confidence
from api.ticket_intelligence import TicketIntelligence
from api.analytics_service import AnalyticsService, ChatHistoryService
from api.feedback_service import FeedbackService
from retrieval.hyde_service import HyDEService
from retrieval.rag_evaluator import RAGEvaluator
from api.agent_orchestrator import AgentOrchestrator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()

# ── Repository stats refresh ──────────────────────────────────────────────────
# Equivalent to calling the `refresh_repository_stats()` Postgres function
# (migration_v2.sql), but inlined as plain SQL: that function is only created
# by running migration_v2.sql, and on some installs (e.g. DBs bootstrapped
# from db_schema.sql / a restored dump without the v2 migration) it doesn't
# exist — calling `SELECT refresh_repository_stats()` then raises
# `UndefinedFunction` and aborts the whole request (e.g. PATCH /documents).
# This inline UPDATE has the same effect and has no such dependency.
_REFRESH_REPO_STATS_SQL = """
    UPDATE repositories r
    SET document_count = (SELECT COUNT(*) FROM documents d WHERE d.repository_id = r.repository_id),
        chunk_count    = (SELECT COALESCE(SUM(d.chunk_count), 0) FROM documents d WHERE d.repository_id = r.repository_id),
        last_updated   = NOW()
"""


def _refresh_repository_stats(conn):
    """Run `_REFRESH_REPO_STATS_SQL` on an open SQLAlchemy connection."""
    from sqlalchemy import text as sql_text
    conn.execute(sql_text(_REFRESH_REPO_STATS_SQL))


# ── Ingestion job tracker (P10: upload progress/cancellation/bulk uploads) ────
# In-memory, process-local — see ingestion/job_tracker.py. Created at import
# time (no dependency on lifespan-initialized services).
job_tracker = get_job_tracker()

# ── Global singletons ─────────────────────────────────────────────────────────
vector_store: Optional[VectorStore] = None
bm25_store: Optional[BM25Store] = None
hybrid_engine: Optional[HybridRetrievalEngine] = None
query_understanding: Optional[QueryUnderstanding] = None
llm_service: Optional[LLMService] = None
ticket_retriever: Optional[TicketRetriever] = None
# v2
repo_service: Optional[RepositoryService] = None
ticket_intelligence: Optional[TicketIntelligence] = None
analytics_service: Optional[AnalyticsService] = None
chat_history_service: Optional[ChatHistoryService] = None
# v3
feedback_service: Optional[FeedbackService] = None
hyde_service: Optional[HyDEService] = None
rag_evaluator: Optional[RAGEvaluator] = None
agent_orchestrator: Optional[AgentOrchestrator] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all services on startup."""
    global vector_store, bm25_store, hybrid_engine, query_understanding, llm_service, ticket_retriever
    global repo_service, ticket_intelligence, analytics_service, chat_history_service
    global feedback_service, hyde_service, rag_evaluator, agent_orchestrator
    logger.info("🚀 Starting Enterprise Knowledge Copilot v3...")

    vector_store = VectorStore()
    logger.info(f"✅ Qdrant: {vector_store.get_collection_info()}")

    bm25_store = BM25Store()
    logger.info(f"✅ BM25 index: {bm25_store.doc_count} docs")

    hyde_service = HyDEService()
    feedback_service = FeedbackService(settings.postgres_url)
    hybrid_engine = HybridRetrievalEngine(vector_store, bm25_store, feedback_service, hyde_service=hyde_service)
    query_understanding = QueryUnderstanding()
    llm_service = LLMService()
    agent_orchestrator = AgentOrchestrator(
        hybrid_engine=hybrid_engine,
        query_understanding=query_understanding,
        ticket_retriever=None,  # set below once ticket_retriever connects
        llm_service=llm_service,
    )

    try:
        ticket_retriever = TicketRetriever(settings.postgres_url)
        ticket_intelligence = TicketIntelligence(settings.postgres_url)
        repo_service = RepositoryService(settings.postgres_url)
        analytics_service = AnalyticsService(settings.postgres_url)
        chat_history_service = ChatHistoryService(settings.postgres_url)
        rag_evaluator = RAGEvaluator(settings.postgres_url)
        agent_orchestrator.ticket_retriever = ticket_retriever
        logger.info("✅ PostgreSQL: all v3 services connected")

        # Self-healing column add: persists the path of the original
        # uploaded file (PDF/DOCX/PPTX) so it can be re-opened/downloaded
        # from the Document Detail page. Uses ADD COLUMN IF NOT EXISTS so
        # it's safe to run on every startup and doesn't require a separate
        # migration step (the previous `refresh_repository_stats()` issue
        # came from relying on a migration that wasn't run).
        from sqlalchemy import create_engine, text as sql_text
        engine = create_engine(settings.postgres_url)
        with engine.connect() as conn:
            conn.execute(sql_text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_path TEXT"))
            conn.commit()
        os.makedirs(settings.uploaded_files_dir, exist_ok=True)
    except Exception as e:
        logger.warning(f"⚠️  PostgreSQL unavailable: {e}")

    ollama_ok = await llm_service.check_ollama()
    logger.info(f"{'✅' if ollama_ok else '⚠️ '} Ollama: {'ready' if ollama_ok else 'offline'} ({settings.ollama_model})")
    logger.info("🎉 Enterprise Knowledge Copilot v3 ready!")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="Enterprise Knowledge Copilot",
    description="Phase 1 — Hybrid RAG with IDP, BM25+Dense retrieval, CrossEncoder reranking",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers (v4: Bundles, Help Center, Admin) ─────────────────────────────────
from api.bundles_help_admin import router as bundles_help_admin_router
app.include_router(bundles_help_admin_router, tags=["bundles", "help-center", "admin"])


# ════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS (LLD §7.1 Authentication Layer)
# ════════════════════════════════════════════════════════════════════════════
from api.auth import (
    CurrentUser, LoginRequest, SSOCallbackRequest, TokenResponse,
    get_auth_service, get_current_user, issue_token, verify_password, require_roles,
)

# ── User management request models ───────────────────────────────────────────
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UpdateRolesRequest(BaseModel):
    role: str
    access_roles: List[str]

class AdminResetPasswordRequest(BaseModel):
    new_password: str

class CreateAccessRequestModel(BaseModel):
    resource_name: str           # e.g. 'FINANCE', 'HR', 'IT_ADMIN'
    justification: str
    resource_type: str = "access_role"

class ResolveAccessRequestModel(BaseModel):
    approve: bool
    rejection_reason: Optional[str] = None


@app.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    auth_service = get_auth_service()
    user_row = auth_service.get_user_by_email(req.email)
    if user_row is None or not user_row.get("is_active", True):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user_row.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    auth_service.touch_last_login(user_row["user_id"])
    auth_service.log_audit(user_id=user_row["user_id"], action="login")
    return issue_token(user_row)


@app.post("/auth/sso/callback", response_model=TokenResponse)
async def sso_callback(req: SSOCallbackRequest):
    """
    Generic SSO callback for Azure AD / Okta / SAML / LDAP / Google.
    The actual token/assertion validation against the IdP happens upstream
    (e.g. in an API gateway or middleware); this endpoint exchanges a
    verified external identity for a Copilot-issued JWT.
    """
    auth_service = get_auth_service()
    user_row = auth_service.get_user_by_sso(req.provider, req.sso_subject)
    if user_row is None:
        user_row = auth_service.create_sso_user(
            provider=req.provider, sso_subject=req.sso_subject,
            email=req.email, username=req.username, department=req.department,
        )
    if not user_row.get("is_active", True):
        raise HTTPException(status_code=403, detail="User account is inactive")
    auth_service.touch_last_login(user_row["user_id"])
    auth_service.log_audit(user_id=user_row["user_id"], action="login")
    return issue_token(user_row)


@app.get("/auth/users", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def list_users():
    """Admin: list all users with roles."""
    return get_auth_service().list_users()


@app.patch("/auth/users/{user_id}/roles", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def update_user_roles(user_id: str, req: UpdateRolesRequest):
    """Admin: update a user's primary role and access_roles."""
    return get_auth_service().update_user_roles(user_id, req.role, req.access_roles)


@app.post("/auth/users/{user_id}/reset-password", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def admin_reset_password(user_id: str, req: AdminResetPasswordRequest):
    """Admin: set a temporary password for a user (forces change on next login)."""
    return get_auth_service().admin_reset_password(user_id, req.new_password)


@app.post("/auth/change-password")
async def change_password(req: ChangePasswordRequest, current_user: CurrentUser = Depends(get_current_user)):
    """User: change own password."""
    if current_user.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    return get_auth_service().change_password(current_user.user_id, req.current_password, req.new_password)


# ── Access Requests (employee-initiated, admin-approved) ─────────────────────
ACCESS_ROLE_OPTIONS = ["EMPLOYEE", "MANAGER", "HR", "FINANCE", "IT_ADMIN", "EXECUTIVE"]


@app.get("/access-requests")
async def my_access_requests(current_user: CurrentUser = Depends(get_current_user)):
    """List the current user's own access requests."""
    if current_user.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    return get_auth_service().list_access_requests(user_id=current_user.user_id)


@app.get("/access-requests/all", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def all_access_requests():
    """Admin: list every access request (pending + resolved)."""
    return get_auth_service().list_access_requests()


@app.post("/access-requests")
async def create_access_request(req: CreateAccessRequestModel, current_user: CurrentUser = Depends(get_current_user)):
    """Employee: request an additional access role (e.g. FINANCE, HR)."""
    if current_user.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    if req.resource_name.upper() not in ACCESS_ROLE_OPTIONS:
        raise HTTPException(status_code=400, detail=f"resource_name must be one of {ACCESS_ROLE_OPTIONS}")
    result = get_auth_service().create_access_request(
        user_id=current_user.user_id, resource_name=req.resource_name.upper(),
        justification=req.justification, resource_type=req.resource_type,
    )
    get_auth_service().log_audit(user_id=current_user.user_id, action="access_request",
                                  query_text=f"Requested {req.resource_name.upper()}: {req.justification[:100]}")
    return result


@app.post("/access-requests/{request_id}/resolve", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def resolve_access_request(request_id: str, req: ResolveAccessRequestModel, current_user: CurrentUser = Depends(get_current_user)):
    """Admin: approve or reject a pending access request."""
    result = get_auth_service().resolve_access_request(
        request_id=request_id, approve=req.approve,
        resolved_by=current_user.user_id, rejection_reason=req.rejection_reason,
    )
    get_auth_service().log_audit(user_id=current_user.user_id, action="access_request",
                                  query_text=f"{'Approved' if req.approve else 'Rejected'} request {request_id}")
    return result


# ── Audit Log (admin) ─────────────────────────────────────────────────────────
@app.get("/audit/logs", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def audit_logs(limit: int = 50, skip: int = 0, action: Optional[str] = None, user_email: Optional[str] = None):
    """Admin: paginated, filterable audit log."""
    return get_auth_service().list_audit_logs(limit=limit, skip=skip, action=action, user_email=user_email)


@app.get("/audit/metrics", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def audit_metrics(days: int = 7):
    """Admin: summary counters (queries, logins, access requests, low-confidence events)."""
    return get_auth_service().audit_metrics(days)


class ClearKnowledgeBaseRequest(BaseModel):
    scope: str = "all"  # "all" | "documents" | "tickets"
    confirm: bool = False


@app.post("/admin/clear-knowledge-base", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def clear_knowledge_base(req: ClearKnowledgeBaseRequest, current_user: CurrentUser = Depends(get_current_user)):
    """
    Admin: wipe ingested data. This removes data from THREE places that must
    stay in sync — Postgres (documents/chunks and/or tickets), the BM25
    in-memory index (+ its Redis-persisted copy), and the Qdrant vector
    collection (deleted and recreated empty).

    scope:
      - "all"       wipe documents, chunks, tickets, AND the BM25/Qdrant indices
      - "documents" wipe only documents/chunks (tickets untouched) — BM25/Qdrant
                     still get fully cleared since they're not split by type
      - "tickets"   wipe only the tickets table — BM25/Qdrant still fully cleared

    Because BM25 and Qdrant store both documents and tickets in the same
    index without an easy per-type bulk-delete, any scope clears BOTH
    indices entirely; Postgres deletion is scoped as requested. After a
    partial-scope clear, re-ingest the data you want to keep so it's
    re-indexed.
    """
    if not req.confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to proceed — this action cannot be undone")
    if req.scope not in ("all", "documents", "tickets"):
        raise HTTPException(status_code=400, detail="scope must be 'all', 'documents', or 'tickets'")

    from sqlalchemy import create_engine, text as sql_text
    engine = create_engine(settings.postgres_url)
    result = {"postgres": {}, "bm25_cleared": False, "qdrant_cleared": False}

    with engine.begin() as conn:
        if req.scope in ("all", "documents"):
            doc_count = conn.execute(sql_text("SELECT COUNT(*) FROM documents")).scalar()
            conn.execute(sql_text("TRUNCATE TABLE chunks, documents CASCADE"))
            conn.execute(sql_text("UPDATE repositories SET document_count = 0, chunk_count = 0"))
            result["postgres"]["documents_deleted"] = doc_count
        if req.scope in ("all", "tickets"):
            ticket_count = conn.execute(sql_text("SELECT COUNT(*) FROM tickets")).scalar()
            conn.execute(sql_text("TRUNCATE TABLE tickets"))
            result["postgres"]["tickets_deleted"] = ticket_count

    # BM25 + Qdrant store documents and tickets in the same index — always
    # clear both, regardless of scope, to avoid stale entries pointing at
    # deleted Postgres rows.
    if bm25_store:
        bm25_store.clear()
        result["bm25_cleared"] = True
    if vector_store:
        vector_store.clear_collection()
        result["qdrant_cleared"] = True

    get_auth_service().log_audit(
        user_id=current_user.user_id, action="admin_clear_kb",
        query_text=f"scope={req.scope}",
    )
    return result



class ChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    chat_history: Optional[List[dict]] = None
    department_filter: Optional[str] = None
    repository_filter: Optional[str] = None
    rbac_roles: Optional[List[str]] = None
    document_ids: Optional[List[str]] = None  # UI-selected document scope


class ChatResponse(BaseModel):
    answer: str
    citations: List[dict]
    confidence: float
    confidence_label: str
    confidence_breakdown: dict
    chunks_used: int
    low_confidence: bool
    latency_ms: int
    session_id: str
    repositories_searched: List[str]
    expanded_queries: List[str]
    sub_queries: List[str]
    intent: str
    hyde_used: bool
    retrieval_transparency: List[dict]
    pipeline_trace: List[dict]
    pipeline_stats: dict
    retrieval_trace: dict = {}
    clarification_needed: bool = False
    clarification_question: Optional[str] = None
    offer_raise_ticket: bool = False
    ticket_candidates: List[dict] = []


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, current_user: CurrentUser = Depends(get_current_user)):
    """
    Full LLD-compliant retrieval pipeline:
    QU(9 steps) → HyDE → Dense+Question+BM25 → RRF → CrossEncoder → Context → LLM
    """
    import time
    t_start = time.time()
    session_id = req.session_id or str(uuid.uuid4())
    # RBAC: prefer authenticated user's access_roles (LLD §7); fall back to
    # request body for unauthenticated/legacy calls.
    if current_user.user_id != "anonymous":
        rbac_roles = current_user.access_roles or ["EMPLOYEE"]
    else:
        rbac_roles = req.rbac_roles or ["EMPLOYEE"]

    # ── Steps 1-6, 8-9: Query Understanding ──────────────────────────────────
    try:
        query_ctx = query_understanding.process(req.query, rbac_roles=rbac_roles)
    except Exception as qu_err:
        logger.warning(f"Query understanding failed, using defaults: {qu_err}")
        from retrieval.query_understanding import QueryContext
        query_ctx = QueryContext(
            original_query=req.query,
            cleaned_query=req.query,
            safe_query=req.query,
            intent="GENERAL_KNOWLEDGE",
            repositories=["HR", "Finance", "IT", "Engineering", "Projects", "External"],
        )

    # Bundle scoping: if this session has an active bundle applied
    # (api.applyBundle → chat_sessions.active_documents), restrict retrieval
    # to just those document IDs.
    if req.session_id:
        try:
            from sqlalchemy import create_engine, text as sql_text
            _engine = create_engine(settings.postgres_url)
            with _engine.connect() as _conn:
                _row = _conn.execute(
                    sql_text("SELECT active_documents FROM chat_sessions WHERE session_id = :sid"),
                    {"sid": req.session_id},
                ).fetchone()
                if _row and _row.active_documents:
                    query_ctx.active_document_ids = list(_row.active_documents)
        except Exception:
            pass

    # UI-selected document IDs override bundle/session scoping
    if req.document_ids:
        query_ctx.active_document_ids = req.document_ids

    # Look up source_file for each scoped doc_id (Postgres), so
    # `_direct_document_retrieval` / `get_chunks_by_doc` can fall back to
    # matching Qdrant chunks by `source_file` when a chunk's `doc_id`
    # payload doesn't match the Postgres `documents.doc_id` (pre-P3
    # ingests) — without this map, that fallback is unreachable from the
    # chat path even though it works on the Document Detail page.
    if query_ctx.active_document_ids:
        try:
            from sqlalchemy import create_engine, text as sql_text
            _engine = create_engine(settings.postgres_url)
            with _engine.connect() as _conn:
                _rows = _conn.execute(sql_text(
                    "SELECT doc_id, source_file FROM documents WHERE doc_id = ANY(:ids)"
                ), {"ids": query_ctx.active_document_ids}).fetchall()
            query_ctx.active_document_source_files = {
                str(r.doc_id): r.source_file for r in _rows if r.source_file
            }
        except Exception as e:
            logger.debug(f"active_document_source_files lookup failed: {e}")

    if req.department_filter:
        query_ctx.filters["department"] = req.department_filter
    if req.repository_filter:
        query_ctx.repositories = [req.repository_filter]
        # Re-expand with forced repository
        query_ctx.expanded_queries = [req.query, f"{req.repository_filter} {req.query}"]

    # Override repository selection with repo_service if available
    if repo_service and not req.repository_filter:
        routed = repo_service.route_query(req.query, query_ctx.departments)
        if routed:
            query_ctx.repositories = routed

    # ── Step 3 Level 2: LLM Intent Classifier (P6) ───────────────────────────
    # Only invoked when the Level-1 rule-based classifier (Step 3 above)
    # didn't find a confident rule/keyword match (ctx.intent_result.low_confidence).
    if query_ctx.intent_result and query_ctx.intent_result.low_confidence:
        try:
            query_ctx = await query_understanding.classify_intent_llm(query_ctx)
        except Exception as intent_err:
            logger.debug(f"LLM intent classification skipped: {intent_err}")

    # ── EXACT TICKET-ID SHORT-CIRCUIT (LLD §5.1) ─────────────────────────────
    # If the query references a specific ticket ID (e.g. "status of ticket
    # TCK000002") AND that ID is an exact PK match in Postgres, answer
    # directly from the row — skip hybrid_engine.retrieve_cascading() (dense/
    # BM25/rerank against semantically-similar-but-wrong tickets) AND the
    # Ollama LLM call entirely. This is both faster (PK lookup is <2ms vs.
    # ~120s for a CPU LLM generation) and correct — the LLM was previously
    # asked to "explain" 3 semantically-similar-but-different tickets and
    # produced a wrong/hedged answer even though the exact ticket existed.
    if (
        query_ctx.intent == "TICKET_LOOKUP"
        and query_ctx.ticket_ids
        and ticket_retriever is not None
    ):
        try:
            exact_rows = ticket_retriever.search(
                query=req.query, ticket_ids=query_ctx.ticket_ids, top_k=5,
            )
        except Exception as e:
            logger.warning(f"Exact ticket-ID lookup failed: {e}")
            exact_rows = []

        if exact_rows:
            latency_ms = int((time.time() - t_start) * 1000)
            answer = _format_ticket_answer(exact_rows)
            citations = [{
                "source": "tickets.csv",
                "ticket_id": t.get("ticket_id"),
                "section": t.get("subject", ""),
                "repository": t.get("category", ""),
                "department": t.get("category", ""),
                "doc_type": "Ticket",
                "score": 1.0,
                "stale": False,
            } for t in exact_rows]

            query_ctx.pipeline_trace.append({
                "step": "ticket_exact_lookup",
                "name": "Exact Ticket ID Lookup (Postgres PK)",
                "ticket_ids": query_ctx.ticket_ids,
                "matched": [t.get("ticket_id") for t in exact_rows],
            })

            chat_user_id = current_user.user_id if current_user.user_id != "anonymous" else None
            if chat_history_service and chat_user_id:
                try:
                    chat_history_service.ensure_session(session_id, chat_user_id, req.query)
                    chat_history_service.add_message(
                        session_id, chat_user_id, role="user", content=req.query,
                    )
                    chat_history_service.add_message(
                        session_id, chat_user_id, role="assistant", content=answer,
                        citations=citations, confidence=1.0,
                        retrieval_meta={"intent": "TICKET_LOOKUP", "exact_match": True, "latency_ms": latency_ms},
                    )
                except Exception:
                    pass

            try:
                get_auth_service().log_audit(
                    user_id=chat_user_id, action="query", query_text=req.query,
                    confidence=1.0, chunks_used=len(exact_rows),
                    latency_ms=latency_ms, session_id=session_id,
                )
            except Exception:
                pass

            return ChatResponse(
                answer=answer,
                citations=citations,
                confidence=1.0,
                confidence_label="HIGH",
                confidence_breakdown={
                    "reranker": 1.0, "retrieval": 1.0, "citation": 1.0,
                    "reasoning": "Exact ticket ID match — Postgres primary-key lookup",
                    "color": "green",
                },
                chunks_used=len(exact_rows),
                low_confidence=False,
                latency_ms=latency_ms,
                session_id=session_id,
                repositories_searched=[],
                expanded_queries=query_ctx.expanded_queries,
                sub_queries=query_ctx.sub_queries,
                intent="TICKET_LOOKUP",
                hyde_used=False,
                retrieval_transparency=[],
                pipeline_trace=query_ctx.pipeline_trace,
                pipeline_stats={"mode": "ticket_exact_lookup", "exact_match": True},
                retrieval_trace={},
                clarification_needed=False,
                clarification_question=None,
                offer_raise_ticket=False,
                ticket_candidates=exact_rows,
            )

    # ── TICKET ANALYTICS SHORT-CIRCUIT (SQL agent) ───────────────────────────
    # "how many open tickets", "count of high priority IT tickets", etc.
    # These are aggregate questions that retrieval/RAG fundamentally can't
    # answer correctly — a handful of retrieved chunks is not a count over
    # the whole table. Run a parameterized COUNT(*) against Postgres
    # directly and answer immediately, skipping retrieval + LLM.
    if query_ctx.intent == "TICKET_ANALYTICS" and ticket_retriever is not None:
        latency_ms = int((time.time() - t_start) * 1000)
        filters = _extract_ticket_filters(req.query)
        try:
            count = _run_ticket_count_query(filters)
            label = _describe_ticket_filters(filters)
            answer = f"There are **{count}** {label}."

            query_ctx.pipeline_trace.append({
                "step": "ticket_analytics_sql_agent",
                "name": "Ticket Analytics SQL Agent",
                "filters": filters,
                "sql": "SELECT COUNT(*) FROM tickets" + (
                    " WHERE " + " AND ".join(f"{k}=%({k})s" for k in filters) if filters else ""
                ),
                "result": count,
            })

            chat_user_id = current_user.user_id if current_user.user_id != "anonymous" else None
            if chat_history_service and chat_user_id:
                try:
                    chat_history_service.ensure_session(session_id, chat_user_id, req.query)
                    chat_history_service.add_message(
                        session_id, chat_user_id, role="user", content=req.query,
                    )
                    chat_history_service.add_message(
                        session_id, chat_user_id, role="assistant", content=answer,
                        citations=[], confidence=1.0,
                        retrieval_meta={"intent": "TICKET_ANALYTICS", "filters": filters, "latency_ms": latency_ms},
                    )
                except Exception:
                    pass

            return ChatResponse(
                answer=answer,
                citations=[],
                confidence=1.0,
                confidence_label="HIGH",
                confidence_breakdown={
                    "reranker": 1.0, "retrieval": 1.0, "citation": 1.0,
                    "reasoning": "Ticket analytics SQL agent — COUNT(*) over tickets table",
                    "color": "green",
                },
                chunks_used=0,
                low_confidence=False,
                latency_ms=latency_ms,
                session_id=session_id,
                repositories_searched=[],
                expanded_queries=query_ctx.expanded_queries,
                sub_queries=query_ctx.sub_queries,
                intent="TICKET_ANALYTICS",
                hyde_used=False,
                retrieval_transparency=[],
                pipeline_trace=query_ctx.pipeline_trace,
                pipeline_stats={"mode": "ticket_analytics_sql_agent", "filters": filters, "count": count},
                retrieval_trace={},
                clarification_needed=False,
                clarification_question=None,
                offer_raise_ticket=False,
                ticket_candidates=[],
            )
        except Exception as e:
            logger.warning(f"Ticket analytics SQL agent failed, falling back to retrieval: {e}")
            # fall through to normal retrieval below

    # ── Step 7 + Retrieval: HyDE is now decided & generated INSIDE
    # hybrid_engine.retrieve() / retrieve_cascading() (P5) — broadened
    # triggers (weak confidence, score below threshold, ambiguous query),
    # not just "short query". query_ctx.hyde_passage / hyde_used and
    # pipeline_trace step 7 are populated there.
    # ── P4: Document-Specific Retrieval cascade ──────────────────────────────
    #   Level 1: Selected Document -> Level 2: Repository -> Level 3:
    #   Department -> Level 4: Global. Expands only on low confidence.
    result = await hybrid_engine.retrieve_cascading(query_ctx, rbac_roles=rbac_roles)
    hyde_used = query_ctx.hyde_used

    clarification_needed = False
    clarification_question = None
    offer_raise_ticket = False
    ticket_candidates: List[dict] = []

    # ── TICKET DUAL-PATH (LLD §5) ─────────────────────────────────────────────
    if query_ctx.intent == "TICKET_LOOKUP" and ticket_retriever is not None:
        ticket_path = ticket_retriever.dual_path_search(
            query=req.query,
            ticket_ids=query_ctx.ticket_ids,
            filters=query_ctx.filters,
            vector_store=hybrid_engine.vector_store,
        )
        ticket_candidates = ticket_path["candidates"]
        result.pipeline_stats["ticket_dual_path"] = ticket_path["stats"]

    # ── LOW CONFIDENCE HANDLING (LLD §7) ────────────────────────────────────────
    # 7.0: Fewer than 3 chunks survive the final_score threshold
    if result.low_confidence:
        # 7.1: Retry — remove internal-origin filter, widen to top_k=25,
        # and explicitly drop ALL scope filters (global search, P4 Level 4)
        logger.info("Low confidence — retrying with expanded candidates (top_k=25, include_external=True, scope=GLOBAL)")
        retried = await hybrid_engine.retrieve(
            query_ctx, top_k=settings.retry_top_k, include_external=True, rbac_roles=rbac_roles,
            skip_doc_scope=True, scope_level=4, scope_label="GLOBAL_RETRY",
        )
        if len(retried.chunks) >= len(result.chunks):
            result = retried

        # 7.2: Still insufficient -> clarification question (no LLM call)
        if len(result.chunks) < settings.min_chunks_after_retry:
            # ── SEARCH-intent ticket fallback ──────────────────────────────
            # Before giving up with a clarification question, check resolved
            # tickets — e.g. "VPN setup process for remote access" has no
            # formal policy doc, but a resolved IT ticket's resolution steps
            # answer it directly. Fold matching tickets in as "IT"-tagged
            # context/citations so they count toward confidence too.
            if query_ctx.intent != "TICKET_LOOKUP" and ticket_retriever is not None:
                ticket_path = ticket_retriever.dual_path_search(
                    query=req.query,
                    filters=query_ctx.filters,
                    vector_store=hybrid_engine.vector_store,
                )
                ticket_candidates = ticket_path["candidates"] or ticket_candidates
                result.pipeline_stats["ticket_fallback"] = ticket_path["stats"]

                ticket_chunks = _tickets_to_chunks(ticket_path["candidates"])
                if ticket_chunks:
                    result.chunks = sorted(
                        result.chunks + ticket_chunks,
                        key=lambda c: c.final_score, reverse=True,
                    )[:settings.reranker_top_k]
                    result.confidence = result.chunks[0].final_score
                    result.low_confidence = result.confidence < settings.low_confidence_threshold

        if len(result.chunks) < settings.min_chunks_after_retry:
            clarification_needed = True
            clarification_question = _build_clarification_question(req.query, query_ctx)

            # 7.3: If intent == TICKET_LOOKUP, offer to raise a new ITSM ticket
            if query_ctx.intent == "TICKET_LOOKUP":
                offer_raise_ticket = True

            # Log knowledge gap regardless of clarification path
            if rag_evaluator:
                rag_evaluator.record_knowledge_gap(
                    query=req.query,
                    intent=query_ctx.intent,
                    repositories=query_ctx.repositories,
                )

    # General knowledge gap logging for very low confidence even if sufficient chunks
    if rag_evaluator and not clarification_needed and (result.total_candidates == 0 or result.confidence < 0.30):
        rag_evaluator.record_knowledge_gap(
            query=req.query,
            intent=query_ctx.intent,
            repositories=query_ctx.repositories,
        )

    # ── Context Builder ───────────────────────────────────────────────────────
    # Document/bundle-scoped SUMMARIZE (`_direct_document_retrieval`) returns
    # one "outline" chunk + an evenly-spaced content sample, all from the same
    # `source_file`(s) — the default max_per_doc=3 would discard most of that
    # sample for a single scoped document. Raise both caps in this mode so the
    # outline + sample actually reach the LLM.
    if result.pipeline_stats.get("mode") == "document_scope_summary":
        # `result.chunks` = 1 outline chunk per scoped doc + an
        # evenly-spaced content sample (up to top_k=8 per doc). Sending
        # ALL of that uncapped to the LLM can produce a prompt large
        # enough that small/CPU-bound models (e.g. phi3:mini) time out
        # (180s). Cap at 6 chunks/doc — outline + ~5 samples is normally
        # plenty for an overview — while still far above the default 3.
        _doc_count = max(1, result.pipeline_stats.get("chunks_per_document") and len(result.pipeline_stats["chunks_per_document"]) or 1)
        _max_per_doc = 6
        built_ctx = hybrid_engine.build_context(
            result.chunks, max_chunks=_max_per_doc * _doc_count, max_per_doc=_max_per_doc,
        )
    elif result.scope_level == 1:
        # Document/bundle-scoped search (intent != SUMMARIZE) that stayed
        # within the user's selection: the default max_per_doc=3 can starve
        # a single selected document of context even when 5-6 of its chunks
        # score above threshold. Relax to 6/doc (max_chunks default of 8
        # still applies as the overall cap).
        built_ctx = hybrid_engine.build_context(result.chunks, max_per_doc=6)
    else:
        built_ctx = hybrid_engine.build_context(result.chunks)

    # ── LLM Generation ────────────────────────────────────────────────────────
    # LLD §7.2: if a clarification question is required, skip the LLM call entirely
    if clarification_needed:
        llm_resp = {"answer": clarification_question}
        logger.info("[RETRIEVAL DEBUG] stage=llm_input skipped=True reason=clarification_needed")
    else:
        logger.info(
            "[RETRIEVAL DEBUG] stage=llm_input chunks=%d context_tokens=%d citations=%d",
            len(built_ctx.chunks), built_ctx.total_tokens, len(built_ctx.citations),
        )
        llm_resp = await llm_service.generate_from_context(
            query=req.query,
            context=built_ctx.context_text,
            citations=built_ctx.citations,
            is_low_confidence=result.low_confidence,
            chat_history=req.chat_history,
        )

    # ── Scope-escape notice ──────────────────────────────────────────────────
    # The user explicitly scoped this query to specific document(s)/a bundle
    # (query_ctx.active_document_ids), but the final result came from a
    # broader cascade level (REPOSITORY/DEPARTMENT/GLOBAL) — i.e.
    # retrieve_cascading() found nothing good enough *within* the selection
    # and fell back to a wider search. Make that explicit instead of
    # silently answering from elsewhere.
    scope_escaped = bool(query_ctx.active_document_ids) and result.scope_level != 1
    if scope_escaped:
        if clarification_needed:
            clarification_question = (
                clarification_question.rstrip()
                + "\n\n_I also couldn't find this in the document(s)/bundle you selected, "
                  "and a broader search across other repositories didn't turn up anything "
                  "reliable either._"
            )
            llm_resp["answer"] = clarification_question
        else:
            scope_note = (
                "_This isn't covered in the document(s)/bundle you selected — "
                f"here's what I found searching more broadly ({result.scope_label.lower()} scope):_\n\n"
            )
            llm_resp["answer"] = scope_note + llm_resp["answer"]

    # ── Step 10: Retrieval ───────────────────────────────────────────────────
    # Surfaces each retrieval stage's chunk/document counts in the pipeline
    # trace (dense/question/summary/bm25 -> RRF -> reranked -> below-threshold
    # -> final -> context), so it's visible after the response completes —
    # not just in the collapsed "dense=20 q=20 bm25=0 -> 0 final" summary line.
    # For document/bundle-scoped SUMMARIZE this also reports how many chunks
    # were found per scoped document.
    _retrieval_step = {
        "step": 10, "name": "Retrieval",
        "scope": result.scope_label,
        "mode": result.pipeline_stats.get("mode", "search"),
        "dense": result.pipeline_stats.get("dense", 0),
        "question": result.pipeline_stats.get("question", 0),
        "summary": result.pipeline_stats.get("summary", 0),
        "bm25": result.pipeline_stats.get("bm25", 0),
        "rrf_candidates": result.pipeline_stats.get("rrf_candidates", 0),
        "reranked": result.retrieval_trace.get("reranked_count", 0),
        "below_threshold": result.pipeline_stats.get("below_threshold", 0),
        "final_chunks": result.pipeline_stats.get("final_chunks", 0),
        "context_chunks": len(built_ctx.chunks),
        "scope_escaped": scope_escaped,
    }
    if result.pipeline_stats.get("chunks_per_document"):
        _retrieval_step["chunks_per_document"] = result.pipeline_stats["chunks_per_document"]
    if result.retrieval_trace.get("cascade"):
        _retrieval_step["cascade"] = result.retrieval_trace["cascade"]
    query_ctx.pipeline_trace.append(_retrieval_step)

    latency_ms = int((time.time() - t_start) * 1000)

    # ── Confidence Engine ──────────────────────────────────────────────────────
    conf_breakdown = compute_confidence(
        reranker_top_score=result.chunks[0].rerank_score if result.chunks else 0.0,
        total_candidates=result.total_candidates,
        chunks_used=len(built_ctx.chunks),
        chunk_scores=[c.final_score for c in result.chunks],
    )

    # ── Analytics logging ──────────────────────────────────────────────────────
    chat_user_id = current_user.user_id if current_user.user_id != "anonymous" else None
    if analytics_service:
        try:
            analytics_service.log_query(
                query_text=req.query,
                intent=query_ctx.intent,
                repository_names=query_ctx.repositories,
                expanded_queries=query_ctx.expanded_queries,
                chunks_retrieved=len(result.chunks),
                confidence=conf_breakdown.overall,
                latency_ms=latency_ms,
                reranker_top_score=result.chunks[0].rerank_score if result.chunks else 0.0,
                low_confidence=result.low_confidence,
                user_id=chat_user_id,
                session_id=session_id,
            )
        except Exception:
            pass

    # ── Audit logging ────────────────────────────────────────────────────────
    try:
        get_auth_service().log_audit(
            user_id=chat_user_id,
            action="low_confidence" if result.low_confidence else "query",
            query_text=req.query,
            confidence=conf_breakdown.overall,
            chunks_used=len(result.chunks),
            latency_ms=latency_ms,
            session_id=session_id,
        )
    except Exception:
        pass

    # ── Chat history persistence (per-user, exclusive sessions) ────────────────
    if chat_history_service and chat_user_id:
        try:
            chat_history_service.ensure_session(session_id, chat_user_id, req.query)
            chat_history_service.add_message(
                session_id, chat_user_id, role="user", content=req.query,
            )
            chat_history_service.add_message(
                session_id, chat_user_id, role="assistant", content=llm_resp["answer"],
                citations=built_ctx.citations,
                confidence=conf_breakdown.overall,
                retrieval_meta={
                    "intent": query_ctx.intent,
                    "repositories_searched": result.repositories_searched,
                    "low_confidence": result.low_confidence,
                    "latency_ms": latency_ms,
                },
            )
        except Exception:
            pass

    # ── Retrieval transparency payload ────────────────────────────────────────
    transparency = [
        {
            "chunk_id": c.chunk_id,
            "doc_id": c.doc_id,
            "source": _basename(c.source_file),
            "section": c.section_title,
            "section_hierarchy": c.section_hierarchy,
            "page": c.page_number,
            "department": c.department,
            "repository": c.repository or c.department,
            "doc_type": c.doc_type,
            "doc_origin": c.doc_origin,
            "origin": c.origin or c.doc_origin,
            "priority_tier": c.priority_tier,
            "project_id": c.project_id,
            "uploaded_by": c.uploaded_by,
            "access_roles": c.access_roles,
            "retrieval_source": c.retrieval_source,
            "rrf_score": round(c.rrf_score, 4),
            "rerank_score": round(c.rerank_score, 4),
            "freshness_decay": round(c.freshness_decay, 4),
            "feedback_boost": round(c.feedback_boost, 4),
            "entity_boost": round(c.entity_boost, 4),
            "final_score": round(c.final_score, 4),
            "keywords": c.keywords[:5],
            "is_image_chunk": c.is_image_chunk,
            "image_path": c.image_path,
        }
        for c in result.chunks
    ]

    return ChatResponse(
        answer=llm_resp["answer"],
        citations=built_ctx.citations,
        confidence=conf_breakdown.overall,
        confidence_label=conf_breakdown.label,
        confidence_breakdown={
            "reranker": conf_breakdown.reranker_component,
            "retrieval": conf_breakdown.retrieval_component,
            "citation": conf_breakdown.citation_component,
            "reasoning": conf_breakdown.reasoning,
            "color": conf_breakdown.color,
        },
        chunks_used=len(built_ctx.chunks),
        low_confidence=result.low_confidence,
        latency_ms=latency_ms,
        session_id=session_id,
        repositories_searched=result.repositories_searched,
        expanded_queries=query_ctx.expanded_queries,
        sub_queries=query_ctx.sub_queries,
        intent=query_ctx.intent,
        hyde_used=hyde_used,
        retrieval_transparency=transparency,
        pipeline_trace=query_ctx.pipeline_trace,
        pipeline_stats=result.pipeline_stats,
        retrieval_trace=result.retrieval_trace,
        clarification_needed=clarification_needed,
        clarification_question=clarification_question,
        offer_raise_ticket=offer_raise_ticket,
        ticket_candidates=ticket_candidates,
    )


@app.get("/chat/sessions")
async def list_chat_sessions(current_user: CurrentUser = Depends(get_current_user)):
    """List the current user's chat sessions, most recently updated first."""
    if current_user.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    if not chat_history_service:
        raise HTTPException(503, "Chat history service unavailable")
    return {"sessions": chat_history_service.list_sessions(current_user.user_id)}


@app.get("/chat/sessions/{session_id}/messages")
async def get_chat_session_messages(session_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """
    Load full message history for a session — only if it belongs to the
    current user. Returns 404 if the session doesn't exist or belongs to
    someone else (sessions are exclusive to the user who created them).
    """
    if current_user.user_id == "anonymous":
        raise HTTPException(status_code=401, detail="Authentication required")
    if not chat_history_service:
        raise HTTPException(503, "Chat history service unavailable")
    messages = chat_history_service.get_session_messages(session_id, current_user.user_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "messages": messages}


def _tickets_to_chunks(tickets: List[dict], limit: int = 3) -> List[RetrievedChunk]:
    """
    Convert resolved-ticket rows (from TicketRetriever.dual_path_search) into
    RetrievedChunk objects so they can be folded into result.chunks /
    build_context() alongside document chunks — citations, repository badge,
    and confidence all treat them the same as any other "IT" source.

    Used as a SEARCH-intent fallback (LLD §7.1 extension): when document
    retrieval is still low-confidence, a resolved ticket's description +
    resolution often contains the actual step-by-step procedure (e.g. "VPN
    setup for remote access") even though no formal policy/SOP exists.
    """
    chunks: List[RetrievedChunk] = []
    for t in (tickets or [])[:limit]:
        parts = []
        if t.get("subject"):
            parts.append(f"Subject: {t['subject']}")
        if t.get("description"):
            parts.append(f"Description: {t['description']}")
        if t.get("resolution"):
            parts.append(f"Resolution: {t['resolution']}")
        if not parts:
            continue
        ticket_id = t.get("ticket_id", "")
        category = t.get("category") or "IT"
        chunks.append(RetrievedChunk(
            chunk_id=f"ticket-{ticket_id}",
            content="\n".join(parts),
            source_file=f"Ticket {ticket_id}",
            section_title=t.get("subject") or "Resolved Ticket",
            page_number=0,
            doc_type="Ticket",
            department=category,
            doc_origin="INTERNAL",
            priority_tier=1,            # resolved ITSM tickets = Tier 1 (LLD §4.3)
            repository=category,
            retrieval_source="ticket_fallback",
            rerank_score=0.55,
            final_score=0.55,
        ))
    return chunks


# ── TICKET ANALYTICS SQL AGENT ───────────────────────────────────────────────
# Lightweight, rule-based "SQL agent" for aggregate ticket questions
# ("how many open tickets", "count of high priority IT tickets"). Deliberately
# NOT a free-form text-to-SQL LLM agent — filter values are matched against a
# fixed allow-list and interpolated only as bound parameters, so there's no
# SQL-injection surface and no hallucinated column/table names. Falls through
# to normal retrieval if no recognizable filter combination is found.
_TICKET_STATUS_MAP = {
    "open": "open", "in progress": "in_progress", "in-progress": "in_progress",
    "pending": "open", "unresolved": "open",
    "resolved": "resolved", "closed": "closed", "rejected": "rejected",
}
_TICKET_PRIORITY_MAP = {"low": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}
_TICKET_CATEGORY_MAP = {
    "hr": "HR", "finance": "Finance", "it": "IT", "engineering": "Engineering",
    "operations": "Operations", "security": "Security",
}


def _extract_ticket_filters(query: str) -> Dict[str, str]:
    lower = query.lower()
    filters: Dict[str, str] = {}
    # Longer keys first so "in progress" matches before a bare "open"/"pending"
    for k, v in sorted(_TICKET_STATUS_MAP.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(k)}\b", lower):
            filters["status"] = v
            break
    for k, v in _TICKET_PRIORITY_MAP.items():
        if re.search(rf"\b{k}\b", lower):
            filters["priority"] = v
            break
    for k, v in _TICKET_CATEGORY_MAP.items():
        if re.search(rf"\b{k}\b", lower):
            filters["category"] = v
            break
    return filters


def _run_ticket_count_query(filters: Dict[str, str]) -> int:
    from sqlalchemy import create_engine, text as sql_text
    engine = create_engine(settings.postgres_url)
    clauses, params = [], {}
    for col in ("status", "priority", "category"):
        if filters.get(col):
            clauses.append(f"{col} = :{col}")
            params[col] = filters[col]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = sql_text(f"SELECT COUNT(*) FROM tickets {where}")
    with engine.connect() as conn:
        return conn.execute(sql, params).scalar() or 0


def _describe_ticket_filters(filters: Dict[str, str]) -> str:
    parts = []
    if filters.get("priority"):
        parts.append(f"{filters['priority'].lower()} priority")
    if filters.get("status"):
        parts.append(filters["status"].replace("_", " "))
    label = " ".join(parts) + " tickets" if parts else "tickets"
    if filters.get("category"):
        label += f" in {filters['category']}"
    return label


def _format_ticket_answer(tickets: List[dict]) -> str:
    """Render Postgres ticket row(s) into a deterministic answer — no LLM
    call needed for an exact ticket-ID lookup."""
    if len(tickets) == 1:
        t = tickets[0]
        lines = [
            f"**Ticket {t.get('ticket_id')}** — {t.get('subject', '')}",
            f"- Status: {t.get('status', 'unknown')}",
            f"- Priority: {t.get('priority', 'unknown')}",
            f"- Category: {t.get('category', 'unknown')}",
        ]
        if t.get("description"):
            lines.append(f"- Description: {t['description']}")
        if t.get("resolution"):
            lines.append(f"- Resolution: {t['resolution']}")
        if t.get("created_at"):
            lines.append(f"- Created: {t['created_at']}")
        return "\n".join(lines)

    lines = [f"Found {len(tickets)} matching tickets:"]
    for t in tickets:
        lines.append(
            f"- **{t.get('ticket_id')}** ({t.get('status', 'unknown')}, "
            f"{t.get('priority', 'unknown')} priority, {t.get('category', 'unknown')}): "
            f"{t.get('subject', '')}"
        )
    return "\n".join(lines)


def _build_clarification_question(query: str, query_ctx) -> str:
    """LLD §7.2 — generate a clarification question without an LLM call."""
    if query_ctx.repositories:
        repos = ", ".join(query_ctx.repositories)
        return (
            f"I couldn't find enough relevant information for \"{query}\" "
            f"in {repos}. Could you clarify what you're looking for — "
            f"e.g. a specific document, system, or time period?"
        )
    if query_ctx.intent == "TICKET_LOOKUP":
        return (
            f"I couldn't find an existing ticket or documentation matching "
            f"\"{query}\". Could you share a ticket ID, or more details "
            f"about the issue (system, error message, when it started)?"
        )
    return (
        f"I couldn't find enough relevant information to answer \"{query}\" "
        f"confidently. Could you rephrase or provide more details — e.g. "
        f"which department, system, or document this relates to?"
    )


# ════════════════════════════════════════════════════════════════════════════
# AGENTIC ENHANCEMENT (Hackathon Use Case 2, Bonus) — ReAct / Plan-Execute
# ════════════════════════════════════════════════════════════════════════════
class AgentChatRequest(BaseModel):
    query: str
    session_id: Optional[str] = None
    chat_history: Optional[List[dict]] = None
    rbac_roles: Optional[List[str]] = None


class AgentStepOut(BaseModel):
    step: int
    action: str
    action_input: str
    observation: str


class AgentChatResponse(BaseModel):
    answer: str
    citations: List[dict]
    steps: List[AgentStepOut]
    pattern: str
    session_id: str


@app.post("/agent/chat", response_model=AgentChatResponse)
async def agent_chat(req: AgentChatRequest, current_user: CurrentUser = Depends(get_current_user)):
    """
    ReAct loop over document_search / ticket_lookup / summarizer tools,
    with a deterministic Plan-Execute fallback if no local LLM is available.
    """
    if agent_orchestrator is None:
        raise HTTPException(status_code=503, detail="Agent orchestrator not initialized")

    session_id = req.session_id or str(uuid.uuid4())
    if current_user.user_id != "anonymous":
        rbac_roles = current_user.access_roles or ["EMPLOYEE"]
    else:
        rbac_roles = req.rbac_roles or ["EMPLOYEE"]

    result = await agent_orchestrator.run(
        query=req.query, rbac_roles=rbac_roles, chat_history=req.chat_history,
    )

    return AgentChatResponse(
        answer=result.answer,
        citations=result.citations,
        steps=[
            AgentStepOut(step=s.step, action=s.action, action_input=s.action_input, observation=s.observation[:2000])
            for s in result.steps
        ],
        pattern=result.pattern,
        session_id=session_id,
    )


# ── Feedback endpoint ─────────────────────────────────────────────────────────
class FeedbackRequest(BaseModel):
    session_id: str
    query_text: str
    rating: int           # 1, -1, or 0
    comment: Optional[str] = None
    cited_chunk_ids: List[str] = []
    repositories_used: List[str] = []
    confidence: float = 0.0


@app.post("/feedback")
async def submit_feedback(req: FeedbackRequest):
    """Record user feedback and update chunk boost signals."""
    if not feedback_service:
        raise HTTPException(503, "Feedback service unavailable")
    ok = feedback_service.record_feedback(
        session_id=req.session_id,
        query_text=req.query_text,
        rating=req.rating,
        comment=req.comment,
        cited_chunk_ids=req.cited_chunk_ids,
        repositories_used=req.repositories_used,
        confidence=req.confidence,
    )
    return {"status": "recorded" if ok else "failed"}


@app.get("/feedback/summary")
async def feedback_summary(days: int = 7):
    if not feedback_service:
        raise HTTPException(503, "Feedback service unavailable")
    return feedback_service.get_feedback_summary(days)


# ── Knowledge gaps endpoint ────────────────────────────────────────────────────
@app.get("/knowledge-gaps")
async def get_knowledge_gaps(limit: int = 20):
    """Return unresolved knowledge gaps for admin review."""
    if not rag_evaluator:
        raise HTTPException(503, "RAG evaluator unavailable")
    gaps = rag_evaluator.get_knowledge_gaps(limit)
    return {"gaps": gaps, "total": len(gaps)}


@app.patch("/knowledge-gaps/{gap_id}/resolve")
async def resolve_gap(gap_id: str):
    if not rag_evaluator:
        raise HTTPException(503, "RAG evaluator unavailable")
    rag_evaluator.resolve_gap(gap_id)
    return {"status": "resolved"}


# ── Evaluation metrics ─────────────────────────────────────────────────────────
@app.get("/analytics/evaluation")
async def evaluation_metrics(days: int = 7):
    """Full retrieval evaluation: Precision@5, Recall@5, MRR, Hit Rate."""
    if not rag_evaluator:
        raise HTTPException(503, "RAG evaluator unavailable")
    metrics = rag_evaluator.get_metrics_from_db(days)
    return {
        "precision_at_5": metrics.precision_at_k,
        "recall_at_5": metrics.recall_at_k,
        "mrr": metrics.mrr,
        "hit_rate": metrics.hit_rate,
        "avg_latency_ms": metrics.avg_latency_ms,
        "p95_latency_ms": metrics.p95_latency_ms,
        "p99_latency_ms": metrics.p99_latency_ms,
        "avg_confidence": metrics.avg_confidence,
        "low_confidence_rate": metrics.low_confidence_rate,
        "total_evaluated": metrics.total_evaluated,
        "knowledge_gap_count": metrics.knowledge_gap_count,
        "confidence_distribution": metrics.confidence_distribution,
    }


# ── Ingest: tickets CSV ────────────────────────────────────────────────────────
# Column-name aliases: CSVs in the wild use different capitalizations/names
# for the same field. Each canonical field maps to a list of accepted header
# names (checked case-insensitively). If your CSV uses a different name
# entirely, add it to the relevant list below.
TICKET_COLUMN_ALIASES = {
    "id": ["id", "ticket_id", "ticketid", "ticket id"],
    "subject": ["subject", "title", "summary"],
    "description": ["description", "details", "body"],
    "priority": ["priority", "severity"],
    "category": ["category", "type", "issue_type"],
    "created_at": ["createdat", "created_at", "created", "date", "opened_at"],
    "requester_email": ["requesteremail", "requester_email", "email", "raised_by"],
    "status": ["status", "ticket_status", "state"],
    "resolution": ["resolution", "resolution_notes", "resolution_text", "fix"],
    "resolved_at": ["resolvedat", "resolved_at", "closed_at", "resolution_date"],
}

# Status values from various source systems normalized to: open | in_progress | resolved | closed
STATUS_NORMALIZE = {
    "open": "open", "new": "open", "unresolved": "open", "pending": "open",
    "in progress": "in_progress", "in_progress": "in_progress", "in-progress": "in_progress",
    "working": "in_progress", "assigned": "in_progress",
    "resolved": "resolved", "fixed": "resolved", "done": "resolved",
    "closed": "closed", "completed": "closed",
}


def _get_col(row: dict, field: str, lower_row: dict, default: str = "") -> str:
    """Look up a CSV field by any of its known aliases (case-insensitive)."""
    for alias in TICKET_COLUMN_ALIASES.get(field, [field]):
        if alias in lower_row:
            val = lower_row[alias]
            return val.strip() if val else default
    return default


# ══════════════════════════════════════════════════════════════════════════
# Ticket CSV ingestion job processing (P10: job tracking / progress / cancel)
#
# Same logic as before (parse rows -> build Chunk per ticket -> batch upsert
# every 100 rows -> flush Postgres `tickets`), now run as a background job
# with stage/progress updates and cooperative cancellation between batches.
# ══════════════════════════════════════════════════════════════════════════
async def _run_ticket_ingest_job(job_id: str, content: bytes, filename: str):
    from sqlalchemy import create_engine, text as sql_text
    from datetime import datetime as _dt

    async def _check_cancelled():
        if await job_tracker.is_cancel_requested(job_id):
            raise JobCancelled()

    try:
        await job_tracker.set_stage(job_id, "parsing_document", f"Parsing {filename}")
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
        total = len(rows)

        chunks: List[Chunk] = []
        pg_rows: List[dict] = []
        inserted = 0

        engine = create_engine(settings.postgres_url)

        def _flush_pg(rows_: List[dict]):
            if not rows_:
                return
            with engine.begin() as conn:
                for r in rows_:
                    conn.execute(
                        sql_text("""INSERT INTO tickets (ticket_id,subject,description,priority,category,
                                status,resolution,requester_email,created_at,resolved_at,
                                source_system) VALUES 
                                (:ticket_id,:subject,:description,:priority,:category,:status,:resolution,:requester_email,
                                :created_at,:resolved_at,'CSV') ON CONFLICT (ticket_id) DO UPDATE SET
                                subject = EXCLUDED.subject,
                                description = EXCLUDED.description,
                                priority = EXCLUDED.priority,
                                category = EXCLUDED.category,
                                status = EXCLUDED.status,
                                resolution = EXCLUDED.resolution,
                                requester_email = EXCLUDED.requester_email,
                                created_at = EXCLUDED.created_at,
                                resolved_at = EXCLUDED.resolved_at"""),
                        r,
                    )

        def _parse_date(raw: str):
            if not raw:
                return None
            try:
                return _dt.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        await job_tracker.set_stage(job_id, "chunking", f"Processing {total} tickets")

        for row in rows:
            lower_row = {k.strip().lower(): v for k, v in row.items() if k}

            ticket_id = _get_col(row, "id", lower_row)
            subject = _get_col(row, "subject", lower_row)
            description = _get_col(row, "description", lower_row)
            priority = _get_col(row, "priority", lower_row) or "Medium"
            category = _get_col(row, "category", lower_row) or "General"
            email = _get_col(row, "requesterEmail", lower_row)
            created_at_raw = _get_col(row, "createdAt", lower_row)
            resolved_at_raw = _get_col(row, "resolvedAt", lower_row)

            resolution = (_get_col(row, "resolution", lower_row) or "").strip()
            resolution = resolution if resolution else None

            status_raw = (_get_col(row, "status", lower_row) or "").strip()

            if not ticket_id or not subject:
                continue

            created_at = _parse_date(created_at_raw) or _dt.utcnow()
            resolved_at = _parse_date(resolved_at_raw)

            if status_raw:
                status = STATUS_NORMALIZE.get(status_raw.lower(), status_raw.lower())
            elif resolution or resolved_at:
                status = "resolved"
            else:
                status = "open"

            pg_rows.append({
                "ticket_id": ticket_id, "subject": subject, "description": description,
                "priority": priority, "category": category, "status": status,
                "resolution": resolution,
                "requester_email": email or None, "created_at": created_at,
                "resolved_at": resolved_at,
            })

            content_text = f"Ticket ID: {ticket_id}\nSubject: {subject}\n\nDescription:\n{description}"

            chunk = Chunk(
                chunk_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, ticket_id)),
                doc_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"tickets_{category}")),
                chunk_index=inserted,
                content=content_text,
                section_title=subject[:100],
                doc_type="Ticket",
                department=_map_category_to_dept(category),
                doc_origin="INTERNAL",
                priority_tier=1,
                source_file=filename,
                hypothetical_questions=[
                    f"How was the {category} issue '{subject[:50]}' resolved?",
                    f"What should I do when I have a {priority.lower()} priority {category} problem?",
                ],
                keywords=[category.lower(), priority.lower()] + subject.lower().split()[:5],
            )
            chunks.append(chunk)
            inserted += 1

            # Batch index every 100 chunks
            if len(chunks) >= 100:
                await _check_cancelled()
                vector_store.upsert_chunks(chunks)
                bm25_store.add_documents([
                    {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "content": c.content,
                     "section_title": c.section_title, "doc_type": c.doc_type,
                     "department": c.department, "doc_origin": c.doc_origin,
                     "priority_tier": c.priority_tier, "source_file": filename}
                    for c in chunks
                ])
                chunks = []
                _flush_pg(pg_rows)
                pg_rows = []

                progress = 20 + int(70 * inserted / total) if total else 90
                await job_tracker.set_progress(job_id, progress, f"{inserted}/{total} tickets ingested")

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "indexing", "Indexing final batch")
        # Final batch
        if chunks:
            vector_store.upsert_chunks(chunks)
            bm25_store.add_documents([
                {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "content": c.content,
                 "section_title": c.section_title, "doc_type": c.doc_type,
                 "department": c.department, "doc_origin": c.doc_origin,
                 "priority_tier": c.priority_tier, "source_file": filename}
                for c in chunks
            ])
        _flush_pg(pg_rows)

        await job_tracker.set_stage(job_id, "finalizing", "Finalizing")
        await job_tracker.mark_completed(job_id, {
            "status": "success", "file": filename, "tickets_ingested": inserted,
        })

    except JobCancelled:
        logger.info(f"Ticket ingestion job {job_id} ({filename}) cancelled by user "
                     f"(partial results up to the last completed batch were already saved)")
        await job_tracker.mark_cancelled(job_id)
    except asyncio.CancelledError:
        await job_tracker.mark_cancelled(job_id)
    except Exception as e:
        logger.exception(f"Ticket ingestion job {job_id} ({filename}) failed")
        await job_tracker.mark_failed(job_id, str(e))


@app.post("/ingest/tickets")
async def ingest_tickets(file: UploadFile = File(...)):
    """
    Start a job to ingest tickets.csv (P10: job-tracked, async).

    Returns immediately with `{job_id, filename, status: "queued"}` — poll
    `GET /ingest/jobs/{job_id}` for progress (row-based, ~20-90%) and result
    (`tickets_ingested`), or `POST /ingest/jobs/{job_id}/cancel` to cancel.

    Default expected columns: id, subject, description, priority, category,
    createdAt, requesterEmail. Optional columns (if present, used; otherwise
    sensibly defaulted): status, resolution, resolvedAt. Column names are
    matched case-insensitively against TICKET_COLUMN_ALIASES above — if your
    CSV uses different header names, add them there rather than renaming
    your file.

    Persists each row to Postgres `tickets` (for ticket_intelligence /
    ticket dual-path search) AND indexes a chunk per ticket into
    Qdrant + BM25 (for hybrid retrieval). Cancellation is cooperative and
    checked between 100-row batches — already-flushed batches remain
    ingested even if the job is cancelled partway through.
    """
    content = await file.read()
    job = await job_tracker.create_job(filename=file.filename, meta={"type": "tickets"})
    task = asyncio.create_task(_run_ticket_ingest_job(job.job_id, content, file.filename))
    job_tracker.register_task(job.job_id, task)
    return {"job_id": job.job_id, "filename": file.filename, "status": job.status}


def _map_category_to_dept(category: str) -> str:
    mapping = {
        "Network": "IT", "Hardware": "IT", "Software": "IT",
        "Access": "IT", "Email": "IT", "Security": "IT",
        "HR": "HR", "Payroll": "HR", "Leave": "HR",
        "Finance": "Finance", "Billing": "Finance",
    }
    return mapping.get(category, "IT")


# ── v2: Repository endpoints ───────────────────────────────────────────────────
@app.get("/repositories")
async def get_repositories():
    """List all knowledge repositories with document/chunk counts."""
    if not repo_service:
        raise HTTPException(503, "Repository service unavailable")
    repos = repo_service.get_all()
    return {"repositories": repos}


@app.get("/repositories/{name}/documents")
async def get_repo_documents(name: str, limit: int = 50):
    """Get documents in a specific repository."""
    if not repo_service:
        raise HTTPException(503, "Repository service unavailable")
    docs = repo_service.get_documents(name, limit=limit)
    return {"repository": name, "documents": docs, "count": len(docs)}


# ── v2: Documents list endpoint ────────────────────────────────────────────────
@app.get("/documents")
async def list_documents(
    limit: int = 100, repository: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    List ingested documents with metadata, filtered to documents the current
    user has access to (RBAC: document.access_roles ∩ user.access_roles).
    This matters for bundle creation — users should only be able to select
    and bundle documents they're actually allowed to see in chat retrieval.
    Admins (ADMIN/IT_ADMIN/EXECUTIVE) see everything.
    """
    from sqlalchemy import create_engine, text as sql_text

    user_roles = set(r.upper() for r in (current_user.access_roles or ["EMPLOYEE"]))
    user_roles.add((current_user.role or "").upper())
    is_admin = bool(user_roles & {"ADMIN", "IT_ADMIN", "EXECUTIVE"})

    try:
        engine = create_engine(settings.postgres_url)
        with engine.connect() as conn:
            if repository:
                rows = conn.execute(sql_text(
                    "SELECT d.doc_id, d.title, d.source_file, d.doc_type, d.department, "
                    "d.doc_origin, d.chunk_count, d.access_roles, d.ingested_at, r.name as repository "
                    "FROM documents d LEFT JOIN repositories r ON d.repository_id = r.repository_id "
                    "WHERE r.name = :repo ORDER BY d.ingested_at DESC LIMIT :limit"
                ), {"repo": repository, "limit": limit})
            else:
                rows = conn.execute(sql_text(
                    "SELECT d.doc_id, d.title, d.source_file, d.doc_type, d.department, "
                    "d.doc_origin, d.chunk_count, d.access_roles, d.ingested_at, "
                    "r.name as repository "
                    "FROM documents d LEFT JOIN repositories r ON d.repository_id = r.repository_id "
                    "ORDER BY d.ingested_at DESC LIMIT :limit"
                ), {"limit": limit})
            all_docs = [dict(r._mapping) for r in rows]

        if is_admin:
            docs = all_docs
        else:
            docs = [
                d for d in all_docs
                if not d.get("access_roles")  # no restriction = visible to all
                or (set(r.upper() for r in d["access_roles"]) & user_roles)
            ]
        return {"documents": docs, "total": len(docs)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── v2: Document detail (metadata + chunks) ────────────────────────────────────
@app.get("/documents/{doc_id}")
async def get_document(
    doc_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Document detail page: full metadata plus every chunk belonging to the
    document (content, LLM-generated summary, and keywords) so the UI can
    show "what's actually in this document" without a separate search.

    RBAC mirrors `list_documents`: admins (ADMIN/IT_ADMIN/EXECUTIVE) see any
    document; other users only see documents whose access_roles intersect
    their own (or documents with no access restriction).
    """
    from sqlalchemy import create_engine, text as sql_text

    user_roles = set(r.upper() for r in (current_user.access_roles or ["EMPLOYEE"]))
    user_roles.add((current_user.role or "").upper())
    is_admin = bool(user_roles & {"ADMIN", "IT_ADMIN", "EXECUTIVE"})

    try:
        engine = create_engine(settings.postgres_url)
        with engine.connect() as conn:
            row = conn.execute(sql_text(
                "SELECT d.doc_id, d.title, d.source_file, d.source_type, d.doc_type, "
                "d.department, d.doc_origin, d.chunk_count, d.access_roles, "
                "d.ingested_at, d.updated_at, d.checksum, d.file_path, "
                "r.name as repository, r.display_name as repository_display_name "
                "FROM documents d LEFT JOIN repositories r ON d.repository_id = r.repository_id "
                "WHERE d.doc_id = :doc_id"
            ), {"doc_id": doc_id}).fetchone()
    except Exception as e:
        raise HTTPException(500, str(e))

    if not row:
        raise HTTPException(404, "Document not found")

    doc = dict(row._mapping)

    if not is_admin:
        access_roles = doc.get("access_roles")
        if access_roles and not (set(r.upper() for r in access_roles) & user_roles):
            raise HTTPException(403, "You don't have access to this document")

    chunks = []
    if vector_store:
        try:
            raw_chunks = vector_store.get_chunks_by_doc(doc_id, source_file=doc.get("source_file"))
            for c in raw_chunks:
                chunks.append({
                    "chunk_id": c.get("chunk_id"),
                    "chunk_index": c.get("chunk_index"),
                    "section_title": c.get("section_title"),
                    "page_number": c.get("page_number"),
                    "content": c.get("content"),
                    "summary": c.get("chunk_summary") or "",
                    "keywords": c.get("chunk_keywords") or c.get("keywords") or [],
                    "questions": c.get("chunk_questions") or c.get("hypothetical_questions") or [],
                })
        except Exception as e:
            logger.warning(f"Failed to load chunks for {doc_id}: {e}")

    doc["chunks"] = chunks
    doc["chunks_found"] = len(chunks)
    # `has_file`: whether the original uploaded file is available to
    # download via GET /documents/{doc_id}/file. Don't leak the raw
    # filesystem path to the client.
    file_path = doc.pop("file_path", None)
    doc["has_file"] = bool(file_path and os.path.exists(file_path))
    return doc


@app.get("/documents/{doc_id}/file")
async def get_document_file(
    doc_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Serve the original uploaded file (PDF/DOCX/PPTX) for a document, if it
    was persisted at ingestion time (see `_run_ingest_job` ->
    `settings.uploaded_files_dir`).

    Documents ingested before this feature was added have `file_path = NULL`
    and return 404 — there is no original file to serve for those (their
    content is still viewable via the chunks on this page).

    RBAC mirrors `get_document`.
    """
    from sqlalchemy import create_engine, text as sql_text

    user_roles = set(r.upper() for r in (current_user.access_roles or ["EMPLOYEE"]))
    user_roles.add((current_user.role or "").upper())
    is_admin = bool(user_roles & {"ADMIN", "IT_ADMIN", "EXECUTIVE"})

    engine = create_engine(settings.postgres_url)
    with engine.connect() as conn:
        row = conn.execute(sql_text(
            "SELECT source_file, source_type, file_path, access_roles "
            "FROM documents WHERE doc_id = :doc_id"
        ), {"doc_id": doc_id}).fetchone()

    if not row:
        raise HTTPException(404, "Document not found")

    if not is_admin:
        access_roles = row.access_roles
        if access_roles and not (set(r.upper() for r in access_roles) & user_roles):
            raise HTTPException(403, "You don't have access to this document")

    if not row.file_path or not os.path.exists(row.file_path):
        raise HTTPException(404, "Original file not available for this document")

    filename = (row.source_file or "document").split("/")[-1]
    media_types = {
        "PDF": "application/pdf",
        "DOCX": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "PPTX": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    media_type = media_types.get((row.source_type or "").upper(), "application/octet-stream")

    return FileResponse(row.file_path, media_type=media_type, filename=filename)


class DocumentUpdateRequest(BaseModel):
    repository: Optional[str] = None      # repository `name` (e.g. "IT")
    doc_origin: Optional[str] = None       # INTERNAL | EXTERNAL
    access_roles: Optional[List[str]] = None


@app.patch("/documents/{doc_id}", dependencies=[Depends(require_roles("IT_ADMIN", "admin", "EXECUTIVE"))])
async def update_document(doc_id: str, req: DocumentUpdateRequest):
    """
    Admin: update a document's repository assignment, origin (INTERNAL/
    EXTERNAL), and/or RBAC access roles.

    Keeps three things in sync:
      1. `documents` row in Postgres (repository_id / doc_origin / access_roles)
      2. Qdrant chunk payloads for this doc (repository / doc_origin /
         access_roles) — retrieval-time RBAC and repository filtering read
         from Qdrant payloads, not Postgres, so this must match or edits
         here would have no effect on chat results.
      3. `repositories.document_count` / `chunk_count` stats (old + new repo)
    """
    from sqlalchemy import create_engine, text as sql_text

    if req.repository is None and req.doc_origin is None and req.access_roles is None:
        raise HTTPException(400, "No fields to update")

    if req.doc_origin and req.doc_origin not in ("INTERNAL", "EXTERNAL"):
        raise HTTPException(400, "doc_origin must be INTERNAL or EXTERNAL")

    engine = create_engine(settings.postgres_url)
    with engine.connect() as conn:
        existing = conn.execute(sql_text(
            "SELECT d.doc_id, d.repository_id, d.source_file, r.name as repository "
            "FROM documents d LEFT JOIN repositories r ON d.repository_id = r.repository_id "
            "WHERE d.doc_id = :doc_id"
        ), {"doc_id": doc_id}).fetchone()
        if not existing:
            raise HTTPException(404, "Document not found")

        set_clauses = ["updated_at = NOW()"]
        params: Dict[str, Any] = {"doc_id": doc_id}
        new_repo_id = None

        if req.repository is not None:
            repo_row = conn.execute(sql_text(
                "SELECT repository_id FROM repositories WHERE name = :name"
            ), {"name": req.repository}).fetchone()
            if not repo_row:
                raise HTTPException(400, f"Unknown repository: {req.repository}")
            new_repo_id = str(repo_row[0])
            set_clauses.append("repository_id = :repository_id")
            params["repository_id"] = new_repo_id

        if req.doc_origin is not None:
            set_clauses.append("doc_origin = :doc_origin")
            params["doc_origin"] = req.doc_origin

        if req.access_roles is not None:
            set_clauses.append("access_roles = :access_roles")
            params["access_roles"] = req.access_roles

        conn.execute(sql_text(
            f"UPDATE documents SET {', '.join(set_clauses)} WHERE doc_id = :doc_id"
        ), params)
        conn.commit()

        # Refresh stats for both old and new repository (doc may have moved)
        _refresh_repository_stats(conn)
        conn.commit()

        row = conn.execute(sql_text(
            "SELECT d.doc_id, d.title, d.source_file, d.doc_type, d.department, "
            "d.doc_origin, d.chunk_count, d.access_roles, d.ingested_at, "
            "r.name as repository, r.display_name as repository_display_name "
            "FROM documents d LEFT JOIN repositories r ON d.repository_id = r.repository_id "
            "WHERE d.doc_id = :doc_id"
        ), {"doc_id": doc_id}).fetchone()
        updated = dict(row._mapping)

    # Keep Qdrant payloads in sync so retrieval-time filtering matches
    if vector_store:
        qdrant_updates: Dict[str, Any] = {}
        if req.repository is not None:
            qdrant_updates["repository"] = req.repository
            # Citations/cascade fall back to chunk.department when
            # chunk.repository is empty on the payload (set once at
            # ingestion). Keep both in sync so badges/cascade scoping
            # reflect the new assignment immediately.
            qdrant_updates["department"] = req.repository
        if req.doc_origin is not None:
            qdrant_updates["doc_origin"] = req.doc_origin
        if req.access_roles is not None:
            qdrant_updates["access_roles"] = req.access_roles
        if qdrant_updates:
            try:
                vector_store.update_payload_for_doc(doc_id, qdrant_updates, source_file=existing.source_file)
            except Exception as e:
                logger.warning(f"Qdrant payload sync failed for {doc_id}: {e}")

    return updated


# ── v2: Ticket intelligence endpoints ─────────────────────────────────────────
@app.get("/tickets/search")
async def search_tickets_v2(
    q: str,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = 10,
):
    """Enhanced ticket search with known issue detection."""
    if not ticket_intelligence:
        raise HTTPException(503, "Ticket intelligence unavailable")
    result = ticket_intelligence.search_tickets(q, category, priority, limit)
    return result


@app.get("/tickets/recent")
async def recent_tickets(limit: int = 20, category: Optional[str] = None):
    """Get most recent tickets."""
    if not ticket_intelligence:
        raise HTTPException(503, "Ticket intelligence unavailable")
    tickets = ticket_intelligence.get_recent_tickets(limit, category)
    return {"tickets": tickets}


@app.get("/tickets/categories")
async def ticket_categories():
    """Ticket breakdown by category for dashboard."""
    if not ticket_intelligence:
        raise HTTPException(503, "Ticket intelligence unavailable")
    breakdown = ticket_intelligence.get_category_breakdown()
    total = ticket_intelligence.get_total_count()
    return {"categories": breakdown, "total": total}


# ── v2: Analytics endpoints ────────────────────────────────────────────────────
@app.get("/analytics/dashboard")
async def analytics_dashboard(days: int = 7, current_user: CurrentUser = Depends(get_current_user)):
    """Dashboard metrics: query volume, confidence, latency, repo usage.
    Recent Queries are scoped to the current user (when authenticated)."""
    if not analytics_service:
        raise HTTPException(503, "Analytics service unavailable")
    uid = current_user.user_id if current_user.user_id != "anonymous" else None
    return analytics_service.get_dashboard_metrics(days, user_id=uid)


@app.get("/analytics/evaluation")
async def evaluation_metrics():
    """Retrieval evaluation: hit rate, confidence distribution, P95 latency."""
    if not analytics_service:
        raise HTTPException(503, "Analytics service unavailable")
    return analytics_service.get_evaluation_metrics()


# ── v2: Ingest with repository assignment ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════
# Ingestion job processing (P10: job tracking / progress / cancellation)
#
# This function contains EXACTLY the same ingestion logic that previously
# lived directly in the `/ingest/file` route handler — idp_pipeline,
# chunk_document, enrich_chunk_async, image_pipeline, vector_store,
# bm25_store, and the Postgres persistence helpers are all called UNCHANGED.
# The only additions are:
#   - `job_tracker.set_stage(...)` calls marking progress for the admin UI
#   - `job_tracker.is_cancel_requested(...)` checks BETWEEN stages, which
#     raise JobCancelled to unwind cleanly without interrupting any single
#     ingestion/embedding/indexing call mid-flight.
#   - `_run_cancellable(...)` wraps the (potentially slow) per-chunk
#     enrichment `asyncio.gather()` calls so a cancel request lands within
#     ~0.5s even while enrichment is still running, instead of only being
#     observed once the whole gather completes.
# ══════════════════════════════════════════════════════════════════════════
async def _run_cancellable(job_id: str, awaitable, poll_interval: float = 0.5):
    """
    Run `awaitable` (typically an `asyncio.gather(...)` call) as a task,
    polling `job_tracker.is_cancel_requested(job_id)` every `poll_interval`
    seconds. If cancellation is requested while it's still running, the task
    (and therefore all of its sub-tasks) is cancelled and `JobCancelled` is
    raised. Otherwise returns the awaitable's result normally.

    This does NOT modify the enrichment/embedding code itself — it only
    controls how/when its result is awaited, so a cancel request doesn't have
    to wait for an entire (possibly slow) enrichment batch to finish before
    the job actually stops.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            if await job_tracker.is_cancel_requested(job_id):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                raise JobCancelled()
            await asyncio.wait({task}, timeout=poll_interval)
    except asyncio.CancelledError:
        # The outer job task itself was cancelled (e.g. while still QUEUED).
        task.cancel()
        raise
    return task.result()


async def _run_ingest_job(
    job_id: str,
    content: bytes,
    filename: str,
    ext: str,
    department: Optional[str],
    doc_origin: str,
    repository: Optional[str],
    access_roles: Optional[str],
    project_id: Optional[str],
    current_user: CurrentUser,
):
    tmp_path = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4()}{ext}")

    async def _check_cancelled():
        if await job_tracker.is_cancel_requested(job_id):
            raise JobCancelled()

    try:
        await job_tracker.set_stage(job_id, "saving_upload", f"Saving {filename}")
        with open(tmp_path, "wb") as f:
            f.write(content)

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "parsing_document", f"Parsing {filename}")
        if ext == ".pdf":
            raw_doc = process_pdf(tmp_path)
        elif ext == ".docx":
            raw_doc = process_docx(tmp_path)
        else:
            raw_doc = process_pptx(tmp_path)

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "classifying", "Classifying document")
        dept, doc_type, conf = classify_l1(filename)
        if department:
            dept = department

        # v2: resolve repository
        resolved_repo = repository
        if not resolved_repo and repo_service:
            resolved_repo = repo_service.map_department_to_repo(dept) or "External"

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "chunking", "Chunking document")
        # P7: pass `department` into chunk_document so PII redaction can apply
        # the IT/Engineering/Runbook technical-content preservation rules.
        chunks = chunk_document(raw_doc, doc_type, department=dept)

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "enriching_chunks", f"Enriching {len(chunks)} chunks")
        enrich_tasks = [enrich_chunk_async(c) for c in chunks]
        enriched_chunks = await _run_cancellable(
            job_id, asyncio.gather(*enrich_tasks, return_exceptions=True)
        )
        chunks = [c for c in enriched_chunks if isinstance(c, Chunk)]

        await _check_cancelled()
        # P9: Image-Aware Retrieval — extract embedded images/diagrams and
        # turn them into additional searchable "Diagram" chunks.
        await job_tracker.set_stage(job_id, "extracting_images", "Extracting embedded images")
        try:
            from ingestion.image_pipeline import extract_image_chunks
            image_chunks = await extract_image_chunks(tmp_path, raw_doc, ext)
            if image_chunks:
                enrich_img_tasks = [enrich_chunk_async(c) for c in image_chunks]
                enriched_img = await _run_cancellable(
                    job_id, asyncio.gather(*enrich_img_tasks, return_exceptions=True)
                )
                image_chunks = [c for c in enriched_img if isinstance(c, Chunk)]
                chunks.extend(image_chunks)
        except JobCancelled:
            raise
        except Exception as img_err:
            logger.warning(f"Image-aware ingestion skipped for {filename}: {img_err}")

        # Parse RBAC roles
        roles_list = ["EMPLOYEE", "MANAGER", "HR", "FINANCE", "IT_ADMIN", "EXECUTIVE"]
        if access_roles:
            roles_list = [r.strip() for r in access_roles.split(",")]

        uploaded_by = current_user.user_id if current_user.user_id != "anonymous" else "system"
        created_at = datetime.utcnow().isoformat()

        # ── P3: stamp the FULL metadata-flow field set onto every chunk ─────
        for chunk in chunks:
            chunk.department = dept
            chunk.doc_origin = doc_origin
            chunk.priority_tier = 1 if doc_origin == "INTERNAL" else 3
            chunk.repository = resolved_repo or chunk.department or "Unknown"
            chunk.access_roles = roles_list
            chunk.project_id = project_id or ""
            chunk.uploaded_by = uploaded_by
            chunk.created_at = created_at
            if not chunk.doc_type:
                chunk.doc_type = doc_type
            # `chunk.source_file` comes out of chunk_document() as the
            # temp filesystem path used during parsing (e.g.
            # "C:\Users\...\AppData\Local\Temp\<uuid>.pptx") — that's what
            # ends up in citations/sources ("Sources" panel, Document
            # Outline label, etc) and what get_chunks_by_doc()'s
            # source_file fallback compares against. Overwrite with the
            # original uploaded filename so it matches `documents
            # .source_file` in Postgres and is human-readable in the UI.
            chunk.source_file = filename

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "redacting_pii", "Auditing PII")
        # PII audit summary (DPDP/GDPR/HIPAA compliance logging)
        pii_summary: dict = {}
        for c in chunks:
            for ptype, count in getattr(c, "pii_audit", {}).items():
                pii_summary[ptype] = pii_summary.get(ptype, 0) + count
        if pii_summary:
            logger.info(f"PII hashed during ingestion of {filename}: {pii_summary}")

        await _check_cancelled()
        await job_tracker.set_stage(job_id, "indexing", f"Indexing {len(chunks)} chunks")
        indexed = vector_store.upsert_chunks(chunks)

        # ── P7: persist the hash->original-value PII vault to PROTECTED
        # storage (pii_vault table). NEVER written to Qdrant/BM25 payloads. ──
        _persist_pii_vault(chunks, raw_doc.doc_id, filename)

        bm25_store.add_documents([
            {
                "chunk_id": c.chunk_id, "doc_id": c.doc_id, "content": c.content,
                "section_title": c.section_title, "section_hierarchy": c.section_hierarchy,
                "page_number": c.page_number,
                "doc_type": c.doc_type, "department": c.department,
                "doc_origin": c.doc_origin, "priority_tier": c.priority_tier,
                "source_file": filename, "repository": c.repository,
                "access_roles": c.access_roles, "project_id": c.project_id,
                "uploaded_by": c.uploaded_by, "created_at": c.created_at,
                "is_image_chunk": c.is_image_chunk, "image_path": c.image_path,
            }
            for c in chunks
        ])

        await job_tracker.set_stage(job_id, "finalizing", "Saving document record")

        # Copy the uploaded file to persistent storage (named by doc_id so
        # it survives re-ingestion / filename collisions) so it can be
        # re-opened/downloaded from the Document Detail page. The temp
        # upload at `tmp_path` is still cleaned up in `finally` below either
        # way — this is a copy, not a move, so a failure here can't leave
        # `tmp_path` missing for any later step.
        persisted_file_path = None
        try:
            os.makedirs(settings.uploaded_files_dir, exist_ok=True)
            persisted_file_path = os.path.join(settings.uploaded_files_dir, f"{raw_doc.doc_id}{ext}")
            shutil.copyfile(tmp_path, persisted_file_path)
        except Exception as e:
            logger.warning(f"Could not persist uploaded file for {filename}: {e}")
            persisted_file_path = None

        # v2: persist document record to PostgreSQL
        _persist_document(
            filename, raw_doc.title, ext[1:].upper(),
            doc_type, dept, doc_origin, len(chunks),
            resolved_repo, roles_list, raw_doc.checksum,
            doc_id=raw_doc.doc_id, project_id=project_id, uploaded_by=uploaded_by,
            file_path=persisted_file_path,
        )

        image_chunk_count = sum(1 for c in chunks if c.is_image_chunk)

        result = {
            "status": "success",
            "file": filename,
            "doc_id": raw_doc.doc_id,
            "department": dept,
            "repository": resolved_repo or dept or "Unknown",
            "doc_type": doc_type,
            "classification_confidence": round(conf, 3),
            "chunks_created": len(chunks),
            "chunks_indexed": indexed,
            "image_chunks": image_chunk_count,
            "access_roles": roles_list,
            "project_id": project_id,
            "uploaded_by": uploaded_by,
            "pii_redacted": pii_summary,
        }
        await job_tracker.mark_completed(job_id, result)

    except JobCancelled:
        logger.info(f"Ingestion job {job_id} ({filename}) cancelled by user")
        await job_tracker.mark_cancelled(job_id)
    except asyncio.CancelledError:
        logger.info(f"Ingestion job {job_id} ({filename}) task cancelled")
        await job_tracker.mark_cancelled(job_id)
    except Exception as e:
        logger.exception(f"Ingestion job {job_id} ({filename}) failed")
        await job_tracker.mark_failed(job_id, str(e))
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _validate_ingest_file(filename: str) -> str:
    """Shared validation for /ingest/file and /ingest/bulk. Returns the
    lowercased extension or raises HTTPException(400)."""
    supported = {".pdf", ".docx", ".pptx"}
    ext = Path(filename).suffix.lower()
    if ext not in supported:
        raise HTTPException(400, f"Unsupported file type: {ext}. Supported: {supported}")
    return ext


# ── v2: Ingest with repository assignment (P10: job-tracked, async) ──────────
@app.post("/ingest/file")
async def ingest_file(
    file: UploadFile = File(...),
    department: Optional[str] = Form(None),
    doc_origin: str = Form("INTERNAL"),
    repository: Optional[str] = Form(None),  # v2: explicit repo override
    access_roles: Optional[str] = Form(None),  # v2: comma-separated RBAC roles
    project_id: Optional[str] = Form(None),  # P3: metadata-flow field
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload a single PDF/DOCX/PPTX and start an ingestion job.

    Returns immediately with `{job_id, filename, status: "queued"}`. The
    actual ingestion (parsing, chunking, embedding, indexing — all UNCHANGED
    from the previous implementation) runs in the background; poll
    `GET /ingest/jobs/{job_id}` for progress/result, or
    `POST /ingest/jobs/{job_id}/cancel` to cancel.

    P3/P7/P9 behaviour (metadata stamping, PII hashing, image-aware chunks)
    is unchanged — see `_run_ingest_job`.
    """
    ext = _validate_ingest_file(file.filename)
    content = await file.read()

    job = await job_tracker.create_job(
        filename=file.filename,
        meta={
            "department": department, "doc_origin": doc_origin,
            "repository": repository, "access_roles": access_roles,
            "project_id": project_id,
        },
    )
    task = asyncio.create_task(_run_ingest_job(
        job.job_id, content, file.filename, ext,
        department, doc_origin, repository, access_roles, project_id, current_user,
    ))
    job_tracker.register_task(job.job_id, task)

    return {"job_id": job.job_id, "filename": file.filename, "status": job.status}


# ── P10: Bulk upload — one job per file ──────────────────────────────────────
@app.post("/ingest/bulk")
async def ingest_bulk(
    files: List[UploadFile] = File(...),
    department: Optional[str] = Form(None),
    doc_origin: str = Form("INTERNAL"),
    repository: Optional[str] = Form(None),
    access_roles: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload multiple PDF/DOCX/PPTX files in one request.

    Each file becomes its own ingestion job (same `_run_ingest_job` as
    `/ingest/file`, run independently/concurrently), grouped under a shared
    `batch_id`. Returns immediately with one `{job_id, filename, status}`
    entry per file — poll `GET /ingest/jobs?batch_id=...` for overall
    progress, or cancel individual jobs via
    `POST /ingest/jobs/{job_id}/cancel`.

    Files with an unsupported extension are reported with
    `status: "rejected"` and do NOT get a job (and don't block the rest of
    the batch).
    """
    if not files:
        raise HTTPException(400, "No files provided")

    batch_id = str(uuid.uuid4())
    meta = {
        "department": department, "doc_origin": doc_origin,
        "repository": repository, "access_roles": access_roles,
        "project_id": project_id,
    }

    jobs_out = []
    for file in files:
        try:
            ext = _validate_ingest_file(file.filename)
        except HTTPException as e:
            jobs_out.append({
                "job_id": None, "filename": file.filename,
                "status": "rejected", "error": e.detail,
            })
            continue

        content = await file.read()
        job = await job_tracker.create_job(filename=file.filename, batch_id=batch_id, meta=meta)
        task = asyncio.create_task(_run_ingest_job(
            job.job_id, content, file.filename, ext,
            department, doc_origin, repository, access_roles, project_id, current_user,
        ))
        job_tracker.register_task(job.job_id, task)
        jobs_out.append({"job_id": job.job_id, "filename": file.filename, "status": job.status})

    return {"batch_id": batch_id, "jobs": jobs_out}


# ── P10: Bulk upload via ZIP archive — one job per supported file inside ─────
@app.post("/ingest/bulk/zip")
async def ingest_bulk_zip(
    file: UploadFile = File(...),
    department: Optional[str] = Form(None),
    doc_origin: str = Form("INTERNAL"),
    repository: Optional[str] = Form(None),
    access_roles: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Upload a single .zip archive containing multiple PDF/DOCX/PPTX files.

    Mirrors `scripts/bulk_ingest.py --folder` (same supported extensions:
    .pdf, .docx, .pptx), but server-side: the archive is read entirely in
    memory, each supported entry becomes its own ingestion job (the same
    `_run_ingest_job` used by `/ingest/file`), and all jobs share one
    `batch_id`.

    Directory entries, hidden files (dotfiles), and `__MACOSX/` metadata are
    skipped. Unsupported file types inside the archive are reported with
    `status: "rejected"` and don't block the rest of the batch.

    Poll `GET /ingest/jobs?batch_id=...` for overall progress. Cancel
    individual files via `POST /ingest/jobs/{job_id}/cancel`, or the whole
    batch via `POST /ingest/batches/{batch_id}/cancel`.
    """
    if Path(file.filename).suffix.lower() != ".zip":
        raise HTTPException(400, f"Expected a .zip archive, got: {file.filename}")

    content = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Uploaded file is not a valid ZIP archive")

    supported = {".pdf", ".docx", ".pptx"}
    batch_id = str(uuid.uuid4())
    meta = {
        "department": department, "doc_origin": doc_origin,
        "repository": repository, "access_roles": access_roles,
        "project_id": project_id, "source_zip": file.filename,
    }

    jobs_out = []
    for info in zf.infolist():
        if info.is_dir():
            continue

        entry_name = info.filename
        base = os.path.basename(entry_name)
        # Skip directory placeholder entries, hidden files, and macOS
        # archive metadata (__MACOSX/._foo.pdf etc.)
        if not base or base.startswith(".") or "__MACOSX" in entry_name:
            continue

        entry_ext = Path(base).suffix.lower()
        if entry_ext not in supported:
            jobs_out.append({
                "job_id": None, "filename": entry_name,
                "status": "rejected", "error": f"Unsupported file type: {entry_ext or '(none)'}",
            })
            continue

        try:
            entry_bytes = zf.read(info)
        except Exception as e:
            jobs_out.append({
                "job_id": None, "filename": entry_name,
                "status": "rejected", "error": f"Failed to read from archive: {e}",
            })
            continue

        job = await job_tracker.create_job(filename=base, batch_id=batch_id, meta=meta)
        task = asyncio.create_task(_run_ingest_job(
            job.job_id, entry_bytes, base, entry_ext,
            department, doc_origin, repository, access_roles, project_id, current_user,
        ))
        job_tracker.register_task(job.job_id, task)
        jobs_out.append({"job_id": job.job_id, "filename": base, "status": job.status})

    if not jobs_out:
        raise HTTPException(400, "ZIP archive contains no supported files (.pdf, .docx, .pptx)")

    return {"batch_id": batch_id, "jobs": jobs_out}
@app.get("/ingest/jobs/{job_id}")
async def get_ingest_job(job_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Poll the status/progress/result of one ingestion job."""
    job = await job_tracker.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@app.get("/ingest/jobs")
async def list_ingest_jobs(
    batch_id: Optional[str] = None,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
):
    """List recent ingestion jobs (optionally filtered by `batch_id`),
    most-recent first. Used by the admin UI to render bulk-upload progress
    and a recent-uploads list."""
    jobs = await job_tracker.list_jobs(batch_id=batch_id, limit=limit)
    return {"jobs": [j.to_dict() for j in jobs]}


# ── P10: Job cancellation ─────────────────────────────────────────────────────
@app.post("/ingest/jobs/{job_id}/cancel")
async def cancel_ingest_job(job_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Request cancellation of an ingestion job.

    Cooperative: a job already past the next stage-boundary check will finish
    that stage before stopping. Jobs still QUEUED are cancelled instantly.
    Returns 409 if the job is already in a terminal state (completed/failed/
    already cancelled)."""
    job = await job_tracker.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    ok = await job_tracker.request_cancel(job_id)
    if not ok:
        raise HTTPException(409, f"Job is already {job.status} and cannot be cancelled")
    return {"job_id": job_id, "status": JobStatus.CANCELLING.value}


@app.post("/ingest/batches/{batch_id}/cancel")
async def cancel_ingest_batch(batch_id: str, current_user: CurrentUser = Depends(get_current_user)):
    """Cancel every still-active job in a batch (from `/ingest/bulk` or
    `/ingest/bulk/zip`). Jobs already completed/failed/cancelled are left
    untouched. Returns the job_ids that were cancelled vs. skipped."""
    jobs = await job_tracker.list_jobs(batch_id=batch_id, limit=1000)
    if not jobs:
        raise HTTPException(404, "Batch not found")

    cancelled, skipped = [], []
    for job in jobs:
        ok = await job_tracker.request_cancel(job.job_id)
        (cancelled if ok else skipped).append(job.job_id)

    return {"batch_id": batch_id, "cancelled": cancelled, "skipped": skipped}


def _persist_pii_vault(chunks: List[Chunk], doc_id: str, source_file: str):
    """
    P7: persist hash_token -> original_value mappings to the PROTECTED
    `pii_vault` table (ingestion/migration_v6.sql). This table is NEVER read
    by the retrieval path and its contents are NEVER returned via /chat or
    /ingest responses — only privileged compliance tooling should query it.
    Non-fatal: ingestion succeeds even if this table doesn't exist yet
    (pre-migration) or Postgres is unavailable.
    """
    rows = []
    for c in chunks:
        vault = getattr(c, "pii_vault", None) or {}
        hash_map = getattr(c, "pii_hash_map", None) or {}
        for token, original_value in vault.items():
            rows.append({
                "hash_token": token,
                "entity_type": hash_map.get(token, "unknown"),
                "original_value": original_value,
                "doc_id": doc_id,
                "chunk_id": c.chunk_id,
                "source_file": source_file,
            })
    if not rows:
        return
    try:
        from sqlalchemy import create_engine, text as sql_text
        engine = create_engine(settings.postgres_url)
        with engine.connect() as conn:
            for row in rows:
                conn.execute(sql_text(
                    "INSERT INTO pii_vault (hash_token, entity_type, original_value, "
                    "doc_id, chunk_id, source_file, created_at) "
                    "VALUES (:hash_token, :entity_type, :original_value, :doc_id, "
                    ":chunk_id, :source_file, now()) "
                    "ON CONFLICT (hash_token) DO NOTHING"
                ), row)
            conn.commit()
        logger.info(f"PII vault: persisted {len(rows)} hash->value entries for {source_file}")
    except Exception as e:
        logger.warning(f"PII vault persist skipped (non-fatal, run migration_v6.sql?): {e}")


def _persist_document(
    source_file, title, source_type, doc_type, department,
    doc_origin, chunk_count, repository_name, access_roles, checksum,
    doc_id=None, project_id=None, uploaded_by=None, file_path=None,
):
    """Persist document record and update repository stats.

    P3: `doc_id` is the SAME id stamped onto every Chunk / Qdrant payload
    (raw_doc.doc_id) — previously this table generated its own UUID, so the
    Postgres catalog's doc_id never matched the doc_id used by retrieval and
    citations. `project_id` / `uploaded_by` are also persisted here so the
    document catalog carries the full metadata-flow field set.

    `file_path`: path to the original uploaded file as copied into
    `settings.uploaded_files_dir` by `_run_ingest_job` (or None if that copy
    failed/wasn't attempted) — lets `GET /documents/{doc_id}/file` serve the
    original document.
    """
    try:
        from sqlalchemy import create_engine, text as sql_text
        engine = create_engine(settings.postgres_url)
        with engine.connect() as conn:
            # Get repository_id
            repo_row = conn.execute(sql_text(
                "SELECT repository_id FROM repositories WHERE name = :name"
            ), {"name": repository_name}).fetchone()
            repo_id = str(repo_row[0]) if repo_row else None

            conn.execute(sql_text(
                "INSERT INTO documents (doc_id, title, source_file, source_type, doc_type, "
                "department, doc_origin, chunk_count, repository_id, access_roles, checksum, "
                "project_id, uploaded_by, file_path, status) "
                "VALUES (:doc_id, :title, :sf, :st, :dt, :dept, :origin, :cc, :rid, :roles, "
                ":chk, :pid, :uploaded_by, :file_path, 'READY') "
                "ON CONFLICT (doc_id) DO NOTHING"
            ), {
                "doc_id": doc_id, "title": title[:512], "sf": source_file, "st": source_type,
                "dt": doc_type, "dept": department, "origin": doc_origin,
                "cc": chunk_count, "rid": repo_id, "roles": access_roles,
                "chk": checksum, "pid": project_id, "uploaded_by": uploaded_by,
                "file_path": file_path,
            })
            conn.commit()

            # Refresh stats
            if repo_id:
                _refresh_repository_stats(conn)
                conn.commit()
    except Exception as e:
        logger.warning(f"Document persist failed (non-fatal): {e}")


# ── Health check (v2 extended) ────────────────────────────────────────────────
@app.get("/status")
async def status():
    """v2 system health with repository stats."""
    qdrant_info = {}
    try:
        qdrant_info = vector_store.get_collection_info()
    except Exception as e:
        qdrant_info = {"error": str(e)}

    ollama_ok = await llm_service.check_ollama()
    repos = []
    if repo_service:
        try:
            repos = repo_service.get_all()
        except Exception:
            pass

    return {
        "status": "running",
        "version": "2.0",
        "model": settings.ollama_model,
        "ollama_ready": ollama_ok,
        "qdrant": qdrant_info,
        "bm25_docs": bm25_store.doc_count if bm25_store else 0,
        "embedding_model": settings.embedding_model,
        "embedding_dim": settings.embedding_dim,
        "repositories": [{"name": r["name"], "docs": r["document_count"]} for r in repos],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host=settings.app_host, port=settings.app_port, reload=True)