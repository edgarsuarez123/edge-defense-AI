"""Hand-computed cases for chunking.chunk_document. Run: python -m pytest services/agents -q"""

import re

from services.agents.chunking import chunk_document, short_title


def ws_spans(text):  # fake tokenizer: one token per whitespace-separated word
    return [m.span() for m in re.finditer(r"\S+", text)]


def run(pages, toc, **kw):
    kw = {"max_tokens": 100, "overlap": 2, "min_tokens": 1, **kw}
    return chunk_document(pages, toc, ws_spans, **kw)


DOC = ("Alpha heading\nalpha body one two three\nBeta heading\nbeta body four five six\n"
       "Child heading\nchild body seven eight\nGamma heading\ngamma body nine ten")
TOC = [[1, "Alpha heading", 1], [2, "Beta heading", 1], [3, "Child heading", 1], [1, "Gamma heading", 1]]


def test_children_fold_into_parent_and_sibling_starts_new_chunk():
    chunks, stats = run([DOC], TOC)
    assert [c.section_path for c in chunks] == ["Alpha heading", "Gamma heading"]
    assert "child body seven eight" in chunks[0].content and "Gamma" not in chunks[0].content
    assert stats == {"entries": 4, "headings_located": 4}


def test_children_split_out_when_parent_is_full():
    chunks, _ = run([DOC], TOC, max_tokens=9)  # Alpha=7 tokens, Beta=7, Child=6: none fit together
    assert [c.section_path for c in chunks] == [
        "Alpha heading", "Alpha heading > Beta heading",
        "Alpha heading > Beta heading > Child heading", "Gamma heading"]


def test_page_spans():
    chunks, _ = run(["Alpha heading\nalpha body", "Beta heading\nbeta body"],
                    [[1, "Alpha heading", 1], [1, "Beta heading", 2]])
    assert [(c.page_start, c.page_end) for c in chunks] == [(1, 1), (2, 2)]


def test_oversized_section_windows_have_exact_page_spans():
    page1 = "Big heading\n" + " ".join(f"w{i}" for i in range(1, 7))   # tokens 0..7
    page2 = " ".join(f"w{i}" for i in range(7, 13))                    # tokens 8..13
    chunks, _ = run([page1, page2], [[1, "Big heading", 1]], max_tokens=6, overlap=2)
    assert [(c.page_start, c.page_end) for c in chunks] == [(1, 1), (1, 2), (2, 2)]
    assert all(c.token_count <= 6 for c in chunks)


def test_tiny_chunk_merges_into_next_and_keeps_the_later_path():
    chunks, _ = run(["Tiny head\nGamma heading\ngamma body words here more"],
                    [[1, "Tiny head", 1], [1, "Gamma heading", 1]], min_tokens=5)
    assert len(chunks) == 1
    assert chunks[0].section_path == "Gamma heading" and chunks[0].content.startswith("Tiny head")


def test_invalid_and_skipped_entries_are_ignored():
    page = "Contents\nlist of stuff 1 2 3\nReal heading\nreal body words"
    toc = [[1, "AC_X_Web", 1], [1, "Contents", 1], [1, "Real heading", 1], [2, "Ghost", -1]]
    chunks, stats = run([page], toc)
    assert [c.section_path for c in chunks] == ["Real heading"]
    assert "list of stuff" not in chunks[0].content
    assert stats["entries"] == 2  # filename root and page -1 entry dropped before resolving


def test_missing_heading_falls_back_to_page_start():
    chunks, stats = run(["Alpha heading\nalpha body", "page two text only"],
                        [[1, "Alpha heading", 1], [1, "Not on the page", 2]])
    assert stats["headings_located"] == 1
    assert chunks[1].page_start == 2 and chunks[1].content == "page two text only"


def test_short_title_keeps_number_and_heading_of_paragraph_bookmarks():
    t = "1.1 Purpose of This Advisory Circular (AC). This AC provides guidance in the areas of..."
    assert short_title(t) == "1.1 Purpose of This Advisory Circular (AC)"
    assert short_title("Counter-UAS") == "Counter-UAS"
    assert len(short_title("x" * 300)) <= 91
