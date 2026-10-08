"""
nodes/style_agent.py — Style Agent Node.

Dispatches files to per-language linters (Ruff, ESLint, Go vet/staticcheck)
running inside the local workspace. Falls back to LLM-based style review when
a tool is unavailable, unconfigured, fails, or is not trusted to run.
"""

from __future__ import annotations

import logging

from prpilot.config import TRUST_REPO_CODE
from prpilot.language_profiles import get_language_profile
from prpilot.state import Finding, ReviewState
from prpilot.tools.eslint_runner import run_eslint
from prpilot.tools.go_vet_runner import run_go_vet
from prpilot.tools.llm_fallback import style_review
from prpilot.tools.ruff_runner import run_ruff
from prpilot.tools.workspace import reviewable_files

logger = logging.getLogger(__name__)


def _ruff_severity(code: str) -> str:
    # F = pyflakes (undefined names, unused imports), E9 = syntax errors: likely bugs.
    return "medium" if code.startswith("F") or code.startswith("E9") else "low"


def _run_linter(linter: str, filename: str, workspace_dir: str | None) -> list[dict] | None:
    """
    Run the file's native linter. Returns None when the LLM path should be used
    instead (no workspace, tool missing/failed, or tool not trusted to run).
    """
    if not workspace_dir:
        return None

    if linter == "ruff":
        return run_ruff(filename, cwd=workspace_dir)

    if linter in ("eslint", "go_vet"):
        if not TRUST_REPO_CODE:
            # These tools execute repository code (eslint.config.js / Go build).
            logger.info(
                "[style_agent] Skipping %s for %s: PRPILOT_TRUST_REPO_CODE is not enabled.", linter, filename
            )
            return None
        if linter == "eslint":
            return run_eslint(filename, cwd=workspace_dir)
        return run_go_vet(filename, cwd=workspace_dir)

    return None


def style_agent_node(state: ReviewState) -> dict:
    """
    Style Agent node.

    Dispatches each changed file to its respective linter:
      - Python → Ruff
      - JS/TS → ESLint (trusted repos only)
      - Go → staticcheck / go vet (trusted repos only)
      - Anything else, or when the tool can't run → LLM fallback
    """
    files = reviewable_files(state.get("raw_files", []))
    file_language_map = state.get("file_language_map", {})
    workspace_dir = state.get("workspace_dir")
    style_findings: list[Finding] = []

    logger.info("[style_agent] Running style agent for %d files", len(files))

    for f in files:
        filename = f["filename"]
        profile = get_language_profile(filename)
        lang_name = file_language_map.get(filename) or profile.name

        violations = _run_linter(profile.linter, filename, workspace_dir)

        if violations is None:
            logger.info("[style_agent] No native linter result for %s. Running LLM style review.", filename)
            style_findings.extend(style_review(f["patch"], file_path=filename, language=lang_name))
            continue

        for v in violations:
            code = v.get("code", "")
            if profile.linter == "ruff":
                severity, source = _ruff_severity(code), "ruff"
            elif profile.linter == "eslint":
                severity, source = "low", "eslint"
            else:
                severity, source = "medium", v.get("tool", "go_vet")  # go vet findings are usually correctness issues

            style_findings.append(
                Finding(
                    file=filename,
                    line=v.get("line"),
                    side="RIGHT",
                    category="style",
                    severity=severity,
                    confidence="high",
                    message=v.get("message", ""),
                    suggested_fix=v.get("fix_suggestion"),
                    source_tool=source,
                    language=lang_name,
                    used_fallback=False,
                )
            )

    logger.info("[style_agent] Finished. Produced %d findings", len(style_findings))
    return {"style_findings": style_findings}
