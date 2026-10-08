"""
baseline.py — "Plain AI" reviewer: the whole PR diff in one prompt, findings out.

This is what pasting a PR into a chat model amounts to. It uses the same model
(get_llm) and the same line-numbered diff format PRPilot's agents get, so the
only difference being measured is PRPilot's design: specialist agents, real
tools and synthesis.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm, truncate_for_llm
from prpilot.state import Finding
from prpilot.tools.diff_parser import annotate_patch

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an expert code reviewer. Review this pull request and report real problems in the changed code: "
    "security vulnerabilities, performance problems, style issues, and new or changed logic that lacks tests. "
    "Report each problem once, on the line where it occurs. Do not report problems that are not there."
)


class BaselineFinding(BaseModel):
    file: str = Field(description="Repo-relative path of the file, exactly as shown in the diff header.")
    line: int | None = Field(default=None, description="Line number in the NEW file, from the diff's prefixes.")
    category: Literal["security", "performance", "style", "test_coverage"]
    severity: Literal["critical", "high", "medium", "low", "info"]
    message: str


class BaselineReview(BaseModel):
    findings: list[BaselineFinding] = Field(default_factory=list)


def format_pr(raw_files: list[dict]) -> str:
    parts = []
    for f in raw_files:
        if f.get("patch") and f.get("status") != "removed":
            parts.append(f"### {f['filename']}\n```diff\n{annotate_patch(f['patch'])}\n```")
    return (
        "Each diff line is prefixed with its line number in the NEW file "
        "('+' = added, ' ' = context, '-' = removed).\n\n" + "\n\n".join(parts)
    )


def baseline_review(raw_files: list[dict]) -> list[Finding]:
    """One structured LLM call over the whole PR. Raises if the call fails."""
    structured = get_llm().with_structured_output(BaselineReview)
    review: BaselineReview = structured.invoke([
        {"role": "system", "content": SYSTEM_PROMPT + UNTRUSTED_INPUT_NOTICE},
        {"role": "user", "content": truncate_for_llm(format_pr(raw_files))},
    ])
    return [
        Finding(
            file=f.file,
            line=f.line,
            category=f.category,
            severity=f.severity,
            confidence="medium",
            message=f.message,
            source_tool="llm-baseline",
            language="unknown",
            used_fallback=True,
        )
        for f in review.findings
    ]
