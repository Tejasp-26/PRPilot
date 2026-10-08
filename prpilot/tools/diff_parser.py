"""
tools/diff_parser.py — unidiff wrapper for parsing GitHub PR patch strings.

Converts the raw `patch` field from GitHub's PR Files API into ParsedHunk
objects that carry the exact line numbers needed by CommentMapper to
validate which lines are commentable.

GitHub only accepts inline review comments on lines that appear in the diff.
Retaining this structure here (rather than only keeping diffs as text) is
what makes the comment-position validation in comment_mapper.py possible.
"""

from __future__ import annotations

import logging

import unidiff

from prpilot.state import ParsedHunk

logger = logging.getLogger(__name__)


def parse_patch(patch_text: str, filename: str) -> ParsedHunk | None:
    """
    Parse a single file's GitHub patch string into a ParsedHunk.

    Args:
        patch_text: The raw `patch` field from the GitHub PR Files API.
                    This is a unified diff string starting with `@@` hunks.
        filename:   Repo-relative file path (used to populate ParsedHunk.file).

    Returns:
        ParsedHunk with populated added/removed/context line lists,
        or None if parsing fails (binary file, empty patch, malformed diff).

    Callers should handle None explicitly — a None ParsedHunk means the file
    cannot receive inline comments (it will appear only in the summary body).
    """
    if not patch_text or not patch_text.strip():
        logger.debug("[diff_parser] Empty/missing patch for %s — skipping", filename)
        return None

    try:
        # unidiff expects a full unified diff with file headers.
        # GitHub's patch field is a headerless diff (starts with @@ lines).
        # We synthesise minimal headers to satisfy unidiff's parser.
        synthetic_diff = (
            f"--- a/{filename}\n"
            f"+++ b/{filename}\n"
            f"{patch_text}\n"
        )

        patch_set = unidiff.PatchSet.from_string(synthetic_diff)

        if not patch_set:
            logger.warning("[diff_parser] PatchSet is empty for %s", filename)
            return None

        patched_file = patch_set[0]

        if len(patched_file) == 0:
            logger.warning("[diff_parser] No hunks found for %s", filename)
            return None

        added_lines: list[int] = []
        removed_lines: list[int] = []
        context_lines: list[int] = []

        for hunk in patched_file:
            for line in hunk:
                if line.is_added:
                    # line.target_line_no is the line number in the NEW file
                    if line.target_line_no is not None:
                        added_lines.append(line.target_line_no)
                elif line.is_removed:
                    # line.source_line_no is the line number in the OLD file
                    if line.source_line_no is not None:
                        removed_lines.append(line.source_line_no)
                elif line.is_context:
                    # Context lines exist in both; GitHub comments use target (new)
                    if line.target_line_no is not None:
                        context_lines.append(line.target_line_no)

        hunk_obj = ParsedHunk(
            file=filename,
            added_lines=added_lines,
            removed_lines=removed_lines,
            context_lines=context_lines,
        )

        logger.debug(
            "[diff_parser] Parsed %s: +%d -%d ctx%d lines",
            filename,
            len(added_lines),
            len(removed_lines),
            len(context_lines),
        )
        return hunk_obj

    except unidiff.errors.UnidiffParseError as e:
        logger.warning(
            "[diff_parser] Failed to parse patch for %s: %s — file will be summary-only",
            filename,
            e,
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[diff_parser] Unexpected error parsing patch for %s: %s",
            filename,
            e,
        )
        return None


def annotate_patch(patch_text: str) -> str:
    """
    Prefix every diff line with its line number in the NEW file so an LLM can
    report accurate line numbers instead of guessing from hunk headers.

    Format:
        "  12 + added line"      (added: new-file line number)
        "  13   context line"    (context: new-file line number)
        "     - removed line"    (removed: no new-file line number)
        "@@ -10,2 +10,3 @@ ..."  (hunk headers kept as-is)

    Falls back to the raw patch if it cannot be parsed.
    """
    if not patch_text or not patch_text.strip():
        return patch_text or ""

    try:
        patch_set = unidiff.PatchSet.from_string(f"--- a/f\n+++ b/f\n{patch_text}\n")
    except Exception:  # noqa: BLE001
        return patch_text
    if not patch_set or len(patch_set[0]) == 0:
        return patch_text

    out: list[str] = []
    for hunk in patch_set[0]:
        out.append(
            f"@@ -{hunk.source_start},{hunk.source_length} "
            f"+{hunk.target_start},{hunk.target_length} @@"
        )
        for line in hunk:
            text = line.value.rstrip("\n")
            if line.is_added:
                out.append(f"{line.target_line_no:>5} + {text}")
            elif line.is_removed:
                out.append(f"{'':>5} - {text}")
            elif line.is_context and line.target_line_no is not None:
                out.append(f"{line.target_line_no:>5}   {text}")
            # else: "\ No newline at end of file" markers and similar — omit
    return "\n".join(out)


def number_lines(content: str) -> str:
    """Prefix each line of a file with its 1-indexed line number (for LLM prompts)."""
    return "\n".join(f"{i:>5}  {line}" for i, line in enumerate(content.splitlines(), 1))


def parse_all_patches(raw_files: list[dict]) -> list[ParsedHunk]:
    """
    Parse the patch field for every file in the GitHub PR Files API response.

    Files without a patch field (binary files, files too large for GitHub to
    return a diff) are silently skipped — they will not receive inline comments
    but are still visible in the overall review summary.

    Args:
        raw_files: List of dicts from GitHub's GET /repos/.../pulls/.../files API.
                   Each dict has at minimum: 'filename', optionally 'patch'.

    Returns:
        List of ParsedHunk objects (one per parseable file).
        Files that couldn't be parsed are excluded (not returned as None entries).
    """
    hunks: list[ParsedHunk] = []
    skipped_no_patch = 0
    skipped_parse_error = 0

    for f in raw_files:
        filename = f.get("filename", "<unknown>")
        patch = f.get("patch")

        if not patch:
            logger.debug(
                "[diff_parser] %s has no patch field (binary or too large) — skipping",
                filename,
            )
            skipped_no_patch += 1
            continue

        hunk = parse_patch(patch, filename)
        if hunk is not None:
            hunks.append(hunk)
        else:
            skipped_parse_error += 1

    logger.info(
        "[diff_parser] Parsed %d hunks from %d files (%d skipped: no patch, %d skipped: parse error)",
        len(hunks),
        len(raw_files),
        skipped_no_patch,
        skipped_parse_error,
    )
    return hunks
