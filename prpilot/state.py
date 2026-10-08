"""
state.py — Core Pydantic/TypedDict schemas for PRPilot.

All data that flows through the LangGraph StateGraph is typed here.
Agents read from and write to ReviewState exclusively — no ad-hoc dicts.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Finding — one discrete review comment
# ──────────────────────────────────────────────

class Finding(BaseModel):
    """A single actionable review finding produced by any agent."""

    file: str = Field(description="Repo-relative path to the changed file.")
    line: int | None = Field(
        default=None,
        description=(
            "Line number in the NEW version of the file (right side of diff). "
            "None means the finding applies to the whole file, not a specific line."
        ),
    )
    side: Literal["LEFT", "RIGHT"] = Field(
        default="RIGHT",
        description=(
            "Which side of the diff this comment targets. "
            "RIGHT = new code (almost always). LEFT = deleted code."
        ),
    )
    category: Literal["security", "performance", "style", "test_coverage"] = Field(
        description="Which review domain produced this finding."
    )
    severity: Literal["critical", "high", "medium", "low", "info"] = Field(
        description="Impact level of the finding."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description=(
            "How confident the system is in this finding. "
            "LLM-only findings are ALWAYS capped at 'medium' — never 'high'. "
            "See used_fallback field."
        )
    )
    message: str = Field(description="Plain-English description of the issue.")
    suggested_fix: str | None = Field(
        default=None,
        description="Concrete suggested fix, or None if no fix can be suggested.",
    )
    source_tool: str = Field(
        description=(
            "The tool that produced this finding. "
            "Examples: 'semgrep', 'ruff', 'eslint', 'go_vet', 'staticcheck', "
            "'llm-fallback', 'llm-reasoning'. "
            "Use 'llm-fallback' when no real static tool was available for this file/language. "
            "Use 'llm-reasoning' when a real tool ran but the LLM added supplementary reasoning."
        )
    )
    language: str = Field(
        description=(
            "Resolved language name for the file this finding came from. "
            "One of: 'python', 'javascript', 'typescript', 'go', or 'unknown'."
        )
    )
    used_fallback: bool = Field(
        description=(
            "True if this finding was produced without a backing static analysis tool "
            "(i.e. LLM-only analysis because the language/tool was unavailable). "
            "Findings with used_fallback=True are always ranked below equivalent "
            "severity findings from real tools in synthesis."
        )
    )


# ──────────────────────────────────────────────
# ParsedHunk — diff structure from unidiff
# ──────────────────────────────────────────────

class ParsedHunk(BaseModel):
    """
    Retains the line-number structure of a single file's diff for later
    position validation in CommentMapper. GitHub only accepts inline comments
    on lines that appear in the diff — ParsedHunk lets us enforce this.
    """

    file: str = Field(description="Repo-relative path to the file.")
    added_lines: list[int] = Field(
        default_factory=list,
        description="Line numbers (in new file) for lines added in this diff.",
    )
    removed_lines: list[int] = Field(
        default_factory=list,
        description="Line numbers (in old file) for lines removed in this diff.",
    )
    context_lines: list[int] = Field(
        default_factory=list,
        description="Line numbers (in new file) for unchanged context lines in the diff.",
    )

    @property
    def commentable_lines(self) -> set[int]:
        """
        Lines that GitHub will accept inline comments on.
        GitHub allows comments on added lines and context lines (right side).
        Removed lines are on the LEFT side — rarely commented on.
        """
        return set(self.added_lines) | set(self.context_lines)


# ──────────────────────────────────────────────
# ReviewState — the full LangGraph state
# ──────────────────────────────────────────────

class ReviewState(TypedDict, total=False):
    """
    The shared mutable state flowing through the LangGraph StateGraph.

    Nodes read from this dict and return partial dicts that LangGraph
    merges back (reducer pattern). Every key is optional (total=False)
    so nodes can return minimal partial updates without KeyErrors.
    """

    # ── Input ─────────────────────────────────
    pr_url: str
    """The full GitHub PR URL passed in by the user (e.g. https://github.com/owner/repo/pull/42)."""

    owner: str
    """Extracted GitHub repo owner (org or user)."""

    repo: str
    """Extracted GitHub repo name."""

    pr_number: int
    """PR number (integer)."""

    commit_id: str
    """
    The HEAD commit SHA of the PR branch. Required by GitHub's review API
    to anchor inline comments to a specific commit.
    """

    # ── Ingestion outputs ─────────────────────
    raw_files: list[dict]
    """Raw GitHub API response objects per changed file (one dict per file)."""

    parsed_hunks: list[ParsedHunk]
    """
    Structured unidiff output per file. Used by CommentMapper to validate
    that (file, line) pairs are actually commentable in this diff.
    """

    workspace_dir: str | None
    """
    Absolute path to a local checkout of the PR head commit (temp directory).
    Static tools (Semgrep, Ruff, ...) run with this as their working directory,
    and agents read file contents from here instead of re-fetching from GitHub.
    None if the checkout could not be created; agents then fall back to the API.
    Removed by the cleanup node at the end of the run.
    """

    # ── Triage outputs ────────────────────────
    relevant_agents: list[str]
    """
    Which domain agents should run for this PR.
    Subset of: ['security', 'performance', 'style', 'coverage'].
    Triage may skip agents (e.g. skip security if only docs changed).
    """

    language_coverage_summary: str
    """
    Human-readable string describing which languages were detected and
    what tool support level each has. Surfaced in the final review summary
    so reviewers know where confidence is lower.
    Example: "python: 4 files, full support (ruff+semgrep+tree-sitter); ruby: 1 file, LLM-fallback only"
    """

    file_language_map: dict[str, str]
    """Maps each changed file path to its resolved language name."""

    # ── Agent findings ────────────────────────
    security_findings: list[Finding]
    performance_findings: list[Finding]
    style_findings: list[Finding]
    coverage_findings: list[Finding]

    # ── Synthesis outputs ─────────────────────
    all_findings: list[Finding]
    """
    Merged, deduplicated, priority-ranked list of all findings.
    Produced by the Synthesis node.
    """

    escalation_count: int
    """Number of escalation loops completed so far. Hard-capped at 2."""

    needs_escalation: bool
    """
    True if Synthesis detected a critical finding with low confidence
    AND escalation_count < MAX_ESCALATION_COUNT.
    """

    use_strict_semgrep: bool
    """
    Flag set by the Escalation node to tell Security Agent to re-run
    with --config=p/security-audit instead of --config=auto.
    """

    # ── Review outputs ────────────────────────
    review_summary: str
    """
    Human-readable paragraph for the top of the GitHub PR review.
    Includes overall verdict, top issues, and language coverage note.
    Generated by the Synthesis node via LLM.
    """

    github_comments: list[dict]
    """
    GitHub-API-ready comment objects from CommentMapper.
    Format: [{"path": str, "line": int, "side": str, "body": str}, ...]
    """

    review_body: str
    """
    Full review body posted to GitHub: review_summary plus a section listing
    findings that could not be attached inline (no line, or outside the diff).
    Built by CommentMapper.
    """

    posted_review_url: str | None
    """URL of the posted GitHub review, or None if posting failed."""

    dry_run: bool
    """True if no GITHUB_TOKEN was set and the review was only logged, not posted."""
