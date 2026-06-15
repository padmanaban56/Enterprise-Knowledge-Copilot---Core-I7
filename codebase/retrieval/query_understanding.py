"""
retrieval/query_understanding.py  —  Lean 6-Step Query Understanding Pipeline

Pipeline:
  Step 1  Normalizer         — unicode NFC, whitespace (case preserved)
  Step 2  PII Scrubber        — emails, phones → <REDACTED>
  Step 3  Intent Detection    — rule-based primary + keyword scoring
                                SEARCH | SUMMARIZE | TICKET_LOOKUP | ESCALATE | SMALLTALK
                                Level-2 LLM fallback (Ollama) when low-confidence
  Step 4  Filter Extraction  — ONLY hard structural filters:
                                • TICKET_LOOKUP  → doc_type = "Ticket"
                                • has_date_filter flag (informational only)
  Step 5  HyDE               — Hypothetical Document Embedding query rewrite
                                (called async by hybrid_engine before retrieval)
  Step 6  Query Expansion    — domain synonym variants (no repo-name prefix pollution)

REMOVED vs previous version
  ✗ Step 4 Entity Extraction  — department / repo / tech-term signals are soft
                                 boosts in _compute_entity_boost() inside
                                 hybrid_engine.py; they must NEVER pre-filter
                                 the candidate set before ranking.
  ✗ Step 5 Repository Selection — keyword-scored routing caused false narrow
                                   scope that starved the reranker of good
                                   cross-department chunks.
  ✗ Repo-name prefix in expansion — "{repo} {query}" variants polluted
                                     embedding space and biased ANN away from
                                     the semantic core of the query.
  ✗ Eager decomposition — conjunction threshold lowered; heuristic "long query"
                           split removed entirely (it was splitting coherent
                           questions that shared a pronoun or subject).

ENTITY SIGNALS — still computed but DEFERRED to hybrid_engine
  QueryContext still exposes .ticket_ids, .tech_terms, .departments so that
  hybrid_engine._compute_entity_boost() can apply additive soft boosts AFTER
  ranking.  They are extracted lazily inside hybrid_engine, not here, keeping
  this pipeline under 10 ms on CPU.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import httpx

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ════════════════════════════════════════════════════════════════
# INTENT: Hybrid detection — rule patterns + keyword scoring
# ════════════════════════════════════════════════════════════════
_VALID_INTENTS = ["SEARCH", "SUMMARIZE", "TICKET_LOOKUP", "TICKET_ANALYTICS", "ESCALATE", "SMALLTALK"]

_INTENT_LLM_PROMPT = """Classify the user's query into EXACTLY ONE of these intents:
SEARCH - looking for information, facts, procedures, or documentation
SUMMARIZE - wants an overview, summary, or high-level explanation of a topic/document
TICKET_LOOKUP - asking about the status of a specific ticket, incident, or request
TICKET_ANALYTICS - asking for a COUNT/TOTAL/NUMBER of tickets matching some criteria
ESCALATE - wants to raise/open/escalate a new issue or get urgent human help
SMALLTALK - greetings, thanks, or casual conversation not requiring document search

Query: {query}

