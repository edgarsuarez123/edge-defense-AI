"""
Incident routes: list, detail, approve, reject.

Why raw text() queries instead of ORM models:
  The schema is already defined in Alembic migrations. Defining SQLAlchemy ORM models
  here would duplicate that schema and add maintenance overhead for a read-heavy API.
  text() queries with named parameters are parameterized (safe from injection) and
  map directly to the migration columns.

Approve/reject write pattern:
  1. Update pending_actions status
  2. Update incidents status
  3. Insert audit_log row (append-only — never updated, never deleted)
  4. Commit atomically — all three or none
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..database import get_db
from ..schemas import IncidentResponse, PendingActionResponse, RejectRequest

router = APIRouter(
    prefix="/incidents",
    tags=["incidents"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=list[IncidentResponse])
async def list_incidents(
    incident_status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[IncidentResponse]:
    """List incidents, optionally filtered by status (OPEN, PENDING_APPROVAL, etc.)."""
    query = "SELECT * FROM incidents"
    params: dict = {}
    if incident_status:
        query += " WHERE status = :status"
        params["status"] = incident_status
    query += " ORDER BY created_at DESC LIMIT 100"

    result = await db.execute(text(query), params)
    rows = result.mappings().all()
    return [IncidentResponse(**dict(row), pending_actions=[]) for row in rows]


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> IncidentResponse:
    """Full incident detail including all pending actions."""
    row = (
        await db.execute(
            text("SELECT * FROM incidents WHERE id = :id"),
            {"id": str(incident_id)},
        )
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")

    actions = (
        await db.execute(
            text(
                "SELECT * FROM pending_actions WHERE incident_id = :id ORDER BY created_at"
            ),
            {"id": str(incident_id)},
        )
    ).mappings().all()

    return IncidentResponse(
        **dict(row),
        pending_actions=[PendingActionResponse(**dict(a)) for a in actions],
    )


@router.post("/{incident_id}/approve", status_code=200)
async def approve_incident(
    incident_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Approve the pending action on an incident.

    Marks the pending action APPROVED, closes the incident as APPROVED,
    and appends an immutable audit_log row.
    """
    incident_id_str = str(incident_id)

    await db.execute(
        text(
            """
            UPDATE pending_actions
               SET status = 'APPROVED', approved_at = now(), approved_by = 'operator'
             WHERE incident_id = :id AND status = 'PENDING'
            """
        ),
        {"id": incident_id_str},
    )
    await db.execute(
        text("UPDATE incidents SET status = 'APPROVED' WHERE id = :id"),
        {"id": incident_id_str},
    )
    await db.execute(
        text(
            """
            INSERT INTO audit_log (incident_id, actor, action, payload)
            VALUES (:incident_id, 'operator', 'APPROVED', :payload::jsonb)
            """
        ),
        {"incident_id": incident_id_str, "payload": json.dumps({})},
    )
    await db.commit()
    return {"status": "approved", "incident_id": incident_id_str}


@router.post("/{incident_id}/reject", status_code=200)
async def reject_incident(
    incident_id: UUID,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Reject the pending action on an incident with operator notes.

    Notes are stored on the pending_action row and echoed into audit_log.payload
    for the T&E audit trail.
    """
    incident_id_str = str(incident_id)

    await db.execute(
        text(
            """
            UPDATE pending_actions
               SET status = 'REJECTED', operator_notes = :notes
             WHERE incident_id = :id AND status = 'PENDING'
            """
        ),
        {"id": incident_id_str, "notes": body.notes},
    )
    await db.execute(
        text("UPDATE incidents SET status = 'REJECTED' WHERE id = :id"),
        {"id": incident_id_str},
    )
    await db.execute(
        text(
            """
            INSERT INTO audit_log (incident_id, actor, action, payload)
            VALUES (:incident_id, 'operator', 'REJECTED', :payload::jsonb)
            """
        ),
        {"incident_id": incident_id_str, "payload": json.dumps({"notes": body.notes})},
    )
    await db.commit()
    return {"status": "rejected", "incident_id": incident_id_str}
