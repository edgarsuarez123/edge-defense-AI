"""Initial schema: tracks, incidents, pending_actions, audit_log, document_chunks

Revision ID: 0001
Revises: None
Create Date: 2026-10-15

"""
from typing import Union
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extensions ────────────────────────────────────────────────────────────
    # pgvector must exist before VECTOR(768) columns can be created.
    # gen_random_uuid() is built into PostgreSQL 13+ — no pgcrypto needed.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── Table: incidents ──────────────────────────────────────────────────────
    # Created first because tracks, pending_actions, and audit_log all FK to it.
    op.execute("""
        CREATE TABLE incidents (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            status              TEXT NOT NULL DEFAULT 'OPEN'
                                CHECK (status IN ('OPEN','PENDING_APPROVAL','APPROVED','REJECTED','CLOSED')),
            threat_level        TEXT NOT NULL DEFAULT 'UNKNOWN'
                                CHECK (threat_level IN ('UNKNOWN','LOW','MEDIUM','HIGH')),
            langgraph_thread_id TEXT,
            synced_at           TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            closed_at           TIMESTAMPTZ
        )
    """)

    # ── Table: tracks ─────────────────────────────────────────────────────────
    # Every sensor detection lands here regardless of classification.
    # incident_id is NULL until a track is escalated and linked to an incident.
    # synced_at is intentionally absent: tracks are local-only (see decision log).
    # processed defaults to false; the compound partial index below makes the
    # Redis fallback poll efficient without picking up benign tracks.
    op.execute("""
        CREATE TABLE tracks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_id         UUID REFERENCES incidents(id),
            detected_at         TIMESTAMPTZ NOT NULL,
            sensor_source       TEXT NOT NULL
                                CHECK (sensor_source IN ('EO','RF','AUDIO','FUSED')),
            confidence          FLOAT NOT NULL
                                CHECK (confidence >= 0 AND confidence <= 1),
            classification      TEXT NOT NULL
                                CHECK (classification IN ('BENIGN','UNKNOWN','LIKELY_THREAT')),
            bounding_box        JSONB,
            metadata            JSONB,
            processed           BOOLEAN NOT NULL DEFAULT false,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Table: pending_actions ────────────────────────────────────────────────
    # Agent-drafted outputs waiting for operator approval.
    # Every row here has a corresponding LangGraph interrupt pending.
    op.execute("""
        CREATE TABLE pending_actions (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_id         UUID NOT NULL REFERENCES incidents(id),
            action_type         TEXT NOT NULL
                                CHECK (action_type IN ('ALERT','PROCEDURE','REPORT')),
            agent_id            TEXT NOT NULL
                                CHECK (agent_id IN ('threat_analyst','ops_agent','reporting_agent')),
            draft_content       JSONB NOT NULL,
            approved_by         TEXT,
            approved_at         TIMESTAMPTZ,
            operator_notes      TEXT,
            status              TEXT NOT NULL DEFAULT 'PENDING'
                                CHECK (status IN ('PENDING','APPROVED','REJECTED')),
            synced_at           TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Table: audit_log ──────────────────────────────────────────────────────
    # Append-only. Never UPDATE or DELETE rows here.
    # actor uses free-text (not CHECK) because values include dynamic patterns
    # like 'agent:threat_analyst' — a CHECK would need a regex or enumerate every agent.
    op.execute("""
        CREATE TABLE audit_log (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            incident_id         UUID REFERENCES incidents(id),
            actor               TEXT NOT NULL,
            action              TEXT NOT NULL,
            payload             JSONB,
            synced_at           TIMESTAMPTZ,
            ts                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Table: document_chunks ────────────────────────────────────────────────
    # RAG knowledge base. VECTOR(768) = nomic-embed-text-v1.5 output dimension.
    # content_hash enables idempotent re-ingestion: skip chunks already present.
    op.execute("""
        CREATE TABLE document_chunks (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_doc          TEXT NOT NULL,
            chunk_index         INT NOT NULL,
            content             TEXT NOT NULL,
            embedding           VECTOR(768) NOT NULL,
            content_hash        TEXT NOT NULL,
            metadata            JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # ── Indexes ───────────────────────────────────────────────────────────────

    # tracks: look up all tracks for a given incident
    op.execute("CREATE INDEX idx_tracks_incident_id ON tracks(incident_id)")

    # tracks: Redis fallback poll — compound partial index.
    # Only LIKELY_THREAT + unprocessed tracks enter this index at write time.
    # Benign and alerted tracks are excluded, so the index stays tiny.
    # Ordered by created_at because the poll query uses ORDER BY created_at.
    op.execute("""
        CREATE INDEX idx_tracks_unprocessed_threats
            ON tracks(created_at)
            WHERE processed = false AND classification = 'LIKELY_THREAT'
    """)

    # incidents: status filter (list open/pending incidents for operator UI)
    op.execute("CREATE INDEX idx_incidents_status ON incidents(status)")

    # sync worker queries: find unsynced rows efficiently
    # Partial indexes (WHERE synced_at IS NULL) stay small — most rows will be synced
    op.execute("""
        CREATE INDEX idx_incidents_synced
            ON incidents(synced_at)
            WHERE synced_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_audit_log_synced
            ON audit_log(synced_at)
            WHERE synced_at IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_pending_actions_synced
            ON pending_actions(synced_at)
            WHERE synced_at IS NULL
    """)

    # audit_log: look up all events for a given incident
    op.execute("CREATE INDEX idx_audit_log_incident_id ON audit_log(incident_id)")

    # document_chunks: hnsw vector similarity search.
    # hnsw chosen over ivfflat for better recall — a missed citation in a military
    # context matters more than build time. m=16 and ef_construction=64 are
    # pgvector defaults; sufficient for a corpus under 1M chunks.
    op.execute("""
        CREATE INDEX idx_document_chunks_embedding
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
    """)

    # document_chunks: enforce deduplication on re-ingest
    op.execute("""
        CREATE UNIQUE INDEX idx_document_chunks_hash
            ON document_chunks(source_doc, content_hash)
    """)


def downgrade() -> None:
    # Drop in reverse FK order: children before parents.
    # document_chunks and audit_log have no dependents — order between them doesn't matter.
    op.execute("DROP TABLE IF EXISTS document_chunks")
    op.execute("DROP TABLE IF EXISTS audit_log")
    op.execute("DROP TABLE IF EXISTS pending_actions")
    op.execute("DROP TABLE IF EXISTS tracks")
    op.execute("DROP TABLE IF EXISTS incidents")
    op.execute("DROP EXTENSION IF EXISTS vector")
