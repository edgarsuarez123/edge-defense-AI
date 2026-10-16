"""
IncidentState + TrackRecord — the shared data model for the LangGraph agent graph.

Why this exists:
  LangGraph passes state between nodes as a typed dict. Every node reads from and
  writes partial updates back to this state. Having a single TypedDict definition
  means the compiler can validate node inputs/outputs and the checkpoint serializer
  knows exactly what to persist to PostgreSQL.

Alternative:
  Plain dict — no type checking, no editor autocomplete, easy to silently drop a field.
  Pydantic model — richer validation but LangGraph's checkpoint serializer works
  natively with TypedDicts; Pydantic models require a custom serializer.
"""

import operator
from typing import Annotated, Literal, TypedDict


class TrackRecord(TypedDict):
    """
    A single sensor detection from the ROS 2 /detections topic.
    One incident may carry multiple tracks from the same threat.
    """
    id: str                         # UUID, FK -> tracks table
    detected_at: str                # ISO-8601 timestamp string (serializes cleanly to JSON checkpoint)
    sensor_source: str              # e.g. "camera_0", "radar_1"
    confidence: float               # 0.0–1.0 from YOLO output
    classification: str             # "BENIGN" | "UNKNOWN" | "LIKELY_THREAT"
    bounding_box: dict | None       # {"x": int, "y": int, "w": int, "h": int} or None


class IncidentState(TypedDict):
    """
    Full state for one LangGraph graph run — one run per incident, start to close.

    Subtlety — completed_agents reducer:
      Annotated[list[str], operator.add] tells LangGraph to merge parallel state
      updates by concatenating lists rather than overwriting. Without this, the
      threat_analyst and ops_agent both return {"completed_agents": ["analyst"]} and
      {"completed_agents": ["ops"]}, and whichever arrives second wins — the other
      is silently dropped. operator.add ensures both names accumulate.
    """
    # Core identity
    incident_id: str                # UUID, FK -> incidents table
    thread_id: str                  # LangGraph checkpoint thread ID (same value, different purpose)

    # Input from the decision model — set in initialize_incident
    tracks: list[TrackRecord]
    threat_level: Literal["UNKNOWN", "LOW", "MEDIUM", "HIGH"]
    classification: Literal["BENIGN", "UNKNOWN", "LIKELY_THREAT"]

    # Agent outputs — populated as agents complete, None until then
    threat_analysis: str | None
    threat_citations: list[str] | None      # document source names, stored in checkpoint for audit
    recommended_procedures: str | None
    procedure_citations: list[str] | None   # same audit rationale as threat_citations

    # Reporting — set by run_reporting_agent
    draft_alert: dict | None                # Pydantic-validated JSON; None if reporting agent failed
    pending_action_id: str | None           # FK -> pending_actions table

    # Fan-in control — append-only list tracks which parallel agents have finished
    # Reducer: operator.add concatenates partial lists rather than overwriting
    completed_agents: Annotated[list[str], operator.add]

    # Approval control — drives the human-in-the-loop interrupt
    awaiting_approval: bool
    approved: bool | None           # None = pending, True = approved, False = rejected
    operator_notes: str | None      # operator's free-text on approval/rejection
