"""
GET /tracks — recent sensor tracks from the perception pipeline.

Tracks are the raw output of the YOLO detector + Kalman tracker.
They are local-only (never synced to GovCloud) and high-volume.
This endpoint is read-only; tracks are written by the perception service via Redis.

Query params:
  limit — max rows returned (default 50, hard cap 500)
  since — ISO 8601 timestamp; if provided, returns only tracks after this time
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..database import get_db
from ..schemas import TrackResponse

router = APIRouter(
    prefix="/tracks",
    tags=["tracks"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=list[TrackResponse])
async def list_tracks(
    limit: int = 50,
    since: datetime | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[TrackResponse]:
    """Return recent tracks ordered by detection time, newest first."""
    # Cap at 500 so the operator terminal never gets a runaway response
    limit = min(limit, 500)

    query = "SELECT * FROM tracks"
    params: dict = {"limit": limit}

    if since:
        query += " WHERE detected_at > :since"
        params["since"] = since

    query += " ORDER BY detected_at DESC LIMIT :limit"

    result = await db.execute(text(query), params)
    return [TrackResponse(**dict(r)) for r in result.mappings().all()]
