"""
language_profiles.py — Single source of truth for per-language tool dispatch.

DESIGN PRINCIPLE:
    Every agent must call get_language_profile(file_path) rather than
    performing its own extension checks. Per-language logic lives here and
    ONLY here. Adding support for a new language = one new LanguageProfile
    entry + one new runner file, nothing else.

    When a file's language cannot be determined, get_language_profile()
    returns UNKNOWN_LANGUAGE_PROFILE — a sentinel value that all agents
    must handle explicitly. "Explicit is better than implicit."
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel


# ──────────────────────────────────────────────
# LanguageProfile model
# ──────────────────────────────────────────────

class LanguageProfile(BaseModel):
    """Describes tool support and conventions for a single programming language."""

    name: str
    """Canonical language identifier, e.g. 'python', 'javascript', 'unknown'."""

    extensions: list[str]
    """
    File extensions that map to this language (including the leading dot).
    Example: ['.ts', '.tsx'] for TypeScript.
    """

    tree_sitter_grammar: str | None
    """
    Grammar name for tree-sitter bindings, e.g. 'python', 'javascript'.
    None means tree-sitter AST parsing is unavailable for this language —
    agents MUST fall back to LLM-only reasoning over raw diff text.
    """

    linter: Literal["ruff", "eslint", "go_vet", "none"]
    """
    Which CLI linter runner to use for this language:
    - 'ruff'    → prpilot/tools/ruff_runner.py
    - 'eslint'  → prpilot/tools/eslint_runner.py
    - 'go_vet'  → prpilot/tools/go_vet_runner.py (+ staticcheck if available)
    - 'none'    → no linter; Style Agent uses LLM-fallback path
    All agents dispatching to a linter must handle the case where the tool
    is not installed on PATH — treat as linter='none' at runtime.
    """

    test_file_patterns: list[str]
    """
    Glob patterns for test files in this language's convention.
    Used by the Test Coverage Agent to locate test files in the repo.
    Example: ['test_*.py', '*_test.py'] for Python.
    Empty list = no known convention (unknown language).
    """

    io_call_keywords: list[str]
    """
    Substrings that identify I/O-looking calls in this language.
    Used by the Performance Agent's "I/O inside loop" heuristic.
    Language-specific because conventions differ: Python uses 'open'/'read',
    Go uses 'Query'/'Exec', JavaScript uses 'fetch'/'readFile'.
    Empty list = unknown language; heuristic is skipped.
    """


# ──────────────────────────────────────────────
# Language registry
# ──────────────────────────────────────────────

LANGUAGE_PROFILES: dict[str, LanguageProfile] = {
    "python": LanguageProfile(
        name="python",
        extensions=[".py", ".pyw"],
        tree_sitter_grammar="python",
        linter="ruff",
        test_file_patterns=["test_*.py", "*_test.py"],
        io_call_keywords=[
            "request", "requests", "query", "fetch", "open", "read",
            "write", "execute", "commit", "connect", "send", "recv",
            "urlopen", "get", "post", "put", "delete", "patch",
        ],
    ),

    "javascript": LanguageProfile(
        name="javascript",
        extensions=[".js", ".jsx", ".mjs", ".cjs"],
        tree_sitter_grammar="javascript",
        linter="eslint",
        test_file_patterns=["*.test.js", "*.spec.js", "*.test.jsx", "*.spec.jsx"],
        io_call_keywords=[
            "fetch", "axios", "query", "readFile", "writeFile", "request",
            "http", "https", "xhr", "XMLHttpRequest", "$.ajax", "$.get",
            "$.post", "superagent", "got", "node-fetch",
        ],
    ),

    "typescript": LanguageProfile(
        name="typescript",
        extensions=[".ts", ".tsx", ".mts", ".cts"],
        # tree-sitter-typescript exposes grammar names 'typescript' and 'tsx' separately.
        # We use 'typescript' as the base; tsx files get the same profile since
        # tree-sitter-typescript handles both. The ast_utils wrapper resolves .tsx correctly.
        tree_sitter_grammar="typescript",
        linter="eslint",
        test_file_patterns=["*.test.ts", "*.spec.ts", "*.test.tsx", "*.spec.tsx"],
        io_call_keywords=[
            "fetch", "axios", "query", "readFile", "writeFile", "request",
            "http", "https", "XMLHttpRequest", "superagent", "got",
            "prisma", "sequelize", "knex",  # common TS ORM I/O keywords
        ],
    ),

    "go": LanguageProfile(
        name="go",
        extensions=[".go"],
        tree_sitter_grammar="go",
        linter="go_vet",
        test_file_patterns=["*_test.go"],
        io_call_keywords=[
            "Query", "Exec", "QueryRow", "QueryContext", "ExecContext",
            "ReadFile", "WriteFile", "Open", "Create", "Get", "Post",
            "Do", "Send", "Dial", "Listen", "Accept",
        ],
    ),
}

# ──────────────────────────────────────────────
# Sentinel for unsupported / unrecognised languages
# ──────────────────────────────────────────────

UNKNOWN_LANGUAGE_PROFILE = LanguageProfile(
    name="unknown",
    extensions=[],
    tree_sitter_grammar=None,
    linter="none",
    test_file_patterns=[],
    io_call_keywords=[],
)
"""
Returned by get_language_profile() when no match is found.
All agents MUST handle this sentinel explicitly:
- Style Agent  → LLM-fallback path
- Security Agent → Semgrep still runs (multi-language), but add LLM supplement
- Performance Agent → LLM-only reasoning over raw diff
- Coverage Agent → confidence='low', used_fallback=True finding
Do NOT add language-specific code paths directly in agent files.
"""

# ──────────────────────────────────────────────
# Extension → language name index (built once at import time)
# ──────────────────────────────────────────────

_EXTENSION_INDEX: dict[str, str] = {}
for _lang_name, _profile in LANGUAGE_PROFILES.items():
    for _ext in _profile.extensions:
        _EXTENSION_INDEX[_ext.lower()] = _lang_name


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def get_language_profile(file_path: str) -> LanguageProfile:
    """
    Resolve the LanguageProfile for a given file path by its extension.

    Returns UNKNOWN_LANGUAGE_PROFILE if no matching language is found.

    This is the ONLY place extension-to-language mapping should happen.
    Agents must call this function, never check extensions directly.

    Args:
        file_path: Any path string — full path, relative path, or just a filename.
                   Only the extension is used.

    Returns:
        The matching LanguageProfile, or UNKNOWN_LANGUAGE_PROFILE.

    Examples:
        >>> get_language_profile("src/main.py").name
        'python'
        >>> get_language_profile("app/index.tsx").name
        'typescript'
        >>> get_language_profile("Makefile").name
        'unknown'
    """
    _, ext = os.path.splitext(file_path)
    lang_name = _EXTENSION_INDEX.get(ext.lower())
    if lang_name is None:
        return UNKNOWN_LANGUAGE_PROFILE
    return LANGUAGE_PROFILES[lang_name]


def get_all_supported_extensions() -> list[str]:
    """Return every file extension with full tool support (non-unknown profile)."""
    return list(_EXTENSION_INDEX.keys())


def describe_tool_support(profile: LanguageProfile) -> str:
    """
    Return a short human-readable description of tool support level for a language.
    Used by the Triage node when building language_coverage_summary.

    Examples:
        "full support (ruff + semgrep + tree-sitter)"
        "LLM-fallback only (no linter, no tree-sitter grammar)"
    """
    if profile.name == "unknown":
        return "LLM-fallback only (no linter, no tree-sitter grammar, semgrep may have limited coverage)"

    parts: list[str] = []

    # Linter
    linter_labels = {
        "ruff": "ruff",
        "eslint": "eslint",
        "go_vet": "go vet (+ staticcheck if available)",
        "none": "no linter → LLM-style-review",
    }
    parts.append(linter_labels[profile.linter])

    # Semgrep is always attempted (handles 30+ languages natively)
    parts.append("semgrep")

    # Tree-sitter
    if profile.tree_sitter_grammar:
        parts.append("tree-sitter")
    else:
        parts.append("no AST → LLM-perf-review")

    support_level = "full support" if profile.linter != "none" else "partial support"
    return f"{support_level} ({' + '.join(parts)})"
