"""
tests/test_graph.py — End-to-end tests of the compiled LangGraph topology.

GitHub, the workspace and the LLM are faked; routing, fan-out/fan-in,
escalation and cleanup run for real.
"""

from __future__ import annotations

import pytest

import prpilot.graph as graph_module
import prpilot.nodes.synthesis as synthesis_module
from prpilot.state import Finding

PY_PATCH = "@@ -1,1 +1,2 @@\n x = 1\n+y = 2\n"


def _finding(category: str, severity: str, confidence: str) -> Finding:
    return Finding(
        file="app.py", line=2, category=category, severity=severity, confidence=confidence,
        message="m", source_tool="semgrep", language="python", used_fallback=False,
    )


@pytest.fixture
def fake_pipeline(mocker):
    """Patch graph nodes that touch the outside world; return a call recorder."""
    calls: list[str] = []

    def ingestion(files):
        def node(state):
            calls.append("ingestion")
            from prpilot.tools.diff_parser import parse_all_patches
            return {"commit_id": "sha", "raw_files": files, "parsed_hunks": parse_all_patches(files),
                    "workspace_dir": None}
        return node

    def recorder(name, result=None):
        def node(state):
            calls.append(name)
            return result or {}
        return node

    mocker.patch.object(synthesis_module, "generate_llm_summary", lambda findings, cov: "LLM summary")
    mocker.patch.object(graph_module, "github_poster_node",
                        lambda s: calls.append("poster") or {"posted_review_url": "url", "dry_run": True})
    mocker.patch.object(graph_module, "cleanup_node", recorder("cleanup", {"workspace_dir": None}))

    def build(files, security=None, can_escalate=False):
        mocker.patch.object(graph_module, "ingestion_node", ingestion(files))
        mocker.patch.object(graph_module, "security_agent_node", security or recorder("security"))
        mocker.patch.object(graph_module, "performance_agent_node", recorder("performance"))
        mocker.patch.object(graph_module, "style_agent_node", recorder("style"))
        mocker.patch.object(graph_module, "coverage_agent_node", recorder("coverage"))
        mocker.patch.object(synthesis_module, "can_escalate", lambda s: can_escalate)
        return graph_module.build_graph()

    return build, calls


def _invoke(app):
    return app.invoke({"pr_url": "https://github.com/o/r/pull/1", "owner": "o", "repo": "r", "pr_number": 1})


def test_docs_only_pr_still_posts_review(fake_pipeline) -> None:
    """Regression: an agent-less PR used to end the graph at triage with nothing posted."""
    build, calls = fake_pipeline
    out = _invoke(build([{"filename": "README.md", "patch": "@@ -1 +1 @@\n-a\n+b\n", "status": "modified"}]))

    assert not {"security", "performance", "style", "coverage"} & set(calls)
    assert calls[-2:] == ["poster", "cleanup"]
    assert "No source code changes" in out["review_summary"]
    assert out["posted_review_url"] == "url"


def test_code_pr_fans_out_to_all_agents(fake_pipeline) -> None:
    build, calls = fake_pipeline
    out = _invoke(build([{"filename": "app.py", "patch": PY_PATCH, "status": "modified"}]))

    assert {"security", "performance", "style", "coverage"} <= set(calls)
    assert calls[-2:] == ["poster", "cleanup"]
    assert out["review_summary"] == "LLM summary"


def test_escalation_loop_is_capped(fake_pipeline) -> None:
    """A critical/low security finding that never resolves re-runs security at most twice."""
    build, calls = fake_pipeline
    runs = []

    def security(state):
        runs.append(state.get("use_strict_semgrep", False))
        return {"security_findings": [_finding("security", "critical", "low")]}

    out = _invoke(build([{"filename": "app.py", "patch": PY_PATCH, "status": "modified"}],
                        security=security, can_escalate=True))

    assert runs == [False, True, True]
    assert out["escalation_count"] == 2
    assert out["review_summary"] == "LLM summary"


def test_no_escalation_when_semgrep_unavailable(fake_pipeline) -> None:
    build, calls = fake_pipeline
    runs = []

    def security(state):
        runs.append(1)
        return {"security_findings": [_finding("security", "critical", "low")]}

    out = _invoke(build([{"filename": "app.py", "patch": PY_PATCH, "status": "modified"}],
                        security=security, can_escalate=False))
    assert len(runs) == 1
    assert out.get("escalation_count", 0) == 0


def test_non_security_findings_do_not_escalate(fake_pipeline, mocker) -> None:
    build, calls = fake_pipeline
    app = build([{"filename": "app.py", "patch": PY_PATCH, "status": "modified"}], can_escalate=True)
    mocker.patch.object(graph_module, "performance_agent_node",
                        lambda s: {"performance_findings": [_finding("performance", "critical", "low")]})
    app = graph_module.build_graph()
    out = _invoke(app)
    assert calls.count("security") == 1
    assert out.get("escalation_count", 0) == 0
