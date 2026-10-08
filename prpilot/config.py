"""
config.py — Global constants, severity rubric, and agent thresholds.

The severity rubric is defined here as an explicit, documented constant
so the prioritization logic in synthesis.py never has magic numbers or
buried conditionals. Any change to ranking policy is a one-line edit here.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Escalation
# ──────────────────────────────────────────────

MAX_ESCALATION_COUNT: int = 2
"""
Maximum number of times the escalation loop can re-run the Security Agent
with stricter Semgrep rules. Hard cap — if we hit this without resolving
confidence, we log a warning and move forward anyway.
"""

# ──────────────────────────────────────────────
# Severity ranking rubric
# ──────────────────────────────────────────────

# Findings are sorted by this rubric (lower index = higher priority).
# Within the same rank, real-tool findings (used_fallback=False) outrank
# LLM-fallback findings (used_fallback=True).
#
# Rubric:
#   1. critical + security
#   2. critical + performance
#   3. critical + any other category
#   4. high + security
#   5. high + performance
#   6. high + style or test_coverage
#   7. medium + any category
#   8. low + any category
#   9. info + any category
#
# Within each tier, used_fallback=False ranks above used_fallback=True.

SEVERITY_ORDER: list[str] = ["critical", "high", "medium", "low", "info"]
"""Ordered from most to least severe. Used as primary sort key."""

CATEGORY_ORDER: list[str] = ["security", "performance", "style", "test_coverage"]
"""
Secondary sort key within same severity.
Security > Performance > Style > Test Coverage reflects real-world
PR review priority: a critical security bug outweighs a performance
suggestion of the same severity label.
"""

def finding_sort_key(finding) -> tuple:
    """
    Returns a tuple suitable for sorting findings in priority order.
    Lower values = higher priority (sort ascending).

    Usage:
        findings.sort(key=finding_sort_key)
    """
    severity_rank = SEVERITY_ORDER.index(finding.severity) if finding.severity in SEVERITY_ORDER else 99
    category_rank = CATEGORY_ORDER.index(finding.category) if finding.category in CATEGORY_ORDER else 99
    fallback_rank = 1 if finding.used_fallback else 0  # real tool findings first
    return (severity_rank, category_rank, fallback_rank)


# ──────────────────────────────────────────────
# Escalation trigger conditions
# ──────────────────────────────────────────────

ESCALATION_TRIGGER_SEVERITY: str = "critical"
"""Finding severity that can trigger escalation."""

ESCALATION_TRIGGER_CONFIDENCE: str = "low"
"""
Finding confidence level that can trigger escalation.
A critical finding with LOW confidence = we aren't sure if it's real,
so we re-run security with stricter rules to resolve ambiguity.
"""

ESCALATION_TRIGGER_CATEGORY: str = "security"
"""
Only security findings can trigger escalation: the escalation loop re-runs
the Security Agent, so it cannot resolve findings from any other agent.
"""

# ──────────────────────────────────────────────
# Comment mapper behavior
# ──────────────────────────────────────────────

OUT_OF_DIFF_COMMENT_STRATEGY: str = "drop"
"""
Strategy for findings whose line number falls outside the diff:
  'drop'      → exclude from inline comments, keep in overall summary
  'reanchor'  → move to the nearest valid diff line (not implemented in v1)

We chose 'drop' because re-anchoring could mislead reviewers by pointing
to the wrong line. Out-of-diff findings are still visible in the summary.
"""

# ──────────────────────────────────────────────
# Subprocess timeouts (seconds)
# ──────────────────────────────────────────────

SEMGREP_TIMEOUT: int = 120
"""
Semgrep can be slow on first run (downloads rules) or on large files.
FREE TIER NOTE: semgrep --config=auto fetches rules from the Semgrep registry.
On first run this may take 20-30 seconds. Subsequent runs use cached rules.
"""

RUFF_TIMEOUT: int = 30
"""Ruff is fast (Rust-based). 30s is generous."""

ESLINT_TIMEOUT: int = 60
"""ESLint via npx can be slow on first invocation (npm package resolution)."""

GO_VET_TIMEOUT: int = 60
"""go vet requires compilation; larger packages may take longer."""

STATICCHECK_TIMEOUT: int = 90
"""staticcheck does deeper analysis than go vet; give it more time."""

# ──────────────────────────────────────────────
# LLM retry config
# ──────────────────────────────────────────────

LLM_MAX_RETRIES: int = 3
"""
Number of retry attempts on rate-limit or transient LLM errors.
Passed to the provider client, which applies its own exponential backoff.
GROQ FREE TIER NOTE: a large PR with 20+ files and 4 parallel agents can hit
the per-minute token limit; retries transparently absorb transient 429s.
"""

LLM_MAX_FILE_CHARS: int = 40_000
"""
Upper bound on file/diff text sent to the LLM in a single prompt.
Longer inputs are truncated (with a marker) to stay inside context and
free-tier token budgets.
"""

# ──────────────────────────────────────────────
# Workspace (local checkout of the PR head)
# ──────────────────────────────────────────────

WORKSPACE_MAX_ARCHIVE_BYTES: int = 200 * 1024 * 1024
"""
Maximum size of the repository tarball we are willing to download.
Above this, ingestion falls back to downloading only the changed files.
"""

TRUST_REPO_CODE: bool = os.getenv("PRPILOT_TRUST_REPO_CODE", "false").lower() in ("1", "true", "yes")
"""
Whether linters that EXECUTE code from the reviewed repository may run.

ESLint loads the repo's eslint.config.js (arbitrary JavaScript), and
go vet / staticcheck compile the repo's packages (and may download modules).
Running them on an untrusted PR is remote code execution on this machine.

Default False: those tools are skipped and the Style Agent uses the LLM path.
Ruff and Semgrep only read files, so they always run.
Set PRPILOT_TRUST_REPO_CODE=true only for repositories you trust.
"""

# ──────────────────────────────────────────────
# Test Coverage Agent
# ──────────────────────────────────────────────

COVERAGE_MAX_TEST_FILES: int = 200
"""Maximum number of test files read from the workspace when checking references."""

# ──────────────────────────────────────────────
# GitHub review posting
# ──────────────────────────────────────────────

GITHUB_REVIEW_BODY_MAX_CHARS: int = 60_000
"""GitHub rejects review bodies over 65,536 characters; keep a safety margin."""

# ──────────────────────────────────────────────
# Semgrep severity mapping
# ──────────────────────────────────────────────

# Semgrep reports: ERROR, WARNING, INFO (and sometimes CRITICAL in pro rules)
# Mapping to our internal severity scale:
SEMGREP_SEVERITY_MAP: dict[str, str] = {
    "CRITICAL": "critical",  # Semgrep Pro rules
    "ERROR": "high",         # High-confidence pattern matches
    "WARNING": "medium",     # Lower-confidence or stylistic security patterns
    "INFO": "low",           # Informational findings (e.g. deprecated API usage)
    # Fallback for any unexpected value:
    "UNKNOWN": "info",
}
"""
Maps Semgrep's severity strings to our internal Literal severity scale.
NOTE: Semgrep rule coverage quality varies significantly by language.
Python, JavaScript, Go, and Java have the deepest community rule sets.
More exotic languages (Ruby, Rust, Kotlin) have thinner coverage —
treat a 'no findings' result from Semgrep for those languages with
more skepticism and supplement with the LLM-fallback pass.
"""
