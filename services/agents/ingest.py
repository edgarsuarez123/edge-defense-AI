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

Chunking strategy (semantic-first, token-capped):
    1. If the document has military numbered headings (1-1., Chapter 3, Appendix A),
       split on those boundaries — each section is a semantically coherent unit.
    2. Otherwise (FAA advisories, spec sheets), split on paragraph breaks, then
       sentence breaks. This is the RecursiveCharacterTextSplitter equivalent, but
       implemented directly to avoid the langchain dependency.
    3. Any section/paragraph that exceeds MAX_TOKENS is further split by a sliding
       token window with OVERLAP_TOKENS overlap.
    Sections split by heading patterns stay coherent; the sliding window only fires
    on oversized sections where no better boundary exists.

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
from pgvector.psycopg2 import register_vector
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

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

# Military numbered heading patterns — these are split points for semantic chunking.
# Covers: "1-1. Purpose", "3-2 Description", "Chapter 3", "Section II",
#         "Appendix A", "ANNEX B", "paragraph 4-7"
_MILITARY_HEADING = re.compile(
    r"(?m)(?=^(?:"
    r"\d+-\d+[\.\s]"
    r"|Chapter\s+\d+"
    r"|Section\s+[A-Z0-9]+"
    r"|Appendix\s+[A-Z]"
    r"|ANNEX\s+[A-Z]"
    r"|paragraph\s+\d+"
    r"))",
    re.IGNORECASE | re.MULTILINE,
)


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


def _chunk_military(text: str) -> list[str]:
    """
    Semantic-first chunking for military manuals.
    Splits on numbered section headings, then caps each section to MAX_TOKENS.

    Why this ordering:
        Military FMs are written so each numbered section is self-contained.
        Splitting on headings keeps related sentences together. The sliding window
        only fires if a section is unusually long (e.g. a table spanning a full page).
    """
    sections = _MILITARY_HEADING.split(text)
    chunks = []
    for section in sections:
        section = section.strip()
        if section:
            chunks.extend(_cap(section))
    return chunks


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


def _chunk(text: str, source_doc: str) -> list[str]:
    """
    Choose chunking strategy based on document structure.
    Military documents (those with numbered heading patterns) get the semantic
    splitter. Everything else gets the paragraph/sentence fallback.
    """
    if _MILITARY_HEADING.search(text):
        logger.info(f"  Military heading chunker → {source_doc}")
        return _chunk_military(text)
    logger.info(f"  Paragraph fallback chunker → {source_doc}")
    return _chunk_fallback(text)


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
    # psycopg2 requires postgresql:// — strip any +driver suffix (asyncpg, psycopg2, etc.)
    raw = os.getenv("DATABASE_SYNC_URL") or os.getenv("DATABASE_URL", "")
    url = re.sub(r"^(postgresql|postgres)\+\w+://", r"\1://", raw)
    if url:
        conn = psycopg2.connect(url)
    else:
        conn = psycopg2.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            port=int(os.getenv("POSTGRES_PORT", "5433")),
            user=os.getenv("POSTGRES_USER", "edge"),
            password=os.getenv("POSTGRES_PASSWORD", "edge"),
            dbname=os.getenv("POSTGRES_DB", "edge"),
        )
    register_vector(conn)
    return conn


def _upsert_chunks(
    conn: psycopg2.extensions.connection,
    source_doc: str,
    chunks: list[str],
    embeddings: list,
    dry_run: bool,
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
        logger.info(f"  [dry-run] {len(chunks)} chunks for {source_doc} — not writing")
        return len(chunks), 0

    inserted = 0
    skipped = 0
    with conn.cursor() as cur:
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
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
                    json.dumps({"chunk_index": i, "total_chunks": len(chunks)}),
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

def ingest(source_dir: str, dry_run: bool = False) -> None:
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

        text = _extract_text(doc_path)
        if not text.strip():
            logger.warning(f"  No text extracted from {source_doc} — skipping")
            continue

        chunks = _chunk(text, source_doc)
        if not chunks:
            logger.warning(f"  No chunks produced for {source_doc} — skipping")
            continue

        logger.info(f"  {len(chunks)} chunks")

        # Embed in batches with the passage prefix required by nomic-embed-text-v1.5.
        # "search_document: " at ingestion time must pair with "search_query: " at
        # retrieval time — the model was trained on this asymmetric prefix convention.
        prefixed = [f"search_document: {c}" for c in chunks]
        embeddings = []
        for batch_start in range(0, len(prefixed), EMBED_BATCH_SIZE):
            batch = prefixed[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_vecs = embedder.encode(batch, show_progress_bar=False)
            embeddings.extend(batch_vecs)

        inserted, skipped = _upsert_chunks(conn, source_doc, chunks, embeddings, dry_run)
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
    args = parser.parse_args()
    ingest(args.source, dry_run=args.dry_run)
