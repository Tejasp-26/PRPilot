"""
tests/test_diff_parser.py — Unit tests for diff_parser.py.
"""

from __future__ import annotations

from prpilot.tools.diff_parser import annotate_patch, number_lines, parse_all_patches, parse_patch


def test_parse_patch_valid() -> None:
    """Verify that a valid patch string parses correctly into a ParsedHunk."""
    patch_text = (
        "@@ -10,2 +10,3 @@ def main():\n"
        "-    x = 1\n"
        "+    x = 2\n"
        "+    y = 3\n"
        "     print(x)\n"
    )
    filename = "main.py"
    hunk = parse_patch(patch_text, filename)

    assert hunk is not None
    assert hunk.file == filename
    assert hunk.added_lines == [10, 11]
    assert hunk.context_lines == [12]
    assert hunk.removed_lines == [10]
    assert hunk.commentable_lines == {10, 11, 12}


def test_parse_patch_empty_or_missing() -> None:
    """Verify that empty, missing, or malformed patches return None."""
    assert parse_patch("", "file.py") is None
    assert parse_patch("  ", "file.py") is None
    assert parse_patch("malformed diff without hunks", "file.py") is None


def test_parse_all_patches() -> None:
    """Verify that multiple patches are correctly parsed and skips binary/malformed files."""
    raw_files = [
        {
            "filename": "src/main.py",
            "patch": (
                "@@ -1,2 +1,2 @@\n"
                "-old\n"
                "+new\n"
                " context\n"
            ),
        },
        {
            "filename": "logo.png",
            "patch": None,  # binary file
        },
        {
            "filename": "malformed.py",
            "patch": "not a valid diff",
        },
    ]

    hunks = parse_all_patches(raw_files)
    assert len(hunks) == 1
    assert hunks[0].file == "src/main.py"
    assert hunks[0].added_lines == [1]


def test_annotate_patch_numbers_new_lines() -> None:
    """LLM prompts get new-file line numbers on added/context lines, none on removed lines."""
    patch_text = (
        "@@ -10,2 +10,3 @@ def main():\n"
        "-    x = 1\n"
        "+    x = 2\n"
        "+    y = 3\n"
        "     print(x)\n"
    )
    lines = annotate_patch(patch_text).splitlines()
    assert lines[0].startswith("@@ -10,2 +10,3 @@")
    assert lines[1] == "      -     x = 1"
    assert lines[2] == "   10 +     x = 2"
    assert lines[3] == "   11 +     y = 3"
    assert lines[4] == "   12       print(x)"


def test_annotate_patch_falls_back_on_garbage() -> None:
    assert annotate_patch("not a diff") == "not a diff"
    assert annotate_patch("") == ""


def test_number_lines() -> None:
    assert number_lines("a\nb") == "    1  a\n    2  b"
