"""
tools/llm_fallback.py — LLM-based analysis for security, performance, style, and coverage.

Used as a fallback when native static analysis tools are not available for a given language.
Findings produced here always have confidence <= 'medium' and used_fallback=True.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm, truncate_for_llm
from prpilot.state import Finding
from prpilot.tools.diff_parser import annotate_patch

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Pydantic schema for LLM structured output
# ──────────────────────────────────────────────

class LLMFinding(BaseModel):
    """Temporary schema for receiving LLM-structured findings before mapping to state.Finding."""

    line: int | None = Field(
        default=None,
        description=(
            "The line number in the NEW version of the file (right side of diff, "
            "corresponding to added/modified lines). Use null/None if the finding "
            "applies to the whole file rather than a specific line."
        ),
    )
    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="The severity of this finding based on impact and likelihood."
    )
    confidence: Literal["medium", "low"] = Field(
        description="Confidence level. For LLM fallback, always cap at 'medium' or 'low' (never 'high')."
    )
    message: str = Field(
        description="Clear, actionable review message explaining the issue, why it is a problem, and how to fix it."
    )
    suggested_fix: str | None = Field(
        default=None,
        description="A code snippet or instructions showing how to fix the issue."
    )


class LLMFindingList(BaseModel):
    """Wrapper list for structured JSON output from LLM."""
    findings: list[LLMFinding] = Field(default_factory=list)


# ──────────────────────────────────────────────
# Helper for LLM execution
# ──────────────────────────────────────────────

def _run_fallback_review(
    diff: str,
    file_path: str,
    language: str,
    category: Literal["security", "performance", "style", "test_coverage"],
    system_prompt: str,
) -> list[Finding]:
    """Helper that queries the LLM and maps the structured output into state.Finding objects."""
    if not diff or not diff.strip():
        logger.debug("[llm_fallback] Empty diff for %s. Skipping LLM review.", file_path)
        return []

    llm = get_llm()
    structured_llm = llm.with_structured_output(LLMFindingList)

    user_prompt = (
        f"File Path: {file_path}\n"
        f"Language: {language}\n"
        f"Category: {category.upper()}\n\n"
        "Unified diff. Each line is prefixed with its line number in the NEW file "
        "('+' = added, ' ' = context, '-' = removed and has no new line number). "
        "Report line numbers using these prefixes, and prefer added lines.\n"
        f"```diff\n{truncate_for_llm(annotate_patch(diff))}\n```\n"
    )

    try:
        response: LLMFindingList = structured_llm.invoke([
            {"role": "system", "content": system_prompt + UNTRUSTED_INPUT_NOTICE},
            {"role": "user", "content": user_prompt},
        ])
    except Exception as e:
        logger.error(
            "[llm_fallback] LLM call failed for %s (%s): %s",
            file_path,
            category,
            e,
        )
        return []

    findings = []
    for lf in response.findings:
        findings.append(
            Finding(
                file=file_path or "unknown",
                line=lf.line,
                side="RIGHT",
                category=category,
                severity=lf.severity,
                # Cap confidence at medium for fallback
                confidence="medium" if lf.confidence == "high" else lf.confidence,
                message=lf.message,
                suggested_fix=lf.suggested_fix,
                source_tool="llm-fallback",
                language=language,
                used_fallback=True,
            )
        )

    logger.info(
        "[llm_fallback] Generated %d %s findings for %s",
        len(findings),
        category,
        file_path,
    )
    return findings


# ──────────────────────────────────────────────
# Public Fallback APIs
# ──────────────────────────────────────────────

def security_review(diff: str, file_path: str = "", language: str = "unknown") -> list[Finding]:
    """Analyze the diff for security vulnerabilities (e.g. OWASP Top 10, credentials, injection)."""
    system_prompt = (
        "You are an expert security auditor. Analyze the provided git diff for security vulnerabilities.\n"
        "Look for:\n"
        "1. Hardcoded secrets, API keys, passwords, or tokens.\n"
        "2. SQL injections, command injections, or path traversals.\n"
        "3. Cross-Site Scripting (XSS) or insecure deserialization.\n"
        "4. Insecure cryptographic algorithms or random number generators.\n"
        "5. Broken authentication or authorization checks.\n\n"
        "Provide your review as a list of findings. Only report real, actionable security risks. "
        "Cap all confidence values at 'medium'."
    )
    return _run_fallback_review(diff, file_path, language, "security", system_prompt)


def style_review(diff: str, file_path: str = "", language: str = "unknown") -> list[Finding]:
    """Analyze the diff for code style, formatting, syntax conventions, and code smells."""
    system_prompt = (
        "You are an expert software engineer. Analyze the provided git diff for style, readability, and formatting issues.\n"
        "Look for:\n"
        "1. Violation of language idiomatic naming conventions (e.g., snake_case vs camelCase).\n"
        "2. Obvious formatting errors, inconsistent indentation, or missing docstrings.\n"
        "3. Bad variable names (e.g., too short, misleading, or single-character names in large scopes).\n"
        "4. Unused imports, unused variables, or dead code.\n"
        "5. Code complexity / smells (e.g., excessively long lines, deep nested structures).\n\n"
        "Provide your review as a list of findings. Be helpful and polite. Cap all confidence values at 'medium'."
    )
    return _run_fallback_review(diff, file_path, language, "style", system_prompt)


def performance_review(diff: str, file_path: str = "", language: str = "unknown") -> list[Finding]:
    """Analyze the diff for performance bottlenecks, resource leaks, or inefficient logic."""
    system_prompt = (
        "You are a performance optimization expert. Analyze the provided git diff for performance bottlenecks.\n"
        "Look for:\n"
        "1. High time/space complexity algorithms where a simpler option is obvious.\n"
        "2. Expensive operations (I/O, database queries, API calls) inside loops (N+1 query problem).\n"
        "3. Memory or resource leaks (e.g., unclosed files, sockets, database connections).\n"
        "4. Redundant calculations, duplicate operations, or lack of caching/memoization.\n"
        "5. Threading, race conditions, or blocking calls in asynchronous code.\n\n"
        "Provide your review as a list of findings. Cap all confidence values at 'medium'."
    )
    return _run_fallback_review(diff, file_path, language, "performance", system_prompt)


def coverage_review(
    diff: str,
    file_path: str = "",
    language: str = "unknown",
    functions: list[str] | None = None,
) -> list[Finding]:
    """Analyze the diff and list of functions to determine test coverage gaps."""
    func_list_str = ", ".join(functions) if functions else "None"
    system_prompt = (
        "You are a QA automation engineer and test coverage expert. Analyze the provided git diff "
        "to check if newly added or modified functions/features have associated unit tests.\n"
        f"Newly added/modified functions/methods: {func_list_str}\n\n"
        "Look for:\n"
        "1. New logic or helper functions defined in the diff that do NOT have accompanying tests in the diff "
        "or test files.\n"
        "2. Complex branch conditions added without new test coverage.\n\n"
        "Provide your review as a list of findings. If you believe tests are missing or insufficient, "
        "explain exactly what test cases should be added. Cap all confidence values at 'medium' or 'low'."
    )
    return _run_fallback_review(diff, file_path, language, "test_coverage", system_prompt)
