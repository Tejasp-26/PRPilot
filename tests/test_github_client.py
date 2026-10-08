"""
tests/test_github_client.py — Unit tests for pure helpers in tools/github_client.py.
"""

from __future__ import annotations

import pytest

from prpilot.config import GITHUB_REVIEW_BODY_MAX_CHARS
from prpilot.tools.github_client import parse_pr_url, truncate_review_body


def test_parse_pr_url() -> None:
    assert parse_pr_url("https://github.com/o/r/pull/42") == ("o", "r", 42)
    assert parse_pr_url("https://www.github.com/o/r/pull/7/files ") == ("o", "r", 7)
    with pytest.raises(ValueError):
        parse_pr_url("https://gitlab.com/o/r/pull/1")
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/o/r/issues/1")


def test_truncate_review_body() -> None:
    assert truncate_review_body("short") == "short"
    long_body = "x" * (GITHUB_REVIEW_BODY_MAX_CHARS + 500)
    out = truncate_review_body(long_body)
    assert len(out) == GITHUB_REVIEW_BODY_MAX_CHARS
    assert out.endswith("--output-json for the full report._")
