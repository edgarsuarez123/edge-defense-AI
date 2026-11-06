"""
services/agents/chunking.py — TOC-driven, structure-aware chunking (chunker_version "toc-v1").

What it does:
    Turns a PDF's pages + its embedded table of contents (bookmarks) into chunks that
    follow the document's own section structure, each carrying section_path and an exact
    page span. Pure functions: no DB, no embedding model (tokenizer is injected), so it
    is unit-testable and the eval can reason about it.

Algorithm (hierarchical greedy packing):
    1. Resolve each TOC entry to a character offset by finding its heading text at a line
       start on its page (falls back to the page start if not found).
    2. Slice the document between consecutive headings; each slice knows its TOC level
       and breadcrumb path ("Chapter 3 > Active Measures > Detect").
    3. Pack: a chunk starts at a slice and absorbs the following *deeper* slices (its
       subsections) while the total stays <= max_tokens. A sibling or higher heading
       always starts a new chunk, so chunks never straddle unrelated sections.
    4. A single slice larger than max_tokens is split by a token window with overlap,
       and each window's page span is computed exactly from token character offsets.
    5. Chunks under min_tokens are merged into the adjacent contiguous chunk (kills
       heading-only stubs).

Known limits: running headers/footers on each page stay in the text; docs without a
usable TOC fall back to the paragraph chunker in ingest.py (no page metadata).
"""

import bisect
import re
from dataclasses import dataclass
from typing import Callable

TokenSpans = Callable[[str], list[tuple[int, int]]]  # text -> (start, end) char span per token

_SKIP_TITLES = {"contents", "table of contents", "figures", "tables", "list of figures",
                "list of tables", "index"}


@dataclass
class Chunk:
    content: str
    section_path: str
    section_level: int
    page_start: int
    page_end: int
    token_count: int


@dataclass
class _Proto:
    start: int
    end: int
    level: int
    path: str
    tokens: int


def hf_token_spans(tokenizer) -> TokenSpans:
    """Adapter: a HuggingFace *fast* tokenizer -> TokenSpans."""
    def spans(text: str) -> list[tuple[int, int]]:
        enc = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
        return [tuple(o) for o in enc["offset_mapping"]]
    return spans


def short_title(title: str) -> str:
    """AC 107-2A bookmarks are 200-char paragraph snippets; keep 'N.N Heading'."""
    t = re.sub(r"\s+", " ", title).strip().rstrip("…").strip()
    m = re.match(r"^(\d+(?:\.\d+)*\.?\s+.{1,90}?)(?:\.\s|$)", t)
    if m:
        return m.group(1).rstrip(".")
    return t if len(t) <= 90 else t[:90].rstrip() + "…"


def _find_heading(title: str, full: str, lo: int, hi: int) -> int | None:
    words = re.sub(r"(…|\.\.\.)$", "", title).split()[:10]
    if not words:
        return None
    pat = r"(?im)^[ \t]*" + r"\s+".join(re.escape(w) for w in words)
    m = re.compile(pat).search(full, lo, hi)
    return m.start() if m else None


def _valid_entries(toc: list) -> list[tuple[int, str, int]]:
    out = []
    for level, title, page in toc:
        if page < 1:  # AC 107-2A carries a duplicate bookmark tree pointing at page -1
            continue
        if level == 1 and " " not in title.strip() and "_" in title:  # filename-like root
            continue
        out.append((level, title, page))
    return out


def chunk_document(
    pages: list[str],
    toc: list,
    count_spans: TokenSpans,
    max_tokens: int = 512,
    overlap: int = 50,
    min_tokens: int = 64,
) -> tuple[list[Chunk], dict]:
    """Return (chunks, stats). stats reports how many headings were located by text."""
    full = "\n".join(pages)
    page_starts, pos = [], 0
    for p in pages:
        page_starts.append(pos)
        pos += len(p) + 1
    page_of = lambda off: bisect.bisect_right(page_starts, off)  # noqa: E731 — 1-based page

    entries = _valid_entries(toc)
    # 1. resolve headings to offsets (monotonic non-decreasing)
    located, positions, prev = 0, [], 0
    for level, title, page in entries:
        lo = max(page_starts[page - 1], prev)
        hi = page_starts[page] if page < len(pages) else len(full)
        found = _find_heading(title, full, lo, hi)
        located += found is not None
        prev = found if found is not None else lo
        positions.append(prev)

    # 2. slices with breadcrumb paths
    stack: list[tuple[int, str]] = []
    slices = []  # (start, end, level, path, skip)
    for i, (level, title, _) in enumerate(entries):
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, short_title(title)))
        end = positions[i + 1] if i + 1 < len(entries) else len(full)
        low = short_title(title).lower()
        skip = low in _SKIP_TITLES or "feedback form" in low
        if end > positions[i]:
            slices.append((positions[i], end, level, " > ".join(t for _, t in stack), skip))

    # 3. pack children into parents while they fit
    protos: list[_Proto] = []
    cur: _Proto | None = None
    for start, end, level, path, skip in slices:
        if skip:
            cur = None
            continue
        n = len(count_spans(full[start:end]))
        if cur and level > cur.level and cur.end == start and cur.tokens + n <= max_tokens:
            cur.end, cur.tokens = end, cur.tokens + n
        else:
            cur = _Proto(start, end, level, path, n)
            protos.append(cur)

    # 5. merge tiny chunks into a contiguous neighbour (later chunk keeps its own path)
    merged: list[_Proto] = []
    for p in protos:
        prev_p = merged[-1] if merged else None
        if prev_p and prev_p.tokens < min_tokens and prev_p.end == p.start \
                and prev_p.tokens + p.tokens <= max_tokens:
            merged[-1] = _Proto(prev_p.start, p.end, p.level, p.path, prev_p.tokens + p.tokens)
        elif prev_p and p.tokens < min_tokens and prev_p.end == p.start \
                and prev_p.tokens + p.tokens <= max_tokens:
            prev_p.end, prev_p.tokens = p.end, prev_p.tokens + p.tokens
        else:
            merged.append(p)

    # 4. emit chunks, windowing any oversized proto with exact page spans
    chunks: list[Chunk] = []

    def emit(start: int, end: int, p: _Proto, tokens: int) -> None:
        raw = full[start:end]
        content = raw.strip()
        if not content:
            return
        s = start + (len(raw) - len(raw.lstrip()))
        chunks.append(Chunk(content, p.path, p.level, page_of(s), page_of(max(s, s + len(content) - 1)), tokens))

    for p in merged:
        if p.tokens <= max_tokens:
            emit(p.start, p.end, p, p.tokens)
            continue
        spans = count_spans(full[p.start:p.end])
        i, step = 0, max_tokens - overlap
        while i < len(spans):
            j = min(i + max_tokens, len(spans))
            emit(p.start + spans[i][0], p.start + spans[j - 1][1], p, j - i)
            if j == len(spans):
                break
            i += step
    return chunks, {"entries": len(entries), "headings_located": located}
