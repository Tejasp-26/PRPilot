"""
nodes/comment_mapper.py — Comment Mapper Node.

Validates (file, line) pairs against ParsedHunk data and produces GitHub
inline comment dicts. Findings that cannot be attached inline (no line, or a
line outside the diff) are listed in a section appended to the review body,
so nothing the agents found is silently lost.
"""

from __future__ import annotations

import logging

from prpilot.config import OUT_OF_DIFF_COMMENT_STRATEGY
from prpilot.state import Finding, ParsedHunk, ReviewState

logger = logging.getLogger(__name__)

MAX_SUMMARY_ONLY_FINDINGS = 50
"""Cap on findings listed in the review body's 'Other findings' section."""


def format_comment_body(finding: Finding) -> str:
    """Markdown body for one inline review comment."""
    body = f"**[{finding.category.upper()} / {finding.severity.upper()}]** "
    body += f"*(source: {finding.source_tool}"
    if finding.used_fallback:
        body += ", ⚠️ LLM-fallback — lower confidence"
    body += f", language: {finding.language})*\n\n"
    body += finding.message
    if finding.suggested_fix:
        body += f"\n\n**Suggested fix:** {finding.suggested_fix}"
    return body


def format_summary_only_section(findings: list[Finding]) -> str:
    """Markdown section listing findings that could not be posted inline."""
    if not findings:
        return ""
    lines = [
        "",
        "---",
        f"### Other findings ({len(findings)})",
        "_Not attached inline: whole-file findings, or lines outside this PR's diff._",
        "",
    ]
    for f in findings[:MAX_SUMMARY_ONLY_FINDINGS]:
        location = f"`{f.file}:{f.line}`" if f.line else f"`{f.file}`"
        fallback = " · LLM-fallback" if f.used_fallback else ""
        message = " ".join(f.message.split())  # one line per finding
        lines.append(f"- **{f.severity.upper()}** · {f.category} · {location} · _{f.source_tool}{fallback}_ — {message}")
    if len(findings) > MAX_SUMMARY_ONLY_FINDINGS:
        lines.append(f"- …and {len(findings) - MAX_SUMMARY_ONLY_FINDINGS} more (see --output-json).")
    return "\n".join(lines)


def comment_mapper_node(state: ReviewState) -> dict:
    """
    Map findings to GitHub inline comments, validating each line against
    ParsedHunk.commentable_lines (GitHub rejects comments outside the diff).
    """
    logger.info("[comment_mapper] Running (out-of-diff strategy: %s)", OUT_OF_DIFF_COMMENT_STRATEGY)

    all_findings: list[Finding] = state.get("all_findings", [])
    parsed_hunks: list[ParsedHunk] = state.get("parsed_hunks", [])

    # Build a lookup: filename → set of commentable lines
    commentable: dict[str, set[int]] = {hunk.file: hunk.commentable_lines for hunk in parsed_hunks}

    github_comments: list[dict] = []
    summary_only: list[Finding] = []

    for finding in all_findings:
        valid_lines = commentable.get(finding.file, set())
        if finding.line is None or finding.line not in valid_lines:
            # 'drop' strategy: exclude from inline comments, keep in the review body
            logger.debug(
                "[comment_mapper] Summary-only finding: %s:%s (valid lines: %s)",
                finding.file,
                finding.line,
                sorted(valid_lines)[:10],
            )
            summary_only.append(finding)
            continue

        github_comments.append({
            "path": finding.file,
            "line": finding.line,
            "side": finding.side,
            "body": format_comment_body(finding),
        })

    review_body = state.get("review_summary", "No summary generated.") + format_summary_only_section(summary_only)

    logger.info(
        "[comment_mapper] Produced %d inline comments, %d summary-only findings",
        len(github_comments),
        len(summary_only),
    )

    return {"github_comments": github_comments, "review_body": review_body}
