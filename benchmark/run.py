"""
run.py — Run the benchmark cases through one or more reviewer setups and compare them.

    python -m benchmark.run                                  # default setups, all cases
    python -m benchmark.run --setups full --cases py-sql-injection
    python -m benchmark.run --output benchmark/results/run.json

Setups:
    plain-llm      one LLM call over the whole diff (the "just ask ChatGPT" baseline)
    llm-agents     PRPilot's agents with every static tool and the AST disabled
    full           PRPilot as shipped
    no-escalation  PRPilot with the escalation loop turned off

All setups use the same model. For the PRPilot setups only ingestion (GitHub
fetch) and posting are replaced; everything else runs for real.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()

from langchain_core.callbacks import get_usage_metadata_callback  # noqa: E402

from benchmark.baseline import baseline_review  # noqa: E402
from benchmark.cases import Case, load_cases  # noqa: E402
from benchmark.scoring import LINE_TOLERANCE, SCORED_CATEGORIES, CaseScore, Totals, score_case  # noqa: E402
from prpilot import graph as graph_module  # noqa: E402
from prpilot.cli import build_initial_state, to_serializable  # noqa: E402
from prpilot.tools.diff_parser import parse_all_patches  # noqa: E402

logger = logging.getLogger("benchmark")

BENCH_OWNER = "benchmark"
DEFAULT_SETUPS = ["plain-llm", "llm-agents", "full"]


# ──────────────────────────────────────────────
# Setups
# ──────────────────────────────────────────────

def build_benchmark_graph(cases: dict[str, Case]):
    """The real graph with ingestion reading from case folders and posting disabled."""

    def ingestion(state):
        case = cases[state["repo"]]
        # The cleanup node deletes the workspace, so give it a throwaway copy.
        workspace = tempfile.mkdtemp(prefix=f"prpilot-bench-{case.name}-")
        shutil.copytree(case.after_dir, workspace, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        return {
            "commit_id": "benchmark",
            "raw_files": case.raw_files,
            "parsed_hunks": parse_all_patches(case.raw_files),
            "workspace_dir": workspace,
        }

    def no_post(state):
        return {"posted_review_url": None, "dry_run": True}

    with patch.object(graph_module, "ingestion_node", ingestion), \
         patch.object(graph_module, "github_poster_node", no_post):
        return graph_module.build_graph()


# Each entry patches the agents' tool entry points to report "unavailable",
# which sends every agent down its LLM fallback path.
_NO_TOOLS = [
    ("prpilot.nodes.security_agent.run_semgrep", None),
    ("prpilot.nodes.style_agent.run_ruff", None),
    ("prpilot.nodes.style_agent.run_eslint", None),
    ("prpilot.nodes.style_agent.run_go_vet", None),
    ("prpilot.nodes.performance_agent.parse_file", None),
    ("prpilot.nodes.coverage_agent.parse_file", None),
    ("prpilot.nodes.synthesis.can_escalate", False),
]
_NO_ESCALATION = [("prpilot.nodes.synthesis.can_escalate", False)]


def _patched(targets: list[tuple[str, object]]) -> ExitStack:
    stack = ExitStack()
    for target, value in targets:
        stack.enter_context(patch(target, lambda *a, _v=value, **k: _v))
    return stack


def graph_setup(app, patches: list[tuple[str, object]]) -> Callable[[Case], list]:
    def review(case: Case) -> list:
        state = build_initial_state(f"https://github.com/{BENCH_OWNER}/{case.name}/pull/1")
        with _patched(patches):
            return app.invoke(state).get("all_findings", [])
    return review


def plain_llm_setup(case: Case) -> list:
    try:
        return baseline_review(case.raw_files)
    except Exception as e:  # one retry: structured output occasionally fails to parse
        logger.error("[baseline] attempt 1 failed for %s: %s", case.name, e)
        return baseline_review(case.raw_files)


# ──────────────────────────────────────────────
# Running and reporting
# ──────────────────────────────────────────────

class _ErrorCounter(logging.Handler):
    """Counts ERROR logs (failed LLM calls, tool crashes) during a run."""

    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.count = 0

    def emit(self, record):
        self.count += 1


@dataclass
class CaseResult:
    case: Case
    score: CaseScore
    findings: list
    seconds: float
    tokens: int
    errors: int


@dataclass
class SetupResult:
    name: str
    rows: list[CaseResult] = field(default_factory=list)
    totals: Totals = field(default_factory=Totals)

    @property
    def avg_seconds(self) -> float:
        return sum(r.seconds for r in self.rows) / len(self.rows)

    @property
    def avg_tokens(self) -> float:
        return sum(r.tokens for r in self.rows) / len(self.rows)

    @property
    def errors(self) -> int:
        return sum(r.errors for r in self.rows)


def run_one(review: Callable[[Case], list], case: Case) -> CaseResult:
    counter = _ErrorCounter()
    logging.getLogger().addHandler(counter)
    started = time.perf_counter()
    findings: list = []
    try:
        with get_usage_metadata_callback() as usage_cb:
            try:
                findings = review(case)
            except Exception as e:
                logger.error("[benchmark] %s crashed: %s", case.name, e)
        tokens = sum(u.get("input_tokens", 0) + u.get("output_tokens", 0)
                     for u in usage_cb.usage_metadata.values())
    finally:
        logging.getLogger().removeHandler(counter)
    return CaseResult(case, score_case(findings, case.bugs), findings,
                      time.perf_counter() - started, tokens, counter.count)


def _fmt_finding(f) -> str:
    return f"{f.category} {f.file}:{f.line} [{f.source_tool}] {' '.join(f.message.split())[:80]}"


def print_setup(result: SetupResult) -> None:
    print(f"\n== {result.name} ==")
    print(f"{'case':<28} {'bugs':>4} {'caught':>6} {'false':>5} {'dup':>4} {'style':>5} {'secs':>6} "
          f"{'tokens':>7} {'errors':>6}")
    for r in result.rows:
        s = r.score
        print(f"{r.case.name:<28} {s.bugs:>4} {s.caught:>6} {s.false_alarms:>5} {s.duplicates:>4} "
              f"{s.unscored:>5} {r.seconds:>6.1f} {r.tokens:>7} {r.errors:>6}")
    for r in result.rows:
        for bug in r.score.missed:
            print(f"  MISSED       {r.case.name}: {bug.category} {bug.file}:{bug.line} — {bug.note}")
        for f in r.score.false_alarm_findings:
            print(f"  FALSE ALARM  {r.case.name}: {_fmt_finding(f)}")


def print_comparison(results: list[SetupResult]) -> None:
    cats = [c for c in SCORED_CATEGORIES if any(b.category == c for r in results[0].rows for b in r.case.bugs)]
    header = (f"{'setup':<14} {'caught':>8} {'recall':>6} {'precis':>6} {'F1':>5} {'false':>5} "
              f"{'clean✗':>6} {'secs':>5} {'tokens':>7} {'errors':>6}")
    header += "".join(f" {c[:11]:>11}" for c in cats)
    print("\n" + header)
    print("-" * len(header))
    for res in results:
        t = res.totals
        line = (f"{res.name:<14} {t.caught:>3}/{t.bugs:<4} {t.recall:>6.0%} {t.precision:>6.0%} {t.f1:>5.2f} "
                f"{t.false_alarms:>5} {t.clean_cases_with_alarms:>2}/{t.clean_cases:<3} {res.avg_seconds:>5.1f} "
                f"{res.avg_tokens:>7,.0f} {res.errors:>6}")
        for c in cats:
            bugs = sum(1 for r in res.rows for b in r.case.bugs if b.category == c)
            missed = sum(1 for r in res.rows for b in r.score.missed if b.category == c)
            line += f" {f'{bugs - missed}/{bugs}':>11}"
        print(line)
    print(f"\nmatch = same file + category, line within ±{LINE_TOLERANCE}; style findings not scored. "
          "clean✗ = clean PRs with any false alarm. secs/tokens are per PR. errors = failed LLM/tool calls.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PRPilot benchmark.")
    parser.add_argument("--cases", nargs="*", help="Case names to run (default: all)")
    parser.add_argument("--setups", nargs="*", default=DEFAULT_SETUPS,
                        choices=["plain-llm", "llm-agents", "full", "no-escalation"])
    parser.add_argument("--output", help="Write all results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.ERROR,
                        format="%(levelname)s %(name)s: %(message)s")
    if not args.verbose:
        # Keep ERROR records flowing to _ErrorCounter, but don't print them.
        logging.getLogger().handlers[0].setLevel(logging.CRITICAL)

    cases = load_cases(args.cases)
    app = build_benchmark_graph({c.name: c for c in cases})
    setups: dict[str, Callable[[Case], list]] = {
        "plain-llm": plain_llm_setup,
        "llm-agents": graph_setup(app, _NO_TOOLS),
        "full": graph_setup(app, []),
        "no-escalation": graph_setup(app, _NO_ESCALATION),
    }

    results = []
    for name in args.setups:
        result = SetupResult(name)
        for case in cases:
            print(f"[{name}] {case.name} ...", flush=True)
            row = run_one(setups[name], case)
            result.rows.append(row)
            result.totals.add(row.score)
        results.append(result)

    for result in results:
        print_setup(result)
    print_comparison(results)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(),
                "model": os.getenv("LLM_MODEL") or os.getenv("LLM_PROVIDER", "groq"),
                "setups": {
                    res.name: {
                        "recall": res.totals.recall,
                        "precision": res.totals.precision,
                        "f1": res.totals.f1,
                        "false_alarms": res.totals.false_alarms,
                        "avg_seconds": res.avg_seconds,
                        "avg_tokens": res.avg_tokens,
                        "errors": res.errors,
                        "cases": [{
                            "case": r.case.name,
                            "caught": r.score.caught,
                            "bugs": r.score.bugs,
                            "false_alarms": r.score.false_alarms,
                            "seconds": round(r.seconds, 2),
                            "tokens": r.tokens,
                            "errors": r.errors,
                            "findings": to_serializable({"f": r.findings})["f"],
                        } for r in res.rows],
                    } for res in results
                },
            }, f, indent=2)
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
