"""
tests/test_comment_mapper.py — Unit tests for comment_mapper.py.
"""

from __future__ import annotations

from prpilot.nodes.comment_mapper import comment_mapper_node
from prpilot.state import Finding, ParsedHunk


def test_comment_mapper_filter() -> None:
    """Verify that comment mapper passes inline comments for modified lines, and drops out-of-diff / whole-file findings."""
    hunks = [
        ParsedHunk(
            file="src/main.py",
            added_lines=[10, 11],
            removed_lines=[9],
            context_lines=[12],
        )
    ]

    findings = [
        # 1. Valid commentable line (added line 10)
        Finding(
            file="src/main.py",
            line=10,
            side="RIGHT",
            category="style",
            severity="low",
            confidence="high",
            message="Style issue",
            source_tool="ruff",
            language="python",
            used_fallback=False,
        ),
        # 2. Out-of-diff line (line 15 is not in added/context lines)
        Finding(
            file="src/main.py",
            line=15,
            side="RIGHT",
            category="security",
            severity="high",
            confidence="high",
            message="Out of diff vulnerability",
            source_tool="semgrep",
            language="python",
            used_fallback=False,
        ),
        # 3. Whole file finding (line is None)
        Finding(
            file="src/main.py",
            line=None,
            side="RIGHT",
            category="performance",
            severity="medium",
            confidence="medium",
            message="General performance issue",
            source_tool="llm-fallback",
            language="python",
            used_fallback=True,
        ),
    ]

    state = {
        "all_findings": findings,
        "parsed_hunks": hunks,
    }

    result = comment_mapper_node(state)
    comments = result["github_comments"]

    # Only finding #1 should be mapped to an inline comment
    assert len(comments) == 1
    assert comments[0]["path"] == "src/main.py"
    assert comments[0]["line"] == 10
    assert "Style issue" in comments[0]["body"]
    assert "ruff" in comments[0]["body"]


def test_comment_mapper_keeps_non_inline_findings_in_review_body() -> None:
    """Findings without a valid diff line are listed in the review body, not silently lost."""
    hunks = [ParsedHunk(file="a.py", added_lines=[3], removed_lines=[], context_lines=[])]
    findings = [
        Finding(file="a.py", line=3, category="style", severity="low", confidence="high",
                message="inline one", source_tool="ruff", language="python", used_fallback=False),
        Finding(file="a.py", line=40, category="security", severity="high", confidence="medium",
                message="outside\ndiff", source_tool="semgrep", language="python", used_fallback=False),
        Finding(file="b.py", line=None, category="test_coverage", severity="medium", confidence="low",
                message="whole file", source_tool="llm-fallback", language="python", used_fallback=True),
    ]
    result = comment_mapper_node({"all_findings": findings, "parsed_hunks": hunks, "review_summary": "SUMMARY"})

    assert len(result["github_comments"]) == 1
    body = result["review_body"]
    assert body.startswith("SUMMARY")
    assert "Other findings (2)" in body
    assert "`a.py:40`" in body and "outside diff" in body
    assert "`b.py`" in body and "LLM-fallback" in body
