"""
nodes/ingestion.py — Ingestion Node.

Fetches PR metadata, changed files, and the HEAD commit ID from GitHub,
parses the patch strings into ParsedHunk objects, and materialises a local
checkout of the PR head (the workspace) for the static analysis tools.
"""

from __future__ import annotations

import logging

from prpilot.state import ReviewState
from prpilot.tools.diff_parser import parse_all_patches
from prpilot.tools.github_client import fetch_pr_files, parse_pr_url
from prpilot.tools.workspace import create_workspace

logger = logging.getLogger(__name__)


def ingestion_node(state: ReviewState) -> dict:
    """
    Ingest PR metadata and files from GitHub.

    Fetches the list of changed files and HEAD commit SHA using PyGithub,
    parses each unidiff patch into ParsedHunks for comment line validation,
    and downloads the repository at the HEAD commit into a temp workspace.
    """
    owner = state.get("owner")
    repo = state.get("repo")
    pr_number = state.get("pr_number")
    pr_url = state.get("pr_url")

    # If identifiers are missing from the state (e.g. LangGraph Studio input),
    # parse them from pr_url.
    if (not owner or not repo or not pr_number) and pr_url:
        try:
            owner, repo, pr_number = parse_pr_url(pr_url)
            logger.info(
                "[ingestion] Parsed identifiers from URL: owner=%s, repo=%s, pr_number=%d",
                owner,
                repo,
                pr_number,
            )
        except ValueError as e:
            logger.warning("[ingestion] Failed to parse pr_url '%s': %s", pr_url, e)

    if not owner or not repo or not pr_number:
        raise ValueError(
            f"Missing required PR identifiers in state: owner={owner}, repo={repo}, pr_number={pr_number}. "
            f"Provide a valid 'pr_url' (e.g., https://github.com/owner/repo/pull/1)"
        )

    logger.info("[ingestion] Ingesting %s/%s#%d", owner, repo, pr_number)

    # 1. Fetch files and commit ID from GitHub API
    commit_id, raw_files = fetch_pr_files(owner, repo, pr_number)

    # 2. Parse all patch fields into ParsedHunk structures
    parsed_hunks = parse_all_patches(raw_files)

    # 3. Local checkout of the PR head for Semgrep/Ruff/ESLint/go vet
    workspace_dir = create_workspace(owner, repo, commit_id, raw_files)

    logger.info(
        "[ingestion] Ingested %d files (parsed %d hunks) at commit %s, workspace=%s",
        len(raw_files),
        len(parsed_hunks),
        commit_id,
        workspace_dir,
    )

    return {
        "owner": owner,
        "repo": repo,
        "pr_number": pr_number,
        "commit_id": commit_id,
        "raw_files": raw_files,
        "parsed_hunks": parsed_hunks,
        "workspace_dir": workspace_dir,
    }
