"""
nodes/synthesis.py — Synthesis/Prioritization Node.

Merges, deduplicates, and ranks all findings from specialized agents.
Determines if an escalation loop is needed to resolve low-confidence critical findings.
Uses the LLM to generate a professional, structured review summary paragraph.
"""

from __future__ import annotations

import logging
import shutil

from pydantic import BaseModel, Field

from prpilot.config import (
    ESCALATION_TRIGGER_CATEGORY,
    ESCALATION_TRIGGER_CONFIDENCE,
    ESCALATION_TRIGGER_SEVERITY,
    MAX_ESCALATION_COUNT,
    SEVERITY_ORDER,
    finding_sort_key,
)
from prpilot.llm import UNTRUSTED_INPUT_NOTICE, get_llm
from prpilot.state import Finding, ReviewState

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Pydantic schema for Review Summary
# ──────────────────────────────────────────────

class PRReviewSummary(BaseModel):
    """Structured LLM output for the top-level PR review summary."""
    verdict: str = Field(
        description="A short, professional verdict sentence (e.g., 'Minor styling issues detected', 'Critical security concerns identified')."
    )
    summary_paragraph: str = Field(
        description="A comprehensive, professional markdown-formatted paragraph summarizing top findings, language coverage, and overall review."
    )


def generate_llm_summary(
    findings: list[Finding],
    coverage_summary: str,
) -> str:
    """Use the LLM to synthesize findings and generate the final PR review summary."""
    llm = get_llm()
    structured_llm = llm.with_structured_output(PRReviewSummary)

    # Summarize top findings for prompt context (limit to top 10 to fit context)
    findings_context = []
    for i, f in enumerate(findings[:10], 1):
        findings_context.append(
            f"Finding #{i}: [{f.severity.upper()}] in {f.file}:{f.line} ({f.category}) - {f.message}"
        )

    findings_str = "\n".join(findings_context) if findings_context else "No issues found."

    system_prompt = (
        "You are a principal software engineer and security architect.\n"
        "Your task is to write a high-level summary of a pull request review.\n"
        "You will be given a list of code review findings (sorted by priority) and a summary of language coverage.\n"
        "Write a constructive, professional markdown review summary. "
        "Highlight the most critical issues if present, mention the language analysis tools used, "
        "and mention any limitations in tool coverage (e.g. fallback to LLM analysis for certain files).\n"
        "Keep the summary concise, professional, and actionable."
    ) + UNTRUSTED_INPUT_NOTICE.replace("code blocks", "findings")

    user_prompt = (
        f"Language Coverage Summary: {coverage_summary}\n\n"
        f"Top Findings:\n{findings_str}\n"
    )

    try:
        response: PRReviewSummary = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        # Combine verdict and summary paragraph into one clean string
        return f"**Verdict:** {response.verdict}\n\n{response.summary_paragraph}"
    except Exception as e:
        logger.error("[synthesis] Failed to generate LLM summary: %s", e)
        # Safe fallback
        verdict = "Critical concerns identified" if any(f.severity == "critical" for f in findings) else "Review completed"
        return (
            f"**Verdict:** {verdict}\n\n"
            f"PRPilot analyzed the PR. Found {len(findings)} issues. "
            f"Language coverage: {coverage_summary}"
        )


def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """
    Deduplicate findings on the same file, line, and category.
    Keeps the highest quality finding:
      - Higher severity outranks lower severity
      - Higher confidence outranks lower confidence
      - Real-tool findings (used_fallback=False) outrank fallback findings
    """
    grouped: dict[tuple[str, int | None, str], list[Finding]] = {}

    for f in findings:
        key = (f.file, f.line, f.category)
        grouped.setdefault(key, []).append(f)

    deduplicated: list[Finding] = []

    for group in grouped.values():
        if len(group) == 1:
            deduplicated.append(group[0])
            continue

        # Sort within the group to choose the best one.
        # We want to sort ascending by a score where lower score = better.
        def rank_finding(find: Finding) -> tuple:
            sev_idx = SEVERITY_ORDER.index(find.severity) if find.severity in SEVERITY_ORDER else 99
            conf_idx = {"high": 0, "medium": 1, "low": 2}.get(find.confidence, 9)
            fallback_idx = 1 if find.used_fallback else 0
            return (sev_idx, conf_idx, fallback_idx)

        group.sort(key=rank_finding)
        deduplicated.append(group[0])

    return deduplicated


def can_escalate(state: ReviewState) -> bool:
    """
    Escalation re-runs Semgrep with a stricter ruleset. That only changes the
    outcome when Semgrep can actually run (installed + workspace checkout);
    otherwise the re-run would repeat the identical LLM review.
    """
    return bool(state.get("workspace_dir")) and shutil.which("semgrep") is not None


def synthesis_node(state: ReviewState) -> dict:
    """
    Synthesis node.

    Merges all finding lists from agents, deduplicates them, sorts them by priority,
    determines if escalation is required, and (on the final pass) invokes the LLM
    to write the review summary.
    """
    logger.info("[synthesis] Running synthesis node")

    # 1. Merge findings from all specialized agent lists
    merged_findings: list[Finding] = []
    merged_findings.extend(state.get("security_findings", []))
    merged_findings.extend(state.get("performance_findings", []))
    merged_findings.extend(state.get("style_findings", []))
    merged_findings.extend(state.get("coverage_findings", []))

    logger.info("[synthesis] Merged %d raw findings from all agents", len(merged_findings))

    # 2. Deduplicate overlapping findings
    deduped_findings = deduplicate_findings(merged_findings)
    logger.info("[synthesis] Deduplicated to %d unique findings", len(deduped_findings))

    # 3. Sort findings by severity and category rubric
    deduped_findings.sort(key=finding_sort_key)

    # 4. Check escalation criteria (security findings only — escalation re-runs Security)
    escalation_count = state.get("escalation_count", 0)
    needs_escalation = False

    if escalation_count < MAX_ESCALATION_COUNT and can_escalate(state):
        for f in deduped_findings:
            if (
                f.category == ESCALATION_TRIGGER_CATEGORY
                and f.severity == ESCALATION_TRIGGER_SEVERITY
                and f.confidence == ESCALATION_TRIGGER_CONFIDENCE
            ):
                needs_escalation = True
                logger.info(
                    "[synthesis] Escalation criteria met: finding in %s is %s with %s confidence. "
                    "Escalation loop count: %d/%d",
                    f.file,
                    f.severity,
                    f.confidence,
                    escalation_count,
                    MAX_ESCALATION_COUNT,
                )
                break

    result: dict = {
        "all_findings": deduped_findings,
        "needs_escalation": needs_escalation,
    }

    # 5. Generate the review summary only on the final pass (escalation discards it)
    if not needs_escalation:
        if state.get("relevant_agents") == []:  # triage ran and chose no agents
            result["review_summary"] = (
                "**Verdict:** No source code changes to review\n\n"
                "This PR only changes documentation, configuration or lockfiles, "
                "so PRPilot's analysis agents were not run."
            )
        else:
            coverage_summary = state.get("language_coverage_summary", "No coverage information.")
            result["review_summary"] = generate_llm_summary(deduped_findings, coverage_summary)

    logger.info(
        "[synthesis] Synthesis complete: needs_escalation=%s, total ranked findings=%d",
        needs_escalation,
        len(deduped_findings),
    )
    return result
