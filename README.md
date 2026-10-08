# PRPilot — Multi-Agent GitHub PR Reviewer

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
| `LLM_MODEL` | Optional model override (defaults: `llama-3.3-70b-versatile`, `gemini-2.5-flash`) |
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

### Web UI

```bash
streamlit run app_streamlit.py
```

### LangGraph Studio

`langgraph.json` registers the graph as `prpilot`; run `langgraph dev` and start a thread with `{"pr_url": "https://github.com/owner/repo/pull/42"}`.

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
tests/                   pytest suite (offline; GitHub, tools and LLM are mocked)
```

---

## Tests

```bash
python -m pytest
```

The suite runs offline: subprocesses, GitHub and the LLM are mocked, while tree-sitter parsing, diff parsing, tarball extraction and the full LangGraph routing run for real.
