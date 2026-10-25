"""
Pydantic response models for the API.

These mirror the PostgreSQL schema (architecture-design.md §3.1) exactly.
They are separate from any ORM models — FastAPI serializes them directly from
raw DB row mappings. This avoids SQLAlchemy model coupling on an API that is
mostly read-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class TrackResponse(BaseModel):
    id: UUID
    incident_id: UUID | None
    detected_at: datetime
    sensor_source: Literal["EO", "RF", "AUDIO", "FUSED"]
    confidence: float
    classification: Literal["BENIGN", "UNKNOWN", "LIKELY_THREAT"]
    bounding_box: dict[str, Any] | None
    metadata: dict[str, Any] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PendingActionResponse(BaseModel):
    id: UUID
    action_type: Literal["ALERT", "PROCEDURE", "REPORT"]
    agent_id: str
    draft_content: dict[str, Any]
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    operator_notes: str | None
    approved_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class IncidentResponse(BaseModel):
    id: UUID
    status: Literal["OPEN", "PENDING_APPROVAL", "APPROVED", "REJECTED", "CLOSED"]
    threat_level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"]
    langgraph_thread_id: str | None
    created_at: datetime
    closed_at: datetime | None
    pending_actions: list[PendingActionResponse] = []

    model_config = {"from_attributes": True}


class RejectRequest(BaseModel):
    notes: str


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: Literal["ok", "error"]
    redis: Literal["ok", "error"]
