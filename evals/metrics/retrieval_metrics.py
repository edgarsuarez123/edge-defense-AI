"""
evals/metrics/retrieval_metrics.py — retrieval metrics scored by page-span overlap.

Why spans and not chunk IDs:
    Rechunking changes every chunk ID. Gold labels are (doc, page range), and a
    retrieved chunk is relevant if its page range overlaps a gold range in the same
    doc. That keeps the golden set valid across chunker versions.

Conventions:
    - Pages are physical 1-based PDF pages.
    - A gold span's gain is credited once, to the first retrieved chunk that hits it.
    - Queries with no gold spans (unanswerable) return None: exclude them upstream.
"""

import math
from typing import NamedTuple


class Span(NamedTuple):
    doc: str
    page_start: int
    page_end: int
    grade: int = 1  # 2 = direct answer, 1 = supporting; ignored on retrieved spans


def overlaps(a: Span, b: Span) -> bool:
    return a.doc == b.doc and a.page_start <= b.page_end and b.page_start <= a.page_end


def _first_hits(retrieved: list[Span], gold: list[Span], k: int) -> dict[int, int]:
    """gold index -> 0-based rank of the first top-k chunk that overlaps it."""
    found: dict[int, int] = {}
    for rank, chunk in enumerate(retrieved[:k]):
        for gi, g in enumerate(gold):
            if gi not in found and overlaps(chunk, g):
                found[gi] = rank
    return found


def recall_at_k(retrieved: list[Span], gold: list[Span], k: int) -> float | None:
    if not gold:
        return None
    return len(_first_hits(retrieved, gold, k)) / len(gold)


def hit_at_k(retrieved: list[Span], gold: list[Span], k: int) -> float | None:
    """1.0 if any top-k chunk overlaps any gold span (citation is correct)."""
    if not gold:
        return None
    return 1.0 if _first_hits(retrieved, gold, k) else 0.0


def precision_at_k(retrieved: list[Span], gold: list[Span], k: int) -> float | None:
    """Fraction of the k slots holding a relevant chunk. Denominator is k, not len(retrieved)."""
    if not gold:
        return None
    return sum(any(overlaps(c, g) for g in gold) for c in retrieved[:k]) / k


def mrr(retrieved: list[Span], gold: list[Span]) -> float | None:
    if not gold:
        return None
    for rank, chunk in enumerate(retrieved):
        if any(overlaps(chunk, g) for g in gold):
            return 1 / (rank + 1)
    return 0.0


def ndcg_at_k(retrieved: list[Span], gold: list[Span], k: int) -> float | None:
    if not gold:
        return None
    gain = lambda grade: 2**grade - 1  # noqa: E731 — graded gain: 2 -> 3, 1 -> 1
    dcg = sum(
        gain(gold[gi].grade) / math.log2(rank + 2)
        for gi, rank in _first_hits(retrieved, gold, k).items()
    )
    ideal_grades = sorted((g.grade for g in gold), reverse=True)[:k]
    ideal = sum(gain(gr) / math.log2(i + 2) for i, gr in enumerate(ideal_grades))
    return dcg / ideal
