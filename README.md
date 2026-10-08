# PRPilot — Multi-Agent GitHub PR Reviewer

[![CI](https://github.com/YOUR-USERNAME/PRPilot/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR-USERNAME/PRPilot/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)

<!-- Demo: record a 30–60s run against a real PR, save it as docs/demo.gif, then uncomment:
![PRPilot reviewing a pull request](docs/demo.gif)
-->

PRPilot reviews GitHub Pull Requests with a team of specialised agents, orchestrated by **LangGraph** and powered by **LangChain**. It downloads the PR's code, runs real static analysis tools where they exist (Semgrep, Ruff, ESLint, go vet / staticcheck, tree-sitter), uses an LLM to explain and fill the gaps, ranks everything by severity, and posts a single review with inline comments to the PR.

---

## Key Features

1. **Parallel agents** — Security, Performance, Style and Test Coverage agents run concurrently (LangGraph `Send` fan-out / fan-in).
2. **Real tools first, LLM second** — every finding records whether it came from a static tool or LLM-only analysis (`used_fallback`); LLM-only findings are capped at medium confidence and ranked below tool findings.
3. **Local checkout of the PR** — the repository is downloaded at the PR head commit into a temp workspace, so tools see real files and repo config (pyproject, eslint config, go.mod). Extraction is hardened against path traversal and symlinks; the workspace is deleted after the run.
4. **Polyglot** — Python, JavaScript, TypeScript/TSX and Go have full support; any other language gets an LLM review. All per-language logic lives in `prpilot/language_profiles.py`.
5. **Accurate inline comments** — diffs and files are sent to the LLM with real line numbers; only findings on lines inside the diff are posted inline. Everything else is listed under *Other findings* in the review body, so nothing is lost.
6. **Escalation loop** — a critical, low-confidence security finding triggers a stricter Semgrep re-run (`auto` + `p/security-audit`), at most twice.
7. **Safe by default** — reviews are always posted as `COMMENT` (never approve / request changes); LLM prompts treat PR content as untrusted data; tools that execute repository code are opt-in.

---

## Architecture

```
                 ┌────────────────────────────┐
                 │ Ingestion                  │  PR files + diff hunks + workspace checkout
                 └─────────────┬──────────────┘
                               ↓
                 ┌────────────────────────────┐
                 │ Triage                     │  languages, file types, which agents to run
                 └──┬──────┬──────┬──────┬────┘
   (parallel)       ↓      ↓      ↓      ↓          docs/config-only PR ─┐
               Security  Perf   Style  Coverage                          │
                    └──────┴──┬───┴──────┘                               │
                              ↓  ←───────────────────────────────────────┘
                 ┌────────────────────────────┐
                 │ Synthesis                  │  dedupe + rank + LLM summary
                 └─────────────┬──────────────┘
          critical/low security? ──yes──→ Escalation (≤2x) ──→ Security ──→ Synthesis
                               ↓ no
                 Comment Mapper → GitHub Poster → Cleanup
```

| Agent | With tooling | Fallback |
|---|---|---|
| Security | Semgrep (+ LLM explanation, fix and confidence per match) | LLM review of the diff |
| Performance | tree-sitter: nested loops, I/O calls inside loops → LLM verification | LLM review of the diff |
| Style | Ruff (Python); ESLint / go vet + staticcheck (trusted repos only) | LLM review of the diff |
| Test Coverage | tree-sitter finds functions the PR touches; checks references in the repo's test files | LLM review of the diff |

## Design decisions

**Real tools first, LLM second.** Static analysers are deterministic and precise about *where* a problem is, but noisy and terse. LLMs explain well but can invent issues and line numbers. So each agent runs the tool first and uses the LLM to explain matches, propose a fix and rate confidence; the LLM reviews the diff on its own only when no tool covers that language or check. Every finding records which path produced it (`used_fallback`), and LLM-only findings are capped at medium confidence and ranked below tool findings, so a reviewer can tell evidence from opinion.

**The escalation loop stops after two re-runs.** A critical security finding with low confidence is the one case where getting it wrong is expensive either way: a false alarm erodes trust, a missed vulnerability ships. So it triggers a re-scan with a stricter Semgrep ruleset (`auto` + `p/security-audit`). It is capped at `MAX_ESCALATION_COUNT = 2` because a third pass with the same rules rarely changes the answer but always costs time and LLM tokens, and an uncapped loop is a liability in an automated pipeline. If the cap is hit, a warning is logged and the finding is still posted rather than silently dropped. Escalation is also skipped entirely when Semgrep can't run, since the re-run would just repeat the same LLM review.

**Reviews are never posted as "Approve" or "Request changes".** Every review is a neutral `COMMENT`. An automated reviewer can be wrong, and PR content is attacker-controlled input (a PR can contain text written to manipulate an LLM). Letting the bot approve would turn a prompt injection into a merged PR; letting it block would make its false positives stop real work. Merge decisions stay with humans, and PRPilot's job is to make that decision faster and better informed.

**Repository code is never executed by default.** ESLint configs and Go builds can run arbitrary code from the reviewed repo, so those tools only run when `PRPILOT_TRUST_REPO_CODE=true`. Ruff and Semgrep only read files and always run. The downloaded workspace is extracted with path-traversal and symlink checks, and deleted after every run.

---

## Installation

Requires **Python 3.11+**.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[tools,ui,dev]"   # tools = ruff + semgrep, ui = streamlit
```

Optional extras: `.[gemini]` for the Gemini provider. For JS/TS linting install Node.js (`npx`); for Go linting install the Go toolchain (and optionally `staticcheck`).

### Configuration

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Read the PR and post the review. Fine-grained token with *Pull requests: read & write* and *Contents: read*. Leave empty for a **dry run** (review logged, not posted). |
| `LLM_PROVIDER` | `groq` (default) or `gemini` |
| `GROQ_API_KEY` / `GOOGLE_API_KEY` | Key for the chosen provider |
| `LLM_MODEL` | Optional model override (defaults: `openai/gpt-oss-120b`, `gemini-2.5-flash`) |
| `PRPILOT_TRUST_REPO_CODE` | `true` to run ESLint and go vet/staticcheck. **These execute code from the reviewed repository** (`eslint.config.js`, Go builds) — enable only for repositories you trust. Default `false`: those files get an LLM style review. Ruff and Semgrep only read files and always run. |

---

## Usage

```bash
prpilot --pr-url https://github.com/owner/repo/pull/42
# or
python -m prpilot --pr-url https://github.com/owner/repo/pull/42
```

Options:
- `--output-json FILE` — write the full final state (all findings, summary, comments) as JSON.
- `-v / --verbose` — debug logging.

Exit codes: `0` review posted (or dry run), `1` invalid input, `2` review could not be posted.

### Docker

```bash
docker build -t prpilot .
docker run --rm --env-file .env prpilot --pr-url https://github.com/owner/repo/pull/42
# Web UI on http://localhost:8501
docker run --rm --env-file .env -p 8501:8501 --entrypoint streamlit prpilot \
  run app_streamlit.py --server.address=0.0.0.0
```

The image includes Ruff and Semgrep. Node.js and Go are not included, so JS/TS and Go files get an LLM style review.

### Web UI

```bash
streamlit run app_streamlit.py
```

### LangGraph Studio

`langgraph.json` registers the graph as `prpilot`; run `langgraph dev` and start a thread with `{"pr_url": "https://github.com/owner/repo/pull/42"}`.

---

## Benchmark

`benchmark/` measures review quality on PRs with a known answer key. Each case in `benchmark/cases/` holds the repo `before/` and `after/` the PR plus an `expected.toml` listing the real bugs; clean cases (no bugs) measure false alarms. Only GitHub fetching and posting are replaced; every agent, tool and LLM call runs for real.

```bash
python -m benchmark.run                          # all cases
python -m benchmark.run --cases py-sql-injection --output benchmark/results/run.json
```

It reports bugs caught (recall), how many comments point at real bugs (precision), false alarms on clean PRs, and time and tokens per PR. A finding counts when it has the same file and category as a known bug, within ±3 lines. Style findings are reported but not scored.

---

## Project layout

```
prpilot/
  cli.py                 CLI entrypoint (`prpilot` command)
  graph.py               LangGraph topology and routing
  state.py               ReviewState, Finding, ParsedHunk
  config.py              severity rubric, escalation rules, timeouts, limits
  language_profiles.py   per-language tool dispatch (single source of truth)
  llm.py                 LLM factory + shared prompt helpers
  nodes/                 ingestion, triage, 4 agents, synthesis, escalation,
                         comment_mapper, github_poster, cleanup
  tools/                 workspace checkout, diff parser, AST utils, tool runners,
                         GitHub client, LLM fallback reviews
app_streamlit.py         Streamlit UI
benchmark/               review-quality benchmark (cases, runner, scoring)
tests/                   pytest suite (offline; GitHub, tools and LLM are mocked)
```

---

## Tests

```bash
python -m pytest
ruff check .
```

The suite runs offline: subprocesses, GitHub and the LLM are mocked, while tree-sitter parsing, diff parsing, tarball extraction and the full LangGraph routing run for real.
