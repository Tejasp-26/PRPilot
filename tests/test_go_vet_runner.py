"""
tests/test_go_vet_runner.py — Unit tests for go_vet_runner.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prpilot.tools.go_vet_runner import run_go_vet


@patch("prpilot.tools.go_vet_runner.shutil.which")
@patch("prpilot.tools.go_vet_runner.subprocess.run")
def test_run_go_vet_staticcheck(mock_run: MagicMock, mock_which: MagicMock) -> None:
    """Verify staticcheck runs if available and outputs are parsed."""
    # Mock staticcheck as available, go as not checked or not needed
    mock_which.side_effect = lambda cmd: "/usr/bin/staticcheck" if cmd == "staticcheck" else None

    mock_response = MagicMock()
    mock_response.stdout = "main.go:10:2: SA5011: possible nil pointer dereference"
    mock_response.stderr = ""
    mock_response.returncode = 1
    mock_run.return_value = mock_response

    violations = run_go_vet("main.go")

    assert len(violations) == 1
    assert violations[0]["code"] == "SA5011"
    assert violations[0]["tool"] == "staticcheck"
    assert violations[0]["line"] == 10
    assert "SA5011" in violations[0]["message"]
    mock_run.assert_called_once()
    assert any("staticcheck" in arg for arg in mock_run.call_args[0][0])


@patch("prpilot.tools.go_vet_runner.shutil.which")
@patch("prpilot.tools.go_vet_runner.subprocess.run")
def test_run_go_vet_fallback(mock_run: MagicMock, mock_which: MagicMock) -> None:
    """Verify go vet runs if staticcheck is unavailable and outputs are parsed."""
    # staticcheck not available, go available
    mock_which.side_effect = lambda cmd: "/usr/bin/go" if cmd == "go" else None

    mock_response = MagicMock()
    mock_response.stdout = ""
    mock_response.stderr = "main.go:15: govet: printf argument mismatch"
    mock_response.returncode = 1
    mock_run.return_value = mock_response

    violations = run_go_vet("main.go")

    assert len(violations) == 1
    assert violations[0]["code"] == "govet"
    assert violations[0]["line"] == 15
    assert "printf argument mismatch" in violations[0]["message"]
    mock_run.assert_called_once()
    assert "vet" in mock_run.call_args[0][0]


@patch("prpilot.tools.go_vet_runner.shutil.which")
def test_run_go_vet_none(mock_which: MagicMock) -> None:
    """Verify None (= use LLM fallback) is returned if no Go toolchain is installed."""
    mock_which.return_value = None
    assert run_go_vet("main.go") is None
