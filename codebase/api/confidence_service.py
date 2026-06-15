"""
api/confidence_service.py  —  Enterprise Confidence Engine

Composite confidence score:
  0.50 × reranker_score    (retrieval precision signal)
  0.30 × retrieval_score   (breadth/coverage signal)
  0.20 × citation_coverage (how well citations cover the answer)

Returns a normalized 0-1 score with a human-readable label.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class ConfidenceBreakdown:
    overall: float          # 0-1 composite
    reranker_component: float
    retrieval_component: float
    citation_component: float
    label: str              # HIGH | MEDIUM | LOW | VERY_LOW
    color: str              # for UI badge
    reasoning: str          # human explanation


WEIGHTS = {"reranker": 0.50, "retrieval": 0.30, "citation": 0.20}


def compute_confidence(
    reranker_top_score: float,
    total_candidates: int,
    chunks_used: int,
    max_chunks: int = 8,
    chunk_scores: Optional[List[float]] = None,
) -> ConfidenceBreakdown:
    """
    Compute multi-signal confidence score.

    reranker_top_score : top cross-encoder score (0-1)
    total_candidates   : how many RRF candidates survived
    chunks_used        : chunks that made it to context
    max_chunks         : expected max (8 by default)
    chunk_scores       : list of individual chunk scores for coverage calc
    """
    # ── 1. Reranker signal ────────────────────────────────────────────────────
    # Raw cross-encoder score is the strongest quality signal
    reranker_component = min(reranker_top_score, 1.0)

    # ── 2. Retrieval coverage signal ─────────────────────────────────────────
    # How many candidates found? More = broader evidence base
    if total_candidates >= 20:
        retrieval_component = 1.0
    elif total_candidates >= 10:
        retrieval_component = 0.75
    elif total_candidates >= 5:
        retrieval_component = 0.50
    elif total_candidates >= 2:
        retrieval_component = 0.30
    else:
        retrieval_component = 0.0

    # ── 3. Citation coverage signal ───────────────────────────────────────────
    # Penalise if very few chunks survived to context
    fill_ratio = chunks_used / max_chunks if max_chunks > 0 else 0
    if fill_ratio >= 0.75:
        citation_component = 1.0
    elif fill_ratio >= 0.5:
        citation_component = 0.75
    elif fill_ratio >= 0.25:
        citation_component = 0.50
    else:
        citation_component = 0.20

    # If chunk_scores provided, also measure score consistency
    if chunk_scores and len(chunk_scores) >= 2:
        score_variance = max(chunk_scores) - min(chunk_scores)
        if score_variance > 0.40:
            citation_component *= 0.85  # high variance = inconsistent quality

    # ── Composite ─────────────────────────────────────────────────────────────
    overall = (
        WEIGHTS["reranker"] * reranker_component
        + WEIGHTS["retrieval"] * retrieval_component
        + WEIGHTS["citation"] * citation_component
    )
    overall = round(min(overall, 1.0), 3)

    # ── Label ──────────────────────────────────────────────────────────────────
    if overall >= 0.75:
        label, color = "HIGH", "#10b981"
        reasoning = "Strong match across multiple relevant sources."
    elif overall >= 0.55:
        label, color = "MEDIUM", "#f59e0b"
        reasoning = "Relevant content found; answer may be partial."
    elif overall >= 0.35:
        label, color = "LOW", "#ef4444"
        reasoning = "Limited matching content. Consider rephrasing."
    else:
        label, color = "VERY_LOW", "#991b1b"
        reasoning = "Insufficient evidence. Answer may be unreliable."

    return ConfidenceBreakdown(
        overall=overall,
        reranker_component=round(reranker_component, 3),
        retrieval_component=round(retrieval_component, 3),
        citation_component=round(citation_component, 3),
        label=label,
        color=color,
        reasoning=reasoning,
    )
