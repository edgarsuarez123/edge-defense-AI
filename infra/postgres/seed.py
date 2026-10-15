"""
Schema smoke test — run after `alembic upgrade head` to verify all tables,
constraints, and indexes accepted real data.

Usage:
    DATABASE_SYNC_URL=postgresql+psycopg2://edge:pass@localhost:5432/edge \
    python infra/postgres/seed.py
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

# Load .env from project root so this script can be run directly without
# manually exporting environment variables.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

url = os.environ.get("DATABASE_SYNC_URL", "")
if not url:
    print("ERROR: DATABASE_SYNC_URL not set", file=sys.stderr)
    sys.exit(1)

# psycopg2 wants a plain DSN, not a SQLAlchemy URL
dsn = url.replace("postgresql+psycopg2://", "postgresql://")
conn = psycopg2.connect(dsn)
conn.autocommit = False
cur = conn.cursor(cursor_factory=RealDictCursor)

try:
    now = datetime.now(timezone.utc)

    # 1. incidents — no FK dependencies, insert first
    cur.execute("""
        INSERT INTO incidents (status, threat_level)
        VALUES ('OPEN', 'HIGH')
        RETURNING id
    """)
    incident_id = cur.fetchone()["id"]
    print(f"incidents OK  — id: {incident_id}")

    # 2. tracks — FK to incidents; one benign, one escalated threat
    cur.execute("""
        INSERT INTO tracks (incident_id, detected_at, sensor_source, confidence, classification, processed)
        VALUES
            (NULL,        %s, 'EO',    0.31, 'BENIGN',       false),
            (%s,          %s, 'FUSED', 0.87, 'LIKELY_THREAT', false)
        RETURNING id, classification
    """, (now, incident_id, now))
    rows = cur.fetchall()
    for r in rows:
        print(f"tracks OK     — id: {r['id']}  classification: {r['classification']}")

    # 3. pending_actions — FK to incidents
    cur.execute("""
        INSERT INTO pending_actions (incident_id, action_type, agent_id, draft_content)
        VALUES (%s, 'ALERT', 'reporting_agent', '{"summary": "test alert"}')
        RETURNING id
    """, (incident_id,))
    action_id = cur.fetchone()["id"]
    print(f"pending_actions OK — id: {action_id}")

    # 4. audit_log — FK to incidents; actor uses dynamic pattern (no CHECK on actor)
    cur.execute("""
        INSERT INTO audit_log (incident_id, actor, action, payload)
        VALUES (%s, 'agent:threat_analyst', 'ANALYSIS_COMPLETE', '{"tokens": 312}')
        RETURNING id
    """, (incident_id,))
    log_id = cur.fetchone()["id"]
    print(f"audit_log OK  — id: {log_id}")

    # 5. document_chunks — requires pgvector; insert a 768-dim zero vector
    zero_vec = "[" + ",".join(["0"] * 768) + "]"
    cur.execute("""
        INSERT INTO document_chunks (source_doc, chunk_index, content, embedding, content_hash)
        VALUES ('FM_3-01_test', 0, 'Test chunk content.', %s::vector, 'abc123')
        RETURNING id
    """, (zero_vec,))
    chunk_id = cur.fetchone()["id"]
    print(f"document_chunks OK — id: {chunk_id}")

    conn.commit()
    print("\nAll tables OK. Schema is healthy.")

except Exception as e:
    conn.rollback()
    print(f"\nFAILED: {e}", file=sys.stderr)
    sys.exit(1)

finally:
    cur.close()
    conn.close()
