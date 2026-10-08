"""
nodes/escalation.py — Escalation Node.

Increments the escalation counter and flags the Security Agent to re-run with
the stricter Semgrep ruleset (auto + p/security-audit). The graph routes
escalation → security_agent → synthesis; route_after_synthesis enforces the cap.
"""

from __future__ import annotations

import logging

from prpilot.config import MAX_ESCALATION_COUNT
from prpilot.state import ReviewState

logger = logging.getLogger(__name__)


def escalation_node(state: ReviewState) -> dict:
    """Increment escalation_count and enable strict Semgrep mode for the re-run."""
    new_count = state.get("escalation_count", 0) + 1

    logger.info(
        "[escalation] Escalation %d/%d: re-running Security Agent with strict Semgrep ruleset.",
        new_count,
        MAX_ESCALATION_COUNT,
    )

    return {
        "escalation_count": new_count,
        "needs_escalation": False,  # reset; synthesis will re-evaluate after re-run
        "use_strict_semgrep": True,
        # Clear previous security findings so the security agent re-populates them
        "security_findings": [],
    }
