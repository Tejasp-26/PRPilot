"""
graph.py — LangGraph StateGraph definition for PRPilot.

Topology:
  Ingestion → Triage → [Security, Performance, Style, Coverage] (parallel fan-out)
           → Synthesis → [Escalation → Security (loop, max 2x)] | [CommentMapper → GithubPoster → Cleanup]
  A PR with no source files skips the agents and goes Triage → Synthesis directly.

Key design decisions:
  - Fan-out via Send() API (true parallel branches, not sequential)
  - Fan-in at synthesis_node (LangGraph waits for all parallel branches)
  - Conditional edge at synthesis for escalation loop (capped at MAX_ESCALATION_COUNT)
  - All nodes return partial state dicts (LangGraph merges via built-in reducer)
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from prpilot.config import MAX_ESCALATION_COUNT
from prpilot.nodes.cleanup import cleanup_node
from prpilot.nodes.comment_mapper import comment_mapper_node
from prpilot.nodes.coverage_agent import coverage_agent_node
from prpilot.nodes.escalation import escalation_node
from prpilot.nodes.github_poster import github_poster_node
from prpilot.nodes.ingestion import ingestion_node
from prpilot.nodes.performance_agent import performance_agent_node
from prpilot.nodes.security_agent import security_agent_node
from prpilot.nodes.style_agent import style_agent_node
from prpilot.nodes.synthesis import synthesis_node
from prpilot.nodes.triage import triage_node
from prpilot.state import ReviewState

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Node name constants (avoid magic strings)
# ──────────────────────────────────────────────

NODE_INGESTION = "ingestion"
NODE_TRIAGE = "triage"
NODE_SECURITY = "security_agent"
NODE_PERFORMANCE = "performance_agent"
NODE_STYLE = "style_agent"
NODE_COVERAGE = "coverage_agent"
NODE_SYNTHESIS = "synthesis"
NODE_ESCALATION = "escalation"
NODE_COMMENT_MAPPER = "comment_mapper"
NODE_GITHUB_POSTER = "github_poster"
NODE_CLEANUP = "cleanup"


# ──────────────────────────────────────────────
# Fan-out router: Triage → parallel agents
# ──────────────────────────────────────────────

def route_to_agents(state: ReviewState) -> list[Send] | str:
    """
    After triage, fan out to all relevant agents IN PARALLEL using Send().

    LangGraph's Send() API dispatches each node with the current state.
    All parallel branches run concurrently; LangGraph waits for all to
    complete before invoking the synthesis node (fan-in).

    If triage skips an agent, that agent simply never receives a Send() call.
    If triage skips ALL agents (docs/config-only PR), route straight to
    synthesis — returning an empty list would end the graph with no review.
    """
    relevant = state.get("relevant_agents", ["security", "performance", "style", "coverage"])
    if not relevant:
        logger.info("[graph] No agents relevant; routing directly to synthesis")
        return NODE_SYNTHESIS

    sends = []

    agent_map = {
        "security": NODE_SECURITY,
        "performance": NODE_PERFORMANCE,
        "style": NODE_STYLE,
        "coverage": NODE_COVERAGE,
    }

    for agent_name, node_name in agent_map.items():
        if agent_name in relevant:
            logger.info("[graph] Dispatching parallel agent: %s", node_name)
            sends.append(Send(node_name, state))
        else:
            logger.info("[graph] Skipping agent (not in relevant_agents): %s", node_name)

    return sends


# ──────────────────────────────────────────────
# Conditional edge: Synthesis → Escalation | CommentMapper
# ──────────────────────────────────────────────

def route_after_synthesis(state: ReviewState) -> str:
    """
    Decide whether to escalate (re-run Security Agent with stricter Semgrep)
    or proceed to comment mapping.

    Escalation is triggered when:
      - needs_escalation is True (set by synthesis_node)
      - escalation_count < MAX_ESCALATION_COUNT (hard cap prevents infinite loop)

    When the cap is hit, we log a warning and proceed to comment mapping
    regardless, so the pipeline always terminates.
    """
    needs_escalation = state.get("needs_escalation", False)
    escalation_count = state.get("escalation_count", 0)

    if needs_escalation and escalation_count < MAX_ESCALATION_COUNT:
        logger.info(
            "[graph] Routing to ESCALATION (count=%d/%d)",
            escalation_count,
            MAX_ESCALATION_COUNT,
        )
        return NODE_ESCALATION
    else:
        if needs_escalation and escalation_count >= MAX_ESCALATION_COUNT:
            logger.warning(
                "[graph] Escalation cap reached (%d/%d). "
                "Could not resolve low-confidence critical finding. "
                "Proceeding to comment mapper with best available findings.",
                escalation_count,
                MAX_ESCALATION_COUNT,
            )
        logger.info("[graph] Routing to COMMENT_MAPPER (no escalation needed)")
        return NODE_COMMENT_MAPPER


# ──────────────────────────────────────────────
# Graph construction
# ──────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Build and compile the PRPilot StateGraph.

    Returns the compiled graph, ready to be invoked with an initial state dict.

    Graph topology:
        START → ingestion → triage
                           → [security, performance, style, coverage]  (parallel, via Send)
                           → synthesis
                           → [escalation → security → synthesis]* (loop, max 2x)
                           → comment_mapper → github_poster → cleanup → END
    """
    graph = StateGraph(ReviewState)

    # ── Register all nodes ──────────────────────────────────
    graph.add_node(NODE_INGESTION, ingestion_node)
    graph.add_node(NODE_TRIAGE, triage_node)
    graph.add_node(NODE_SECURITY, security_agent_node)
    graph.add_node(NODE_PERFORMANCE, performance_agent_node)
    graph.add_node(NODE_STYLE, style_agent_node)
    graph.add_node(NODE_COVERAGE, coverage_agent_node)
    graph.add_node(NODE_SYNTHESIS, synthesis_node)
    graph.add_node(NODE_ESCALATION, escalation_node)
    graph.add_node(NODE_COMMENT_MAPPER, comment_mapper_node)
    graph.add_node(NODE_GITHUB_POSTER, github_poster_node)
    graph.add_node(NODE_CLEANUP, cleanup_node)

    # ── Linear edges ────────────────────────────────────────
    graph.add_edge(START, NODE_INGESTION)
    graph.add_edge(NODE_INGESTION, NODE_TRIAGE)

    # ── Fan-out: Triage → parallel agents (via Send) ────────
    # route_to_agents() returns a list of Send() objects, one per relevant agent.
    # LangGraph executes all of them concurrently.
    graph.add_conditional_edges(
        NODE_TRIAGE,
        route_to_agents,
        # All four agents are valid targets, plus synthesis for agent-less PRs
        [NODE_SECURITY, NODE_PERFORMANCE, NODE_STYLE, NODE_COVERAGE, NODE_SYNTHESIS],
    )

    # ── Fan-in: all parallel agents → synthesis ─────────────
    # LangGraph waits for all parallel branches to complete before
    # invoking the synthesis node (implicit fan-in).
    graph.add_edge(NODE_SECURITY, NODE_SYNTHESIS)
    graph.add_edge(NODE_PERFORMANCE, NODE_SYNTHESIS)
    graph.add_edge(NODE_STYLE, NODE_SYNTHESIS)
    graph.add_edge(NODE_COVERAGE, NODE_SYNTHESIS)

    # ── Conditional: synthesis → escalation | comment_mapper ─
    graph.add_conditional_edges(
        NODE_SYNTHESIS,
        route_after_synthesis,
        {
            NODE_ESCALATION: NODE_ESCALATION,
            NODE_COMMENT_MAPPER: NODE_COMMENT_MAPPER,
        },
    )

    # ── Escalation loop: escalation → security → synthesis ───
    # This creates a real cycle in the graph (allowed by LangGraph).
    # The MAX_ESCALATION_COUNT guard in route_after_synthesis breaks the loop.
    graph.add_edge(NODE_ESCALATION, NODE_SECURITY)

    # ── Final linear path ────────────────────────────────────
    graph.add_edge(NODE_COMMENT_MAPPER, NODE_GITHUB_POSTER)
    graph.add_edge(NODE_GITHUB_POSTER, NODE_CLEANUP)
    graph.add_edge(NODE_CLEANUP, END)

    compiled = graph.compile()
    logger.info("[graph] StateGraph compiled successfully")
    return compiled


# ── Module-level compiled graph (imported by prpilot.cli and LangGraph Studio) ──────
app = build_graph()
