"""
tests/test_agents.py — Unit tests for agent node helpers and fallback behaviour.
"""

from __future__ import annotations

from prpilot.language_profiles import get_language_profile
from prpilot.nodes.coverage_agent import find_test_files, is_referenced, is_test_file, touched_functions
from prpilot.nodes.style_agent import style_agent_node
from prpilot.nodes.triage import triage_node
from prpilot.state import Finding

PATCH = "@@ -1 +1,2 @@\n x = 1\n+y = 2\n"


# ── Coverage helpers ──────────────────────────────────────

def test_touched_functions_only_includes_changed_spans() -> None:
    defs = [
        {"name": "old", "start_line": 1, "end_line": 5},
        {"name": "edited", "start_line": 7, "end_line": 12},
    ]
    assert [d["name"] for d in touched_functions(defs, {9})] == ["edited"]
    assert touched_functions(defs, set()) == []


def test_is_referenced_uses_word_boundaries() -> None:
    assert is_referenced("get", ["assert get(1) == 2"]) is True
    assert is_referenced("get", ["target = 3"]) is False
    assert is_referenced("parse_url", ["from app import parse_url_v2"]) is False


def test_find_test_files_walks_workspace(tmp_path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("")
    (tmp_path / "app.py").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "test_vendor.py").write_text("")
    (tmp_path / "web" / "__tests__").mkdir(parents=True)
    (tmp_path / "web" / "__tests__" / "button.js").write_text("")

    assert find_test_files(str(tmp_path), get_language_profile("x.py")) == ["tests/test_app.py"]
    assert find_test_files(str(tmp_path), get_language_profile("x.js")) == ["web/__tests__/button.js"]
    assert is_test_file("pkg/server_test.go", get_language_profile("x.go"))


# ── Triage ────────────────────────────────────────────────

def test_triage_nested_lockfile_and_removed_files_are_not_code() -> None:
    result = triage_node({"raw_files": [
        {"filename": "web/yarn.lock", "patch": PATCH, "status": "modified"},
        {"filename": "old.py", "patch": PATCH, "status": "removed"},
        {"filename": "docs/guide.md", "patch": PATCH, "status": "modified"},
        {"filename": "config/.env.example", "patch": PATCH, "status": "modified"},
    ]})
    assert result["relevant_agents"] == []


def test_triage_code_file_enables_agents() -> None:
    result = triage_node({"raw_files": [{"filename": "app.py", "patch": PATCH, "status": "modified"}]})
    assert result["relevant_agents"] == ["security", "performance", "style", "coverage"]


# ── Style agent fallback / trust gating ───────────────────

def _llm_finding(file: str) -> Finding:
    return Finding(file=file, line=2, category="style", severity="low", confidence="medium",
                   message="llm", source_tool="llm-fallback", language="javascript", used_fallback=True)


def test_style_agent_skips_eslint_unless_trusted(mocker, tmp_path) -> None:
    mocker.patch("prpilot.nodes.style_agent.TRUST_REPO_CODE", False)
    eslint = mocker.patch("prpilot.nodes.style_agent.run_eslint")
    llm = mocker.patch("prpilot.nodes.style_agent.style_review", side_effect=lambda p, file_path, language: [_llm_finding(file_path)])

    out = style_agent_node({"workspace_dir": str(tmp_path),
                            "raw_files": [{"filename": "app.js", "patch": PATCH, "status": "modified"}]})

    eslint.assert_not_called()
    llm.assert_called_once()
    assert out["style_findings"][0].used_fallback is True


def test_style_agent_falls_back_when_ruff_fails(mocker, tmp_path) -> None:
    mocker.patch("prpilot.nodes.style_agent.run_ruff", return_value=None)
    llm = mocker.patch("prpilot.nodes.style_agent.style_review", return_value=[])
    style_agent_node({"workspace_dir": str(tmp_path),
                      "raw_files": [{"filename": "app.py", "patch": PATCH, "status": "modified"}]})
    llm.assert_called_once()


def test_style_agent_uses_ruff_results(mocker, tmp_path) -> None:
    mocker.patch("prpilot.nodes.style_agent.run_ruff", return_value=[
        {"line": 2, "code": "F401", "message": "[F401] unused import", "fix_suggestion": None},
    ])
    llm = mocker.patch("prpilot.nodes.style_agent.style_review")
    out = style_agent_node({"workspace_dir": str(tmp_path),
                            "raw_files": [{"filename": "app.py", "patch": PATCH, "status": "modified"}]})
    llm.assert_not_called()
    f = out["style_findings"][0]
    assert (f.source_tool, f.severity, f.used_fallback) == ("ruff", "medium", False)
