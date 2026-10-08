"""
nodes/cleanup.py — Cleanup Node.

Final node: deletes the temporary workspace checkout created by ingestion.
(The CLI also cleans up in a `finally` block, in case the graph fails midway.)
"""

from __future__ import annotations

import logging

from prpilot.state import ReviewState
from prpilot.tools.workspace import cleanup_workspace

logger = logging.getLogger(__name__)


def cleanup_node(state: ReviewState) -> dict:
    """Remove the workspace directory, if any."""
    workspace_dir = state.get("workspace_dir")
    if workspace_dir:
        cleanup_workspace(workspace_dir)
        logger.info("[cleanup] Removed workspace %s", workspace_dir)
    return {"workspace_dir": None}
