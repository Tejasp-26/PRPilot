"""
tests/test_ruff_runner.py — Unit tests for ruff_runner.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from prpilot.tools.ruff_runner import run_ruff


@patch("prpilot.tools.ruff_runner.shutil.which")
@patch("prpilot.tools.ruff_runner.subprocess.run")
def test_run_ruff_success(mock_run: MagicMock, mock_which: MagicMock) -> None:
    """Verify ruff runs successfully and parses output correctly."""
    mock_which.return_value = "/usr/bin/ruff"

    # Ruff output format mock
    mock_violations = [
        {
            "code": "E501",
            "message": "Line too long (90 > 88 characters)",
            "location": {"row": 10, "column": 5},
            "filename": "main.py",
            "fix": {"description": "Reformat line"},
        }
    ]
    mock_response = MagicMock()
    mock_response.stdout = json.dumps(mock_violations)
    mock_response.returncode = 1  # Ruff returns 1 if findings exist
    mock_run.return_value = mock_response

    violations = run_ruff("main.py")

    assert len(violations) == 1
    assert violations[0]["code"] == "E501"
    assert violations[0]["line"] == 10
    assert violations[0]["message"] == "[E501] Line too long (90 > 88 characters)"
    assert violations[0]["fix_suggestion"] == "Apply auto-fix: Reformat line"

    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert "check" in cmd
    # Ruff removed `--format`; the current flag is `--output-format`.
    assert "--output-format" in cmd
    assert "--format" not in cmd


@patch("prpilot.tools.ruff_runner.shutil.which")
def test_run_ruff_not_installed(mock_which: MagicMock) -> None:
    """Verify ruff returns None (= use LLM fallback) if not installed."""
    mock_which.return_value = None
    assert run_ruff("main.py") is None
