"""
cases.py — Load benchmark cases from disk.

A case is a folder:

    cases/<name>/
      before/          repo before the PR (may be empty or missing for an all-new PR)
      after/           repo after the PR
      expected.toml    answer key

expected.toml:

    description = "user input concatenated into SQL"

    [[bugs]]
    file = "app/db.py"
    line = 14
    category = "security"     # security | performance | style | test_coverage
    note = "f-string SQL query"

A case with no [[bugs]] is a clean PR: every finding on it is a false alarm.

The PR diff is computed from before/ and after/ in the same headerless format
as GitHub's `patch` field, so the graph sees exactly what real ingestion returns.
"""

from __future__ import annotations

import difflib
import os
import tomllib
from dataclasses import dataclass, field

CASES_DIR = os.path.join(os.path.dirname(__file__), "cases")

CATEGORIES = ("security", "performance", "style", "test_coverage")

_SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules"}


@dataclass(frozen=True)
class ExpectedBug:
    file: str
    line: int
    category: str
    note: str = ""


@dataclass
class Case:
    name: str
    path: str
    description: str
    raw_files: list[dict]
    bugs: list[ExpectedBug] = field(default_factory=list)

    @property
    def after_dir(self) -> str:
        return os.path.join(self.path, "after")


def _read_tree(root: str) -> dict[str, str]:
    """Map repo-relative path -> text for every file under root."""
    files: dict[str, str] = {}
    if not os.path.isdir(root):
        return files
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, encoding="utf-8") as f:
                files[rel] = f.read()
    return files


def make_patch(before: str, after: str) -> str:
    """Unified diff of two texts without the ---/+++ headers (GitHub `patch` format)."""
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=3)
    return "\n".join(list(lines)[2:])


def diff_trees(before: dict[str, str], after: dict[str, str]) -> list[dict]:
    """Build raw_files entries (as fetch_pr_files returns them) for every changed file."""
    raw_files: list[dict] = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        status = "added" if old is None else "removed" if new is None else "modified"
        patch = make_patch(old or "", new or "")
        additions = sum(1 for ln in patch.splitlines() if ln.startswith("+"))
        deletions = sum(1 for ln in patch.splitlines() if ln.startswith("-"))
        raw_files.append({
            "filename": path,
            "status": status,
            "patch": patch,
            "additions": additions,
            "deletions": deletions,
            "changes": additions + deletions,
        })
    return raw_files


def load_case(path: str) -> Case:
    name = os.path.basename(os.path.normpath(path))
    with open(os.path.join(path, "expected.toml"), "rb") as f:
        spec = tomllib.load(f)

    bugs = [ExpectedBug(**b) for b in spec.get("bugs", [])]
    for bug in bugs:
        if bug.category not in CATEGORIES:
            raise ValueError(f"{name}: unknown category {bug.category!r}")

    raw_files = diff_trees(_read_tree(os.path.join(path, "before")), _read_tree(os.path.join(path, "after")))
    if not raw_files:
        raise ValueError(f"{name}: before/ and after/ are identical")

    return Case(name=name, path=path, description=spec.get("description", ""), raw_files=raw_files, bugs=bugs)


def load_cases(names: list[str] | None = None, cases_dir: str = CASES_DIR) -> list[Case]:
    available = sorted(d for d in os.listdir(cases_dir) if os.path.isdir(os.path.join(cases_dir, d)))
    if names:
        missing = set(names) - set(available)
        if missing:
            raise ValueError(f"Unknown case(s): {', '.join(sorted(missing))}")
        available = [n for n in available if n in names]
    return [load_case(os.path.join(cases_dir, n)) for n in available]
