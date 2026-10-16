"""
LangGraph graph definition — the full agent mesh for one incident.

Graph topology (from architecture-design.md §4.1):
  START → initialize_incident
    → [run_threat_analyst, run_ops_agent]  (parallel via Send API)
    → fan_in_gate (waits until both agents complete)
    → validate_output_pass1 (injection check on analyst + ops prose)
    → run_reporting_agent  (or skip to approval if injection detected)
    → validate_output_pass2 (injection check on draft_alert JSON)
    → request_human_approval  (interrupt: graph pauses for operator)
    → finalize → END

Key concepts for this file:
  - StateGraph(IncidentState): typed graph; LangGraph validates state updates
  - Send API: dispatches to multiple nodes in parallel; convergence is automatic
    when both nodes add edges to the same downstream node (fan_in_gate)
  - interrupt(): raises a special LangGraph exception that serializes the current
    checkpoint and pauses graph execution. Resume via graph.invoke(None, config)
    with a Command(resume=decision) object. Requires a checkpointer.
  - MemorySaver: in-memory checkpoint store for dev/testing. Replace with
    AsyncPostgresSaver (langgraph-checkpoint-postgres) in production.
"""

import json
import logging
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from services.agents.state import IncidentState
from services.agents.validation import validate_agent_output

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Node: initialize_incident
# ---------------------------------------------------------------------------

def initialize_incident(state: IncidentState) -> dict:
    """
    Entry point. Sets initial state fields that are not provided by the caller.

    What it does:
      In production: CREATE the incidents row in PostgreSQL and set the LangGraph
      thread_id = incident_id (they match so resumption queries are straightforward).
      Emit a "created" event to Redis Streams.

    What it returns:
      Partial state update with defaults for all mutable fields.
      The caller (Redis consumer) is responsible for providing: incident_id,
      thread_id, tracks, threat_level, classification.
    """
    # STUB — replace with real PostgreSQL INSERT in Week 6
    # Real implementation will:
    #   INSERT INTO incidents (id, status, threat_level, classification, created_at)
    #   VALUES ($1, 'OPEN', $2, $3, NOW())
    #   followed by XADD incident_events {"incident_id": ..., "event": "created"}
    logger.info("initialize_incident: incident_id=%s", state.get("incident_id"))
    return {
        "threat_analysis": None,
        "threat_citations": None,
        "recommended_procedures": None,
        "procedure_citations": None,
        "draft_alert": None,
        "pending_action_id": None,
        "completed_agents": [],     # reducer will append; start clean
        "awaiting_approval": False,
        "approved": None,
        "operator_notes": None,
    }


# ---------------------------------------------------------------------------
# Node: run_threat_analyst
# ---------------------------------------------------------------------------

def run_threat_analyst(state: IncidentState) -> dict:
    """
    Runs the threat analyst LLM agent.

    What it does:
      1. Call search_knowledge_base for threat-relevant docs
      2. Build user message with [DOCUMENTS] block + incident context
      3. Call vLLM via ChatOpenAI with THREAT_ANALYST_PROMPT system prompt
      4. Parse response into threat_analysis prose + threat_citations list
      5. Append "threat_analyst" to completed_agents (reducer accumulates)

    On failure (timeout / malformed output):
      Retry once with truncated context (drop oldest chunks).
      If retry fails: threat_analysis = "[Analysis unavailable — LLM timeout]",
      threat_citations = [], completed_agents still appends "threat_analyst"
      so fan_in_gate is not blocked.
    """
    # STUB — replace with real LLM call in Week 6
    # Real implementation: build_llm_agent(THREAT_ANALYST_PROMPT, THREAT_ANALYST_TOOLS)
    logger.info("run_threat_analyst: STUB — returning placeholder analysis")
    return {
        "threat_analysis": "[STUB] Threat analysis not yet implemented — vLLM not connected.",
        "threat_citations": [],
        "completed_agents": ["threat_analyst"],  # operator.add reducer appends this
    }


# ---------------------------------------------------------------------------
# Node: run_ops_agent
# ---------------------------------------------------------------------------

