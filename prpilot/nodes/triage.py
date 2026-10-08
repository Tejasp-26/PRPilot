"""
nodes/triage.py — Triage/Router Node.

Classifies changed files by language and file type (source, test, config, doc, lockfile).
Determines which specialized review agents are relevant to run.
"""

from __future__ import annotations

import logging
import os

from prpilot.language_profiles import (
    LANGUAGE_PROFILES,
    UNKNOWN_LANGUAGE_PROFILE,
    describe_tool_support,
    get_language_profile,
)
from prpilot.state import ReviewState

logger = logging.getLogger(__name__)

_DOC_EXTENSIONS = {".md", ".txt", ".rst", ".pdf", ".html", ".adoc"}
_DOC_BASENAMES = {"license", "contributing", "changelog", "authors", "notice"}
_LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "gemfile.lock",
    "cargo.lock", "go.sum", "poetry.lock", "uv.lock", "pipfile.lock", "composer.lock",
}
_CONFIG_EXTENSIONS = {".json", ".yaml", ".yml", ".toml", ".ini", ".xml", ".cfg"}
_CONFIG_BASENAMES = {".gitattributes", ".gitignore", ".editorconfig", ".dockerignore"}


def triage_node(state: ReviewState) -> dict:
    """
    Triage node.

    - Resolves the language profile for each changed file.
    - Classifies files (source, test, config, doc, lockfile).
    - Excludes non-code files (like docs) to dynamically scale agent runs.
    - Builds a detailed `language_coverage_summary` explaining tool support.
    """
    raw_files = state.get("raw_files", [])
    if not raw_files:
        logger.warning("[triage] No files found in PR. Skipping all agents.")
        return {
            "relevant_agents": [],
            "language_coverage_summary": "No files changed in this PR.",
            "file_language_map": {},
        }

    file_language_map: dict[str, str] = {}
    lang_counts: dict[str, int] = {}
    has_code = False

    # 1. Resolve languages & classify files
    for f in raw_files:
        filename = f.get("filename", "")
        if not filename:
            continue

        profile = get_language_profile(filename)
        file_language_map[filename] = profile.name

        # Count occurrences per language (excluding non-code classifications where possible,
        # but tracking all for the coverage summary)
        lang_counts[profile.name] = lang_counts.get(profile.name, 0) + 1

        # Deleted files carry no new code to review
        if f.get("status") == "removed":
            continue

        # Check if the file is a doc, config, or lockfile (by basename, so
        # nested files like web/yarn.lock are recognised too)
        basename = os.path.basename(filename).lower()
        _, ext = os.path.splitext(basename)
        is_doc = ext in _DOC_EXTENSIONS or basename in _DOC_BASENAMES
        is_lockfile = basename in _LOCKFILES
        is_config = (
            ext in _CONFIG_EXTENSIONS or basename in _CONFIG_BASENAMES or basename.startswith(".env")
        ) and not is_lockfile

        if not is_doc and not is_config and not is_lockfile:
            has_code = True

    # 2. Build dynamic relevant_agents list
    relevant_agents: list[str] = []
    if has_code:
        relevant_agents.extend(["security", "performance", "style", "coverage"])
    else:
        logger.info("[triage] PR contains only docs, config, or lockfiles. Skipping analysis agents.")

    # 3. Construct language_coverage_summary
    summary_parts: list[str] = []
    for lang_name, count in sorted(lang_counts.items()):
        # Retrieve the profile to describe tool support
        if lang_name == "unknown":
            profile = UNKNOWN_LANGUAGE_PROFILE
        else:
            profile = LANGUAGE_PROFILES.get(lang_name, UNKNOWN_LANGUAGE_PROFILE)

        support_desc = describe_tool_support(profile)
        file_plural = "file" if count == 1 else "files"
        summary_parts.append(f"{lang_name}: {count} {file_plural}, {support_desc}")

    language_coverage_summary = "; ".join(summary_parts)

    logger.info("[triage] File language map: %s", file_language_map)
    logger.info("[triage] Language coverage summary: %s", language_coverage_summary)
    logger.info("[triage] Dynamic relevant agents: %s", relevant_agents)

    return {
        "relevant_agents": relevant_agents,
        "language_coverage_summary": language_coverage_summary,
        "file_language_map": file_language_map,
    }

