"""
tools/github_client.py — GitHub API wrapper.

Split into two clear responsibilities:
  1. Read operations (PyGithub) — fetching PR metadata, files, and file contents.
  2. Write operations (raw requests) — posting the PR review with inline comments.

The write path uses raw requests deliberately (not PyGithub) so the exact
payload structure (commit_id, event, comments list) is explicit and
inspectable in this file rather than hidden inside PyGithub internals.
This also makes the diff-position mapping logic easy to audit.

FREE TIER NOTE:
  GitHub's unauthenticated API rate limit is 60 req/hour.
  Authenticated (PAT) limit is 5,000 req/hour.
  A large PR with many files + full content fetches may make ~50–100 requests.
  Always set GITHUB_TOKEN in .env to avoid hitting the unauthenticated limit.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from urllib.parse import urlparse

import requests
from github import Auth, Github, GithubException

from prpilot.config import GITHUB_REVIEW_BODY_MAX_CHARS

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# GitHub client factory
# ──────────────────────────────────────────────

def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """
    Extract (owner, repo, pr_number) from a GitHub PR URL.

    Accepts:
        https://github.com/owner/repo/pull/42
        https://github.com/owner/repo/pull/42/files

    Raises:
        ValueError if the URL doesn't match the expected pattern.
    """
    parsed = urlparse(pr_url.strip())
    if parsed.netloc not in ("github.com", "www.github.com"):
        raise ValueError(f"Not a GitHub URL: {pr_url}")

    match = re.match(r"^/([^/]+)/([^/]+)/pull/(\d+)", parsed.path)
    if not match:
        raise ValueError(
            f"Could not parse PR URL: {pr_url}\n"
            "Expected format: https://github.com/owner/repo/pull/NUMBER"
        )

    owner, repo, pr_number_str = match.groups()
    return owner, repo, int(pr_number_str)


@functools.lru_cache(maxsize=1)
def _get_github_client() -> Github:
    """Return an authenticated PyGithub client (or unauthenticated if no token). Cached."""
    token = os.getenv("GITHUB_TOKEN")
    if token:
        return Github(auth=Auth.Token(token))
    else:
        logger.warning(
            "[github_client] GITHUB_TOKEN not set — using unauthenticated API "
            "(60 req/hour limit). Set GITHUB_TOKEN in .env for full access."
        )
        return Github()


@functools.lru_cache(maxsize=32)
def _get_repo(full_name: str):
    """Return a cached PyGithub Repository object (avoids one API call per file fetch)."""
    return _get_github_client().get_repo(full_name)


# ──────────────────────────────────────────────
# PR metadata + file fetching (read, via PyGithub)
# ──────────────────────────────────────────────

def fetch_pr_files(owner: str, repo: str, pr_number: int) -> tuple[str, list[dict]]:
    """
    Fetch the list of changed files for a PR plus the HEAD commit SHA.

    Args:
        owner:      GitHub repo owner (org or user).
        repo:       GitHub repo name.
        pr_number:  PR number (integer).

    Returns:
        (commit_id, raw_files) where:
          - commit_id: HEAD commit SHA of the PR branch (needed for posting review)
          - raw_files: list of dicts with keys:
              filename, status, patch (may be absent for binary/large files),
              sha, raw_url, additions, deletions, changes

    Raises:
        GithubException: if the repo/PR is not found or access is denied.
    """
    logger.info("[github_client] Fetching PR %s/%s#%d", owner, repo, pr_number)

    gh_repo = _get_repo(f"{owner}/{repo}")
    pull = gh_repo.get_pull(pr_number)

    commit_id = pull.head.sha
    logger.info("[github_client] HEAD commit: %s", commit_id)

    files = pull.get_files()
    raw_files: list[dict] = []

    for f in files:
        file_dict = {
            "filename": f.filename,
            "status": f.status,          # 'added', 'removed', 'modified', 'renamed'
            "sha": f.sha,
            "additions": f.additions,
            "deletions": f.deletions,
            "changes": f.changes,
            "raw_url": f.raw_url,
            # patch is None for binary files or files where the diff is too large
            # (GitHub caps diff output at ~20KB per file).
            # Callers MUST handle patch=None gracefully.
            "patch": f.patch,
        }
        raw_files.append(file_dict)
        if f.patch is None:
            logger.debug(
                "[github_client] %s has no patch (binary or too large)", f.filename
            )

    logger.info("[github_client] Found %d changed files", len(raw_files))
    return commit_id, raw_files


def fetch_file_content(owner: str, repo: str, file_path: str, ref: str) -> str | None:
    """
    Fetch the full decoded content of a file at a specific commit/branch ref.

    Used by agents that need more context than the diff alone provides
    (e.g., Security Agent fetching surrounding code for an ambiguous finding).

    Args:
        owner:     GitHub repo owner.
        repo:      GitHub repo name.
        file_path: Repo-relative path to the file.
        ref:       Commit SHA or branch name.

    Returns:
        Decoded UTF-8 file content as a string, or None on any error
        (file not found, encoding error, file too large for GitHub API).
    """
    try:
        gh_repo = _get_repo(f"{owner}/{repo}")
        contents = gh_repo.get_contents(file_path, ref=ref)

        # GitHub API caps file content at 1MB via the contents endpoint.
        # For larger files, content is empty and we must use the blob API.
        if isinstance(contents, list):
            # Should not happen for a file path, but guard anyway
            logger.warning("[github_client] Path %s returned a list (directory?)", file_path)
            return None

        if contents.encoding == "none" or contents.content is None:
            logger.warning(
                "[github_client] %s is too large for the contents API (>1MB) — skipping full fetch",
                file_path,
            )
            return None

        decoded = contents.decoded_content.decode("utf-8", errors="replace")
        logger.debug("[github_client] Fetched %s (%d chars)", file_path, len(decoded))
        return decoded

    except GithubException as e:
        logger.warning(
            "[github_client] Could not fetch %s@%s: %s %s",
            file_path, ref, e.status, e.data,
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning("[github_client] Unexpected error fetching %s: %s", file_path, e)
        return None


# ──────────────────────────────────────────────
# Review posting (write, via raw requests)
# ──────────────────────────────────────────────

def post_pull_request_review(
    owner: str,
    repo: str,
    pr_number: int,
    commit_id: str,
    body: str,
    comments: list[dict],
) -> str | None:
    """
    Post a PR review with inline comments using the GitHub REST API directly.

    We use raw requests (not PyGithub) here so the exact payload is explicit
    and inspectable — the comment position mapping is a critical correctness
    concern and must not be hidden inside library abstractions.

    IMPORTANT: Always posts with event='COMMENT' (neutral review).
    Never uses 'APPROVE' or 'REQUEST_CHANGES' — an autonomous agent should
    not have unilateral merge-gating authority.

    Args:
        owner:      GitHub repo owner.
        repo:       GitHub repo name.
        pr_number:  PR number (integer).
        commit_id:  HEAD commit SHA — must match the current PR head or GitHub rejects.
        body:       Review summary text (top of the review).
        comments:   List of inline comment dicts. Each must have:
                      path (str), line (int), side ('LEFT'|'RIGHT'), body (str)
                    GitHub will reject comments on lines not in the diff.
                    Run through CommentMapper first to ensure validity.

    Returns:
        The URL of the posted review, or None if posting failed.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.error(
            "[github_client] GITHUB_TOKEN not set — cannot post review. "
            "Add GITHUB_TOKEN to .env."
        )
        return None

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    body = truncate_review_body(body)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    payload = {
        "commit_id": commit_id,
        "body": body,
        "event": "COMMENT",  # NEVER 'APPROVE' or 'REQUEST_CHANGES'
        "comments": comments,
    }

    logger.info(
        "[github_client] POSTing review to %s (%d inline comments)", url, len(comments)
    )

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)

        if response.status_code == 200:
            review_data = response.json()
            review_url = review_data.get("html_url")
            logger.info("[github_client] Review posted successfully: %s", review_url)
            return review_url

        # GitHub returns 422 Unprocessable Entity when individual comments
        # have invalid positions. Try posting without inline comments as fallback.
        elif response.status_code == 422:
            logger.warning(
                "[github_client] 422 Unprocessable Entity — one or more comment "
                "positions are invalid. Retrying with summary-only (no inline comments)."
            )
            return _post_review_summary_only(url, headers, commit_id, body)

        else:
            logger.error(
                "[github_client] Failed to post review: HTTP %d — %s",
                response.status_code,
                response.text[:500],
            )
            return None

    except requests.RequestException as e:
        logger.error("[github_client] Network error posting review: %s", e)
        return None


def truncate_review_body(body: str) -> str:
    """GitHub rejects review bodies over 65,536 characters; trim with a visible marker."""
    if len(body) <= GITHUB_REVIEW_BODY_MAX_CHARS:
        return body
    marker = "\n\n_…review truncated (GitHub size limit). Run PRPilot with --output-json for the full report._"
    return body[: GITHUB_REVIEW_BODY_MAX_CHARS - len(marker)] + marker


def _post_review_summary_only(
    url: str, headers: dict, commit_id: str, body: str
) -> str | None:
    """
    Fallback: post the review body only (no inline comments) when the full
    payload was rejected due to invalid comment positions.
    """
    payload = {
        "commit_id": commit_id,
        "body": body,
        "event": "COMMENT",
        "comments": [],
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            review_url = response.json().get("html_url")
            logger.info(
                "[github_client] Summary-only review posted: %s", review_url
            )
            return review_url
        else:
            logger.error(
                "[github_client] Summary-only review also failed: HTTP %d",
                response.status_code,
            )
            return None
    except requests.RequestException as e:
        logger.error("[github_client] Network error on summary-only fallback: %s", e)
        return None