def run_ops_agent(state: IncidentState) -> dict:
    """
    Runs the operations agent LLM agent.

    Runs in parallel with run_threat_analyst — no data dependency between them.
    Primary inputs: tracks, threat_level, classification (from the incident record),
    NOT the threat analyst's prose output.

    What it does:
      1. Call search_knowledge_base for procedure-relevant docs
      2. Call get_procedures(threat_level, incident_type)
      3. Build user message with [DOCUMENTS] block + incident context
      4. Call vLLM with OPS_AGENT_PROMPT system prompt
      5. Parse response into recommended_procedures prose + procedure_citations

    On failure: same retry-once policy as threat_analyst.
    """
    # STUB — replace with real LLM call in Week 6
    logger.info("run_ops_agent: STUB — returning placeholder procedures")
    return {
        "recommended_procedures": "[STUB] Procedure recommendations not yet implemented — vLLM not connected.",
        "procedure_citations": [],
        "completed_agents": ["ops_agent"],  # operator.add reducer appends this
    }


# ---------------------------------------------------------------------------
# Node: fan_in_gate
# ---------------------------------------------------------------------------

def fan_in_gate(state: IncidentState) -> dict:
    """
    Convergence point after parallel agents complete. No state changes here.

    In LangGraph, when both run_threat_analyst and run_ops_agent have edges to
    fan_in_gate, LangGraph holds this node until BOTH upstream branches complete.
    The completed_agents list (via operator.add reducer) will have 2 entries.

    This node exists explicitly (rather than routing directly to validate_pass_1)
    so the convergence point is named and visible in graph.get_graph().nodes.
    It's a no-op — routing to the next step happens via the conditional edge below.
    """
    logger.info(
        "fan_in_gate: both agents complete — completed_agents=%s",
        state.get("completed_agents"),
    )
    return {}   # no state update; routing handled by the conditional edge


def _route_after_fan_in(state: IncidentState) -> str:
    """
    Conditional edge: proceeds to validation when both agents have completed.
    Safety check — the topology should guarantee this, but we log if not.
    """
    completed = state.get("completed_agents", [])
    if len(completed) >= 2:
        return "validate_output_pass1"
    # Should not happen — LangGraph topology guarantees both branches complete
    # before fan_in_gate runs. Log and proceed defensively.
    logger.error(
        "fan_in_gate reached with only %d completed agents: %s — proceeding anyway",
        len(completed), completed,
    )
    return "validate_output_pass1"


# ---------------------------------------------------------------------------
# Node: validate_output_pass1
# ---------------------------------------------------------------------------

def validate_output_pass1(state: IncidentState) -> dict:
    """
    Injection check on threat_analyst and ops_agent prose outputs (pass 1 of 2).

    Checks both outputs concatenated — one check for the combined prose block.
    If injection detected: sets a flag that routes the graph to request_human_approval,
    skipping run_reporting_agent. The operator sees the incident with a warning;
    they can manually write a report.

    Why skip reporting on injection:
      If analyst or ops output contains injection artifacts, feeding them to the
      reporting agent could propagate the attack into the draft_alert JSON. It's
      safer to surface the flagged outputs to the operator directly.
    """
    combined = " ".join([
        state.get("threat_analysis") or "",
        state.get("recommended_procedures") or "",
    ])
    is_clean = validate_agent_output(combined)

    if not is_clean:
        logger.warning(
            "validate_output_pass1: injection artifact detected in agent outputs "
            "for incident_id=%s — skipping reporting agent",
            state.get("incident_id"),
        )
        # Flag injection by setting draft_alert to a sentinel value.
        # The conditional edge below routes to approval on this sentinel.
        return {
            "draft_alert": {
                "error": "Injection artifact detected in agent outputs — manual report required",
                "_injection_flagged": True,
            }
        }

    return {}   # clean: no state change, conditional edge routes to reporting agent


def _route_after_pass1(state: IncidentState) -> str:
    """Skip reporting agent if injection was flagged in pass 1."""
    alert = state.get("draft_alert") or {}
    if alert.get("_injection_flagged"):
        return "request_human_approval"
    return "run_reporting_agent"


# ---------------------------------------------------------------------------
# Node: run_reporting_agent
# ---------------------------------------------------------------------------

