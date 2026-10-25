"""
GET /health — liveness check, no auth required.

Why no auth: monitoring tools and load balancers call /health without credentials.
Requiring a key here breaks standard infra tooling and adds no security benefit
(the endpoint reveals nothing sensitive).
"""

import os

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(db: AsyncSession = Depends(get_db)) -> HealthResponse:
    # Check PostgreSQL
    try:
        await db.execute(text("SELECT 1"))
        pg_status = "ok"
    except Exception:
        pg_status = "error"

    # Check Redis — create a short-lived client, ping, close immediately
    try:
        r = aioredis.from_url(os.environ["REDIS_URL"])
        await r.ping()
        await r.aclose()
        redis_status = "ok"
    except Exception:
        redis_status = "error"

    overall = "ok" if pg_status == "ok" and redis_status == "ok" else "degraded"
    return HealthResponse(status=overall, postgres=pg_status, redis=redis_status)
