"""
nodes/coverage_agent.py — Test Coverage Agent Node.

Uses tree-sitter AST to find the functions a PR adds or modifies.
Scans the local workspace for test files matching the language's conventions
and checks whether each touched function is referenced in them.
If functions lack test references, calls the LLM to generate coverage findings
with sample test cases. Falls back to an LLM-only review when no AST or
workspace is available.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from prpilot.config import COVERAGE_MAX_TEST_FILES
from prpilot.language_profiles import LanguageProfile, get_language_profile
from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm, truncate_for_llm
from prpilot.state import Finding, ReviewState
from prpilot.tools.ast_utils import extract_function_defs, parse_file
from prpilot.tools.diff_parser import annotate_patch
from prpilot.tools.llm_fallback import coverage_review
from prpilot.tools.workspace import get_file_content, read_workspace_file, reviewable_files

logger = logging.getLogger(__name__)

_SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv", "__pycache__", "third_party"}
"""Directories never scanned for test files (dependencies, build output)."""


# ──────────────────────────────────────────────
# Pydantic schema for Coverage findings
# ──────────────────────────────────────────────

class ASTCoverageFinding(BaseModel):
    function_name: str = Field(description="Name of the untested function.")
    line: int | None = Field(
        default=None,
        description="Line number in the new file where the function is defined."
    )
    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="Severity of the missing test coverage."
    )
    message: str = Field(
        description="Explanation of why this function needs tests and what scenarios should be verified."
    )
    suggested_fix: str | None = Field(
        default=None,
        description="A concrete example of a unit test for this function."
    )


class ASTCoverageFindingsList(BaseModel):
    findings: list[ASTCoverageFinding] = Field(default_factory=list)


def run_ast_coverage_review(
    patch: str,
    file_path: str,
    language: str,
    uncovered_functions: list[dict],
) -> list[Finding]:
    """Use LLM to generate detailed test coverage findings for uncovered functions."""
    llm = get_llm()
    structured_llm = llm.with_structured_output(ASTCoverageFindingsList)

    system_prompt = (
        "You are an expert QA and test coverage engineer.\n"
        "You are given a git diff patch and a list of functions changed by it that do NOT "
        "appear to be referenced by any unit test in the codebase.\n"
        "Your job is to generate test coverage findings detailing:\n"
        "1. What unit test cases are needed for each function.\n"
        "2. Suggest a mock unit test code structure.\n"
        "3. Determine severity (medium/low based on function complexity).\n"
        "Use each function's given definition line as the finding's line."
    ) + UNTRUSTED_INPUT_NOTICE

    functions_desc = ", ".join(f"{d['name']} (line {d['start_line']})" for d in uncovered_functions)
    user_prompt = (
        f"File: {file_path}\n"
        f"Language: {language}\n"
        f"Untested Functions: {functions_desc}\n\n"
        f"Git Diff Patch (lines prefixed with new-file line numbers):\n"
        f"```diff\n{truncate_for_llm(annotate_patch(patch))}\n```\n"
    )

    try:
        response: ASTCoverageFindingsList = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
    except Exception as e:
        logger.error("[coverage_agent] LLM coverage review failed: %s", e)
        return []

    findings = []
    for lf in response.findings:
        findings.append(
            Finding(
                file=file_path,
                line=lf.line,
                side="RIGHT",
                category="test_coverage",
                severity=lf.severity,
                confidence="medium",
                message=lf.message,
                suggested_fix=lf.suggested_fix,
                source_tool="llm-reasoning",
                language=language,
                used_fallback=False,
            )
        )
    return findings


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def is_test_file(path: str, profile: LanguageProfile) -> bool:
    """True if the path matches the language's test conventions (or lives in __tests__/)."""
    basename = os.path.basename(path)
    if any(fnmatch.fnmatch(basename, p) for p in profile.test_file_patterns):
        return True
    return "__tests__" in path.replace(os.sep, "/").split("/") and profile.name in ("javascript", "typescript")


