"""
Tests for the benchmark harness: case loading, diff generation and scoring.
"""

from __future__ import annotations

from benchmark.cases import diff_trees, load_cases, make_patch
from benchmark.scoring import ExpectedBug, Totals, score_case
from prpilot.state import Finding
from prpilot.tools.diff_parser import parse_patch


def _finding(file="app.py", line=10, category="security") -> Finding:
    return Finding(file=file, line=line, category=category, severity="high", confidence="high",
                   message="m", source_tool="semgrep", language="python", used_fallback=False)


BUG = ExpectedBug(file="app.py", line=10, category="security")


def test_make_patch_matches_github_format() -> None:
    patch = make_patch("a\nb\n", "a\nb\nc\n")
    assert patch.startswith("@@")
    hunk = parse_patch(patch, "x.py")
    assert hunk.added_lines == [3]


def test_diff_trees_statuses() -> None:
    raw = diff_trees({"same.py": "x", "old.py": "x", "mod.py": "a"}, {"same.py": "x", "new.py": "y", "mod.py": "b"})
    assert {f["filename"]: f["status"] for f in raw} == {"mod.py": "modified", "new.py": "added", "old.py": "removed"}


def test_all_cases_load_and_bug_lines_are_in_the_diff() -> None:
    cases = load_cases()
    assert cases
    for case in cases:
        hunks = {f["filename"]: parse_patch(f["patch"], f["filename"]) for f in case.raw_files}
        for bug in case.bugs:
            assert bug.line in hunks[bug.file].added_lines, f"{case.name}: {bug.file}:{bug.line} not added"


def test_score_within_tolerance_counts_as_caught() -> None:
    score = score_case([_finding(line=12)], [BUG])
    assert (score.caught, score.false_alarms) == (1, 0)


def test_score_wrong_category_or_far_line_is_false_alarm_and_miss() -> None:
    score = score_case([_finding(category="performance"), _finding(line=30)], [BUG])
    assert (score.caught, score.false_alarms, len(score.missed)) == (0, 2, 1)


def test_score_second_finding_on_same_bug_is_duplicate() -> None:
    score = score_case([_finding(line=10), _finding(line=11)], [BUG])
    assert (score.caught, score.duplicates, score.false_alarms) == (1, 1, 0)


def test_style_findings_are_not_scored() -> None:
    score = score_case([_finding(category="style")], [])
    assert (score.false_alarms, score.unscored) == (0, 1)


def test_totals() -> None:
    totals = Totals()
    totals.add(score_case([_finding()], [BUG]))
    totals.add(score_case([_finding(file="clean.py")], []))
    assert (totals.recall, totals.precision, totals.clean_cases_with_alarms) == (1.0, 0.5, 1)
