"""
tests/test_llm_fallback.py — Unit tests for llm_fallback.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prpilot.tools.llm_fallback import (
    LLMFinding,
    LLMFindingList,
    coverage_review,
    performance_review,
    security_review,
    style_review,
)


@patch("prpilot.tools.llm_fallback.get_llm")
def test_security_review_fallback(mock_get_llm: MagicMock) -> None:
    """Verify security review fallback invokes LLM and maps findings."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    # Mock return value from LLM
    mock_structured.invoke.return_value = LLMFindingList(
        findings=[
            LLMFinding(
                line=12,
                severity="high",
                confidence="medium",
                message="SQL Injection detected",
                suggested_fix="Use parameterized queries",
            )
        ]
    )

    findings = security_review("some diff", "main.py", "python")

    assert len(findings) == 1
    f = findings[0]
    assert f.file == "main.py"
    assert f.line == 12
    assert f.category == "security"
    assert f.severity == "high"
    assert f.confidence == "medium"
    assert f.used_fallback is True
    assert f.source_tool == "llm-fallback"

    mock_llm.with_structured_output.assert_called_once()
    mock_structured.invoke.assert_called_once()


@patch("prpilot.tools.llm_fallback.get_llm")
def test_style_review_fallback(mock_get_llm: MagicMock) -> None:
    """Verify style review fallback invokes LLM and maps findings."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    mock_structured.invoke.return_value = LLMFindingList(
        findings=[
            LLMFinding(
                line=20,
                severity="low",
                confidence="medium",
                message="Line too long",
                suggested_fix="Break line",
            )
        ]
    )

    findings = style_review("some diff", "main.py", "python")
    assert len(findings) == 1
    assert findings[0].category == "style"
    assert findings[0].used_fallback is True


@patch("prpilot.tools.llm_fallback.get_llm")
def test_performance_review_fallback(mock_get_llm: MagicMock) -> None:
    """Verify performance review fallback invokes LLM and maps findings."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    mock_structured.invoke.return_value = LLMFindingList(
        findings=[
            LLMFinding(
                line=None,
                severity="medium",
                confidence="low",
                message="N+1 query issue",
                suggested_fix="Batch queries",
            )
        ]
    )

    findings = performance_review("some diff", "main.py", "python")
    assert len(findings) == 1
    assert findings[0].category == "performance"
    assert findings[0].line is None
    assert findings[0].used_fallback is True


@patch("prpilot.tools.llm_fallback.get_llm")
def test_coverage_review_fallback(mock_get_llm: MagicMock) -> None:
    """Verify coverage review fallback invokes LLM and maps findings."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_get_llm.return_value = mock_llm
    mock_llm.with_structured_output.return_value = mock_structured

    mock_structured.invoke.return_value = LLMFindingList(
        findings=[
            LLMFinding(
                line=5,
                severity="low",
                confidence="low",
                message="Missing tests for func",
                suggested_fix="Write a unit test",
            )
        ]
    )

    findings = coverage_review("some diff", "main.py", "python", ["func"])
    assert len(findings) == 1
    assert findings[0].category == "test_coverage"
    assert findings[0].used_fallback is True