def run_reporting_agent(state: IncidentState) -> dict:
    """
    Runs the reporting agent to produce a structured draft_alert dict.

    What it does:
      1. Build user message with threat_analysis, recommended_procedures, all citations
      2. Call vLLM with REPORTING_AGENT_PROMPT system prompt
      3. Parse JSON response into draft_alert dict (Pydantic validation)
      4. On malformed JSON: retry once with explicit format reminder
      5. On retry failure: draft_alert = {"error": "Draft unavailable — manual report required"}

    The reporting agent also calls request_approval tool, which writes to pending_actions
    and fires an SSE event. The graph interrupt at request_human_approval handles
    the actual pause — request_approval just prepares the DB state.
    """
    # STUB — replace with real LLM call in Week 6
    logger.info("run_reporting_agent: STUB — returning placeholder draft alert")
    return {
        "draft_alert": {
            "incident_id": state.get("incident_id"),
            "threat_level": state.get("threat_level"),
            "classification": state.get("classification"),
            "summary": "[STUB] Draft alert not yet implemented — vLLM not connected.",
            "threat_analysis_excerpt": "[STUB]",
            "recommended_procedures_excerpt": "[STUB]",
            "citations": [],
            "confidence": "LOW",
            "requires_immediate_action": False,
        }
    }


# ---------------------------------------------------------------------------
# Node: validate_output_pass2
# ---------------------------------------------------------------------------

def validate_output_pass2(state: IncidentState) -> dict:
    """
    Injection check on the draft_alert JSON (pass 2 of 2).

    Serializes draft_alert to string before checking — the JSON key values
    could contain injected text even if the keys themselves are clean.

    On detection: flags the alert but does NOT skip approval. The operator
    must see the incident. They will see the warning and decide whether
    to write a manual report or accept the flagged draft.
    """
    alert = state.get("draft_alert") or {}
    alert_str = json.dumps(alert)

    if not validate_agent_output(alert_str):
        logger.warning(
            "validate_output_pass2: injection artifact detected in draft_alert "
            "for incident_id=%s",
            state.get("incident_id"),
        )
        return {
            "draft_alert": {
                **alert,
                "_injection_flagged": True,
                "warning": "Injection artifact detected in draft alert — operator review required",
            }
        }

    return {}   # clean: no state change


# ---------------------------------------------------------------------------
# Node: request_human_approval
# ---------------------------------------------------------------------------

def request_human_approval(state: IncidentState) -> dict:
    """
    Pauses the graph for operator approval using LangGraph's interrupt mechanism.

    How interrupt() works:
      interrupt(value) raises a special GraphInterrupt exception that LangGraph
      catches internally. The current state is serialized to the checkpoint store
      (PostgreSQL in production). The graph pauses and returns control to the caller.

      To resume: call graph.invoke(Command(resume=decision), config=thread_config)
      where decision = {"approved": True/False, "notes": "..."}

    The interrupt value is sent as the SSE payload to the operator UI, which
    displays the draft_alert and approval buttons.

    Why this and not a polling loop:
      The graph is persisted. The service can restart, and startup recovery
      (architecture §4.6) resumes all PENDING_APPROVAL incidents from checkpoint.
      No polling, no lost incidents.
    """
    logger.info(
        "request_human_approval: interrupting graph for operator review — incident_id=%s",
        state.get("incident_id"),
    )

    # interrupt() pauses execution here. When the operator submits a decision,
    # graph.invoke(Command(resume=response), config) resumes from this exact point.
    # `response` is whatever the API layer passes as the resume value.
    response: dict[str, Any] = interrupt({
        "incident_id": state.get("incident_id"),
        "draft_alert": state.get("draft_alert"),
        "threat_level": state.get("threat_level"),
        "classification": state.get("classification"),
        "message": "Operator approval required for draft alert",
    })

    # Execution resumes here after the operator submits their decision.
    approved = response.get("approved", False)
    notes = response.get("notes")

    logger.info(
        "request_human_approval: operator decision received — approved=%s, incident_id=%s",
        approved, state.get("incident_id"),
    )

    return {
        "approved": approved,
        "operator_notes": notes,
        "awaiting_approval": False,
    }


# ---------------------------------------------------------------------------
# Node: finalize
# ---------------------------------------------------------------------------