def find_test_files(workspace_dir: str, profile: LanguageProfile) -> list[str]:
    """Walk the workspace and return repo-relative paths of this language's test files."""
    matches: list[str] = []
    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), workspace_dir).replace(os.sep, "/")
            if get_language_profile(rel).name == profile.name and is_test_file(rel, profile):
                matches.append(rel)
                if len(matches) >= COVERAGE_MAX_TEST_FILES:
                    logger.warning("[coverage_agent] Test file cap (%d) reached", COVERAGE_MAX_TEST_FILES)
                    return matches
    return matches


def touched_functions(defs: list[dict], added_lines: set[int]) -> list[dict]:
    """Functions whose definition span contains at least one added line."""
    return [d for d in defs if any(d["start_line"] <= ln <= d["end_line"] for ln in added_lines)]


def is_referenced(name: str, test_contents: list[str]) -> bool:
    """Whole-word match: 'get' must not count as covered because a test says 'target'."""
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    return any(pattern.search(tc) for tc in test_contents)


# ──────────────────────────────────────────────
# Node
# ──────────────────────────────────────────────

def coverage_agent_node(state: ReviewState) -> dict:
    """Test Coverage Agent node."""
    files = reviewable_files(state.get("raw_files", []))
    workspace_dir = state.get("workspace_dir")
    added_by_file = {h.file: set(h.added_lines) for h in state.get("parsed_hunks", [])}

    coverage_findings: list[Finding] = []
    test_corpus: dict[str, list[str]] = {}  # language -> test file contents (loaded once)

    logger.info("[coverage_agent] Running coverage agent on %d files", len(files))

    for f in files:
        filename, patch = f["filename"], f["patch"]
        profile = get_language_profile(filename)

        # Test files themselves don't need coverage
        if is_test_file(filename, profile):
            logger.debug("[coverage_agent] Skipping test file: %s", filename)
            continue

        # 1. AST + workspace approach if grammar and checkout are available
        tree = None
        content = None
        if profile.tree_sitter_grammar and workspace_dir:
            content = get_file_content(state, filename)
            if content:
                tree = parse_file(content, profile.name, filename)

        if tree and content:
            changed = touched_functions(extract_function_defs(tree, content), added_by_file.get(filename, set()))
            # Dunder methods (__init__, __repr__, ...) are exercised indirectly; skip them.
            changed = [d for d in changed if not (d["name"].startswith("__") and d["name"].endswith("__"))]
            if not changed:
                logger.debug("[coverage_agent] No changed functions in %s", filename)
                continue

            if profile.name not in test_corpus:
                test_paths = find_test_files(workspace_dir, profile)
                logger.info("[coverage_agent] Found %d %s test files", len(test_paths), profile.name)
                test_corpus[profile.name] = [
                    c for c in (read_workspace_file(workspace_dir, p) for p in test_paths) if c
                ]

            uncovered = [d for d in changed if not is_referenced(d["name"], test_corpus[profile.name])]

            if uncovered:
                logger.info(
                    "[coverage_agent] %d changed functions in %s have no test references. Running LLM review.",
                    len(uncovered),
                    filename,
                )
                coverage_findings.extend(
                    run_ast_coverage_review(
                        patch=patch,
                        file_path=filename,
                        language=profile.name,
                        uncovered_functions=uncovered,
                    )
                )
            else:
                logger.debug("[coverage_agent] All %d changed functions in %s referenced in tests", len(changed), filename)

        else:
            # 2. Fall back to LLM coverage review
            logger.info("[coverage_agent] AST/workspace unavailable for %s. Running fallback LLM coverage review.", filename)
            coverage_findings.extend(coverage_review(patch, file_path=filename, language=profile.name))

    logger.info("[coverage_agent] Finished coverage review. Produced %d findings", len(coverage_findings))
    return {"coverage_findings": coverage_findings}
