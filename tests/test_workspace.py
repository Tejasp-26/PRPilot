"""
tests/test_workspace.py — Unit tests for tools/workspace.py.
"""

from __future__ import annotations

import io
import os
import tarfile

from prpilot.tools.workspace import (
    cleanup_workspace,
    extract_tarball,
    get_file_content,
    read_workspace_file,
    reviewable_files,
    safe_join,
)


def _make_tarball(path: str, members: list[tuple[str, bytes | None, str]]) -> None:
    """members: (name, data, kind) where kind is 'file', 'dir' or 'symlink'."""
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = data.decode()
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))


def test_reviewable_files_skips_removed_and_patchless() -> None:
    raw = [
        {"filename": "a.py", "patch": "@@ -1 +1 @@\n-x\n+y", "status": "modified"},
        {"filename": "gone.py", "patch": "@@ -1 +0,0 @@\n-x", "status": "removed"},
        {"filename": "logo.png", "patch": None, "status": "added"},
    ]
    assert [f["filename"] for f in reviewable_files(raw)] == ["a.py"]


def test_safe_join_blocks_escape(tmp_path) -> None:
    root = str(tmp_path)
    assert safe_join(root, "src/app.py") == os.path.realpath(os.path.join(root, "src/app.py"))
    assert safe_join(root, "../outside.txt") is None
    assert safe_join(root, "a/../../outside.txt") is None
    assert safe_join(root, "/etc/passwd") is None
    assert safe_join(root, "") is None


def test_extract_tarball_strips_prefix_and_rejects_unsafe(tmp_path) -> None:
    archive = str(tmp_path / "repo.tar.gz")
    dest = tmp_path / "ws"
    dest.mkdir()
    _make_tarball(archive, [
        ("owner-repo-abc123", None, "dir"),
        ("owner-repo-abc123/src", None, "dir"),
        ("owner-repo-abc123/src/app.py", b"print('hi')\n", "file"),
        ("owner-repo-abc123/../../evil.txt", b"pwned", "file"),
        ("owner-repo-abc123/link", b"/etc/passwd", "symlink"),
    ])

    written = extract_tarball(archive, str(dest))

    assert written == 1
    assert (dest / "src" / "app.py").read_text() == "print('hi')\n"
    assert not (dest / "link").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not (dest.parent.parent / "evil.txt").exists()


def test_read_and_get_file_content_prefers_workspace(tmp_path, mocker) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("x = 1\n")
    fetch = mocker.patch("prpilot.tools.github_client.fetch_file_content", return_value="from api")

    state = {"workspace_dir": str(tmp_path), "owner": "o", "repo": "r", "commit_id": "c"}
    assert read_workspace_file(str(tmp_path), "pkg/m.py") == "x = 1\n"
    assert get_file_content(state, "pkg/m.py") == "x = 1\n"
    fetch.assert_not_called()

    # Missing locally → falls back to the GitHub API
    assert get_file_content(state, "pkg/other.py") == "from api"
    fetch.assert_called_once_with("o", "r", "pkg/other.py", "c")


def test_cleanup_only_removes_prpilot_dirs(tmp_path) -> None:
    keep = tmp_path / "important"
    keep.mkdir()
    cleanup_workspace(str(keep))
    assert keep.exists()

    ws = tmp_path / "prpilot-abc"
    ws.mkdir()
    cleanup_workspace(str(ws))
    assert not ws.exists()