Respond with ONLY ONE WORD: SEARCH, SUMMARIZE, TICKET_LOOKUP, TICKET_ANALYTICS, ESCALATE, or SMALLTALK."""

_INTENT_RULES: Dict[str, List[str]] = {
    # ── Checked BEFORE TICKET_LOOKUP ────────────────────────────────────────
    # "how many open tickets" / "count of open tickets" / "total IT tickets"
    # would otherwise also match TICKET_LOOKUP's broadened "open ... tickets"
    # rule below — TICKET_ANALYTICS must win for aggregate/count questions.
    "TICKET_ANALYTICS": [
        r"\b(how\s+many|count\s+of|number\s+of|total\s+(?:number|count)?|"
        r"how\s+much)\b[\w\s\-]{0,40}\b(tickets?|incidents?|requests?)\b",
        r"\b(tickets?|incidents?|requests?)\b[\w\s\-]{0,20}\b(count|total)\b",
        # ── Follow-up analytics question, e.g. "what about high priority and
        # closed tickets?" continuing a previous count query. Requires a
        # status/priority keyword before "tickets/incidents/requests" so it
        # doesn't swallow generic "what about IT tickets" topic questions,
        # and excludes "my"/"I" so personal lookups ("what about my open
        # tickets") still route to TICKET_LOOKUP instead.
        r"^(?!.*\b(my|i)\b)\b(what about|how about|and what about|also)\b"
        r"[\w\s\-]{0,60}\b(open|closed|resolved|rejected|pending|"
        r"in[\s\-]?progress|low|medium|high|critical)\b[\w\s\-]{0,40}\b"
        r"(tickets?|incidents?|requests?)\b",
    ],
    "TICKET_LOOKUP": [
        r"\b(ticket|incident|INC|JIRA|issue|bug|request)\s*[#\-]?\d+",
        r"\b(look\s*up|find|show|get|fetch|retrieve)\s+(ticket|incident|issue|request)",
        r"\b(my\s+ticket|open\s+ticket|recent\s+ticket|ticket\s+status|pending\s+ticket)",
        r"\bstatus\s+of\s+(ticket|incident|request)",
        # ── Broadened: trigger word and "ticket(s)/incident(s)/request(s)"
        # may be separated by a topic word, e.g. "my VPN ticket",
        # "any open VPN tickets", "status of my access request",
        # "show my password reset tickets".
        r"\b(my|any|open|recent|pending|all)\b[\w\s\-]{0,40}\b"
        r"(ticket|tickets|incident|incidents|request|requests)\b",
        r"\b(ticket|tickets|incident|incidents|request|requests)\b[\w\s\-]{0,40}\b"
        r"(status|update|progress|pending|open|resolved|closed)\b",
    ],
    "SUMMARIZE": [
        r"\b(summarize|summarise|summary|overview|brief|tl;?dr|abstract)\b",
        r"\bgive\s+me\s+(an?\s+)?(overview|summary|brief|synopsis)\b",
        r"\bwhat\s+(does|is|are)\s+.{3,60}\s+(about|cover|contain|explain)\b",
        r"\b(explain|describe)\s+(the\s+)?(whole|entire|full|complete)\b",
    ],
    "ESCALATE": [
        r"\b(escalate|raise|create|open|log|submit)\s+(a\s+|an\s+|new\s+)?(ticket|incident|issue|case)\b",
        r"\b(nobody|no\s+one|not\s+resolved|unresolved).{0,40}(help|respond|fix|solve)",
        r"\bneed\s+(human|agent|support|help)\s+(now|urgently|immediately)",
    ],
    "SMALLTALK": [
        r"^(hi|hello|hey|howdy|greetings|good\s+(morning|afternoon|evening))\b",
        r"^(how\s+are\s+you|what\s+can\s+you\s+do|who\s+are\s+you|what\s+are\s+you)\b",
        r"^(thank|thanks|bye|goodbye|see\s+you|cheers)\b",
    ],
    "SEARCH": [],
}

_INTENT_SIGNALS: Dict[str, List[str]] = {
    "TICKET_LOOKUP": ["incident", "outage", "broken", "error", "crash", "not working",
                      "failing", "bug", "defect", "cannot access", "blocked"],
    "SUMMARIZE":     ["summary", "overview", "what is", "explain", "describe", "brief",
                      "key points", "main points", "highlights"],
    "SEARCH":        ["how to", "steps to", "procedure for", "policy on", "guide to",
                      "process for", "what is the", "where can i", "how do i"],
    "ESCALATE":      ["urgent", "critical", "asap", "immediately", "still broken",
                      "nobody responding", "escalate", "raise ticket"],
}

# ════════════════════════════════════════════════════════════════
# QUERY EXPANSION — domain synonyms only, NO repo-prefix variants
# ════════════════════════════════════════════════════════════════
_EXPANSION_MAP: Dict[str, List[str]] = {
    "vpn":          ["vpn access", "remote access", "virtual private network"],
    "leave":        ["annual leave", "leave policy", "time off request", "leave entitlement"],
    "password":     ["password reset", "account access recovery", "credential reset"],
    "invoice":      ["invoice processing", "invoice approval", "billing document"],
    "deploy":       ["deployment process", "release pipeline", "ci/cd workflow"],
    "onboard":      ["employee onboarding", "new hire process", "joining procedure"],
    "expense":      ["expense claim", "expense reimbursement", "travel expense"],
    "access":       ["access request", "permission grant", "role assignment"],
    "incident":     ["incident response", "incident management", "outage handling"],
    "performance":  ["performance review", "appraisal process", "annual evaluation"],
    "kubernetes":   ["k8s cluster", "pod deployment", "container orchestration"],
    "docker":       ["container build", "dockerfile", "image registry"],
    "monitoring":   ["grafana dashboard", "prometheus alerts", "observability setup"],
    "database":     ["database backup", "db performance", "query optimization"],
    "security":     ["security policy", "access control", "vulnerability management"],
    "budget":       ["budget approval", "budget allocation", "cost centre"],
    "training":     ["employee training", "learning program", "skill development"],
    "offboard":     ["employee offboarding", "exit process", "account deprovisioning"],
}

# ════════════════════════════════════════════════════════════════
# DECOMPOSITION — only fire on explicit comparison or strong compound
# ════════════════════════════════════════════════════════════════
# Conjunctions alone are NOT enough; query must also be > 12 tokens
# so that "how do I reset password and get VPN access" doesn't split
# on "and" prematurely.
_DECOMPOSITION_CONJUNCTIONS = re.compile(
    r'\b(and also|as well as|additionally|furthermore|along with)\b',
    re.IGNORECASE,
)
# Comparison-framed queries are always worth splitting
_DECOMPOSITION_COMPARE = re.compile(
    r'\b(difference between|compare|versus|vs\.?)\b',
    re.IGNORECASE,
)# ════════════════════════════════════════════════════════════════
# REPOSITORY / DEPARTMENT PREFERENCE DETECTION — SOFT SIGNALS ONLY
# These NEVER filter retrieval. They only populate
# ctx.retrieval_signal["repository_weight"] / ["department_weight"],
# which hybrid_engine adds as additive ranking boosts AFTER reranking.
# ════════════════════════════════════════════════════════════════
_REPOSITORY_KEYWORDS: Dict[str, List[str]] = {
    "Policies":  ["policy", "policies", "pto", "leave", "code of conduct",
                   "compliance", "guideline", "handbook"],
    "Tickets":   ["ticket", "incident", "INC", "issue", "outage"],
    "Engineering": ["deploy", "kubernetes", "docker", "ci/cd", "pipeline",
                     "database", "api", "monitoring", "infrastructure"],
    "Finance":   ["invoice", "budget", "expense", "reimbursement", "cost centre"],
    "HR":        ["onboard", "offboard", "performance review", "appraisal",
                    "training", "leave", "pto", "hr policy"],
    "IT":        ["vpn", "password", "access request", "account", "software install"],
}

_DEPARTMENT_KEYWORDS: Dict[str, List[str]] = {
    "HR":        ["leave", "pto", "onboarding", "offboarding", "performance review",
                    "appraisal", "training", "hr policy", "employee"],
    "Finance":   ["invoice", "budget", "expense", "reimbursement", "payroll", "cost centre"],
    "IT":        ["vpn", "password", "access request", "account", "network", "software"],
    "Engineering": ["deploy", "kubernetes", "docker", "pipeline", "database", "monitoring"],
}

# Soft weight magnitudes — applied as additive ranking boosts in hybrid_engine,
# never as hard filters. Calibrated to LLD scoring example:
#   department_weight ~0.15, repository_weight ~0.20 for a strong single hit.
_REPOSITORY_WEIGHT = 0.20
_DEPARTMENT_WEIGHT = 0.15



_EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')
_PHONE_PATTERN = re.compile(r'\b(?:\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4,6}\b')

# Ticket ID — kept for TICKET_LOOKUP filter extraction only.
# Matches:
#   ABC-123          (prefix-number, e.g. TICKET-123, JIRA-456)
#   INC1234          (INC + 4+ digits)
#   REQ123           (REQ + digits)
#   TCK000002        (2-8 uppercase letters directly followed by 4+ digits —
#                     covers TCK/OPS/SEC-style IDs from CSV ticket exports,
#                     which have no hyphen separator)
_TICKET_ID = re.compile(r'\b([A-Z]{2,8}-\d+|INC\d{4,}|REQ\d+|[A-Z]{2,8}\d{4,})\b')


# ════════════════════════════════════════════════════════════════
# DATA MODELS
# ════════════════════════════════════════════════════════════════
@dataclass
class IntentResult:
    intent: str
    confidence: float
    rule_matched: Optional[str] = None
    signal_scores: Dict[str, float] = field(default_factory=dict)
    low_confidence: bool = False


@dataclass
class QueryContext:
    # ── Input ─────────────────────────────────────────────────────────────────
    original_query: str = ""
    # Bundle scoping (set by api/main.py from chat_sessions.active_documents)
    active_document_ids: List[str] = field(default_factory=list)
    active_document_source_files: Dict[str, str] = field(default_factory=dict)

    # Step 1: Normalizer
    cleaned_query: str = ""
    # Step 2: PII Scrubber
    safe_query: str = ""
    pii_detected: bool = False
    pii_types: List[str] = field(default_factory=list)
    # Step 3: Intent
    intent: str = "SEARCH"
    intent_confidence: float = 0.95
    intent_result: Optional[IntentResult] = None
    # Step 4: Filters (structural only)
    filters: Dict[str, object] = field(default_factory=dict)
    # Step 5: HyDE (populated async by hybrid_engine)
    hyde_passage: str = ""
    hyde_used: bool = False
    # Step 6: Query expansion
    expanded_queries: List[str] = field(default_factory=list)
    # Decomposition (optional, only for explicit comparisons)
    sub_queries: List[str] = field(default_factory=list)
    is_decomposed: bool = False

    # ── Deferred entity signals — populated by hybrid_engine._compute_entity_boost()
    # after ranking, never used to pre-filter the candidate set.
    # Kept here so the boost step has a place to stash results for the trace.
    ticket_ids: List[str] = field(default_factory=list)
    tech_terms: List[str] = field(default_factory=list)
    departments: List[str] = field(default_factory=list)
    repositories: List[str] = field(default_factory=list)

    # ── Soft retrieval signals (weights/hints only — NEVER filters) ─────────
    # Populated by department/repository detectors (Step 4). hybrid_engine
    # reads these to apply additive ranking boosts. Department and repository
    # detection must NEVER narrow the candidate set — this is metadata for
    # scoring, not a Qdrant filter.
    retrieval_signal: Dict[str, Dict[str, float]] = field(default_factory=lambda: {
        "department_weight": {},
        "repository_weight": {},
    })

    # ── Meta ─────────────────────────────────────────────────────────────────
    needs_clarification: bool = False
    rbac_roles: List[str] = field(default_factory=list)
    pipeline_trace: List[Dict] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════
# QUERY UNDERSTANDING PIPELINE
# ════════════════════════════════════════════════════════════════
class QueryUnderstanding:
    """
    Lean 6-step query understanding pipeline.
    Steps 1–4 and 6 run synchronously (<10 ms on CPU).
    Step 5 (HyDE) is async and called by the retrieval orchestrator.

    Entity extraction and repository selection have been removed from this
    pipeline. They caused premature scope narrowing that starved the reranker.
    Entity signals are now computed inside hybrid_engine as additive soft boosts
    applied AFTER RRF fusion and cross-encoder reranking.
    """

    def process(self, raw_query: str, rbac_roles: List[str] = None) -> QueryContext:
        ctx = QueryContext(original_query=raw_query)
        if rbac_roles:
            ctx.rbac_roles = rbac_roles

        # ── Step 1: Normalizer ────────────────────────────────────────────────
        ctx.cleaned_query = self._step1_normalize(raw_query)
        ctx.pipeline_trace.append({"step": 1, "name": "Normalizer", "output": ctx.cleaned_query})

        # ── Step 2: PII Scrubber ──────────────────────────────────────────────
        ctx.safe_query, ctx.pii_detected, ctx.pii_types = self._step2_pii_scrub(ctx.cleaned_query)
        ctx.pipeline_trace.append({
            "step": 2, "name": "PII Scrubber",
            "output": ctx.safe_query, "pii_found": ctx.pii_detected, "types": ctx.pii_types,
        })

        # ── Step 3: Intent Detection (hybrid rule + keyword) ──────────────────
        ctx.intent_result = self._step3_intent(ctx.safe_query)
        ctx.intent = ctx.intent_result.intent
        ctx.intent_confidence = ctx.intent_result.confidence
        if ctx.intent_confidence < 0.55:
            ctx.needs_clarification = True
        ctx.pipeline_trace.append({
            "step": 3, "name": "Intent Detection",
            "output": ctx.intent, "confidence": round(ctx.intent_confidence, 3),
            "signal_scores": ctx.intent_result.signal_scores,
        })

        # ── Step 4: Filter Extraction (structural hard filters only) ──────────
        # ⚠ Department / repository / tech-term signals are NOT filters here.
        #   They are soft boosts applied by hybrid_engine AFTER ranking.
        ctx.filters = self._step4_filters(ctx)
        ctx.pipeline_trace.append({
            "step": 4, "name": "Filter Extraction", "filters": ctx.filters,
            "retrieval_signal": ctx.retrieval_signal,
        })

        # ── Step 5: HyDE — handled async externally ───────────────────────────
        ctx.pipeline_trace.append({
            "step": 5, "name": "HyDE", "output": "pending_async",
        })

        # ── Step 6: Query Expansion (domain synonyms only) ────────────────────
        ctx.expanded_queries = self._step6_expand(ctx.safe_query)
        ctx.pipeline_trace.append({
            "step": 6, "name": "Query Expansion",
            "expanded": ctx.expanded_queries,
        })

        # ── Decomposition — only for explicit comparison/compound queries ─────
        ctx.sub_queries, ctx.is_decomposed = self._decompose(ctx.safe_query)
        if ctx.is_decomposed:
            ctx.pipeline_trace.append({
                "step": "6b", "name": "Query Decomposition",
                "decomposed": ctx.is_decomposed, "sub_queries": ctx.sub_queries,
            })

        logger.info(
            "QU pipeline: intent=%s(%.2f) filters=%s expanded=%d decomposed=%s",
            ctx.intent, ctx.intent_confidence, ctx.filters,
            len(ctx.expanded_queries), ctx.is_decomposed,
        )
        return ctx

    # ══════════════════════════════════════════════════════════════════════
    # Level-2 LLM Intent Classifier (Ollama) — called by api/main.py
    # when intent_result.low_confidence is True
    # ══════════════════════════════════════════════════════════════════════
    async def classify_intent_llm(self, ctx: QueryContext) -> QueryContext:
        if not ctx.intent_result or not ctx.intent_result.low_confidence:
            return ctx

        query = ctx.safe_query
        prompt = _INTENT_LLM_PROMPT.format(query=query)

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.intent_classifier_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 20},
                    },
                )
                data = resp.json()
                raw = data.get("response", "").strip().upper()

            label = None
            for candidate in _VALID_INTENTS:
                if candidate in raw:
                    label = candidate
                    break

            if label is None:
                logger.debug("LLM intent classifier returned unrecognised label: %r", raw)
                ctx.pipeline_trace[2]["llm_classifier"] = {
                    "model": settings.intent_classifier_model,
                    "raw_response": raw[:80], "applied": False,
                    "reason": "unrecognised_label",
                }
                return ctx

            previous_intent = ctx.intent
            ctx.intent = label
            ctx.intent_confidence = 0.75
            ctx.needs_clarification = False

            ctx.pipeline_trace[2]["llm_classifier"] = {
                "model": settings.intent_classifier_model,
                "raw_response": raw[:80],
                "applied": True,
                "previous_intent": previous_intent,
                "new_intent": label,
            }
            ctx.pipeline_trace[2]["output"] = ctx.intent
            ctx.pipeline_trace[2]["confidence"] = round(ctx.intent_confidence, 3)

            logger.info(
                "[INTENT] Level-2 LLM: %s -> %s (model=%s)",
                previous_intent, label, settings.intent_classifier_model,
            )

        except Exception as e:
            logger.debug("LLM intent classifier unavailable, keeping rule-based intent: %s", e)
            ctx.pipeline_trace[2]["llm_classifier"] = {
                "model": settings.intent_classifier_model,
                "applied": False, "reason": f"error: {e}",
            }

        return ctx

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Normalizer
    # ─────────────────────────────────────────────────────────────────────
    def _step1_normalize(self, text: str) -> str:
        text = unicodedata.normalize("NFC", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: PII Scrubber
    # ─────────────────────────────────────────────────────────────────────
    def _step2_pii_scrub(self, text: str) -> Tuple[str, bool, List[str]]:
        pii_types = []
        if _EMAIL_PATTERN.search(text):
            text = _EMAIL_PATTERN.sub("<EMAIL_REDACTED>", text)
            pii_types.append("email")
        if _PHONE_PATTERN.search(text):
            text = _PHONE_PATTERN.sub("<PHONE_REDACTED>", text)
            pii_types.append("phone")
        return text, bool(pii_types), pii_types

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Hybrid Intent Detection
    # ─────────────────────────────────────────────────────────────────────
    def _step3_intent(self, text: str) -> IntentResult:
        """
        Layer 1 (regex rules): high-confidence (0.92), low_confidence=False.
        Layer 2 (keyword scoring): scores [0.55, 0.70) flagged low_confidence=True
          so api/main.py can escalate to the Ollama Level-2 classifier.
        Fallback: SEARCH catch-all, low_confidence=True.
        """
        lower = text.lower()

        # Layer 1: Hard rule match
        for intent, patterns in _INTENT_RULES.items():
            if intent == "SEARCH":
                continue
            for pattern in patterns:
                if re.search(pattern, lower):
                    return IntentResult(
                        intent=intent, confidence=0.92,
                        rule_matched=pattern, signal_scores={intent: 0.92},
                        low_confidence=False,
                    )

        # Layer 2: Keyword signal scoring
        signal_scores: Dict[str, float] = {}
        for intent, signals in _INTENT_SIGNALS.items():
            hits = sum(1 for sig in signals if sig in lower)
            if hits > 0:
                signal_scores[intent] = min(0.50 + hits * 0.12, 0.85)

        if signal_scores:
            best_intent = max(signal_scores, key=signal_scores.get)
            best_score = signal_scores[best_intent]
            if best_score >= 0.55:
                return IntentResult(
                    intent=best_intent, confidence=best_score,
                    signal_scores=signal_scores,
                    low_confidence=(best_score < 0.70),
                )

        return IntentResult(
            intent="SEARCH", confidence=0.95,
            signal_scores=signal_scores, low_confidence=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Filter Extraction — structural hard filters ONLY
    # ─────────────────────────────────────────────────────────────────────
    def _step4_filters(self, ctx: QueryContext) -> Dict[str, object]:
        """
        Only two hard filters ever leave this step:

        1. doc_type = "Ticket"  when intent == TICKET_LOOKUP.
           A ticket lookup should only surface ticket chunks; there is no
           semantic ambiguity here, so a hard Qdrant filter is correct.

        2. has_date_filter = True  (informational flag for the trace/UI only,
           never passed to Qdrant).

        Department / repository / tech-term signals extracted from the query
        are NOT filters. They are additive soft boosts applied by
        hybrid_engine._compute_entity_boost() AFTER reranking so that a
        strong cross-department chunk is never silently excluded.
        """
        filters: Dict[str, object] = {}

        if ctx.intent == "TICKET_LOOKUP":
            filters["doc_type"] = "Ticket"
            # Also extract any explicit ticket IDs for the exact-match fast path
            # in TicketRetriever.dual_path_search (5.1). Store on ctx so the
            # engine can reach them without re-running the regex.
            ctx.ticket_ids = _TICKET_ID.findall(ctx.safe_query)

        # ── Repository / Department preference detection (SOFT SIGNALS) ──────
        # These NEVER become filters and NEVER narrow the candidate set.
        # They populate retrieval_signal.repository_weight /
        # retrieval_signal.department_weight, which hybrid_engine applies as
        # additive ranking boosts AFTER reranking.
        lower = ctx.safe_query.lower()

        repo_hits = []
        for repo, keywords in _REPOSITORY_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                repo_hits.append(repo)
        if repo_hits:
            ctx.repositories = repo_hits
            for repo in repo_hits:
                ctx.retrieval_signal["repository_weight"][repo] = _REPOSITORY_WEIGHT

        dept_hits = []
        for dept, keywords in _DEPARTMENT_KEYWORDS.items():
            if any(kw in lower for kw in keywords):
                dept_hits.append(dept)
        if dept_hits:
            ctx.departments = dept_hits
            for dept in dept_hits:
                ctx.retrieval_signal["department_weight"][dept] = _DEPARTMENT_WEIGHT

        return filters

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Query Expansion — domain synonyms only
    # ─────────────────────────────────────────────────────────────────────
    def _step6_expand(self, query: str) -> List[str]:
        """
        Adds up to 3 domain-synonym variants of the query.

        Intentionally omits:
          • Repository-name prefix variants ("{repo} {query}") — these bias the
            embedding away from the semantic core of the query and caused the
            vector search to favour same-department chunks even when better
            answers existed elsewhere.
          • Multiple expansion key matches — only the first (strongest) keyword
            match is expanded to avoid diluting RRF with too many variants.
        """
        lower = query.lower()
        expanded = [query]

        for key, synonyms in _EXPANSION_MAP.items():
            if key in lower:
                for syn in synonyms[:3]:
                    if syn not in lower:
                        expanded.append(syn)
                break  # only the strongest match

        # Deduplicate preserving order
        seen, result = set(), []
        for q in expanded:
            if q not in seen:
                seen.add(q)
                result.append(q)

        return result[:4]  # original + up to 3 synonyms

    # ─────────────────────────────────────────────────────────────────────
    # Decomposition — only explicit comparisons or strong compound signals
    # ─────────────────────────────────────────────────────────────────────
    def _decompose(self, query: str) -> Tuple[List[str], bool]:
        """
        Split compound queries into atomic sub-queries.

        Fires ONLY when:
          - Explicit comparison framing (vs / compare / difference between)
          - Strong multi-clause conjunction (as well as / additionally / …)
            AND token count > 12 (avoids splitting short natural-language queries
            that happen to contain "and").

        The heuristic "split at how/what/when boundaries when token_count > 15"
        has been removed — it was splitting coherent questions and returning
        semantically incomplete sub-queries that scored poorly at reranking.
        """
        lower = query.lower()
        token_count = len(query.split())

        # Explicit comparison always splits
        if _DECOMPOSITION_COMPARE.search(lower):
            match = _DECOMPOSITION_COMPARE.search(lower)
            pivot = match.start()
            part1 = query[:pivot].strip()
            part2 = query[pivot + len(match.group()):].strip()
            if len(part1) > 5 and len(part2) > 5:
                return [part1, part2], True

        # Strong conjunction only when query is clearly compound (>12 tokens)
        if _DECOMPOSITION_CONJUNCTIONS.search(lower) and token_count > 12:
            parts = _DECOMPOSITION_CONJUNCTIONS.split(query, maxsplit=1)
            parts = [p.strip() for p in parts if len(p.strip()) > 5]
            if len(parts) >= 2:
                return parts[:3], True

        return [query], False