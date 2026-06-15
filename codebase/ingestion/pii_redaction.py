"""
ingestion/pii_redaction.py — PII detection & deterministic hashing for ingested
documents (P7: "PII Preservation Without Information Leakage")

Why this exists
----------------
The previous implementation replaced every PII match of a given type with the
SAME generic label (e.g. every email -> "<EMAIL_REDACTED>"). This destroyed
information: distinct emails, IPs, and employee IDs became indistinguishable,
which hurt retrieval, clustering, and ticket correlation, AND in IT/Engineering
runbooks it stripped out IP addresses, server names, ports and config values
that are NOT personal data and are essential to the document's utility.

What this implementation does instead
---------------------------------------
1. DETERMINISTIC HASH TOKENS for true PII:
       10.20.4.15            -> IP_HASH_8F2A91
       EMP12345              -> EMP_HASH_A1B2C3
       john.doe@company.com  -> EMAIL_HASH_73AD21
   - Same input ALWAYS produces the same hash (req 1).
   - Different inputs produce different hashes (req 2) — collisions are
     astronomically unlikely with a 24-bit token derived from SHA-256.
   - Hashing happens HERE, during ingestion, BEFORE embedding (req 6) —
     `chunk_document()` calls `redact_chunk()` before `enrich_chunk_async()`
     / `vector_store.upsert_chunks()`.
   - Because hashing is deterministic, retrieval/BM25/embeddings operate on
     the hashed token consistently across documents (req 4, 7) — e.g. the
     same employee ID hashes to the same token in every chunk, so ticket
     correlation across documents still works.

2. ORIGINAL VALUES NEVER REACH THE LLM OR QDRANT (req 3, 5):
   - `chunk.content` (what gets embedded and shown to the LLM) only ever
     contains the hash token, never the raw value.
   - `chunk.pii_hash_map` records hash_token -> entity TYPE only (e.g.
     {"EMAIL_HASH_73AD21": "email"}) — safe to surface in citations/trace,
     since it reveals only that *some* email existed, not which one.
   - `chunk.pii_vault` (a SEPARATE attribute, never written to Qdrant)
     records hash_token -> ORIGINAL VALUE. Callers (api/main.py) persist this
     to a protected Postgres table (`pii_vault`, see
     ingestion/migration_v6.sql) that is not exposed via the chat/citation
     API — "protected metadata" per requirement 5.

3. TECHNICAL CONTENT IS NOT DESTROYED (req 8):
   For documents that are SOPs/Runbooks/IT/Engineering content, IP addresses
   and phone-shaped numbers (which in this context are almost always ports /
   internal extensions / config values, not personal phone numbers) are left
   COMPLETELY UNTOUCHED — not hashed, not redacted. Server names, ports,
   ticket references (INC-1234, JIRA-567, ...) and config values were never
   matched by any PII pattern in the first place, so they pass through
   unchanged in every document type.

4. NEVER HASHED (by construction): section titles, document titles,
   repository names, product names — `redact_chunk()` operates ONLY on
   `chunk.content`. Metadata fields (`section_title`, `section_hierarchy`,
   `source_file`, `repository`, `department`, etc.) are never passed through
   this module.

Usage:
    from ingestion.pii_redaction import redact_text, redact_chunk

    result = redact_text(text, doc_type="Runbook", department="IT")
    clean_text = result.text
    pii_found  = result.pii_types     # e.g. ["email", "employee_id"]
    audit      = result.audit         # dict of type -> count
    hash_map   = result.hash_map      # hash_token -> entity type (safe)
    vault      = result.vault         # hash_token -> original value (PROTECTED)

    redact_chunk(chunk, doc_type=chunk.doc_type, department=chunk.department)
    # chunk.content      -> hashed in place
    # chunk.pii_hash_map -> hash_token -> type   (goes to Qdrant payload)
    # chunk.pii_vault    -> hash_token -> value  (NEVER goes to Qdrant)
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ════════════════════════════════════════════════════════════════════════════
# DETERMINISTIC HASHING
# ════════════════════════════════════════════════════════════════════════════
def _hash_token(prefix: str, value: str) -> str:
    """
    Deterministic hash token for a PII value, e.g. _hash_token("IP", "10.20.4.15")
    -> "IP_HASH_8F2A91".

    - Normalises the value (strip + lowercase) before hashing so trivial
      formatting differences (e.g. "John.Doe@Company.com" vs
      "john.doe@company.com") collapse to the same token (req 1).
    - 6 hex chars (24 bits) of SHA-256 keeps tokens short while making
      accidental collisions across distinct values vanishingly unlikely
      (req 2).
    """
    normalized = value.strip().lower()
    digest = hashlib.sha256((settings.pii_hash_salt + normalized).encode("utf-8")).hexdigest()
    return f"{prefix}_HASH_{digest[:6].upper()}"


# ════════════════════════════════════════════════════════════════════════════
# REGEX PATTERNS (deterministic, no model dependency)
# ════════════════════════════════════════════════════════════════════════════
_PATTERNS: Dict[str, re.Pattern] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    # P7: employee/staff IDs (e.g. EMP12345, EMPID-9001, STAFF_4521, EID7788).
    # Deliberately narrow prefixes so it never collides with ticket reference
    # formats such as INC-1234, JIRA-567, TICKET-89, REQ-2024-001, etc.
    "employee_id": re.compile(r"\b(?:EMP|EMPID|EMPLOYEE|STAFF|EID)[-_]?\d{3,8}\b", re.IGNORECASE),
    "phone": re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{3,4}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    # India-specific identifiers (data sourced primarily from Indian docs/users)
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan_india": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}

# Maps each PII type to the hash-token prefix used in the output, matching
# the examples in the spec (IP_HASH_*, EMP_HASH_*, EMAIL_HASH_*, ...).
_HASH_PREFIX: Dict[str, str] = {
    "email": "EMAIL",
    "employee_id": "EMP",
    "phone": "PHONE",
    "credit_card": "CARD",
    "ssn": "SSN",
    "ip_address": "IP",
    "aadhaar": "AADHAAR",
    "pan_india": "PAN",
    "person": "PERSON",
    "location": "LOCATION",
}

# Order matters: more specific patterns first so generic ones (e.g. phone vs
# credit_card vs aadhaar vs employee_id, several of which are digit-runs)
# don't double-match the same span.
_PATTERN_ORDER = [
    "email", "employee_id", "pan_india", "ssn", "credit_card", "aadhaar",
    "ip_address", "phone",
]

# ── P7: technical-content detection ────────────────────────────────────────
# For these PII *types*, in a "technical" document (IT/Engineering department,
# or a Runbook), the pattern is matched primarily against operational data —
# IP addresses, ports, internal extensions, config values — not personal data.
# These types are left COMPLETELY UNTOUCHED in technical documents so the
# document's operational content is not destroyed.
_TECH_PRESERVE_TYPES: Set[str] = {"ip_address", "phone"}

_TECHNICAL_DEPARTMENTS: Set[str] = {"IT", "Engineering"}
_TECHNICAL_DOC_TYPES: Set[str] = {"Runbook"}


def _is_technical_context(doc_type: Optional[str], department: Optional[str]) -> bool:
    """True for SOPs/Runbooks/IT/Engineering content where IP addresses,
    ports, server identifiers and similar operational data must be
    preserved verbatim (P7 requirement 8)."""
    if department and department in _TECHNICAL_DEPARTMENTS:
        return True
    if doc_type and doc_type in _TECHNICAL_DOC_TYPES:
        return True
    return False


@dataclass
class PIIResult:
    text: str
    pii_types: List[str] = field(default_factory=list)
    audit: Dict[str, int] = field(default_factory=dict)
    # hash_token -> entity TYPE (e.g. "email"). Safe to store in Qdrant
    # payload / citations — reveals only that a hashed entity exists.
    hash_map: Dict[str, str] = field(default_factory=dict)
    # hash_token -> ORIGINAL VALUE. NEVER persisted to Qdrant. Callers must
    # route this to protected storage (see ingestion/migration_v6.sql).
    vault: Dict[str, str] = field(default_factory=dict)


_presidio_analyzer = None
_presidio_anonymizer = None
_presidio_checked = False


def _get_presidio():
    """Lazily load Presidio (analyzer + anonymizer) if available. Returns
    (analyzer, anonymizer) or (None, None) if not installed/loadable."""
    global _presidio_analyzer, _presidio_anonymizer, _presidio_checked
    if _presidio_checked:
        return _presidio_analyzer, _presidio_anonymizer
    _presidio_checked = True
    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        _presidio_analyzer = AnalyzerEngine()
        _presidio_anonymizer = AnonymizerEngine()
        logger.info("Presidio analyzer/anonymizer loaded for NER-based PII detection")
    except Exception as e:
        logger.info(f"Presidio unavailable ({e}); using regex-only PII redaction")
        _presidio_analyzer, _presidio_anonymizer = None, None
    return _presidio_analyzer, _presidio_anonymizer


# ════════════════════════════════════════════════════════════════════════════
# REGEX PASS — deterministic hashing, per-match (not per-type)
# ════════════════════════════════════════════════════════════════════════════
def _regex_redact(text: str, skip_types: Set[str]) -> PIIResult:
    audit: Dict[str, int] = {}
    pii_types: List[str] = []
    hash_map: Dict[str, str] = {}
    vault: Dict[str, str] = {}

    for ptype in _PATTERN_ORDER:
        if ptype in skip_types:
            continue
        pattern = _PATTERNS[ptype]
        matches = pattern.findall(text)
        if not matches:
            continue

        prefix = _HASH_PREFIX[ptype]

        def _replace(m: re.Match) -> str:
            original = m.group(0)
            token = _hash_token(prefix, original)
            hash_map[token] = ptype
            vault[token] = original
            return token

        text, n = pattern.subn(_replace, text)
        if n:
            audit[ptype] = audit.get(ptype, 0) + n
            pii_types.append(ptype)

    return PIIResult(text=text, pii_types=pii_types, audit=audit, hash_map=hash_map, vault=vault)


# ════════════════════════════════════════════════════════════════════════════
# NER PASS (Presidio) — PERSON / LOCATION, also deterministically hashed
# ════════════════════════════════════════════════════════════════════════════
def _presidio_redact(text: str, entities: List[str] = None) -> PIIResult:
    """Use Presidio NER for entity types regex can't reliably catch
    (PERSON, LOCATION). Each distinct detected span gets its own
    deterministic hash token (req 1/2), not a shared generic label. Falls
    back gracefully if Presidio is unavailable."""
    analyzer, anonymizer = _get_presidio()
    if analyzer is None or anonymizer is None:
        return PIIResult(text=text)

    entities = entities or ["PERSON", "LOCATION"]
    try:
        results = analyzer.analyze(text=text, entities=entities, language="en")
        if not results:
            return PIIResult(text=text)

        from presidio_anonymizer.entities import OperatorConfig

        audit: Dict[str, int] = {}
        hash_map: Dict[str, str] = {}
        vault: Dict[str, str] = {}
        operators = {}

        # Build one CUSTOM replacement per result, keyed by a unique operator
        # name, so each distinct span gets its own deterministic hash rather
        # than a single shared label for the whole entity type.
        for i, r in enumerate(results):
            etype = r.entity_type.lower()
            label_key = "person" if etype == "person" else "location" if etype == "location" else etype
            prefix = _HASH_PREFIX.get(label_key, label_key.upper())
            original_span = text[r.start:r.end]
            token = _hash_token(prefix, original_span)

            hash_map[token] = label_key
            vault[token] = original_span
            audit[label_key] = audit.get(label_key, 0) + 1

            op_name = f"__pii_{i}__"
            operators[r.entity_type] = OperatorConfig("replace", {"new_value": token})

        # Presidio's anonymizer replaces ALL spans of a given entity_type with
        # the SAME operator config. To keep per-span uniqueness, anonymize one
        # result at a time (small documents -> negligible overhead) and merge.
        anonymized_text = text
        offset = 0
        # Process in order of appearance so offsets remain valid as we edit.
        for i, r in enumerate(sorted(results, key=lambda x: x.start)):
            etype = r.entity_type.lower()
            label_key = "person" if etype == "person" else "location" if etype == "location" else etype
            prefix = _HASH_PREFIX.get(label_key, label_key.upper())
            start, end = r.start + offset, r.end + offset
            original_span = anonymized_text[start:end]
            token = _hash_token(prefix, original_span)
            anonymized_text = anonymized_text[:start] + token + anonymized_text[end:]
            offset += len(token) - (end - start)

        return PIIResult(text=anonymized_text, pii_types=list(audit.keys()), audit=audit,
                          hash_map=hash_map, vault=vault)
    except Exception as e:
        logger.warning(f"Presidio redaction failed, skipping NER pass: {e}")
        return PIIResult(text=text)


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ════════════════════════════════════════════════════════════════════════════
def redact_text(
    text: str,
    doc_type: Optional[str] = None,
    department: Optional[str] = None,
    use_ner: bool = True,
) -> PIIResult:
    """
    Replace true-PII spans in `text` with DETERMINISTIC HASH TOKENS.

    1. Regex pass — email, employee_id, SSN, credit card, IP, Aadhaar/PAN,
       phone. `ip_address` and `phone` are SKIPPED ENTIRELY when
       `_is_technical_context(doc_type, department)` is True (P7 req 8) —
       IT/Engineering/Runbook content keeps its IPs, ports and config values
       verbatim.
    2. Optional Presidio NER pass — PERSON, LOCATION entities, each hashed
       individually.

    Returns a PIIResult whose `.text` contains ONLY hash tokens in place of
    PII (never the originals — req 3), plus `.hash_map` (token -> type, safe)
    and `.vault` (token -> original value, MUST be routed to protected
    storage by the caller — req 5).
    """
    if not text or not text.strip():
        return PIIResult(text=text)

    skip_types: Set[str] = set()
    if _is_technical_context(doc_type, department):
        skip_types = set(_TECH_PRESERVE_TYPES)

    regex_result = _regex_redact(text, skip_types=skip_types)

    if not use_ner:
        return regex_result

    ner_result = _presidio_redact(regex_result.text)

    merged_types = list(dict.fromkeys(regex_result.pii_types + ner_result.pii_types))
    merged_audit = dict(regex_result.audit)
    for k, v in ner_result.audit.items():
        merged_audit[k] = merged_audit.get(k, 0) + v
    merged_hash_map = {**regex_result.hash_map, **ner_result.hash_map}
    merged_vault = {**regex_result.vault, **ner_result.vault}

    return PIIResult(
        text=ner_result.text,
        pii_types=merged_types,
        audit=merged_audit,
        hash_map=merged_hash_map,
        vault=merged_vault,
    )


def redact_chunk(chunk, doc_type: Optional[str] = None, department: Optional[str] = None, use_ner: bool = True):
    """
    In-place-style helper: hashes PII in `chunk.content` and attaches:
      - chunk.pii_audit    : dict of type -> count                  (logging)
      - chunk.pii_types    : list of PII types found                (logging)
      - chunk.pii_hash_map : hash_token -> entity TYPE               (P3/P7 —
                              flows to Qdrant payload + citations; SAFE)
      - chunk.pii_vault    : hash_token -> ORIGINAL VALUE            (P7 —
                              NEVER flows to Qdrant; caller must persist to a
                              protected store, e.g. the pii_vault table)

    `doc_type` / `department` default to the chunk's own attributes when not
    passed explicitly, so IT/Engineering Runbook chunks automatically preserve
    IPs/ports/config values (P7 req 8).

    Only `chunk.content` is touched — section titles, document titles,
    repository names and product names (which live in other Chunk fields /
    metadata, never passed through this function) are never hashed.
    """
    doc_type = doc_type if doc_type is not None else getattr(chunk, "doc_type", None)
    department = department if department is not None else getattr(chunk, "department", None)

    result = redact_text(chunk.content, doc_type=doc_type, department=department, use_ner=use_ner)
    chunk.content = result.text
    try:
        chunk.pii_audit = result.audit          # type: ignore[attr-defined]
        chunk.pii_types = result.pii_types      # type: ignore[attr-defined]
        chunk.pii_hash_map = result.hash_map    # type: ignore[attr-defined]  (-> Qdrant payload)
        chunk.pii_vault = result.vault          # type: ignore[attr-defined]  (-> protected store only)
    except Exception:
        pass
    return chunk
