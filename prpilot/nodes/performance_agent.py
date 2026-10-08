"""
nodes/performance_agent.py — Performance Agent Node.

Uses tree-sitter AST parser to find loops, nested loops, and function calls in loops.
If performance heuristics are triggered, uses the LLM to reason over the code and
generate actionable performance findings. Falls back to raw diff analysis if AST is unavailable.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from prpilot.language_profiles import get_language_profile
from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm, truncate_for_llm
from prpilot.state import Finding, ReviewState
from prpilot.tools.ast_utils import find_calls_in_loops, get_loop_depth, parse_file
from prpilot.tools.diff_parser import number_lines
from prpilot.tools.llm_fallback import performance_review
from prpilot.tools.workspace import get_file_content, reviewable_files

MAX_IO_CALLS_IN_PROMPT = 30
"""Cap on I/O-in-loop call sites listed in the prompt (keeps prompts small)."""

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Pydantic schema for Performance Agent reasoning
# ──────────────────────────────────────────────

class ASTPerformanceFinding(BaseModel):
    line: int | None = Field(
        default=None,
        description="Line number in the new file where the bottleneck occurs."
    )
    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="Severity based on the bottleneck's performance impact."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence level in this performance finding."
    )
    message: str = Field(
        description="A clear explanation of the bottleneck (e.g. N+1 query, nested loop, heavy allocation) and its cost."
    )
    suggested_fix: str | None = Field(
        default=None,
        description="A concrete code snippet or structural suggestion to optimize the bottleneck."
    )


class ASTPerformanceFindingsList(BaseModel):
    findings: list[ASTPerformanceFinding] = Field(default_factory=list)


def run_ast_performance_review(
    content: str,
    file_path: str,
    language: str,
    loop_depth: int,
    calls_in_loops: list[dict],
) -> list[Finding]:
    """Use LLM to verify and format performance issues found via AST heuristics."""
    llm = get_llm()
    structured_llm = llm.with_structured_output(ASTPerformanceFindingsList)

    system_prompt = (
        "You are an expert performance engineer.\n"
        "You are given file content and AST performance heuristics that were triggered:\n"
        f"- Max Nested Loop Depth: {loop_depth}\n"
        f"- I/O-looking calls inside loops: {calls_in_loops[:MAX_IO_CALLS_IN_PROMPT]}\n\n"
        "Analyze the code to determine if these signals represent real performance bottlenecks.\n"
        "Focus on N+1 database/API query problems, CPU-heavy nested loops, and memory leaks. "
        "Discard false positives. Each file line is prefixed with its line number; use those "
        "numbers when reporting. Output a list of findings."
    ) + UNTRUSTED_INPUT_NOTICE

    user_prompt = (
        f"File: {file_path}\n"
        f"Language: {language}\n\n"
        f"File Content:\n"
        f"```\n{truncate_for_llm(number_lines(content))}\n```\n"
    )

    try:
        response: ASTPerformanceFindingsList = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
    except Exception as e:
        logger.error("[performance_agent] LLM AST performance review failed: %s", e)
        return []

    findings = []
    for lf in response.findings:
        findings.append(
            Finding(
                file=file_path,
                line=lf.line,
                side="RIGHT",
                category="performance",
                severity=lf.severity,
                confidence=lf.confidence,
                message=lf.message,
                suggested_fix=lf.suggested_fix,
                source_tool="llm-reasoning",
                language=language,
                used_fallback=False,
            )
        )
    return findings


def performance_agent_node(state: ReviewState) -> dict:
    """
    Performance Agent node.

    With a tree-sitter grammar: run AST heuristics (nested loops, I/O calls
    inside loops) and ask the LLM to confirm/explain only when they trigger.
    Without one: LLM review of the diff.
    """
    files = reviewable_files(state.get("raw_files", []))
    performance_findings: list[Finding] = []

    logger.info("[performance_agent] Running performance agent on %d files", len(files))

    for f in files:
        filename = f["filename"]
        profile = get_language_profile(filename)

        # 1. Try tree-sitter AST path
        tree = None
        content = None
        if profile.tree_sitter_grammar:
            content = get_file_content(state, filename)
            if content:
                tree = parse_file(content, profile.name, filename)

        if tree and content:
            loop_depth = get_loop_depth(tree)
            io_calls = [c for c in find_calls_in_loops(tree, content, profile.io_call_keywords) if c["is_io"]]

            # Heuristics: nested loops OR an I/O-looking call inside a loop
            if loop_depth >= 2 or io_calls:
                logger.info(
                    "[performance_agent] Heuristics triggered for %s (depth=%d, io_calls=%d). Running LLM reasoning.",
                    filename,
                    loop_depth,
                    len(io_calls),
                )
                performance_findings.extend(
                    run_ast_performance_review(
                        content=content,
                        file_path=filename,
                        language=profile.name,
                        loop_depth=loop_depth,
                        calls_in_loops=io_calls,
                    )
                )
            else:
                logger.debug("[performance_agent] Heuristics clean for %s", filename)

        else:
            # 2. Fall back to LLM performance review on patch
            logger.info("[performance_agent] AST unavailable for %s. Running fallback LLM performance review.", filename)
            performance_findings.extend(performance_review(f["patch"], file_path=filename, language=profile.name))

    logger.info("[performance_agent] Finished performance review. Produced %d findings", len(performance_findings))
    return {"performance_findings": performance_findings}
