"""
Edge Base-Defense Copilot — Agents service.

Entry point: build_graph() returns the compiled LangGraph graph.
All external dependencies (vLLM, PostgreSQL, Redis) are stubbed until Week 6.
"""

from services.agents.graph import build_graph
from services.agents.state import IncidentState, TrackRecord

__all__ = ["build_graph", "IncidentState", "TrackRecord"]
