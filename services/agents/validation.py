"""
Output validation — Layer 3 of the prompt injection defense.

Three-layer defense (from architecture-design.md §4.4):
  Layer 1 — Structural isolation: retrieved text goes in [DOCUMENTS] block in the
             user message, never in the system prompt. System prompt context is clean.
  Layer 2 — Meta-instruction: system prompt explicitly names the attack and tells
             the model to treat injected commands as document artifacts.
  Layer 3 — This module: regex pass after each agent run, before the graph proceeds.

Why regex and not a classifier:
  A second LLM classifier would add 200-400ms per retrieval, another model to
  manage, and another VRAM cost on a constrained edge node. The patterns here
  target known jailbreak phrasing, not semantic intent — regex is fast, auditable,
  and has no false-negative failure mode (it never passes a clear pattern match).
  The tradeoff: novel phrasing can slip through. The T&E security test requirement
  is zero bypass from the listed patterns — this satisfies that requirement.
"""

import re

# Patterns from architecture-design.md §4.4.
# All matched case-insensitively (applied to output.lower()).
# If any pattern matches, the output is flagged as a potential injection artifact.
INJECTION_PATTERNS = [
    r"ignore.{0,30}(previous|above|prior).{0,30}instruction",
    r"approve.{0,20}(all|every|any).{0,20}action",
    r"bypass.{0,20}approval",
    r"you are now",
    r"new instructions?:",
]

_COMPILED_PATTERNS = [re.compile(p) for p in INJECTION_PATTERNS]


def validate_agent_output(output: str) -> bool:
    """
    Returns True if the output is clean, False if an injection artifact is detected.

    Called twice in the graph:
      Pass 1 — after threat_analyst and ops_agent complete (checks their prose outputs)
      Pass 2 — after reporting_agent completes (checks the draft_alert JSON string)

    On False: the graph routes to request_human_approval without running the
    reporting agent (pass 1) or with a flagged alert (pass 2). The operator always
    sees the incident — validation failure does not drop the incident silently.

    Args:
        output: The agent's full text output as a string. For dict outputs (draft_alert),
                call with json.dumps(draft_alert) before passing here.
    """
    lower = output.lower()
    return not any(pattern.search(lower) for pattern in _COMPILED_PATTERNS)
