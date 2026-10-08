"""
tests/test_synthesis.py — Unit tests for synthesis.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prpilot.nodes.synthesis import deduplicate_findings, synthesis_node
from prpilot.state import Finding


def test_deduplicate_findings() -> None:
    """Verify that duplicate findings on same file, line, and category are resolved in favor of highest quality."""
    f1 = Finding(
        file="src/main.py",
        line=10,
        side="RIGHT",
        category="security",
        severity="medium",
        confidence="medium",
        message="A medium finding",
        source_tool="semgrep",
        language="python",
        used_fallback=False,
    )
    f2 = Finding(
        file="src/main.py",
        line=10,
        side="RIGHT",
        category="security",
        severity="high",  # higher severity
        confidence="medium",
        message="A high finding",
        source_tool="semgrep",
        language="python",
        used_fallback=False,
    )
    f3 = Finding(
        file="src/main.py",
        line=10,
        side="RIGHT",
        category="security",
        severity="high",
        confidence="medium",
        message="LLM fallback high finding",
        source_tool="llm-fallback",
        language="python",
        used_fallback=True,  # fallback is lower priority
    )

    deduped = deduplicate_findings([f1, f3, f2])

    assert len(deduped) == 1
    # Should keep f2 because it has the highest severity and is not fallback
    assert deduped[0].severity == "high"
    assert deduped[0].used_fallback is False


@patch("prpilot.nodes.synthesis.can_escalate", return_value=True)
@patch("prpilot.nodes.synthesis.get_llm")
def test_synthesis_node_escalation_trigger(mock_get_llm: MagicMock, _can_escalate: MagicMock) -> None:
    """Verify that synthesis node triggers escalation when a critical+low-confidence finding is present."""
    # Mock LLM for summary generation
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    from prpilot.nodes.synthesis import PRReviewSummary
    mock_structured.invoke.return_value = PRReviewSummary(
        verdict="Critical issues detected",
        summary_paragraph="Review summary paragraph",
    )

    # State with a critical security finding with LOW confidence
    state = {
        "security_findings": [
            Finding(
                file="main.py",
                line=12,
                side="RIGHT",
                category="security",
                severity="critical",
                confidence="low",  # Triggers escalation!
                message="Critical leak",
                source_tool="semgrep",
                language="python",
                used_fallback=False,
            )
        ],
        "performance_findings": [],
        "style_findings": [],
        "coverage_findings": [],
        "escalation_count": 0,
        "language_coverage_summary": "python: 1 file",
    }

    result = synthesis_node(state)

    assert result["needs_escalation"] is True
    # Summary is only generated on the final (non-escalating) pass
    assert "review_summary" not in result
    mock_structured.invoke.assert_not_called()
    assert len(result["all_findings"]) == 1


@patch("prpilot.nodes.synthesis.get_llm")
def test_synthesis_node_no_escalation(mock_get_llm: MagicMock) -> None:
    """Verify that synthesis node does not trigger escalation for non-escalation criteria."""
    # Mock LLM
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    from prpilot.nodes.synthesis import PRReviewSummary
    mock_structured.invoke.return_value = PRReviewSummary(
        verdict="Looks good",
        summary_paragraph="Review summary paragraph",
    )

    # State with critical but HIGH confidence finding (no escalation needed)
    state = {
        "security_findings": [
            Finding(
                file="main.py",
                line=12,
                side="RIGHT",
                category="security",
                severity="critical",
                confidence="high",  # High confidence -> no escalation
                message="Critical leak",
                source_tool="semgrep",
                language="python",
                used_fallback=False,
            )
        ],
        "performance_findings": [],
        "style_findings": [],
        "coverage_findings": [],
        "escalation_count": 0,
        "language_coverage_summary": "python: 1 file",
    }

    result = synthesis_node(state)

    assert result["needs_escalation"] is False
