"""
services/agents/ingest.py — RAG ingestion pipeline

What it does:
    Reads PDF and TXT documents from a source directory, parses them with pymupdf,
    splits them into ≤512-token chunks using a hybrid military-aware strategy, embeds
    each chunk with nomic-embed-text-v1.5 (CPU), and upserts into the document_chunks
    table in PostgreSQL via psycopg2 + pgvector. Idempotent: the unique index on
    (source_doc, content_hash) means re-runs skip already-ingested chunks.

Why it exists:
    search_knowledge_base is the only way agents retrieve factual context. Without
    ingested documents, agents have no grounding and generate hallucinated citations.
    This script is a manual batch job — it runs when the document set changes, not
    at query time.

Why it lives in services/agents/ and not services/sync/:
    The embedding model, pgvector schema, and document_chunks table are all agent-
    side concerns. services/sync/ handles GovCloud data movement (Week 10). Putting
    ingestion there would create a false dependency on the sync layer.

Chunking strategy (chunker_version "toc-v1", see services/agents/chunking.py):
    1. PDFs with an embedded table of contents are chunked along the TOC: subsections
       fold into their parent while they fit in MAX_TOKENS, oversized sections are
       windowed with exact page spans. Every chunk carries section_path + page range.
    2. Anything else (TXT files, PDFs without a TOC) falls back to paragraph/sentence
       splitting (no page metadata). Replaces the old numbered-heading regex chunker.
    The embedded text is prefixed with "<doc> > <section path>" for retrieval; the stored
    `content` stays clean so agents see (and cite) the original text.

Embedding prefix convention (nomic-embed-text-v1.5):
    Passages ingested with "search_document: <text>" prefix.
    Queries embedded with "search_query: <text>" prefix at retrieval time.
    These prefixes are part of the model's training — omitting them degrades recall.

Usage:
    python -m services.agents.ingest --source data/docs/
    python -m services.agents.ingest --source data/docs/ --dry-run
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from pathlib import Path

import fitz  # pymupdf
import psycopg2
import psycopg2.extensions
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

from services.agents import chunking
from services.agents.db import connect_sync

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOKENS = 512
OVERLAP_TOKENS = 50
EMBED_BATCH_SIZE = 64  # chunks per encode() call — balances throughput vs RAM

# Qwen tokenizer: load from local weights if already downloaded, else fetch
# the tokenizer config only from HuggingFace (~10MB, not the 4GB model weights).
# Token counts must match vLLM's tokenizer so chunk boundaries are precise.
_MODEL_LOCAL = Path(__file__).resolve().parents[2] / "models" / "weights" / "qwen2.5-7b-awq"
TOKENIZER_NAME = str(_MODEL_LOCAL) if _MODEL_LOCAL.exists() else "Qwen/Qwen2.5-7B-Instruct-AWQ"

CHUNKER_VERSION = "toc-v1"
MIN_TOC_ENTRIES = 5  # fewer valid bookmarks than this = no usable TOC -> paragraph fallback


# ---------------------------------------------------------------------------
# Lazy model singletons — loaded once, reused across all documents
# ---------------------------------------------------------------------------

_embedder: SentenceTransformer | None = None
_tokenizer = None


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        logger.info("Loading nomic-embed-text-v1.5 on CPU (one-time, ~270MB)...")
        _embedder = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", device="cpu")
    return _embedder


def _get_tokenizer():
    global _tokenizer
    if _tokenizer is None:
        logger.info(f"Loading Qwen tokenizer from {TOKENIZER_NAME} (one-time)...")
        _tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_NAME)
    return _tokenizer


# ---------------------------------------------------------------------------
# PDF / text extraction
# ---------------------------------------------------------------------------

def _extract_text(path: Path) -> str:
    """Extract full plain text from a PDF, or read a TXT file directly."""
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(str(path))
        pages = [page.get_text("text") for page in doc]
        doc.close()
        return "\n".join(pages)
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _count_tokens(text: str) -> int:
    return len(_get_tokenizer().encode(text, add_special_tokens=False))


def _sliding_window(text: str) -> list[str]:
    """
    Split text into overlapping token windows.
    Called only when a semantic section exceeds MAX_TOKENS — the sliding window
    is the last resort, not the first pass.
    """
    tokens = _get_tokenizer().encode(text, add_special_tokens=False)
    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + MAX_TOKENS, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(_get_tokenizer().decode(chunk_tokens, skip_special_tokens=True))
        if end == len(tokens):
            break
        start += MAX_TOKENS - OVERLAP_TOKENS
    return chunks


def _cap(text: str) -> list[str]:
    """Return text as a single chunk, or split with sliding window if it exceeds MAX_TOKENS."""
    if _count_tokens(text) <= MAX_TOKENS:
        return [text]
    return _sliding_window(text)


def _chunk_fallback(text: str) -> list[str]:
    """
    Fallback chunker for unstructured documents (FAA advisories, spec sheets).
    Splits on paragraph breaks → sentence breaks → sliding window.

    This is functionally equivalent to LangChain's RecursiveCharacterTextSplitter
    with separators=["\n\n", "\n", ". ", ""] but implemented directly to avoid
    adding langchain-text-splitters as a dependency.
    """
    chunks = []
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    for para in paragraphs:
        if _count_tokens(para) <= MAX_TOKENS:
            chunks.append(para)
            continue

        # Paragraph too large — split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", para)
        current = ""
        for sentence in sentences:
            candidate = (current + " " + sentence).strip()
            if _count_tokens(candidate) <= MAX_TOKENS:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                # Single sentence that exceeds MAX_TOKENS — sliding window
                chunks.extend(_cap(sentence))
                current = ""
        if current:
            chunks.append(current)

    return [c for c in chunks if c.strip()]


def _chunk_doc(path: Path) -> list[dict]:
    """
    Return chunk records: {"content", "embed_text", "metadata"} for one document.
    PDFs with a usable TOC use the structure-aware chunker; everything else falls back
    to paragraph/sentence splitting (no page metadata).
    """
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    base = {"chunker_version": CHUNKER_VERSION, "doc_title": path.stem, "source_hash": source_hash[:16]}

    if path.suffix.lower() == ".pdf":
        with fitz.open(str(path)) as doc:
            pages, toc = [p.get_text("text") for p in doc], doc.get_toc()
        if len(chunking._valid_entries(toc)) >= MIN_TOC_ENTRIES:
            logger.info(f"  TOC chunker → {path.name}")
            chunks, stats = chunking.chunk_document(
                pages, toc, chunking.hf_token_spans(_get_tokenizer()), MAX_TOKENS, OVERLAP_TOKENS)
            logger.info(f"  headings located by text: {stats['headings_located']}/{stats['entries']}")
            return [{
                "content": c.content,
                "embed_text": f"{path.stem} > {c.section_path}\n{c.content}",
                "metadata": {**base, "section_path": c.section_path, "section_level": c.section_level,
                             "page_start": c.page_start, "page_end": c.page_end,
                             "token_count": c.token_count},
            } for c in chunks]
        text = "\n".join(pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")

    logger.info(f"  Paragraph fallback chunker → {path.name}")
    return [{"content": c, "embed_text": c, "metadata": {**base, "token_count": _count_tokens(c)}}
            for c in _chunk_fallback(text)]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _connect_db() -> psycopg2.extensions.connection:
    """
    Open a psycopg2 connection and register the pgvector type adapter.

    Why psycopg2 (not asyncpg) here:
        The ingestion script is a batch CLI job. Async adds complexity with no
        throughput benefit for sequential file processing. psycopg2 is simpler
        and correct for this use case.

    Why register_vector:
        Without it, passing a numpy array as a query parameter raises
        "can't adapt type 'numpy.ndarray'". register_vector teaches psycopg2
        how to serialize numpy arrays as the PostgreSQL vector type.
    """
    return connect_sync()


def _upsert_chunks(
    conn: psycopg2.extensions.connection,
    source_doc: str,
    records: list[dict],
    embeddings: list,
    dry_run: bool,
    rebuild: bool = False,
) -> tuple[int, int]:
    """
    Insert chunks into document_chunks. Returns (inserted, skipped).

    Why ON CONFLICT DO NOTHING (no conflict target):
        The unique index is defined as CREATE UNIQUE INDEX ... (source_doc, content_hash).
        PostgreSQL supports conflict target inference for unique indexes, but omitting
        the target and using DO NOTHING catches any unique violation safely. This is
        idempotent: re-running the script on an already-ingested document is a no-op.
    """
    if dry_run:
        logger.info(f"  [dry-run] {len(records)} chunks for {source_doc} — not writing")
        return len(records), 0

    inserted = 0
    skipped = 0
    with conn.cursor() as cur:
        if rebuild:
            # Delete + insert share one transaction (committed below): a failure leaves
            # the old chunks intact rather than a half-empty document.
            cur.execute("DELETE FROM document_chunks WHERE source_doc = %s", (source_doc,))
            logger.info(f"  --rebuild: removed {cur.rowcount} existing chunks for {source_doc}")
        for i, (rec, embedding) in enumerate(zip(records, embeddings)):
            chunk = rec["content"]
            content_hash = hashlib.sha256(chunk.encode()).hexdigest()
            cur.execute(
                """
                INSERT INTO document_chunks
                    (source_doc, chunk_index, content, embedding, content_hash, metadata)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT DO NOTHING
                """,
                (
                    source_doc,
                    i,
                    chunk,
                    embedding,  # numpy array — pgvector adapter handles serialization
                    content_hash,
                    json.dumps({**rec["metadata"], "chunk_index": i, "total_chunks": len(records)}),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
    conn.commit()
    return inserted, skipped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def ingest(source_dir: str, dry_run: bool = False, rebuild: bool = False) -> None:
    source_path = Path(source_dir)
    if not source_path.exists():
        logger.error(f"Source directory not found: {source_path}")
        sys.exit(1)

    docs = sorted(
        list(source_path.glob("**/*.pdf")) + list(source_path.glob("**/*.txt"))
    )
    if not docs:
        logger.error(f"No PDF or TXT files found in {source_path}")
        sys.exit(1)

    logger.info(f"Found {len(docs)} document(s) in {source_path}")

    embedder = _get_embedder()
    conn = None if dry_run else _connect_db()

    total_inserted = 0
    total_skipped = 0

    for doc_path in docs:
        source_doc = doc_path.name
        logger.info(f"Processing {source_doc}...")

        records = _chunk_doc(doc_path)
        if not records:
            logger.warning(f"  No chunks produced for {source_doc} — skipping")
            continue

        logger.info(f"  {len(records)} chunks")

        # Embed in batches with the passage prefix required by nomic-embed-text-v1.5.
        # "search_document: " at ingestion time must pair with "search_query: " at
        # retrieval time — the model was trained on this asymmetric prefix convention.
        prefixed = [f"search_document: {r['embed_text']}" for r in records]
        embeddings = []
        for batch_start in range(0, len(prefixed), EMBED_BATCH_SIZE):
            batch = prefixed[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_vecs = embedder.encode(batch, show_progress_bar=False)
            embeddings.extend(batch_vecs)

        inserted, skipped = _upsert_chunks(conn, source_doc, records, embeddings, dry_run, rebuild)
        logger.info(f"  Inserted: {inserted} | Skipped (already exists): {skipped}")
        total_inserted += inserted
        total_skipped += skipped

    if conn:
        conn.close()

    logger.info(
        f"Ingestion complete. Total inserted: {total_inserted} | "
        f"Total skipped: {total_skipped}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Ingest Distribution A documents into pgvector RAG store"
    )
    parser.add_argument(
        "--source", required=True, help="Directory containing PDF/TXT documents"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk without writing to the database",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Delete each ingested document's existing chunks first (use after changing "
             "the chunker; without it, hash dedup would mix old and new chunks)",
    )
    args = parser.parse_args()
    ingest(args.source, dry_run=args.dry_run, rebuild=args.rebuild)
