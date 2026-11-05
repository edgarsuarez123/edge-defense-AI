from evals.metrics.chunk_locator import locate_in_pages

PAGES = [
    "Cover page   title",
    "1-1. Purpose. This publication describes counter-UAS employment considerations for units.",
    "More text on page three.\n3-2. Employment. Units should   assume the adversary observes\nthe outpost with small UAS.",
    "Page four continues the discussion of employment and ends here.",
]


def test_chunk_inside_one_page():
    assert locate_in_pages("3-2. Employment. Units should assume the adversary", PAGES) == (3, 3)


def test_whitespace_and_case_are_normalized():
    assert locate_in_pages("1-1.  PURPOSE. this publication\ndescribes counter-uas", PAGES) == (2, 2)


def test_chunk_spanning_a_page_break():
    chunk = "small UAS. Page four continues the discussion of employment and ends here."
    assert locate_in_pages(chunk, PAGES, anchor=20) == (3, 4)


def test_unlocatable_chunk_returns_none():
    assert locate_in_pages("text that appears nowhere in this document at all", PAGES) is None
