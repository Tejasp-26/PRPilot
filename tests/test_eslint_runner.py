"""
tests/test_eslint_runner.py — Unit tests for eslint_runner.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from prpilot.tools.eslint_runner import has_eslint_config, run_eslint


@patch("prpilot.tools.eslint_runner.os.path.exists")
def test_has_eslint_config(mock_exists: MagicMock) -> None:
    """Verify that eslint configuration detection works."""
    # Simulates finding .eslintrc.json
    mock_exists.side_effect = lambda path: ".eslintrc.json" in path
    assert has_eslint_config(".") is True

    # Simulates finding package.json with no config
    mock_exists.side_effect = lambda path: "package.json" in path
    assert has_eslint_config(".") is False


@patch("prpilot.tools.eslint_runner.has_eslint_config")
@patch("prpilot.tools.eslint_runner.shutil.which")
@patch("prpilot.tools.eslint_runner.subprocess.run")
def test_run_eslint_success(mock_run: MagicMock, mock_which: MagicMock, mock_has_config: MagicMock) -> None:
    """Verify eslint runs successfully via npx and parses output correctly."""
    mock_has_config.return_value = True
    mock_which.return_value = "/usr/bin/npx"

    mock_eslint_output = [
        {
            "filePath": "app.js",
            "messages": [
                {
                    "ruleId": "no-unused-vars",
                    "severity": 2,
                    "message": "'x' is assigned a value but never used.",
                    "line": 4,
                    "column": 5,
                    "fix": {"range": [10, 15], "text": ""},
                }
            ],
        }
    ]

    mock_response = MagicMock()
    mock_response.stdout = json.dumps(mock_eslint_output)
    mock_response.returncode = 1
    mock_run.return_value = mock_response

    violations = run_eslint("app.js")

    assert len(violations) == 1
    assert violations[0]["code"] == "no-unused-vars"
    assert violations[0]["line"] == 4
    assert "no-unused-vars" in violations[0]["message"]
    assert violations[0]["fix_suggestion"] is not None

    mock_run.assert_called_once()


@patch("prpilot.tools.eslint_runner.has_eslint_config")
def test_run_eslint_no_config(mock_has_config: MagicMock) -> None:
    """Verify eslint returns None (= use LLM fallback) if config is not present."""
    mock_has_config.return_value = False
    assert run_eslint("app.js") is None
