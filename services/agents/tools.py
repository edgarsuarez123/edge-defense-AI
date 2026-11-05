"""
Agent tools — shared and agent-specific.

Why tools are defined here (not inline in each agent):
  search_knowledge_base is identical regardless of which agent calls it. Query
  intent (threat analysis vs procedure retrieval) is determined by the agent's
  system prompt, not by having separate tool implementations. One definition =
  one place to maintain, one schema in the OpenAPI spec, one test.

  Agent-specific tools (get_track_history, get_procedures, etc.) are also here
  so the tool set is visible in one file and injected at graph build time.

Stub policy:
  All tools return placeholder data. Real implementations require PostgreSQL
  (for track history, incident context) and the RAG stack (for knowledge base).
  Each stub is labeled with what the real implementation does and what week it
  ships. Stubs return the correct shape so graph logic can be tested now.
"""

import logging
import os
import re

import psycopg2
import psycopg2.extensions
from langchain_core.tools import tool
from sentence_transformers import CrossEncoder, SentenceTransformer

from services.agents.db import connect_sync

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy model singletons — loaded once per process, reused per tool call.
# Both models run on CPU: nomic-embed is 270MB, cross-encoder is 80MB.
# Loading on GPU would add ~350MB VRAM pressure on top of vLLM's 7.5GB budget.
# At query time, one embed call takes <50ms on CPU — invisible to the operator.
# ---------------------------------------------------------------------------

_embedder: SentenceTransformer | None = None
_reranker: CrossEncoder | None = None


def _get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        logger.info("Loading nomic-embed-text-v1.5 on CPU...")
        _embedder = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", device="cpu")
    return _embedder


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        logger.info("Loading cross-encoder/ms-marco-MiniLM-L-6-v2...")
        _reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _reranker


def _open_db() -> psycopg2.extensions.connection:
    """
    Open a fresh psycopg2 connection per tool call.

    Why a new connection per call and not a pool:
        search_knowledge_base is called from LangGraph's thread pool executor
        (because it is a sync tool in an async graph). A new connection per call
        is safe across threads — no shared state to corrupt. The latency cost is
        <5ms, negligible compared to the embedding (~50ms) and re-ranking (~30ms).
        A pool can be added in Week 6 when the agents service is containerized and
        connection count becomes a concern.
    """
    return connect_sync()


# ---------------------------------------------------------------------------
# Shared tool — injected into ALL three agents
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i if in is it its "
    "my of on or our so than that the their then there these they this to up was we "
    "what when where which who why will with you your".split()
)
RRF_K = 60  # standard reciprocal-rank-fusion damping constant


def _where(source_filter: str | None) -> tuple[str, list]:
    if source_filter:
        return "WHERE source_doc LIKE %s", [f"{source_filter}%"]
    return "", []


def _vector_candidates(query: str, fetch_k: int, source_filter: str | None) -> list[dict]:
    # "search_query: " prefix is required by nomic-embed-text-v1.5 at retrieval time;
    # passages were ingested with "search_document: " (asymmetric training convention).
    query_vec = _get_embedder().encode(f"search_query: {query}")
    where, wparams = _where(source_filter)
    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, content, source_doc, metadata,
                       1 - (embedding <=> %s) AS cosine_similarity
                FROM document_chunks {where}
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                [query_vec, *wparams, query_vec, fetch_k],
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"id": str(i), "content": c, "source": src, "metadata": md or {}, "cosine": float(cos)}
        for i, c, src, md, cos in rows
    ]


