"""Hand-computed cases for retrieval_metrics. Run: python -m pytest evals/metrics -q"""

import math

from evals.metrics.retrieval_metrics import (
    Span,
    hit_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

GOLD = [Span("a.pdf", 10, 11, 2)]
MISS = Span("a.pdf", 5, 6)
HIT = Span("a.pdf", 11, 12)  # overlaps gold on page 11


def test_overlap_needs_same_doc_and_pages():
    assert recall_at_k([Span("b.pdf", 10, 11)], GOLD, 1) == 0.0  # right pages, wrong doc
    assert recall_at_k([Span("a.pdf", 12, 13)], GOLD, 1) == 0.0  # right doc, wrong pages
    assert recall_at_k([HIT], GOLD, 1) == 1.0


def test_single_gold_hit_at_rank_two():
    ranked = [MISS, HIT]
    assert recall_at_k(ranked, GOLD, 1) == 0.0
    assert recall_at_k(ranked, GOLD, 2) == 1.0
    assert mrr(ranked, GOLD) == 0.5
    assert precision_at_k(ranked, GOLD, 2) == 0.5
    assert hit_at_k(ranked, GOLD, 1) == 0.0 and hit_at_k(ranked, GOLD, 2) == 1.0
    # dcg = (2^2-1)/log2(3); ideal = (2^2-1)/log2(2)
    assert math.isclose(ndcg_at_k(ranked, GOLD, 2), (3 / math.log2(3)) / 3)


def test_perfect_ranking_is_ndcg_one():
    gold = [Span("a.pdf", 1, 1, 2), Span("a.pdf", 9, 9, 1)]
    ranked = [Span("a.pdf", 1, 1), Span("a.pdf", 9, 9)]
    assert math.isclose(ndcg_at_k(ranked, gold, 5), 1.0)


def test_graded_gain_prefers_grade_two_first():
    gold = [Span("a.pdf", 1, 1, 2), Span("a.pdf", 9, 9, 1)]
    ranked = [Span("a.pdf", 9, 9), Span("a.pdf", 1, 1)]  # grade-1 hit before grade-2 hit
    dcg = 1 / 1 + 3 / math.log2(3)
    ideal = 3 / 1 + 1 / math.log2(3)
    assert math.isclose(ndcg_at_k(ranked, gold, 5), dcg / ideal)


def test_two_chunks_on_same_span_count_once():
    ranked = [HIT, Span("a.pdf", 10, 10)]  # both overlap the single gold span
    assert recall_at_k(ranked, GOLD, 2) == 1.0
    assert math.isclose(ndcg_at_k(ranked, GOLD, 2), 1.0)  # second chunk adds no gain
    assert precision_at_k(ranked, GOLD, 2) == 1.0  # but both chunks are "relevant"


def test_nothing_retrieved():
    assert recall_at_k([], GOLD, 5) == 0.0
    assert mrr([], GOLD) == 0.0
    assert ndcg_at_k([], GOLD, 5) == 0.0
    assert precision_at_k([], GOLD, 5) == 0.0


def test_unanswerable_query_has_no_score():
    # No gold spans -> metric undefined; the runner must exclude these rows.
    assert recall_at_k([HIT], [], 5) is None
    assert mrr([HIT], []) is None
    assert ndcg_at_k([HIT], [], 5) is None
