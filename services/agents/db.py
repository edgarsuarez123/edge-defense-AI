"""
services/agents/db.py — shared synchronous PostgreSQL connection helper.

What it does:
    connect_sync() returns a psycopg2 connection with the pgvector adapter registered.

Why it exists:
    ingest.py and tools.py each had their own copy of "open a psycopg2 connection".
    Only ingest.py's copy handled the SQLAlchemy "+asyncpg" URL suffix, so
    search_knowledge_base could never connect. One shared function = one fix.
"""

import os
import re

import psycopg2
import psycopg2.extensions
from pgvector.psycopg2 import register_vector


def _normalize_url(raw: str) -> str:
    """psycopg2 only understands postgresql://. Strip '+asyncpg', '+psycopg2', etc."""
    return re.sub(r"^(postgresql|postgres)\+\w+://", r"\1://", raw)


def connect_sync() -> psycopg2.extensions.connection:
    """
    Open a fresh psycopg2 connection and register the pgvector type adapter.

    DATABASE_SYNC_URL wins over DATABASE_URL: it is the variable meant for sync
    drivers (Alembic uses it too). Falls back to POSTGRES_* vars if neither is set.
    register_vector teaches psycopg2 to serialize numpy arrays as the vector type.
    """
    raw = os.getenv("DATABASE_SYNC_URL") or os.getenv("DATABASE_URL", "")
    url = _normalize_url(raw)
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
