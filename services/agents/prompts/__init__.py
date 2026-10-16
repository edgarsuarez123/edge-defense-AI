"""
Re-exports all agent system prompts under canonical names.

Callers use:
  from services.agents.prompts import THREAT_ANALYST_PROMPT, OPS_AGENT_PROMPT, REPORTING_AGENT_PROMPT
"""

from services.agents.prompts.threat_analyst import SYSTEM_PROMPT as THREAT_ANALYST_PROMPT
from services.agents.prompts.ops_agent import SYSTEM_PROMPT as OPS_AGENT_PROMPT
from services.agents.prompts.reporting_agent import SYSTEM_PROMPT as REPORTING_AGENT_PROMPT
from services.agents.prompts.supervisor import ROUTING_DESCRIPTION as SUPERVISOR_ROUTING

__all__ = [
    "THREAT_ANALYST_PROMPT",
    "OPS_AGENT_PROMPT",
    "REPORTING_AGENT_PROMPT",
    "SUPERVISOR_ROUTING",
]
