"""
scoring.py — Match findings against a case's answer key.

A finding matches an expected bug when it is on the same file, in the same
category, and within LINE_TOLERANCE lines (a finding with no line matches any
line in that file). Each bug is matched by its closest finding. Extra findings
that match an already-caught bug are counted as duplicates, not false alarms.

Only SCORED_CATEGORIES count. Style findings are reported but not scored: an
answer key can't list every style nit, so counting them as false alarms would
measure the answer key, not the reviewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from benchmark.cases import ExpectedBug

LINE_TOLERANCE = 3
SCORED_CATEGORIES = ("security", "performance", "test_coverage")


@dataclass
class CaseScore:
    bugs: int = 0
    caught: int = 0
    false_alarms: int = 0
    duplicates: int = 0
    unscored: int = 0
    missed: list[ExpectedBug] = field(default_factory=list)
    false_alarm_findings: list = field(default_factory=list)


def _matches(finding, bug: ExpectedBug, tolerance: int) -> bool:
    if finding.file != bug.file or finding.category != bug.category:
        return False
    return finding.line is None or abs(finding.line - bug.line) <= tolerance


def _distance(finding, bug: ExpectedBug) -> int:
    return 0 if finding.line is None else abs(finding.line - bug.line)


def score_case(findings: list, bugs: list[ExpectedBug], tolerance: int = LINE_TOLERANCE) -> CaseScore:
    score = CaseScore(bugs=len(bugs))
    scored = [f for f in findings if f.category in SCORED_CATEGORIES]
    score.unscored = len(findings) - len(scored)

    used: set[int] = set()
    for bug in bugs:
        candidates = [i for i, f in enumerate(scored) if i not in used and _matches(f, bug, tolerance)]
        if candidates:
            used.add(min(candidates, key=lambda i: _distance(scored[i], bug)))
            score.caught += 1
        else:
            score.missed.append(bug)

    for i, f in enumerate(scored):
        if i in used:
            continue
        if any(_matches(f, bug, tolerance) for bug in bugs):
            score.duplicates += 1
        else:
            score.false_alarms += 1
            score.false_alarm_findings.append(f)
    return score


@dataclass
class Totals:
    bugs: int = 0
    caught: int = 0
    false_alarms: int = 0
    clean_cases: int = 0
    clean_cases_with_alarms: int = 0

    def add(self, score: CaseScore) -> None:
        self.bugs += score.bugs
        self.caught += score.caught
        self.false_alarms += score.false_alarms
        if score.bugs == 0:
            self.clean_cases += 1
            self.clean_cases_with_alarms += score.false_alarms > 0

    @property
    def recall(self) -> float:
        return self.caught / self.bugs if self.bugs else 0.0

    @property
    def precision(self) -> float:
        reported = self.caught + self.false_alarms
        return self.caught / reported if reported else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0
