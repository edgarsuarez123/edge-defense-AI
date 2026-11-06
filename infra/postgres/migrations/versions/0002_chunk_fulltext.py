"""Full-text search column on document_chunks for hybrid retrieval

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05

Adds content_tsv, a stored generated tsvector using the 'simple' text-search config
(no stemming, no stopword removal) so acronyms and part numbers survive tokenization
exactly. A GIN index makes the lexical branch of hybrid retrieval fast.
Generated column: PostgreSQL keeps it in sync on INSERT/UPDATE; ingest.py is unchanged.
"""
from typing import Union
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
    """)
    op.execute(
        "CREATE INDEX idx_document_chunks_tsv ON document_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
