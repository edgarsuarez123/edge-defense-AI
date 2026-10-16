"""
Supervisor routing description — documents the graph topology in one place.

Not a callable system prompt. This module exists so that the routing logic is
co-located with the other prompts and visible to anyone reading the prompts/ dir.

The actual routing is implemented in graph.py via LangGraph conditional edges.
"""

ROUTING_DESCRIPTION = """
Graph topology (see graph.py for implementation):

  START
    |
  initialize_incident
    |
    +--- [Send] ---+
    |               |
  run_threat_analyst  run_ops_agent   (parallel, no data dependency between them)
    |               |
    +-------+-------+
            |
        fan_in_gate     (conditional: proceeds only when len(completed_agents) == 2)
            |
      validate_output   (pass 1: checks analyst + ops outputs for injection artifacts)
            |                 |
        [clean]       [injection detected]
            |                 |
   run_reporting_agent   request_human_approval   (skip reporting if compromised)
            |
      validate_output   (pass 2: checks draft_alert for injection artifacts)
            |
   request_human_approval   (interrupt: graph pauses, fires SSE to operator UI)
            |           |
       [approved]   [rejected]
            +----+----+
                 |
             finalize   (writes audit_log, closes incident, sets synced_at = NULL)
                 |
               END

Approval rule:
  The ONLY way to change an incident's status is through the operator approval
  interrupt. No agent can bypass this step — request_approval tool routes through
  the interrupt, not around it.
"""
