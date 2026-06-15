"""
retrieval/hybrid_engine.py  —  Full LLD-Compliant Retrieval Pipeline

Exact pipeline from LLD:
  Dense Retrieval     — BGE content vectors (top 20, per sub-query)
  Question Retrieval  — BGE HyDE question vectors (top 20)
  BM25 Retrieval      — Keyword sparse (top 20)
  RRF Fusion          — Reciprocal Rank Fusion k=60 with priority boosts
  Source Weighting    — INTERNAL 1.30× TIER1, 1.15× TIER2 | EXTERNAL 1.00×
  Cross Encoder       — ms-marco-MiniLM-L-6-v2 reranker → Top 8
  Context Builder     — Token budget, max 3 chunks/doc, freshness decay, feedback boost

Priority Tiers (doc_origin-based):
  Tier 1: INTERNAL authored SOPs, policies, runbooks  → RRF ×1.30, additive +0.05
  Tier 2: INTERNAL comms, Slack, email threads        → RRF ×1.15, additive +0.02
  Tier 3: EXTERNAL vendor PDFs, uploaded docs         → RRF ×1.00, additive  0.00
  Tier 4: EXTERNAL crawled public portals             → RRF ×0.85, additive -0.05
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import tiktoken

from configs.settings import get_settings
from retrieval.bm25_store import BM25Store
from retrieval.query_understanding import QueryContext
from retrieval.vector_store import VectorStore

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Priority boost tables ─────────────────────────────────────────────────────
_RRF_BOOST  = {1: 1.30, 2: 1.15, 3: 1.00, 4: 0.85}
_ADDITIVE   = {1: 0.05, 2: 0.02, 3: 0.00, 4: -0.05}

# ── RRF source-list influence multiplier ──────────────────────────────────────
# BM25 gets a slight boost relative to dense/question/summary lists so exact
# keyword matches aren't drowned out by semantically-similar-but-off-target
# vector hits. Applied on top of the existing tier-based _RRF_BOOST.
_RRF_SOURCE_MULTIPLIER = {
    "bm25": 1.15,
}

# ── P2: soft entity-boost cap (NEVER used as a hard filter) ───────────────────
# Final cap on the combined additive entity/department/repository/ticket
# boosts applied to a chunk's final_score.
_ENTITY_BOOST_CAP        = 0.30

# Additive scoring boosts per LLD scoring example (entity/department/
# repository/ticket matches). These STACK with _ENTITY_BOOST_* above and with
# query_ctx.retrieval_signal weights — all additive, never exclusionary.
_SCORE_BOOST_ENTITY_MATCH      = 0.10
_SCORE_BOOST_DEPARTMENT_MATCH  = 0.08
_SCORE_BOOST_REPOSITORY_MATCH  = 0.12
_SCORE_BOOST_TICKET_MATCH      = 0.25

# ── P5: ambiguous-query heuristic (broadens HyDE triggers beyond "short query")
_AMBIGUOUS_TOKENS = {
    "it", "this", "that", "these", "those", "they", "them", "there", "here",
    "one", "ones", "thing", "stuff", "such", "same", "above", "below",
    "previous", "former", "latter", "again", "also", "too",
}
_WORD_RE = re.compile(r"[a-z']+")

# ── Token counter ─────────────────────────────────────────────────────────────
_tokenizer = tiktoken.get_encoding("cl100k_base")

def _token_count(text: str) -> int:
    return len(_tokenizer.encode(text))

# ── Freshness decay formula (LLD §4.5) ────────────────────────────────────────
# decay = −0.01 × floor(doc_age_months / 6), capped at −0.08
def _freshness_decay(doc_age_days: int) -> float:
    months = doc_age_days / 30.0
    return max(-0.08, -0.01 * math.floor(months / 6))


def _doc_age_days(ingested_at: str) -> int:
    """Compute age in days from an ISO timestamp; 0 if unavailable/invalid."""
    if not ingested_at:
        return 0
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ingested_at)
        return max(0, (datetime.utcnow() - dt).days)
    except Exception:
        return 0


def _basename(path: str) -> str:
    """Filename portion of `path`, handling both POSIX ('/') and Windows
    ('\\') separators — chunks ingested on Windows can have `source_file`
    values like "C:\\Users\\...\\Temp\\<uuid>.pptx", where `.split("/")[-1]`
    alone returns the whole path unchanged."""
    if not path:
        return path
    return path.replace("\\", "/").split("/")[-1]


# ════════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ════════════════════════════════════════════════════════════════════════════════
@dataclass
class RetrievedChunk:
    chunk_id: str
    content: str
    source_file: str
    section_title: str
    page_number: int
    doc_type: str
    department: str
    doc_origin: str
    priority_tier: int
    rrf_score: float = 0.0
    rerank_score: float = 0.0
    freshness_decay: float = 0.0
    feedback_boost: float = 0.0
    entity_boost: float = 0.0
    final_score: float = 0.0
    keywords: List[str] = field(default_factory=list)
    hypothetical_questions: List[str] = field(default_factory=list)
    repository: str = ""
    access_roles: List[str] = field(default_factory=list)
    retrieval_source: str = ""      # "dense" | "question" | "bm25" | "multi"
    ingested_at: str = ""
    # ── P3: full metadata flow fields (ingestion -> Qdrant -> retrieval -> citation) ──
    doc_id: str = ""
    project_id: str = ""
    uploaded_by: str = ""
    origin: str = ""                       # alias of doc_origin, carried separately per LLD field list
    section_hierarchy: List[str] = field(default_factory=list)
    created_at: str = ""
    # ── P7: PII vault reference (if chunk content contains hashed PII tokens) ──
    pii_hash_map: Dict[str, str] = field(default_factory=dict)
    # ── P9: image-aware retrieval ──
    is_image_chunk: bool = False
    image_path: str = ""


@dataclass
class ContextChunk:
    """A chunk selected for the LLM context window."""
    chunk: RetrievedChunk
    token_count: int
    source_label: str              # "[Source N] filename | section | page"
    is_stale: bool = False         # True if doc_age > 180 days


@dataclass
class RetrievalResult:
    chunks: List[RetrievedChunk]
    query_context: QueryContext
    confidence: float
    low_confidence: bool
    latency_ms: int
    total_candidates: int
    repositories_searched: List[str] = field(default_factory=list)
    expanded_queries: List[str] = field(default_factory=list)
    # Pipeline breakdown for UI transparency
    pipeline_stats: Dict[str, int] = field(default_factory=dict)
    # P8: structured retrieval trace (per-stage counts/scores for the trace panel)
    retrieval_trace: Dict[str, Any] = field(default_factory=dict)
    # P4: which document-specific-retrieval cascade level produced this result
    # 1 = selected document, 2 = repository scope, 3 = department scope, 4 = global
    scope_level: int = 4
    scope_label: str = "GLOBAL"


@dataclass
class BuiltContext:
    context_text: str
    chunks: List[ContextChunk]
    total_tokens: int
    citations: List[Dict]


# ════════════════════════════════════════════════════════════════════════════════
# HYBRID RETRIEVAL ENGINE
# ════════════════════════════════════════════════════════════════════════════════
class HybridRetrievalEngine:
    """
    Full LLD-compliant hybrid retrieval:
    Dense + Question + BM25 → RRF+Boost → CrossEncoder → ContextBuilder
    """

    def __init__(
        self,
        vector_store: VectorStore,
        bm25_store: BM25Store,
        feedback_service=None,     # optional FeedbackService for boost
        hyde_service=None,         # optional HyDEService (P5: invoked internally)
    ):
        self.vector_store = vector_store
        self.bm25_store = bm25_store
        self.feedback_service = feedback_service
        self.hyde_service = hyde_service
        self._reranker = None

    def _get_reranker(self):
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            logger.info(f"Loading CrossEncoder reranker: {settings.reranker_model}")
            self._reranker = CrossEncoder(settings.reranker_model, max_length=512)
            logger.info("Reranker ready")
        return self._reranker

    # ══════════════════════════════════════════════════════════════════════════
    # PUBLIC: retrieve()
    # ══════════════════════════════════════════════════════════════════════════
    async def retrieve(
        self,
        query_ctx: QueryContext,
        top_k: int = None,
        include_external: bool = False,
        rbac_roles: Optional[List[str]] = None,
        # ── P4: document-specific retrieval cascade controls ──────────────────
        # scope_repository / scope_department are HARD filters applied ONLY by
        # the explicit cascade orchestration in retrieve_cascading() /
        # api/main.py. They are distinct from entity-extraction signals (P2),
        # which are NEVER hard filters.
        scope_repository: Optional[str] = None,
        scope_department: Optional[str] = None,
        skip_doc_scope: bool = False,
        scope_level: int = 4,
        scope_label: str = "GLOBAL",
    ) -> RetrievalResult:
        t_start = time.time()
        top_k = top_k or settings.reranker_top_k

        # ── SUMMARIZE: broad retrieval + larger context window ────────────────
        # SUMMARIZE wants a wider net (more chunks survive to reranking/context)
        # rather than a narrowly-targeted top-k. Does not affect filters/scope.
        if query_ctx.intent == "SUMMARIZE":
            top_k = max(top_k, getattr(settings, "summarize_top_k", 16))

        trace: Dict[str, Any] = {"scope_level": scope_level, "scope_label": scope_label}

        # ── P2: Build metadata filters — NEVER include entity-derived
        # department/repository here. Only structural filters survive:
        #   - doc_type   : e.g. TICKET_LOOKUP -> "Ticket" (intentional hard filter)
        #   - doc_origin : dual-pass internal/full-corpus split (LLD §4.1-4.3)
        #   - access_roles : RBAC (if provided)
        # ─────────────────────────────────────────────────────────────────────
        base_filters: Dict[str, Any] = {}
        if query_ctx.filters.get("doc_type"):
            base_filters["doc_type"] = query_ctx.filters["doc_type"]
        if rbac_roles:
            base_filters["access_roles"] = rbac_roles

        qdrant_filters = {k: v for k, v in base_filters.items()
                          if k in ("doc_type", "doc_origin", "access_roles")}

        # ── P4: Document-Specific Retrieval cascade scoping ────────────────────
        # Level 1 (doc_id) takes precedence; Level 2 (repository) and Level 3
        # (department) are HARD filters set explicitly by retrieve_cascading().
        # Level 4 = no scope filter at all (global).
        if not skip_doc_scope and query_ctx.active_document_ids:
            qdrant_filters["doc_id"] = query_ctx.active_document_ids
            trace["scope_filter"] = {"type": "doc_id", "value": query_ctx.active_document_ids}

            # ── Document/bundle-scoped SUMMARIZE shortcut ──────────────────────
            # A generic instruction like "summarise the selected document" has
            # almost no semantic/lexical overlap with the document's own
            # content, so dense/BM25/rerank against that literal query return
            # near-zero scores and every candidate gets dropped by
            # final_score_threshold (LLD §6.3) — the user sees "couldn't find
            # enough relevant information" even though the scoped document(s)
            # plainly have plenty of chunks (see retrieval_trace.dense_results
            # etc. = 0/low and final_chunks = 0). When the user has explicitly
            # scoped to specific document(s) (Document picker or a Bundle) AND
            # intent == SUMMARIZE, skip similarity search/reranking entirely
            # and pull the documents' chunks directly.
            if query_ctx.intent == "SUMMARIZE":
                return self._direct_document_retrieval(
                    query_ctx, query_ctx.active_document_ids, top_k,
                    t_start, scope_level, scope_label,
                )
        elif scope_repository:
            qdrant_filters["repository"] = scope_repository
            trace["scope_filter"] = {"type": "repository", "value": scope_repository}
        elif scope_department:
            qdrant_filters["department"] = scope_department
            trace["scope_filter"] = {"type": "department", "value": scope_department}
        else:
            trace["scope_filter"] = None

        # ── P5: HyDE — broadened trigger conditions ────────────────────────────
        # Trigger when ANY of:
        #   - first-pass retrieval score is below the internal-pass cosine floor
        #   - first-pass retrieval score is below the low-confidence threshold
        #   - the query is ambiguous (vague pronouns / very short / referential)
        #   - intent == SUMMARIZE (document-level match desired)
        #   - query token_count < 5 (kept as one signal, not the only one)
        first_pass_score = 0.0
        if not query_ctx.hyde_used:
            try:
                probe = self.vector_store.search(
                    query=query_ctx.cleaned_query, top_k=1,
                    filters={"doc_origin": "INTERNAL"}, vector_name="content",
                )
                first_pass_score = probe[0]["score"] if probe else 0.0
            except Exception as e:
                logger.debug(f"HyDE first-pass probe failed: {e}")
                first_pass_score = 0.0

            token_count = len(query_ctx.cleaned_query.split())
            ambiguous = self._is_ambiguous_query(query_ctx.cleaned_query, token_count)
            should_hyde = (
                first_pass_score < settings.internal_pass_min_cosine
                or first_pass_score < settings.low_confidence_threshold
                or ambiguous
                or token_count < 5
            )

            if should_hyde and self.hyde_service is not None:
                passage, used = await self.hyde_service.generate(
                    query=query_ctx.cleaned_query,
                    intent=query_ctx.intent,
                    token_count=token_count,
                    first_pass_score=first_pass_score,
                    force=True,
                )
                query_ctx.hyde_passage = passage
                query_ctx.hyde_used = used
            else:
                used = False

            trace["hyde"] = {
                "first_pass_score": round(first_pass_score, 4),
                "ambiguous": ambiguous,
                "triggered": should_hyde,
                "used": query_ctx.hyde_used,
            }
            for step in query_ctx.pipeline_trace:
                if step.get("step") == 7:
                    step["output"] = (
                        (query_ctx.hyde_passage[:100] + "...")
                        if len(query_ctx.hyde_passage) > 100 else query_ctx.hyde_passage
                    )
                    step["used"] = query_ctx.hyde_used
                    step["first_pass_score"] = round(first_pass_score, 4)
                    step["trigger_reason"] = trace["hyde"]
                    break
        else:
            trace["hyde"] = {"used": True, "note": "hyde_passage already set by caller"}

        # ── Determine all queries to search ──────────────────────────────────
        # Primary query = HyDE passage (if generated) else cleaned query
        primary_query = query_ctx.hyde_passage if query_ctx.hyde_used else query_ctx.cleaned_query

        # For dense search: primary + expansions + sub-queries
        dense_queries = [primary_query]
        if query_ctx.expanded_queries:
            dense_queries.extend(query_ctx.expanded_queries[1:3])  # up to 2 expansions
        if query_ctx.is_decomposed and query_ctx.sub_queries:
            dense_queries.extend(query_ctx.sub_queries[:2])

        # Deduplicate
        seen_q, unique_queries = set(), []
        for q in dense_queries:
            if q not in seen_q:
                seen_q.add(q)
                unique_queries.append(q)

        # ── DUAL-PASS RETRIEVAL (LLD §4.1-4.3) ────────────────────────────────
        # 4.1 ANN Internal-only pass (top 15, cosine >= 0.70)
        internal_filters = dict(qdrant_filters)
        internal_filters["doc_origin"] = "INTERNAL"

        internal_pass_raw = self.vector_store.search(
            query=primary_query,
            top_k=settings.internal_pass_top_k,
            filters=internal_filters,
            vector_name="content",
        )
        internal_pass = [
            r for r in internal_pass_raw
            if r.get("score", 0.0) >= settings.internal_pass_min_cosine
        ]

        # 4.2 Short-circuit: skip full-corpus pass if internal pass is sufficient
        internal_pass_sufficient = len(internal_pass) >= settings.internal_pass_min_results
        skip_full_corpus = internal_pass_sufficient and not include_external

        # ── DENSE RETRIEVAL (content vectors) ─────────────────────────────────
        all_dense: List[Dict] = []
        for r in internal_pass:
            r["retrieval_source"] = "dense_internal"
            all_dense.append(r)

        if not skip_full_corpus:
            for q in unique_queries:
                # 4.3 ANN full-corpus pass (top 20)
                results = self.vector_store.search(
                    query=q,
                    top_k=settings.dense_top_k,
                    filters=qdrant_filters or None,
                    vector_name="content",
                )
                for r in results:
                    r["retrieval_source"] = "dense"
                    all_dense.append(r)

        dense_results = self._dedup_by_chunk_id(all_dense, settings.dense_top_k)

        dual_pass_stats = {
            "internal_pass_count": len(internal_pass),
            "internal_pass_sufficient": internal_pass_sufficient,
            "full_corpus_pass_skipped": skip_full_corpus,
        }

        # ── QUESTION/HyDE VECTOR RETRIEVAL ────────────────────────────────────
        question_results: List[Dict] = []
        if not skip_full_corpus:
            question_query = query_ctx.hyde_passage if query_ctx.hyde_used else query_ctx.cleaned_query
            raw_question = self.vector_store.search(
                query=question_query,
                top_k=settings.dense_top_k,
                filters=qdrant_filters or None,
                vector_name="question",
            )
            for r in raw_question:
                r["retrieval_source"] = "question"
            question_results = raw_question

        # ── SUMMARY VECTOR RETRIEVAL ───────────────────────────────────────────
        # Each chunk also has an LLM-generated `chunk_summary` embedded as a
        # "summary" named vector (see retrieval/vector_store.py). Searching it
        # too — and folding it into RRF alongside dense/question/bm25 — gives a
        # "two-way"/multi-vector match: a query can hit either a chunk's literal
        # content/wording (content vector, BM25) OR what the chunk is *about*
        # (summary vector), which helps overview-style ("what does X cover",
        # "explain Y") and keyword-light questions that wouldn't score well
        # against raw chunk text alone.
        summary_results: List[Dict] = []
        if not skip_full_corpus:
            summary_query = query_ctx.hyde_passage if query_ctx.hyde_used else query_ctx.cleaned_query
            raw_summary = self.vector_store.search(
                query=summary_query,
                top_k=settings.dense_top_k,
                filters=qdrant_filters or None,
                vector_name="summary",
            )
            for r in raw_summary:
                r["retrieval_source"] = "summary"
            summary_results = raw_summary

        # ── BM25 RETRIEVAL ────────────────────────────────────────────────────
        # BM25 must NEVER be skipped — even when the internal-pass dense
        # results are sufficient and the full-corpus dense/question/summary
        # passes are skipped, keyword recall from BM25 still runs so exact
        # term matches (policy names, ticket refs, acronyms) aren't lost.
        bm25_results: List[Dict] = []
        bm25_query = query_ctx.safe_query.lower()
        # For BM25: also search expanded queries to improve keyword coverage
        all_bm25: List[Dict] = []
        bm25_queries = [bm25_query] + [q.lower() for q in (query_ctx.expanded_queries or [])[:1]]
        # P4: BM25 must respect the same document/repository/department scope
        # as the dense passes — otherwise out-of-scope chunks can dilute RRF.
        bm25_doc_ids = qdrant_filters.get("doc_id")
        bm25_repository = qdrant_filters.get("repository")
        bm25_department = qdrant_filters.get("department")
        for q in bm25_queries:
            results = self.bm25_store.search(
                q, top_k=settings.bm25_top_k,
                doc_ids=bm25_doc_ids,
                repository=bm25_repository,
                department=bm25_department,
            )
            for r in results:
                r["retrieval_source"] = "bm25"
            all_bm25.extend(results)
        bm25_results = self._dedup_by_chunk_id(all_bm25, settings.bm25_top_k)

        # ── P1: DEBUG LOG — stage 1: raw retrieval counts ──────────────────────
        logger.info(
            "[RETRIEVAL DEBUG] stage=retrieved scope=%s level=%d dense=%d question=%d "
            "summary=%d bm25=%d internal_pass=%d skip_full_corpus=%s hyde_used=%s",
            scope_label, scope_level, len(dense_results), len(question_results),
            len(summary_results), len(bm25_results), len(internal_pass), skip_full_corpus, query_ctx.hyde_used,
        )

        # ── RRF FUSION + SOURCE WEIGHTING ────────────────────────────────────
        candidates = self._rrf_fusion(
            lists=[
                ("dense", dense_results),
                ("question", question_results),
                ("summary", summary_results),
                ("bm25", bm25_results),
            ],
            k=settings.rrf_k,
        )

        pipeline_stats = {
            "dense": len(dense_results),
            "question": len(question_results),
            "summary": len(summary_results),
            "bm25": len(bm25_results),
            "rrf_candidates": len(candidates),
            **dual_pass_stats,
        }
        trace["dense_results"] = len(dense_results)
        trace["question_results"] = len(question_results)
        trace["summary_results"] = len(summary_results)
        trace["bm25_results"] = len(bm25_results)
        trace["rrf_candidates"] = len(candidates)
        trace["dual_pass"] = dual_pass_stats

        if not candidates:
            logger.info(
                "[RETRIEVAL DEBUG] stage=rrf_empty scope=%s level=%d candidates=0 "
                "-> returning empty result (will surface as low_confidence)",
                scope_label, scope_level,
            )
            return RetrievalResult(
                chunks=[], query_context=query_ctx, confidence=0.0,
                low_confidence=True,
                latency_ms=int((time.time() - t_start) * 1000),
                total_candidates=0,
                pipeline_stats=pipeline_stats,
                retrieval_trace=trace,
                scope_level=scope_level,
                scope_label=scope_label,
            )

        # ── CROSS ENCODER RERANKING ───────────────────────────────────────────
        # LLD: up to 44 pairs normally; SUMMARIZE gets a wider pool (broad
        # retrieval) so the larger context window has more to draw from.
        rerank_pool = 44 if query_ctx.intent != "SUMMARIZE" else max(44, getattr(settings, "summarize_rerank_pool", 60))
        top_candidates = candidates[:rerank_pool]
        reranked = self._cross_encode(query_ctx.cleaned_query, top_candidates)
        pipeline_stats["reranker_input"] = len(reranked)

        # ── P1: DEBUG LOG — stage 2: reranked counts + score range ────────────
        if reranked:
            rerank_scores = [c.rerank_score for c in reranked]
            logger.info(
                "[RETRIEVAL DEBUG] stage=reranked scope=%s level=%d count=%d "
                "max_score=%.4f min_score=%.4f avg_score=%.4f",
                scope_label, scope_level, len(reranked),
                max(rerank_scores), min(rerank_scores),
                sum(rerank_scores) / len(rerank_scores),
            )
        trace["reranked_count"] = len(reranked)
        trace["rerank_scores"] = {
            "max": round(max((c.rerank_score for c in reranked), default=0.0), 4),
            "min": round(min((c.rerank_score for c in reranked), default=0.0), 4),
            "avg": round(sum(c.rerank_score for c in reranked) / len(reranked), 4) if reranked else 0.0,
        }

        # ── FINAL SCORE: reranker + priority additive + freshness + feedback
        #                 + P2 soft entity boost (NEVER a hard filter) ────────
        final_chunks: List[RetrievedChunk] = []
        below_threshold = 0
        for chunk in reranked:
            additive    = _ADDITIVE.get(chunk.priority_tier, 0.0)
            freshness   = _freshness_decay(_doc_age_days(chunk.ingested_at))
            fb_boost    = 0.0
            if self.feedback_service:
                try:
                    fb_boost = self.feedback_service.get_chunk_boost(chunk.chunk_id)
                except Exception:
                    pass

            entity_boost = self._compute_entity_boost(query_ctx, chunk)

            chunk.freshness_decay = freshness
            chunk.feedback_boost = fb_boost
            chunk.entity_boost = entity_boost
            chunk.final_score = chunk.rerank_score + additive + freshness + fb_boost + entity_boost

            # LLD §6.3: drop chunks with final_score < 0.38
            if chunk.final_score >= settings.final_score_threshold:
                final_chunks.append(chunk)
            else:
                below_threshold += 1

        final_chunks.sort(key=lambda c: c.final_score, reverse=True)
        final_chunks = final_chunks[:top_k]
        chunk_count_low = len(final_chunks) < 3
        pipeline_stats["final_chunks"] = len(final_chunks)
        pipeline_stats["below_threshold"] = below_threshold
        pipeline_stats["chunk_count_low"] = chunk_count_low
        trace["below_threshold"] = below_threshold
        trace["final_chunks"] = len(final_chunks)
        trace["chunk_count_low"] = chunk_count_low
        trace["final_chunk_ids"] = [c.chunk_id for c in final_chunks]

        # ── CONFIDENCE ────────────────────────────────────────────────────────
        # low_confidence is now SCORE-based only — it answers "is the best
        # chunk we found actually relevant enough to answer from?". A single
        # high-scoring chunk (e.g. final_score=0.83 for a precise factual
        # lookup) is NOT low confidence just because it's the only chunk.
        #
        # "Did we get enough chunks for a broad/SUMMARIZE-style answer?" is a
        # SEPARATE signal (chunk_count_low) — surfaced for the UI/trace but no
        # longer forces the "couldn't find enough relevant information"
        # fallback for a perfectly good single-chunk factual answer.
        confidence = final_chunks[0].final_score if final_chunks else 0.0
        low_confidence = confidence < settings.low_confidence_threshold

        latency_ms = int((time.time() - t_start) * 1000)
        logger.info(
            "[RETRIEVAL DEBUG] stage=final scope=%s level=%d dense=%d q=%d bm25=%d "
            "reranked=%d below_threshold=%d final=%d conf=%.3f latency=%dms",
            scope_label, scope_level, len(dense_results), len(question_results),
            len(bm25_results), len(reranked), below_threshold, len(final_chunks),
            confidence, latency_ms,
        )

        return RetrievalResult(
            chunks=final_chunks,
            query_context=query_ctx,
            confidence=confidence,
            low_confidence=low_confidence,
            latency_ms=latency_ms,
            total_candidates=len(candidates),
            repositories_searched=query_ctx.repositories,
            expanded_queries=query_ctx.expanded_queries,
            pipeline_stats=pipeline_stats,
            retrieval_trace=trace,
            scope_level=scope_level,
            scope_label=scope_label,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Document/bundle-scoped SUMMARIZE — bypasses similarity search entirely
    # ══════════════════════════════════════════════════════════════════════════
    def _direct_document_retrieval(
        self,
        query_ctx: QueryContext,
        doc_ids: List[str],
        top_k: int,
        t_start: float,
        scope_level: int,
        scope_label: str,
    ) -> RetrievalResult:
        """
        Pull chunks directly from the scoped document(s) — via
        `vector_store.get_chunks_by_doc()` (same lookup used by the Document
        Detail page, including its `source_file` fallback for docs whose
        Qdrant `doc_id` doesn't match Postgres) — instead of running
        dense/BM25/rerank against a generic instruction like "summarise the
        selected document".

        Builds:
          - one synthetic "Document Outline" chunk per document, made by
            concatenating each chunk's `chunk_summary` (so the LLM sees a
            map of the whole document even though only a sample of full
            chunk content fits in the context window), and
          - an evenly-spaced sample of up to `top_k` real content chunks per
            document (spanning beginning/middle/end, not just the first few),
            so `build_context()`'s per-document cap doesn't starve a single
            scoped document of context.
        """
        per_doc_counts: Dict[str, int] = {}
        per_doc_source_files: Dict[str, str] = {}
        outline_chunks: List[RetrievedChunk] = []
        sampled_chunks: List[RetrievedChunk] = []

        def _to_retrieved_chunk(p: Dict, score: float, source: str, chunk_id: Optional[str] = None,
                                 content: Optional[str] = None, section_title: Optional[str] = None,
                                 page_number: Optional[int] = None) -> RetrievedChunk:
            return RetrievedChunk(
                chunk_id=chunk_id or p.get("chunk_id", ""),
                content=content if content is not None else p.get("content", ""),
                source_file=p.get("source_file", ""),
                section_title=section_title if section_title is not None else (p.get("section_title") or ""),
                page_number=page_number if page_number is not None else (p.get("page_number") or 0),
                doc_type=p.get("doc_type", ""),
                department=p.get("department", ""),
                doc_origin=p.get("doc_origin", "INTERNAL"),
                priority_tier=p.get("priority_tier", 3),
                rrf_score=score, rerank_score=score, final_score=score,
                keywords=p.get("chunk_keywords") or p.get("keywords") or [],
                hypothetical_questions=p.get("chunk_questions") or p.get("hypothetical_questions") or [],
                repository=p.get("repository") or p.get("department", ""),
                access_roles=p.get("access_roles", []),
                retrieval_source=source,
                ingested_at=p.get("ingested_at", ""),
                doc_id=p.get("doc_id", ""),
                project_id=p.get("project_id", ""),
                uploaded_by=p.get("uploaded_by", ""),
                origin=p.get("doc_origin", "INTERNAL"),
                section_hierarchy=p.get("section_hierarchy", []),
                created_at=p.get("created_at", ""),
                pii_hash_map=p.get("pii_hash_map", {}),
                is_image_chunk=p.get("is_image_chunk", False),
                image_path=p.get("image_path", ""),
            )

        for doc_id in doc_ids:
            raw = self.vector_store.get_chunks_by_doc(
                doc_id, source_file=query_ctx.active_document_source_files.get(doc_id), limit=500,
            )
            per_doc_counts[doc_id] = len(raw)
            if not raw:
                continue
            per_doc_source_files[doc_id] = raw[0].get("source_file", "")

            # Outline chunk: every chunk's summary (or section title as a
            # fallback), so the LLM sees the document's full structure.
            outline_lines = []
            for c in raw:
                label = c.get("section_title") or f"Section {c.get('chunk_index', '?')}"
                summary = (c.get("chunk_summary") or "").strip()
                outline_lines.append(f"- {label}: {summary}" if summary else f"- {label}")
            if outline_lines:
                outline_chunks.append(_to_retrieved_chunk(
                    raw[0], score=0.99, source="document_scope_outline",
                    chunk_id=f"outline-{doc_id}",
                    content="\n".join(outline_lines)[:3000],
                    section_title="Document Outline (all sections)",
                    page_number=0,
                ))

            # Evenly-spaced content sample across the document.
            n = min(top_k, len(raw))
            if len(raw) <= n:
                sample = raw
            else:
                step = len(raw) / n
                sample = [raw[int(i * step)] for i in range(n)]
            for i, p in enumerate(sample):
                sampled_chunks.append(_to_retrieved_chunk(p, score=max(0.95 - i * 0.001, 0.5), source="document_scope"))

        final_chunks = outline_chunks + sampled_chunks
        latency_ms = int((time.time() - t_start) * 1000)
        confidence = 0.95 if final_chunks else 0.0

        trace: Dict[str, Any] = {
            "scope_level": scope_level,
            "scope_label": scope_label,
            "scope_filter": {"type": "doc_id", "value": doc_ids},
            "mode": "document_scope_summary",
            "documents_scoped": len(doc_ids),
            "chunks_per_document": per_doc_counts,
            "source_files": per_doc_source_files,
            "dense_results": 0, "question_results": 0, "summary_results": 0, "bm25_results": 0,
            "rrf_candidates": sum(per_doc_counts.values()),
            "reranked_count": 0, "below_threshold": 0,
            "final_chunks": len(final_chunks),
            "final_chunk_ids": [c.chunk_id for c in final_chunks],
            "hyde": {"used": False, "note": "skipped — document scope + SUMMARIZE"},
        }

        logger.info(
            "[RETRIEVAL DEBUG] stage=document_scope_summary scope=%s level=%d "
            "docs=%d chunks_per_doc=%s final=%d",
            scope_label, scope_level, len(doc_ids), per_doc_counts, len(final_chunks),
        )

        return RetrievalResult(
            chunks=final_chunks,
            query_context=query_ctx,
            confidence=confidence,
            low_confidence=(len(final_chunks) == 0),
            latency_ms=latency_ms,
            total_candidates=sum(per_doc_counts.values()),
            repositories_searched=query_ctx.repositories,
            expanded_queries=query_ctx.expanded_queries,
            pipeline_stats={
                "dense": 0, "question": 0, "summary": 0, "bm25": 0,
                "rrf_candidates": sum(per_doc_counts.values()),
                "final_chunks": len(final_chunks), "below_threshold": 0,
                "mode": "document_scope_summary",
                "chunks_per_document": per_doc_counts,
            },
            retrieval_trace=trace,
            scope_level=scope_level,
            scope_label=scope_label,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # P4: DOCUMENT-SPECIFIC RETRIEVAL CASCADE
    #   Level 1: Selected Document   (hard filter: doc_id)
    #   Level 2: Repository Scope    (hard filter: repository)
    #   Level 3: Department Scope    (hard filter: department)
    #   Level 4: Global Search       (no scope filter)
    # Expansion to the next level happens ONLY when the previous level's
    # result is low_confidence. The selected document (if any) is ALWAYS
    # searched first.
    # ══════════════════════════════════════════════════════════════════════════
    async def retrieve_cascading(
        self,
        query_ctx: QueryContext,
        top_k: int = None,
        rbac_roles: Optional[List[str]] = None,
    ) -> RetrievalResult:
        levels_tried: List[Dict[str, Any]] = []

        # ── Level 1: selected document(s), if any ──────────────────────────────
        if query_ctx.active_document_ids:
            result = await self.retrieve(
                query_ctx, top_k=top_k, rbac_roles=rbac_roles,
                scope_level=1, scope_label="DOCUMENT",
            )
            levels_tried.append({"level": 1, "label": "DOCUMENT",
                                  "chunks": len(result.chunks),
                                  "confidence": result.confidence,
                                  "low_confidence": result.low_confidence})
            if not result.low_confidence:
                result.retrieval_trace["cascade"] = levels_tried
                return result

            # ── Level 1b: scoped fallback ──────────────────────────────────────
            # Similarity search within the selected document(s) found NOTHING
            # (final_chunks == 0) — this happens for ANY generic "go through it
            # and summarise/explain/tell me about this" instruction whose intent
            # wasn't classified as SUMMARIZE (so the immediate shortcut at the
            # top of retrieve() didn't fire) but which still has essentially no
            # semantic/lexical overlap with the document's content. Rather than
            # immediately cascading out of the user's explicit selection (and
            # answering from somewhere else, or "couldn't find anything"), fall
            # back to the document's own outline + an evenly-spaced content
            # sample — same as the SUMMARIZE shortcut.
            #
            # If Level 1 DID find a chunk or two (just not enough to clear
            # low_confidence's "< 3 chunks" bar), prefer the normal cascade
            # below — those chunks are likely more on-topic than a generic
            # outline, and the cascade's repository/department/global levels
            # may surface 1-2 more relevant chunks to top them up.
            if not result.chunks:
                fallback = self._direct_document_retrieval(
                    query_ctx, query_ctx.active_document_ids, top_k,
                    time.time(), scope_level=1, scope_label="DOCUMENT_FALLBACK",
                )
                if fallback.chunks:
                    levels_tried.append({"level": "1b", "label": "DOCUMENT_FALLBACK",
                                          "chunks": len(fallback.chunks),
                                          "confidence": fallback.confidence,
                                          "low_confidence": fallback.low_confidence})
                    fallback.retrieval_trace["cascade"] = levels_tried
                    return fallback

            # Determine repository/department of the selected document(s) from
            # whatever chunks Level 1 *did* return (even if low confidence),
            # so Level 2/3 can scope sensibly instead of falling straight to global.
            repo_hint = next((c.repository for c in result.chunks if c.repository), None)
            dept_hint = next((c.department for c in result.chunks if c.department), None)
        else:
            result = None
            repo_hint = query_ctx.repositories[0] if query_ctx.repositories else None
            dept_hint = query_ctx.departments[0] if query_ctx.departments else None

        # ── Level 2: repository scope ───────────────────────────────────────────
        if repo_hint:
            level2 = await self.retrieve(
                query_ctx, top_k=top_k, rbac_roles=rbac_roles,
                skip_doc_scope=True, scope_repository=repo_hint,
                scope_level=2, scope_label="REPOSITORY",
            )
            levels_tried.append({"level": 2, "label": "REPOSITORY", "value": repo_hint,
                                  "chunks": len(level2.chunks),
                                  "confidence": level2.confidence,
                                  "low_confidence": level2.low_confidence})
            if not level2.low_confidence or level2.chunks:
                if not level2.low_confidence:
                    level2.retrieval_trace["cascade"] = levels_tried
                    return level2
                result = level2 if result is None or len(level2.chunks) > len(result.chunks) else result

        # ── Level 3: department scope ───────────────────────────────────────────
        if dept_hint:
            level3 = await self.retrieve(
                query_ctx, top_k=top_k, rbac_roles=rbac_roles,
                skip_doc_scope=True, scope_department=dept_hint,
                scope_level=3, scope_label="DEPARTMENT",
            )
            levels_tried.append({"level": 3, "label": "DEPARTMENT", "value": dept_hint,
                                  "chunks": len(level3.chunks),
                                  "confidence": level3.confidence,
                                  "low_confidence": level3.low_confidence})
            if not level3.low_confidence:
                level3.retrieval_trace["cascade"] = levels_tried
                return level3
            if result is None or len(level3.chunks) > len(result.chunks):
                result = level3

        # ── Level 4: global search (no scope filter) ────────────────────────────
        level4 = await self.retrieve(
            query_ctx, top_k=top_k, rbac_roles=rbac_roles,
            skip_doc_scope=True,
            scope_level=4, scope_label="GLOBAL",
        )
        levels_tried.append({"level": 4, "label": "GLOBAL",
                              "chunks": len(level4.chunks),
                              "confidence": level4.confidence,
                              "low_confidence": level4.low_confidence})
        level4.retrieval_trace["cascade"] = levels_tried
        if result is None or len(level4.chunks) >= len(result.chunks):
            return level4
        result.retrieval_trace["cascade"] = levels_tried
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # P5: ambiguous-query heuristic — broadens HyDE beyond "short query only"
    # ══════════════════════════════════════════════════════════════════════════
    def _is_ambiguous_query(self, query: str, token_count: int) -> bool:
        if token_count <= 3:
            return True
        words = _WORD_RE.findall(query.lower())
        if not words:
            return False
        ambiguous_hits = sum(1 for w in words if w in _AMBIGUOUS_TOKENS)
        return (ambiguous_hits / len(words)) >= 0.3

    # ══════════════════════════════════════════════════════════════════════════
    # P2: soft entity boost — additive only, NEVER excludes a candidate
    # ══════════════════════════════════════════════════════════════════════════
    def _compute_entity_boost(self, query_ctx: QueryContext, chunk: "RetrievedChunk") -> float:
        boost = 0.0

        # Boost matching departments (entity match)
        if query_ctx.departments and chunk.department in query_ctx.departments:
            boost += _SCORE_BOOST_DEPARTMENT_MATCH

        # Boost matching repositories (entity match)
        if query_ctx.repositories and chunk.repository in query_ctx.repositories:
            boost += _SCORE_BOOST_REPOSITORY_MATCH

        # Boost matching tech terms (entity extraction tech vocabulary)
        if query_ctx.tech_terms:
            chunk_keywords = {k.lower() for k in (chunk.keywords or [])}
            content_lower = chunk.content.lower()
            for term in query_ctx.tech_terms:
                if term in chunk_keywords or term in content_lower:
                    boost += _SCORE_BOOST_ENTITY_MATCH
                    break

        # Boost matching documents (e.g. ticket IDs referenced in the query
        # appearing in this chunk's source file / content)
        if query_ctx.ticket_ids:
            content_lower = chunk.content.lower()
            source_lower = chunk.source_file.lower()
            for tid in query_ctx.ticket_ids:
                tid_l = tid.lower()
                if tid_l in content_lower or tid_l in source_lower:
                    boost += _SCORE_BOOST_TICKET_MATCH
                    break

        # ── Soft retrieval_signal weights — department/repository PREFERENCE,
        # never a filter. Adds on top of the entity-match boosts above so a
        # chunk that matches both the detected preference repository/department
        # AND the extracted entity gets compounded (but capped) credit.
        repo_weights = query_ctx.retrieval_signal.get("repository_weight", {})
        if chunk.repository in repo_weights:
            boost += repo_weights[chunk.repository]

        dept_weights = query_ctx.retrieval_signal.get("department_weight", {})
        if chunk.department in dept_weights:
            boost += dept_weights[chunk.department]

        return min(boost, _ENTITY_BOOST_CAP)

    # ══════════════════════════════════════════════════════════════════════════
    # RRF FUSION
    # ══════════════════════════════════════════════════════════════════════════
    def _rrf_fusion(
        self, lists: List[Tuple[str, List[Dict]]], k: int = 60
    ) -> List[RetrievedChunk]:
        """
        RRF(d) = Σ [ boost_tier(d) × source_multiplier / (k + rank_i(d)) ]
        Internal Tier-1 docs get 1.30× boost; external get 1.00× or 0.85×.
        BM25 list additionally gets _RRF_SOURCE_MULTIPLIER (slightly higher
        influence) so exact keyword matches aren't drowned out by
        semantically-close-but-off-target vector hits.
        """
        rrf_scores: Dict[str, float] = {}
        payloads: Dict[str, Dict] = {}
        retrieval_sources: Dict[str, List[str]] = defaultdict(list)

        for source_name, result_list in lists:
            source_mult = _RRF_SOURCE_MULTIPLIER.get(source_name, 1.0)
            for rank, item in enumerate(result_list, start=1):
                cid = item["chunk_id"]
                payload = item["payload"]
                tier = payload.get("priority_tier", 3)
                boost = _RRF_BOOST.get(tier, 1.0) * source_mult

                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + boost / (k + rank)
                payloads[cid] = payload
                src = item.get("retrieval_source", source_name)
                if src not in retrieval_sources[cid]:
                    retrieval_sources[cid].append(src)

        sorted_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)
        candidates = []
        for cid in sorted_ids:
            p = payloads[cid]
            sources = retrieval_sources[cid]
            candidates.append(RetrievedChunk(
                chunk_id=cid,
                content=p.get("content", ""),
                source_file=p.get("source_file", ""),
                section_title=p.get("section_title", ""),
                page_number=p.get("page_number", 0),
                doc_type=p.get("doc_type", ""),
                department=p.get("department", ""),
                doc_origin=p.get("doc_origin", "INTERNAL"),
                priority_tier=p.get("priority_tier", 3),
                rrf_score=rrf_scores[cid],
                keywords=p.get("keywords", []),
                hypothetical_questions=p.get("hypothetical_questions", []),
                repository=p.get("repository", p.get("department", "")),
                access_roles=p.get("access_roles", []),
                retrieval_source="+".join(sources),
                ingested_at=p.get("ingested_at", ""),
                # ── P3: metadata flow fields ──
                doc_id=p.get("doc_id", ""),
                project_id=p.get("project_id", ""),
                uploaded_by=p.get("uploaded_by", ""),
                origin=p.get("doc_origin", "INTERNAL"),
                section_hierarchy=p.get("section_hierarchy", []),
                created_at=p.get("created_at", p.get("ingested_at", "")),
                # ── P7: PII hash map (if present on the chunk payload) ──
                pii_hash_map=p.get("pii_hash_map", {}),
                # ── P9: image-aware retrieval ──
                is_image_chunk=p.get("is_image_chunk", False),
                image_path=p.get("image_path", ""),
            ))
        return candidates

    # ══════════════════════════════════════════════════════════════════════════
    # CROSS ENCODER RERANKING
    # ══════════════════════════════════════════════════════════════════════════
    def _cross_encode(
        self, query: str, candidates: List[RetrievedChunk]
    ) -> List[RetrievedChunk]:
        if not candidates:
            return []

        reranker = self._get_reranker()
        # Truncate content to 1500 chars for reranker (more context per chunk)
        pairs = [[query, c.content[:1500]] for c in candidates]
        raw_scores = reranker.predict(pairs, show_progress_bar=False)

        import numpy as np
        # Sigmoid normalise to [0, 1] — matches LLD spec
        scores = 1.0 / (1.0 + np.exp(-raw_scores))

        for chunk, score in zip(candidates, scores):
            chunk.rerank_score = float(score)

        candidates.sort(key=lambda c: c.rerank_score, reverse=True)
        return candidates

    # ══════════════════════════════════════════════════════════════════════════
    # CONTEXT BUILDER
    # ══════════════════════════════════════════════════════════════════════════
    def _citation_dict(self, chunk: RetrievedChunk, is_stale: bool) -> Dict[str, Any]:
        """P3: full metadata-flow citation — every field that should survive
        ingestion -> Qdrant -> retrieval -> citation is represented here."""
        return {
            "source": _basename(chunk.source_file),
            "section": chunk.section_title,
            "section_hierarchy": chunk.section_hierarchy,
            "page": chunk.page_number,
            "repository": chunk.repository or chunk.department,
            "department": chunk.department,
            "score": round(chunk.final_score, 4),
            "rerank_score": round(chunk.rerank_score, 4),
            "rrf_score": round(chunk.rrf_score, 4),
            "entity_boost": round(chunk.entity_boost, 4),
            "doc_origin": chunk.doc_origin,
            "origin": chunk.origin or chunk.doc_origin,
            "priority_tier": chunk.priority_tier,
            "chunk_id": chunk.chunk_id,
            "doc_id": chunk.doc_id,
            "doc_type": chunk.doc_type,
            "project_id": chunk.project_id,
            "uploaded_by": chunk.uploaded_by,
            "access_roles": chunk.access_roles,
            "created_at": chunk.created_at,
            "is_image_chunk": chunk.is_image_chunk,
            "image_path": chunk.image_path,
            "stale": is_stale,
        }

    def build_context(
        self,
        chunks: List[RetrievedChunk],
        max_context_tokens: int = 4096,
        max_chunks: int = 8,
        max_per_doc: int = 3,
        intent: str = "SEARCH",
    ) -> BuiltContext:
        """
        LLD §4.6 Context Builder rules:
          - Max 8 chunks total (SUMMARIZE: settings.summarize_max_chunks)
          - Max 3 chunks per source document
          - Prioritise internal docs (already sorted by final_score)
          - Token budget: 4096 tokens (SUMMARIZE: settings.summarize_context_tokens)
          - Chunks older than 180 days labelled [STALE]
          - At least one Tier-1 INTERNAL chunk guaranteed (if available)

        SUMMARIZE intent uses a larger context budget — broad retrieval (more
        candidate chunks survive reranking) pairs with a bigger context window
        so the overview reflects more of the source material.

        P1: logs how many chunks arrived here vs. how many were actually
        selected into the LLM context window, so "retrieval found chunks but
        the answer says nothing was found" can be diagnosed from logs alone.
        """
        if intent == "SUMMARIZE":
            max_context_tokens = max(max_context_tokens, getattr(settings, "summarize_context_tokens", 8192))
            max_chunks = max(max_chunks, getattr(settings, "summarize_max_chunks", 16))

        logger.info(
            "[RETRIEVAL DEBUG] stage=context_builder_input chunks_in=%d "
            "chunk_ids=%s",
            len(chunks), [c.chunk_id for c in chunks][:max_chunks + 2],
        )

        doc_count: Dict[str, int] = defaultdict(int)
        selected: List[ContextChunk] = []
        used_tokens = 0
        citations: List[Dict] = []

        # Guarantee at least one Tier-1 chunk (LLD §8.4)
        tier1_present_in_pool = any(c.priority_tier == 1 for c in chunks)
        tier1_selected = False

        for chunk in chunks:
            if len(selected) >= max_chunks:
                break
            doc_key = chunk.source_file
            if doc_count[doc_key] >= max_per_doc:
                continue
            chunk_tokens = _token_count(chunk.content)
            if used_tokens + chunk_tokens > max_context_tokens:
                continue

            # Stale label (LLD §8.3 — chunks older than 180 days)
            is_stale = _doc_age_days(chunk.ingested_at) > settings.stale_days_threshold

            source_label = (
                f"[Source {len(selected)+1}] {chunk.source_file.split('/')[-1]}"
                f" | {chunk.section_title or 'General'}"
                f" | p.{chunk.page_number}"
                f" | {chunk.repository or chunk.department}"
            )

            if is_stale:
                source_label += " [STALE – verify before actioning]"

            selected.append(ContextChunk(
                chunk=chunk,
                token_count=chunk_tokens,
                source_label=source_label,
                is_stale=is_stale,
            ))
            doc_count[doc_key] += 1
            used_tokens += chunk_tokens
            if chunk.priority_tier == 1:
                tier1_selected = True
            citations.append(self._citation_dict(chunk, is_stale))

        # Tier-1 guarantee: if pool has a Tier-1 chunk but none was selected,
        # swap in the highest-scoring Tier-1 chunk for the lowest-scoring
        # non-Tier-1 selected chunk (if room allows).
        if tier1_present_in_pool and not tier1_selected and selected:
            tier1_candidates = [c for c in chunks if c.priority_tier == 1]
            if tier1_candidates:
                best_tier1 = max(tier1_candidates, key=lambda c: c.final_score)
                t1_tokens = _token_count(best_tier1.content)

                # Find lowest-scoring non-Tier-1 selected chunk to replace
                replace_idx = None
                lowest_score = float("inf")
                for idx, sc in enumerate(selected):
                    if sc.chunk.priority_tier != 1 and sc.chunk.final_score < lowest_score:
                        lowest_score = sc.chunk.final_score
                        replace_idx = idx

                if replace_idx is not None:
                    removed = selected[replace_idx]
                    new_used_tokens = used_tokens - removed.token_count + t1_tokens
                    if new_used_tokens <= max_context_tokens:
                        is_stale = _doc_age_days(best_tier1.ingested_at) > settings.stale_days_threshold
                        source_label = (
                            f"[Source {replace_idx+1}] {best_tier1.source_file.split('/')[-1]}"
                            f" | {best_tier1.section_title or 'General'}"
                            f" | p.{best_tier1.page_number}"
                            f" | {best_tier1.repository or best_tier1.department}"
                        )
                        if is_stale:
                            source_label += " [STALE – verify before actioning]"

                        selected[replace_idx] = ContextChunk(
                            chunk=best_tier1,
                            token_count=t1_tokens,
                            source_label=source_label,
                            is_stale=is_stale,
                        )
                        used_tokens = new_used_tokens
                        citations[replace_idx] = self._citation_dict(best_tier1, is_stale)

        # Build formatted context text
        parts = []
        for ctx_chunk in selected:
            parts.append(f"{ctx_chunk.source_label}\n{ctx_chunk.chunk.content}")

        context_text = "\n\n---\n\n".join(parts)

        logger.info(
            "[RETRIEVAL DEBUG] stage=context_builder_output chunks_selected=%d "
            "chunks_in=%d used_tokens=%d context_chars=%d chunk_ids=%s",
            len(selected), len(chunks), used_tokens, len(context_text),
            [c.chunk.chunk_id for c in selected],
        )

        return BuiltContext(
            context_text=context_text,
            chunks=selected,
            total_tokens=used_tokens,
            citations=citations,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════════════════════════
    def _dedup_by_chunk_id(self, results: List[Dict], max_k: int) -> List[Dict]:
        """Deduplicate by chunk_id keeping highest score entry."""
        seen: Dict[str, Dict] = {}
        for item in results:
            cid = item["chunk_id"]
            if cid not in seen or item["score"] > seen[cid]["score"]:
                seen[cid] = item
        return sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:max_k]


# ════════════════════════════════════════════════════════════════════════════════
# TICKET RETRIEVER (unchanged from Phase 1 — SQL + vector fallback)
# ════════════════════════════════════════════════════════════════════════════════
class TicketRetriever:
    def __init__(self, db_url: str):
        from sqlalchemy import create_engine, text
        self.engine = create_engine(db_url)
        self._text = text

    def search(
        self,
        query: str,
        ticket_ids: Optional[List[str]] = None,
        top_k: int = 5,
    ) -> List[Dict]:
        with self.engine.connect() as conn:
            if ticket_ids:
                sql = self._text(
                    "SELECT ticket_id, subject, description, priority, "
                    "category, status, resolution, created_at "
                    "FROM tickets WHERE ticket_id = ANY(:ids) LIMIT :limit"
                )
                rows = conn.execute(sql, {"ids": ticket_ids, "limit": top_k})
            else:
                sql = self._text(
                    "SELECT ticket_id, subject, description, priority, "
                    "category, status, resolution, created_at, "
                    "ts_rank(fts_vector, plainto_tsquery('english', :q)) as rank "
                    "FROM tickets "
                    "WHERE fts_vector @@ plainto_tsquery('english', :q) "
                    "ORDER BY rank DESC LIMIT :limit"
                )
                rows = conn.execute(sql, {"q": query, "limit": top_k})
            return [dict(row._mapping) for row in rows]

    # ══════════════════════════════════════════════════════════════════════
    # TICKET DUAL-PATH  (LLD §5)
    # 5.1 Exact ticket ID regex + PK lookup (<2ms)
    # 5.2 Full-text SQL + structured filters
    # 5.3 Semantic fallback on ticket index (if SQL results < 2)
    # 5.4 Merge SQL + vector results by ticket_id + dedupe
    # ══════════════════════════════════════════════════════════════════════
    def dual_path_search(
        self,
        query: str,
        ticket_ids: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        vector_store=None,
        top_k: int = 5,
        sql_min_results: int = 2,
    ) -> Dict[str, Any]:
        filters = filters or {}
        stats = {
            "exact_match": False,
            "sql_results": 0,
            "semantic_fallback_used": False,
            "merged_count": 0,
        }

        # 5.1 Exact ticket ID match — fast PK lookup
        exact_results: List[Dict] = []
        if ticket_ids:
            exact_results = self.search(query=query, ticket_ids=ticket_ids, top_k=top_k)
            stats["exact_match"] = len(exact_results) > 0

        # 5.2 Full-text SQL + structured filters
        sql_results: List[Dict] = []
        if not exact_results:
            sql_results = self._search_with_filters(query, filters, top_k=top_k)
        stats["sql_results"] = len(sql_results)

        # 5.3 Semantic fallback on ticket index (if SQL results insufficient)
        vector_results: List[Dict] = []
        if not exact_results and len(sql_results) < sql_min_results and vector_store is not None:
            stats["semantic_fallback_used"] = True
            raw = vector_store.search(
                query=query,
                top_k=top_k,
                filters={"doc_type": "Ticket"},
                vector_name="content",
            )
            for r in raw:
                p = r["payload"]
                vector_results.append({
                    "ticket_id": p.get("ticket_id") or p.get("chunk_id"),
                    "subject": p.get("section_title", ""),
                    "description": p.get("content", ""),
                    "priority": p.get("priority", ""),
                    "category": p.get("department", ""),
                    "status": p.get("status", ""),
                    "resolution": p.get("resolution", ""),
                    "created_at": p.get("ingested_at", ""),
                    "_source": "vector",
                })

        # 5.4 Merge by ticket_id, dedupe
        merged: Dict[str, Dict] = {}
        for r in exact_results:
            merged[r["ticket_id"]] = {**r, "_source": "exact"}
        for r in sql_results:
            merged.setdefault(r["ticket_id"], {**r, "_source": "sql"})
        for r in vector_results:
            merged.setdefault(r["ticket_id"], r)

        candidates = list(merged.values())[:top_k]
        stats["merged_count"] = len(candidates)

        return {"candidates": candidates, "stats": stats}

    def _search_with_filters(
        self, query: str, filters: Dict[str, Any], top_k: int = 5
    ) -> List[Dict]:
        """Full-text search combined with structured filters (status, category, priority)."""
        clauses = ["fts_vector @@ plainto_tsquery('english', :q)"]
        params: Dict[str, Any] = {"q": query, "limit": top_k}

        for key in ("status", "category", "priority"):
            val = filters.get(key)
            if val:
                clauses.append(f"{key} = :{key}")
                params[key] = val

        where_clause = " AND ".join(clauses)
        sql = self._text(
            "SELECT ticket_id, subject, description, priority, category, "
            "status, resolution, created_at, "
            "ts_rank(fts_vector, plainto_tsquery('english', :q)) as rank "
            f"FROM tickets WHERE {where_clause} "
            "ORDER BY rank DESC LIMIT :limit"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, params)
            return [dict(row._mapping) for row in rows]