"""
evals/metrics/chunk_locator.py — map a chunk's text back to PDF page numbers.

Why: chunks ingested before the TOC chunker carry no page metadata, but the golden
set is labeled by page. This lets us score the baseline without re-ingesting.

Method: normalize (lowercase, collapse whitespace), find the chunk's head and tail
anchors in the concatenated page text, convert character offsets to pages.
Known limit: boilerplate that repeats on several pages (running headers) can match
the earliest page; anchors are body text so this is rare. Unplaceable chunks return
None and the runner reports how many there were.
"""

import bisect
import re
from functools import lru_cache
from pathlib import Path

import pymupdf

DOCS_DIR = Path(__file__).resolve().parents[2] / "data" / "docs"
ANCHOR_CHARS = (80, 40)  # try the long anchor first, then a shorter one


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def locate_in_pages(chunk: str, pages: list[str], anchor: int | None = None) -> tuple[int, int] | None:
    """Return (page_start, page_end), 1-based, or None if the chunk can't be placed."""
    norm_pages = [_norm(p) for p in pages]
    starts, pos = [], 0
    for p in norm_pages:  # starts[i] = offset where page i+1 begins in the joined text
        starts.append(pos)
        pos += len(p) + 1
    joined = " ".join(norm_pages)
    text = _norm(chunk)
    page_of = lambda off: bisect.bisect_right(starts, off)  # noqa: E731 — 1-based page

    for n in ([anchor] if anchor else ANCHOR_CHARS):
        head, tail = text[:n], text[-n:]
        h = joined.find(head)
        if h < 0:
            continue
        t = joined.find(tail, h)
        end = (t + len(tail) - 1) if t >= 0 else (h + len(head) - 1)
        return page_of(h), page_of(end)
    return None


@lru_cache(maxsize=None)
def _load_pages(doc: str) -> list[str]:
    with pymupdf.open(DOCS_DIR / doc) as d:
        return [p.get_text("text") for p in d]


def locate(chunk: str, doc: str, metadata: dict | None = None) -> tuple[int, int] | None:
    """Prefer page metadata written by the new chunker; fall back to text matching."""
    if metadata and "page_start" in metadata:
        return metadata["page_start"], metadata["page_end"]
    if not (DOCS_DIR / doc).exists():  # e.g. seed.py's 'FM_3-01_test' row
        return None
    return locate_in_pages(chunk, _load_pages(doc))