def finalize(state: IncidentState) -> dict:
    """
    Closes the incident and writes the audit trail.

    What it does (real implementation — Week 6):
      1. UPDATE incidents SET status = 'CLOSED', closed_at = NOW(),
         approved = $1, operator_notes = $2 WHERE id = $3
      2. INSERT INTO audit_log (incident_id, event, payload, created_at) VALUES (...)
      3. SET synced_at = NULL so the sync worker picks up the final state
      4. XADD incident_events {"incident_id": ..., "event": "incident_closed"}
         → FastAPI SSE layer picks this up and sends incident_update to the operator UI

    Why set synced_at = NULL (not a timestamp):
      The sync worker polls for rows where synced_at IS NULL. Setting it to NULL
      marks this incident as needing sync to cloud HQ. The worker sets it to NOW()
      after successful sync. This is the edge-wins pattern — local state is truth,
      cloud is a lagged copy.
    """
    # STUB — replace with real PostgreSQL + Redis writes in Week 6
    status = "APPROVED" if state.get("approved") else "REJECTED"
    logger.info(
        "finalize: closing incident_id=%s with status=%s",
        state.get("incident_id"), status,
    )
    return {}   # terminal node — no further state changes needed


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _route_to_parallel_agents(state: IncidentState) -> list[Send]:
    """
    Fan-out: dispatch both analyst and ops agent in parallel via LangGraph Send API.

    Send(node_name, state) creates a parallel branch with the current state.
    Both branches run concurrently. Their outputs are merged via state reducers
    (operator.add for completed_agents; last-write-wins for other fields).

    The graph topology (both nodes → fan_in_gate) ensures LangGraph waits for
    both to complete before running fan_in_gate.
    """
    return [
        Send("run_threat_analyst", state),
        Send("run_ops_agent", state),
    ]


def build_graph():
    """
    Assemble and compile the full agent graph.

    Returns a compiled LangGraph graph ready for graph.invoke() calls.

    Checkpointer:
      MemorySaver is used here (dev/test). In production, replace with:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        checkpointer = AsyncPostgresSaver.from_conn_string(DATABASE_URL)
      The async Postgres checkpointer is what enables startup recovery (§4.6) —
      checkpoints survive service restarts.

    Interrupt:
      The graph is compiled with interrupt_before=["request_human_approval"]
      is NOT used here — instead, interrupt() is called inside the node itself,
      which is the LangGraph >= 0.2 pattern. This gives the node control over
      what value to pass to the operator.
    """
    builder = StateGraph(IncidentState)

    # Register all nodes
    builder.add_node("initialize_incident", initialize_incident)
    builder.add_node("run_threat_analyst", run_threat_analyst)
    builder.add_node("run_ops_agent", run_ops_agent)
    builder.add_node("fan_in_gate", fan_in_gate)
    builder.add_node("validate_output_pass1", validate_output_pass1)
    builder.add_node("run_reporting_agent", run_reporting_agent)
    builder.add_node("validate_output_pass2", validate_output_pass2)
    builder.add_node("request_human_approval", request_human_approval)
    builder.add_node("finalize", finalize)

    # Entry point
    builder.add_edge(START, "initialize_incident")

    # Fan-out: parallel dispatch to analyst + ops
    builder.add_conditional_edges(
        "initialize_incident",
        _route_to_parallel_agents,
        ["run_threat_analyst", "run_ops_agent"],
    )

    # Fan-in: both parallel branches converge at fan_in_gate
    # LangGraph holds fan_in_gate until BOTH upstream nodes complete
    builder.add_edge("run_threat_analyst", "fan_in_gate")
    builder.add_edge("run_ops_agent", "fan_in_gate")

    # Fan-in gate → validation pass 1 (unconditional — routing is in _route_after_fan_in
    # but since fan_in_gate is a no-op, we route directly)
    builder.add_conditional_edges("fan_in_gate", _route_after_fan_in, ["validate_output_pass1"])

    # Validation pass 1 → reporting agent OR approval (if injection detected)
    builder.add_conditional_edges(
        "validate_output_pass1",
        _route_after_pass1,
        ["run_reporting_agent", "request_human_approval"],
    )

    # Reporting agent → validation pass 2
    builder.add_edge("run_reporting_agent", "validate_output_pass2")

    # Validation pass 2 → approval (always — validation may flag but doesn't skip approval)
    builder.add_edge("validate_output_pass2", "request_human_approval")

    # Approval → finalize (regardless of approved/rejected — operator made a decision)
    builder.add_edge("request_human_approval", "finalize")

    # Terminal
    builder.add_edge("finalize", END)

    # STUB checkpointer — replace with AsyncPostgresSaver in Week 6
    # Real implementation: langgraph-checkpoint-postgres, same PG instance as the rest of the stack
    checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)
