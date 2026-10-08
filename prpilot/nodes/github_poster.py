"""
nodes/github_poster.py — GitHub Review Poster Node.

Posts the review summary and inline comments to the GitHub PR using
the REST API client wrapper. Handles dry-run fallback if GITHUB_TOKEN is missing.
"""

from __future__ import annotations

import json
import logging
import os

from prpilot.state import ReviewState
from prpilot.tools.github_client import post_pull_request_review

logger = logging.getLogger(__name__)


def github_poster_node(state: ReviewState) -> dict:
    """
    GitHub Review Poster node.

    Calls post_pull_request_review to submit the summary review body
    and all validated inline comments. If GITHUB_TOKEN is not present,
    performs a dry run by logging the payload and returns a placeholder URL.
    """
    owner = state.get("owner", "unknown")
    repo = state.get("repo", "unknown")
    pr_number = state.get("pr_number", 0)
    commit_id = state.get("commit_id", "unknown")
    summary = state.get("review_body") or state.get("review_summary", "No summary generated.")
    comments = state.get("github_comments", [])

    token = os.getenv("GITHUB_TOKEN")

    if not token:
        logger.warning(
            "[github_poster] GITHUB_TOKEN is not set in environment. "
            "Skipping real review submission and executing DRY RUN."
        )
        payload = {
            "commit_id": commit_id,
            "body": summary,
            "event": "COMMENT",
            "comments": comments,
        }
        logger.info(
            "[github_poster] DRY RUN would POST to: https://api.github.com/repos/%s/%s/pulls/%d/reviews",
            owner,
            repo,
            pr_number,
        )
        logger.info("[github_poster] DRY RUN payload summary: %d inline comments", len(comments))
        logger.info("[github_poster] DRY RUN payload (truncated):\n%s", json.dumps(payload, indent=2)[:1000])

        mock_review_url = f"https://github.com/{owner}/{repo}/pull/{pr_number}#pullrequestreview-DRYRUN"
        return {"posted_review_url": mock_review_url, "dry_run": True}

    logger.info(
        "[github_poster] Posting review to GitHub: %s/%s#%d (%d comments)",
        owner,
        repo,
        pr_number,
        len(comments),
    )

    review_url = post_pull_request_review(
        owner=owner,
        repo=repo,
        pr_number=pr_number,
        commit_id=commit_id,
        body=summary,
        comments=comments,
    )

    if review_url:
        logger.info("[github_poster] Review successfully posted! URL: %s", review_url)
    else:
        logger.error("[github_poster] Failed to post review.")

    return {"posted_review_url": review_url, "dry_run": False}

