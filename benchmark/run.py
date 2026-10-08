"""
run.py — Run PRPilot on the benchmark cases and score it.

    python -m benchmark.run                      # all cases
    python -m benchmark.run --cases py-sql-injection py-clean-refactor
    python -m benchmark.run --output results.json

Only ingestion (GitHub fetch) and posting are replaced; triage, the agents,
Semgrep/Ruff, escalation and synthesis all run for real, including LLM calls.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from unittest.mock import patch

from dotenv import load_dotenv

load_dotenv()

from langchain_core.callbacks import get_usage_metadata_callback  # noqa: E402

from benchmark.cases import Case, load_cases  # noqa: E402
from benchmark.scoring import LINE_TOLERANCE, SCORED_CATEGORIES, CaseScore, Totals, score_case  # noqa: E402
from prpilot import graph as graph_module  # noqa: E402
from prpilot.cli import build_initial_state, to_serializable  # noqa: E402
from prpilot.tools.diff_parser import parse_all_patches  # noqa: E402

logger = logging.getLogger("benchmark")

BENCH_OWNER = "benchmark"


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


def run_case(app, case: Case) -> tuple[dict, float, dict]:
    state = build_initial_state(f"https://github.com/{BENCH_OWNER}/{case.name}/pull/1")
    started = time.perf_counter()
    with get_usage_metadata_callback() as usage_cb:
        final_state = app.invoke(state)
    elapsed = time.perf_counter() - started

    tokens = Counter()
    for usage in usage_cb.usage_metadata.values():
        tokens["input"] += usage.get("input_tokens", 0)
        tokens["output"] += usage.get("output_tokens", 0)
    return final_state, elapsed, dict(tokens)


def _fmt_finding(f) -> str:
    return f"{f.category} {f.file}:{f.line} [{f.source_tool}] {' '.join(f.message.split())[:90]}"


def print_report(rows: list[tuple[Case, CaseScore, float, dict]], totals: Totals) -> None:
    print()
    print(f"{'case':<28} {'bugs':>4} {'caught':>6} {'false':>5} {'dup':>4} {'style':>5} {'secs':>6} {'tokens':>7}")
    print("-" * 70)
    for case, score, secs, tokens in rows:
        print(f"{case.name:<28} {score.bugs:>4} {score.caught:>6} {score.false_alarms:>5} "
              f"{score.duplicates:>4} {score.unscored:>5} {secs:>6.1f} {sum(tokens.values()):>7}")
    print("-" * 70)

    for case, score, _, _ in rows:
        for bug in score.missed:
            print(f"MISSED       {case.name}: {bug.category} {bug.file}:{bug.line} — {bug.note}")
        for f in score.false_alarm_findings:
            print(f"FALSE ALARM  {case.name}: {_fmt_finding(f)}")

    by_cat_bugs, by_cat_missed = Counter(), Counter()
    for case, score, _, _ in rows:
        by_cat_bugs.update(b.category for b in case.bugs)
        by_cat_missed.update(b.category for b in score.missed)

    total_secs = sum(r[2] for r in rows)
    total_tokens = sum(sum(r[3].values()) for r in rows)
    print()
    print(f"Recall (bugs caught):        {totals.caught}/{totals.bugs} = {totals.recall:.0%}")
    print(f"Precision (comments right):  {totals.precision:.0%}   ({totals.false_alarms} false alarms)")
    print(f"F1:                          {totals.f1:.2f}")
    print(f"Clean PRs with any alarm:    {totals.clean_cases_with_alarms}/{totals.clean_cases}")
    for cat in SCORED_CATEGORIES:
        if by_cat_bugs[cat]:
            caught = by_cat_bugs[cat] - by_cat_missed[cat]
            print(f"  {cat:<26} {caught}/{by_cat_bugs[cat]} caught")
    print(f"Avg time per PR:             {total_secs / len(rows):.1f}s")
    print(f"Avg tokens per PR:           {total_tokens / len(rows):,.0f}")
    print(f"(match = same file + category, line within ±{LINE_TOLERANCE}; style findings not scored)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the PRPilot benchmark.")
    parser.add_argument("--cases", nargs="*", help="Case names to run (default: all)")
    parser.add_argument("--output", help="Write per-case results as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cases = load_cases(args.cases)
    app = build_benchmark_graph({c.name: c for c in cases})

    rows, totals, results = [], Totals(), []
    for case in cases:
        print(f"Running {case.name} ...", flush=True)
        final_state, secs, tokens = run_case(app, case)
        findings = final_state.get("all_findings", [])
        score = score_case(findings, case.bugs)
        totals.add(score)
        rows.append((case, score, secs, tokens))
        results.append({
            "case": case.name,
            "bugs": score.bugs,
            "caught": score.caught,
            "false_alarms": score.false_alarms,
            "duplicates": score.duplicates,
            "seconds": round(secs, 2),
            "tokens": tokens,
            "findings": to_serializable({"all_findings": findings})["all_findings"],
        })

    print_report(rows, totals)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump({
                "run_at": datetime.now(UTC).isoformat(),
                "model": os.getenv("LLM_MODEL") or os.getenv("LLM_PROVIDER", "groq"),
                "recall": totals.recall,
                "precision": totals.precision,
                "f1": totals.f1,
                "cases": results,
            }, f, indent=2)
        print(f"\nWrote {args.output}")


if __name__ == "__main__":
    main()
