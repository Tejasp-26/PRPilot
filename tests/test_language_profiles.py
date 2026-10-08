"""
tests/test_language_profiles.py — Unit tests for language_profiles.py.
"""

from __future__ import annotations

from prpilot.language_profiles import (
    UNKNOWN_LANGUAGE_PROFILE,
    describe_tool_support,
    get_all_supported_extensions,
    get_language_profile,
)


def test_get_language_profile_known() -> None:
    """Verify that known extensions resolve to correct profiles."""
    py_profile = get_language_profile("main.py")
    assert py_profile.name == "python"
    assert py_profile.linter == "ruff"
    assert "test_*.py" in py_profile.test_file_patterns

    js_profile = get_language_profile("/path/to/some/file.jsx")
    assert js_profile.name == "javascript"
    assert js_profile.linter == "eslint"

    ts_profile = get_language_profile("component.tsx")
    assert ts_profile.name == "typescript"
    assert ts_profile.linter == "eslint"

    go_profile = get_language_profile("server.go")
    assert go_profile.name == "go"
    assert go_profile.linter == "go_vet"


def test_get_language_profile_unknown() -> None:
    """Verify that unknown extensions resolve to the fallback sentinel."""
    profile = get_language_profile("README.md")
    assert profile == UNKNOWN_LANGUAGE_PROFILE
    assert profile.name == "unknown"
    assert profile.linter == "none"

    profile_no_ext = get_language_profile("Makefile")
    assert profile_no_ext == UNKNOWN_LANGUAGE_PROFILE


def test_get_all_supported_extensions() -> None:
    """Verify all supported extensions lists are populated and contain key extensions."""
    exts = get_all_supported_extensions()
    assert ".py" in exts
    assert ".js" in exts
    assert ".ts" in exts
    assert ".go" in exts
    assert ".md" not in exts


def test_describe_tool_support() -> None:
    """Verify the formatting of language support descriptions."""
    py_profile = get_language_profile("main.py")
    desc = describe_tool_support(py_profile)
    assert "full support" in desc
    assert "ruff" in desc
    assert "semgrep" in desc
    assert "tree-sitter" in desc

    unknown_desc = describe_tool_support(UNKNOWN_LANGUAGE_PROFILE)
    assert "LLM-fallback only" in unknown_desc
