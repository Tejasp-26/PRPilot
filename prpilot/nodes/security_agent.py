"""
nodes/security_agent.py — Security Agent Node.

Runs Semgrep on all changed source files in the local workspace.
For each finding, retrieves surrounding code context and uses the LLM to
enrich the finding with a plain-English explanation, suggested fix, and confidence level.
Falls back to LLM security review if Semgrep is unavailable or fails.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from prpilot.language_profiles import get_language_profile
from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm
from prpilot.state import Finding, ReviewState
from prpilot.tools.llm_fallback import security_review
from prpilot.tools.semgrep_runner import run_semgrep
from prpilot.tools.workspace import get_file_content, reviewable_files

logger = logging.getLogger(__name__)

CONTEXT_RADIUS = 5
"""Lines of code shown to the LLM above and below a Semgrep match."""


# ──────────────────────────────────────────────
# Pydantic schema for Semgrep enrichment
# ──────────────────────────────────────────────

class FindingEnrichment(BaseModel):
    """Enriches a raw tool finding with LLM explanation and suggested fix."""

    confidence: Literal["high", "medium", "low"] = Field(
        description="Confidence in this finding being a real security issue. Cap at medium if there is ambiguity."
    )
    message: str = Field(
        description="A clear, plain-English explanation of why this is a security risk and how it can be exploited."
    )
    suggested_fix: str | None = Field(
        default=None,
        description="A concrete code suggestion or step-by-step fix instructions."
    )


def enrich_semgrep_finding(
    file_path: str,
    line: int | None,
    original_message: str,
    code_context: str,
    language: str,
) -> FindingEnrichment:
    """Use the LLM to explain and fix a Semgrep violation using surrounding code context."""
    llm = get_llm()
    structured_llm = llm.with_structured_output(FindingEnrichment)

    system_prompt = (
        "You are an expert security engineer and code reviewer.\n"
        "You are given a security warning from a static analysis tool (Semgrep) "
        "and the surrounding code context. Your job is to:\n"
        "1. Evaluate if it's a real issue or a false positive.\n"
        "2. Rewrite the message to be clear, professional, and explain the threat model.\n"
        "3. Provide a concrete suggested fix if possible.\n"
        "4. Determine your confidence (high/medium/low)."
    ) + UNTRUSTED_INPUT_NOTICE

    user_prompt = (
        f"File: {file_path}\n"
        f"Language: {language}\n"
        f"Line: {line}\n"
        f"Original Tool Message: {original_message}\n\n"
        f"Surrounding Code Context (prefixed with line numbers):\n"
        f"```\n{code_context}\n```\n"
    )

    try:
        return structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
    except Exception as e:
        logger.error("[security_agent] Enrichment LLM call failed: %s", e)
        # Safe fallback
        return FindingEnrichment(
            confidence="medium",
            message=original_message,
            suggested_fix=None,
        )


def _code_context(content: str | None, line: int | None) -> str:
    """Return the lines around `line`, each prefixed with its line number."""
    if not content or line is None:
        return ""
    lines = content.splitlines()
    start = max(0, line - 1 - CONTEXT_RADIUS)
    end = min(len(lines), line + CONTEXT_RADIUS)
    return "\n".join(f"{i + 1:>5}  {lines[i]}" for i in range(start, end))


def security_agent_node(state: ReviewState) -> dict:
    """
    Security Agent node.

    Semgrep (multi-language) runs over every reviewable file in the workspace;
    each match is enriched by the LLM. Files in unknown languages additionally
    get an LLM review because Semgrep coverage there is thin. If Semgrep cannot
    run at all, every file gets the LLM security review instead.
    """
    files = reviewable_files(state.get("raw_files", []))
    workspace_dir = state.get("workspace_dir")
    use_strict = state.get("use_strict_semgrep", False)

    if not files:
        logger.info("[security_agent] No files to scan.")
        return {"security_findings": []}

    security_findings: list[Finding] = []

    # 1. Run Semgrep in the workspace (None = unavailable/failed → fallback)
    raw_findings = None
    if workspace_dir:
        raw_findings = run_semgrep([f["filename"] for f in files], strict=use_strict, cwd=workspace_dir)
    else:
        logger.warning("[security_agent] No workspace checkout; Semgrep cannot run.")

    if raw_findings is not None:
        for rf in raw_findings:
            file_path = rf["file"]
            line = rf["line"]
            lang_name = get_language_profile(file_path).name

            code_context = _code_context(get_file_content(state, file_path), line)

            logger.info("[security_agent] Enriching Semgrep finding at %s:%s", file_path, line)
            enrichment = enrich_semgrep_finding(
                file_path=file_path,
                line=line,
                original_message=f"[{rf['code']}] {rf['message']}",
                code_context=code_context,
                language=lang_name,
            )

            security_findings.append(
                Finding(
                    file=file_path,
                    line=line,
                    side="RIGHT",
                    category="security",
                    severity=rf["severity"],
                    confidence=enrichment.confidence,
                    message=enrichment.message,
                    suggested_fix=enrichment.suggested_fix,
                    source_tool="semgrep",
                    language=lang_name,
                    used_fallback=False,
                )
            )

        # Semgrep's rules for unknown/unsupported languages are thin: supplement with the LLM.
        fallback_files = [f for f in files if get_language_profile(f["filename"]).name == "unknown"]
    else:
        logger.warning("[security_agent] Semgrep unavailable. Falling back to LLM security review for all files.")
        fallback_files = files

    # 2. LLM security review for files Semgrep did not (adequately) cover
    for f in fallback_files:
        profile = get_language_profile(f["filename"])
        security_findings.extend(security_review(f["patch"], file_path=f["filename"], language=profile.name))

    logger.info("[security_agent] Finished security scan. Generated %d findings.", len(security_findings))
    return {"security_findings": security_findings}
