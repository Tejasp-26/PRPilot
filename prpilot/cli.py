"""
prpilot/cli.py — Command-line entrypoint for PRPilot.

Usage:
    prpilot --pr-url https://github.com/owner/repo/pull/42
    python -m prpilot --pr-url ...

The PR URL is parsed to extract owner, repo, and PR number.
All secrets must be in .env (see .env.example).

LangSmith tracing (optional):
    Set LANGCHAIN_TRACING_V2=true and LANGCHAIN_API_KEY in .env.
    No code changes required — LangChain reads these env vars automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

# Load .env before importing prpilot modules that read env vars at import time
load_dotenv()

from prpilot import __version__  # noqa: E402
from prpilot.tools.github_client import parse_pr_url  # noqa: E402
from prpilot.tools.workspace import cleanup_workspace  # noqa: E402

logger = logging.getLogger("prpilot")


def build_initial_state(pr_url: str) -> dict:
    """Build the initial ReviewState for a PR URL (raises ValueError if invalid)."""
    owner, repo, pr_number = parse_pr_url(pr_url)
    return {
        "pr_url": pr_url,
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        # Filled in by the Ingestion node:
        "commit_id": "",
        "raw_files": [],
        "parsed_hunks": [],
        "workspace_dir": None,
        # Triage outputs:
        "relevant_agents": [],
        "language_coverage_summary": "",
        "file_language_map": {},
        # Agent findings:
        "security_findings": [],
        "performance_findings": [],
        "style_findings": [],
        "coverage_findings": [],
        # Synthesis outputs:
        "all_findings": [],
        "escalation_count": 0,
        "needs_escalation": False,
        "use_strict_semgrep": False,
        "review_summary": "",
        "review_body": "",
        "github_comments": [],
        "posted_review_url": None,
        "dry_run": False,
    }


def run(pr_url: str) -> dict:
    """
    Build the initial state and invoke the compiled graph.

    Args:
        pr_url: Full GitHub PR URL.

    Returns:
        Final ReviewState dict after graph execution.
    """
    from prpilot.graph import app  # imported lazily so logging is configured first

    initial_state = build_initial_state(pr_url)

    logger.info("=" * 60)
    logger.info("PRPilot %s starting", __version__)
    logger.info("PR: %s", pr_url)
    logger.info("=" * 60)

    final_state: dict = {}
    workspace_dir = None
    try:
        # Stream full-state snapshots so the workspace path is known even if a
        # later node raises; the last snapshot is the final state.
        for snapshot in app.stream(initial_state, stream_mode="values"):
            final_state = snapshot
            workspace_dir = snapshot.get("workspace_dir") or workspace_dir
    finally:
        # The graph's cleanup node normally removes the workspace; this covers failures.
        cleanup_workspace(workspace_dir)

    logger.info("=" * 60)
    logger.info("PRPilot completed")
    logger.info("Total findings: %d", len(final_state.get("all_findings", [])))
    logger.info("Posted review URL: %s", final_state.get("posted_review_url"))
    logger.info("=" * 60)

    return final_state


def to_serializable(state: dict) -> dict:
    """Convert a final state (with Pydantic models) to JSON-safe data."""

    def convert(value):
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if isinstance(value, list):
            return [convert(v) for v in value]
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, set):
            return sorted(value)
        return value

    return {k: convert(v) for k, v in state.items() if k not in ("parsed_hunks", "raw_files")}


def print_final_state(state: dict) -> None:
    """Pretty-print the final state for verification."""
    print("\n" + "=" * 60)
    print("PRPILOT REVIEW SUMMARY")
    print("=" * 60)
    print(f"PR URL: {state.get('pr_url')}")
    print(f"Owner/Repo: {state.get('owner')}/{state.get('repo')}")
    print(f"PR Number: {state.get('pr_number')}")
    print(f"Commit ID: {state.get('commit_id')}")
    print(f"Escalation count: {state.get('escalation_count', 0)}")
    dry = " (DRY RUN — not posted)" if state.get("dry_run") else ""
    print(f"Posted review URL: {state.get('posted_review_url')}{dry}")
    print()
    print("Language coverage:")
    print(f"  {state.get('language_coverage_summary', 'N/A')}")
    print()
    print("Review summary:")
    print(f"  {state.get('review_summary', 'N/A')}")
    print()

    all_findings = state.get("all_findings", [])
    print(f"All findings ({len(all_findings)} total, ranked by severity):")
    for i, f in enumerate(all_findings, 1):
        fallback_marker = " [LLM-FALLBACK]" if f.used_fallback else ""
        print(
            f"  {i:2d}. [{f.severity.upper()}/{f.confidence}] {f.category} | "
            f"{f.file}:{f.line} | {f.source_tool}{fallback_marker}"
        )
        print(f"      {f.message[:80]}{'...' if len(f.message) > 80 else ''}")

    print()
    github_comments = state.get("github_comments", [])
    print(f"GitHub inline comments ({len(github_comments)}):")
    for c in github_comments:
        print(f"  {c['path']}:{c['line']} ({c['side']})")

    print("=" * 60)


def cli() -> None:
    """CLI entrypoint (registered as the `prpilot` console script)."""
    parser = argparse.ArgumentParser(
        prog="prpilot",
        description="PRPilot — multi-agent GitHub Pull Request reviewer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  prpilot --pr-url https://github.com/owner/repo/pull/42\n"
            "\n"
            "Environment variables (set in .env, see .env.example):\n"
            "  GITHUB_TOKEN             GitHub token (required to post; omit for a dry run)\n"
            "  GROQ_API_KEY             Groq API key for LLM calls\n"
            "  LLM_PROVIDER             'groq' (default) or 'gemini'\n"
            "  PRPILOT_TRUST_REPO_CODE  'true' to allow ESLint/go vet (they execute repo code)\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--pr-url",
        required=True,
        help="Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/42",
    )
    parser.add_argument(
        "--output-json",
        metavar="FILE",
        help="Write the full final state as JSON to this file (optional).",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        final_state = run(args.pr_url)
        print_final_state(final_state)

        if args.output_json:
            with open(args.output_json, "w") as fp:
                json.dump(to_serializable(final_state), fp, indent=2, default=str)
            logger.info("Final state written to: %s", args.output_json)

        if not final_state.get("posted_review_url"):
            sys.exit(2)  # review could not be posted

    except ValueError as e:
        logger.error("Invalid input: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(130)


if __name__ == "__main__":
    cli()
