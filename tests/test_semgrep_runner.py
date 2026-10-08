"""
tests/test_semgrep_runner.py — Unit tests for semgrep_runner.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from prpilot.tools.semgrep_runner import run_semgrep


@patch("prpilot.tools.semgrep_runner.shutil.which")
@patch("prpilot.tools.semgrep_runner.subprocess.run")
def test_run_semgrep_auto(mock_run: MagicMock, mock_which: MagicMock) -> None:
    """Verify semgrep scan runs with config=auto and parses outputs."""
    mock_which.return_value = "/usr/bin/semgrep"

    mock_output = {
        "results": [
            {
                "check_id": "rules.security.sql-injection",
                "path": "main.py",
                "start": {"line": 12, "col": 5},
                "extra": {
                    "message": "SQL Injection vulnerability",
                    "severity": "ERROR",
                },
            }
        ]
    }
    mock_response = MagicMock()
    mock_response.stdout = json.dumps(mock_output)
    mock_response.returncode = 0
    mock_run.return_value = mock_response

    findings = run_semgrep(["main.py"], strict=False)

    assert len(findings) == 1
    assert findings[0]["code"] == "rules.security.sql-injection"
    assert findings[0]["line"] == 12
    assert findings[0]["severity"] == "high"  # ERROR -> high

    mock_run.assert_called_once()
    # Check config parameter
    cmd_args = mock_run.call_args[0][0]
    assert "--config=auto" in cmd_args


@patch("prpilot.tools.semgrep_runner.shutil.which")
@patch("prpilot.tools.semgrep_runner.subprocess.run")
def test_run_semgrep_strict(mock_run: MagicMock, mock_which: MagicMock) -> None:
    """Verify semgrep scan runs with config=p/security-audit in strict mode."""
    mock_which.return_value = "/usr/bin/semgrep"

    mock_response = MagicMock()
    mock_response.stdout = json.dumps({"results": []})
    mock_response.returncode = 0
    mock_run.return_value = mock_response

    run_semgrep(["main.py"], strict=True)

    cmd_args = mock_run.call_args[0][0]
    assert "--config=p/security-audit" in cmd_args