def _lexical_candidates(query: str, fetch_k: int, source_filter: str | None) -> list[dict]:
    """
    Keyword branch: OR of the query's non-stopword terms against the 'simple' tsvector.
    Caveat: ts_rank_cd has no IDF, so rare terms are not weighted above common ones;
    the fusion + cross-encoder downstream compensate. Revisit with BM25 if the eval says so.
    """
    terms = [
        t for t in dict.fromkeys(re.findall(r"[a-z0-9]+", query.lower()))
        if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())
    ]
    if not terms:
        return []
    tsq = " | ".join(terms)
    where, wparams = _where(source_filter)
    clause = "content_tsv @@ to_tsquery('simple', %s)"
    where = f"{where} AND {clause}" if where else f"WHERE {clause}"
    conn = _open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, content, source_doc, metadata
                FROM document_chunks {where}
                ORDER BY ts_rank_cd(content_tsv, to_tsquery('simple', %s), 32) DESC
                LIMIT %s
                """,
                [*wparams, tsq, tsq, fetch_k],
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {"id": str(i), "content": c, "source": src, "metadata": md or {}, "cosine": None}
        for i, c, src, md in rows
    ]


def _rrf(*ranked_lists: list[dict]) -> list[dict]:
    """Reciprocal rank fusion: score = sum over lists of 1/(RRF_K + rank). No score
    normalization needed, which is why it suits mixing cosine similarity with ts_rank."""
    fused: dict[str, dict] = {}
    for lst in ranked_lists:
        for rank, c in enumerate(lst, start=1):
            entry = fused.setdefault(c["id"], {**c, "rrf": 0.0})
            entry["rrf"] += 1.0 / (RRF_K + rank)
            if entry.get("cosine") is None and c.get("cosine") is not None:
                entry["cosine"] = c["cosine"]
    return sorted(fused.values(), key=lambda c: c["rrf"], reverse=True)


def retrieve_candidates(
    query: str,
    fetch_k: int,
    source_filter: str | None = None,
    mode: str = "vector",
) -> list[dict]:
    """
    Stage 1 candidate generation. mode="vector": pgvector HNSW cosine order.
    mode="hybrid": vector + keyword branches fused with RRF, truncated to fetch_k.
    Split out from the tool so the eval harness can score each ordering separately.
    """
    vec = _vector_candidates(query, fetch_k, source_filter)
    if mode == "vector":
        return vec
    if mode != "hybrid":
        raise ValueError(f"unknown retrieval mode: {mode!r}")
    return _rrf(vec, _lexical_candidates(query, fetch_k, source_filter))[:fetch_k]


def rerank(query: str, candidates: list[dict]) -> list[dict]:
    """
    Stage 2: cross-encoder scores each (query, passage) pair jointly, catching
    relevance that embedding cosine misses. Returns candidates sorted by score desc,
    each with a "score" key added.
    """
    if not candidates:
        return []
    scores = _get_reranker().predict([(query, c["content"]) for c in candidates])
    scored = [{**c, "score": float(s)} for c, s in zip(candidates, scores)]
    return sorted(scored, key=lambda c: c["score"], reverse=True)


@tool
def search_knowledge_base(
    query: str,
    top_k: int = 5,
    source_filter: str | None = None,
) -> list[dict]:
    """
    Retrieve relevant passages from the RAG knowledge base.
    Returns chunks with content, source document name, and relevance score.
    Query intent is determined by the calling agent's system prompt.

    Args:
        query: Natural-language query to embed and search.
        top_k: Maximum number of chunks to return (default 5).
        source_filter: Optional document name prefix to restrict results (e.g. "FM 3-01").
    """
    # Over-fetch 3x so the cross-encoder has enough candidates to reorder.
    candidates = retrieve_candidates(query, top_k * 3, source_filter)
    if not candidates:
        logger.warning(
            "search_knowledge_base returned no results. "
            "Run: python -m services.agents.ingest --source data/docs/"
        )
        return []
    return [
        {"content": c["content"], "source": c["source"], "score": c["score"],
         "metadata": c["metadata"]}
        for c in rerank(query, candidates)[:top_k]
    ]


# ---------------------------------------------------------------------------
# Threat Analyst tools
# ---------------------------------------------------------------------------

@tool
def get_track_history(incident_id: str) -> list[dict]:
    """
    Retrieve all track records associated with this incident from the database.
    Provides the analyst with the full detection sequence, not just the latest track.
    """
    # STUB — replace with real PostgreSQL query in Week 6
    # Real implementation: SELECT * FROM tracks WHERE incident_id = $1 ORDER BY detected_at
    logger.warning("get_track_history is a stub — returning placeholder data")
    return [
        {
            "incident_id": incident_id,
            "track_count": 0,
            "note": "STUB — real implementation queries tracks table",
        }
    ]


@tool
def get_drone_specs(model_name: str) -> dict:
    """
    Look up known specifications for a drone model from the knowledge base.
    Used by the threat analyst to assess capability (range, payload, speed).
    """
    # STUB — replace with RAG lookup or structured DB table in Week 5
    # Real implementation: query document_chunks with source_filter="drone_specs"
    logger.warning("get_drone_specs is a stub — returning placeholder data")
    return {
        "model": model_name,
        "note": "STUB — real implementation queries drone specs knowledge base",
        "max_range_km": None,
        "max_payload_kg": None,
        "top_speed_ms": None,
    }


# ---------------------------------------------------------------------------
# Ops Agent tools
# ---------------------------------------------------------------------------

@tool
def get_procedures(threat_level: str, incident_type: str) -> list[dict]:
    """
    Retrieve standard operating procedures for a given threat level and incident type.
    Primary lookup for the ops agent — supplements document search.
    """
    # STUB — replace with RAG lookup in Week 5
    # Real implementation: search_knowledge_base with source_filter="SOP" + structured filter
    logger.warning("get_procedures is a stub — returning placeholder data")
    return [
        {
            "threat_level": threat_level,
            "incident_type": incident_type,
            "note": "STUB — real implementation queries procedures from knowledge base",
            "steps": [],
        }
    ]


@tool
def get_incident_context(incident_id: str) -> dict:
    """
    Retrieve the full incident record from the database.
    Shared by ops agent and reporting agent for incident metadata.
    """
    # STUB — replace with real PostgreSQL query in Week 6
    # Real implementation: SELECT * FROM incidents WHERE id = $1
    logger.warning("get_incident_context is a stub — returning placeholder data")
    return {
        "incident_id": incident_id,
        "note": "STUB — real implementation queries incidents table",
        "status": None,
        "created_at": None,
    }


# ---------------------------------------------------------------------------
# Reporting Agent tools
# ---------------------------------------------------------------------------

@tool
def get_approved_actions(incident_id: str) -> list[dict]:
    """
    Retrieve previously approved actions for this incident from pending_actions table.
    Used by reporting agent to include prior approvals in the draft alert.
    """
    # STUB — replace with real PostgreSQL query in Week 6
    # Real implementation: SELECT * FROM pending_actions WHERE incident_id = $1 AND status = 'APPROVED'
    logger.warning("get_approved_actions is a stub — returning placeholder data")
    return []


@tool
def draft_alert(incident_id: str) -> dict:
    """
    Retrieve the current draft alert for this incident if one was previously created.
    Allows the reporting agent to build on or revise an existing draft.
    """
    # STUB — replace with real PostgreSQL query in Week 6
    # Real implementation: SELECT draft_alert FROM incidents WHERE id = $1
    logger.warning("draft_alert tool is a stub — returning empty draft")
    return {"incident_id": incident_id, "draft": None}


@tool
def request_approval(incident_id: str, action_id: str) -> dict:
    """
    Signal that the draft alert is ready for operator approval.

    IMPORTANT: This tool does NOT approve anything itself. It sets the incident
    status to PENDING_APPROVAL and the graph pauses at the human interrupt node.
    The operator's decision comes back through graph.resume(), not through this tool.

    Approval rule (from architecture-design.md §4.3):
      request_approval is the ONLY tool that changes incident status.
      It routes through the supervisor interrupt — agents cannot modify their
      own approval rules or bypass the operator step.
    """
    # STUB — replace with real PostgreSQL UPDATE + SSE event in Week 6
    # Real implementation:
    #   UPDATE incidents SET status = 'PENDING_APPROVAL', pending_action_id = $2 WHERE id = $1
    #   XADD incident_events {"incident_id": ..., "event": "approval_requested"}
    logger.warning("request_approval is a stub — no real DB write or SSE event")
    return {
        "incident_id": incident_id,
        "action_id": action_id,
        "status": "PENDING_APPROVAL",
        "note": "STUB — real implementation writes to DB and fires SSE",
    }


# ---------------------------------------------------------------------------
# Tool sets — injected per agent in graph.py
# ---------------------------------------------------------------------------

SHARED_TOOLS = [search_knowledge_base]

THREAT_ANALYST_TOOLS = [search_knowledge_base, get_track_history, get_drone_specs]

OPS_AGENT_TOOLS = [search_knowledge_base, get_procedures, get_incident_context]

REPORTING_AGENT_TOOLS = [
    search_knowledge_base,
    get_incident_context,
    get_approved_actions,
    draft_alert,
    request_approval,
]
