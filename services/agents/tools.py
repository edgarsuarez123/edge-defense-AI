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

import psycopg2
import psycopg2.extensions
from langchain_core.tools import tool
from pgvector.psycopg2 import register_vector
from sentence_transformers import CrossEncoder, SentenceTransformer

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
    if url := os.getenv("DATABASE_URL"):
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


# ---------------------------------------------------------------------------
# Shared tool — injected into ALL three agents
# ---------------------------------------------------------------------------

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
    # Step 1 — embed the query.
    # "search_query: " prefix is required by nomic-embed-text-v1.5 at retrieval time.
    # Passages were ingested with "search_document: " — the asymmetric prefixes are
    # part of the model's training and omitting them measurably degrades recall.
    embedder = _get_embedder()
    query_vec = embedder.encode(f"search_query: {query}")

    # Step 2 — pgvector HNSW search with over-fetch for re-ranking.
    # We retrieve top_k * 3 candidates so the cross-encoder has enough to work with.
    # The HNSW index (m=16, ef_construction=64, cosine) was built at migration time.
    # The <=> operator is cosine distance (lower = more similar). 1 - distance = similarity.
    fetch_k = top_k * 3
    conn = _open_db()
    try:
        with conn.cursor() as cur:
            if source_filter:
                cur.execute(
                    """
                    SELECT content, source_doc,
                           1 - (embedding <=> %s) AS cosine_similarity
                    FROM document_chunks
                    WHERE source_doc LIKE %s
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (query_vec, f"{source_filter}%", query_vec, fetch_k),
                )
            else:
                cur.execute(
                    """
                    SELECT content, source_doc,
                           1 - (embedding <=> %s) AS cosine_similarity
                    FROM document_chunks
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (query_vec, query_vec, fetch_k),
                )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        logger.warning(
            "search_knowledge_base returned no results. "
            "Run: python -m services.agents.ingest --source data/docs/"
        )
        return []

    # Step 3 — cross-encoder re-ranking.
    # The HNSW search returns approximate nearest neighbors by embedding similarity.
    # The cross-encoder scores each (query, passage) pair jointly — it reads both
    # texts together, so it catches semantic relevance that embedding cosine similarity
    # misses (e.g. "drone countermeasures" matching a passage about jamming procedures).
    reranker = _get_reranker()
    pairs = [(query, row[0]) for row in rows]
    scores = reranker.predict(pairs)

    # Zip scores back to rows, sort descending, return top_k
    ranked = sorted(zip(scores, rows), key=lambda x: x[0], reverse=True)
    return [
        {
            "content": row[0],
            "source": row[1],
            "score": float(score),
        }
        for score, row in ranked[:top_k]
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
