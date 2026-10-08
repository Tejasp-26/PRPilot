"""
tools/workspace.py — Local checkout of the PR head commit.

Static analysis tools (Semgrep, Ruff, ESLint, go vet) operate on files on
disk. Ingestion calls create_workspace() to materialise the repository at
the PR's HEAD commit in a temp directory; every tool then runs with that
directory as its cwd, and agents read file contents from it rather than
re-fetching each file from the GitHub API.

Strategy:
  1. Download the repository tarball for the commit (one request, includes
     config files such as pyproject.toml / eslint.config.js / go.mod).
  2. If that fails or the archive is too large, download only the changed
     files via the contents API.

SECURITY: the archive is untrusted input. Extraction rejects absolute paths,
'..' components, symlinks, hardlinks and device files, so nothing can be
written outside the workspace directory.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile

import requests

from prpilot.config import WORKSPACE_MAX_ARCHIVE_BYTES

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# File selection helpers
# ──────────────────────────────────────────────

def reviewable_files(raw_files: list[dict]) -> list[dict]:
    """
    Return the changed files agents should analyse: files with a textual
    patch that still exist at the PR head (deleted files are skipped —
    there is no new code to review and fetching them would 404).
    """
    return [
        f for f in raw_files
        if f.get("filename") and f.get("patch") and f.get("status") != "removed"
    ]


def safe_join(root: str, rel_path: str) -> str | None:
    """
    Join rel_path onto root, returning None if the result would escape root.
    Used for every path that originates from GitHub or an archive.
    """
    if not rel_path or os.path.isabs(rel_path):
        return None
    root_abs = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_abs, rel_path))
    if candidate != root_abs and not candidate.startswith(root_abs + os.sep):
        return None
    return candidate


def read_workspace_file(workspace_dir: str | None, rel_path: str) -> str | None:
    """Read a repo-relative file from the workspace, or None if unavailable."""
    if not workspace_dir:
        return None
    path = safe_join(workspace_dir, rel_path)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fp:
            return fp.read()
    except OSError as e:
        logger.debug("[workspace] Could not read %s: %s", rel_path, e)
        return None


def get_file_content(state: dict, rel_path: str) -> str | None:
    """
    Return the content of a file at the PR head.
    Reads from the local workspace first; falls back to the GitHub API.
    """
    content = read_workspace_file(state.get("workspace_dir"), rel_path)
    if content is not None:
        return content

    from prpilot.tools.github_client import fetch_file_content

    return fetch_file_content(
        state.get("owner", ""), state.get("repo", ""), rel_path, state.get("commit_id", "")
    )


# ──────────────────────────────────────────────
# Workspace creation
# ──────────────────────────────────────────────

def create_workspace(owner: str, repo: str, commit_id: str, raw_files: list[dict]) -> str | None:
    """
    Materialise the repository at commit_id in a new temp directory.

    Returns the absolute workspace path, or None if neither the tarball nor
    the per-file download produced anything usable.
    """
    workspace = tempfile.mkdtemp(prefix="prpilot-")

    if _download_tarball(owner, repo, commit_id, workspace):
        logger.info("[workspace] Extracted %s/%s@%s into %s", owner, repo, commit_id[:12], workspace)
        return workspace

    logger.warning("[workspace] Tarball unavailable — downloading changed files only")
    written = _download_changed_files(owner, repo, commit_id, raw_files, workspace)
    if written:
        logger.info("[workspace] Wrote %d changed files into %s", written, workspace)
        return workspace

    logger.error("[workspace] Could not create a workspace; tools will be skipped")
    cleanup_workspace(workspace)
    return None


def cleanup_workspace(workspace_dir: str | None) -> None:
    """Remove a workspace directory created by create_workspace()."""
    if workspace_dir and os.path.basename(workspace_dir).startswith("prpilot-"):
        shutil.rmtree(workspace_dir, ignore_errors=True)


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download_tarball(owner: str, repo: str, commit_id: str, workspace: str) -> bool:
    url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{commit_id}"
    archive_path = os.path.join(workspace, ".prpilot-archive.tar.gz")

    try:
        with requests.get(url, headers=_github_headers(), stream=True, timeout=60) as resp:
            if resp.status_code != 200:
                logger.warning("[workspace] Tarball request failed: HTTP %d", resp.status_code)
                return False

            declared = int(resp.headers.get("Content-Length") or 0)
            if declared > WORKSPACE_MAX_ARCHIVE_BYTES:
                logger.warning("[workspace] Tarball too large (%d bytes)", declared)
                return False

            total = 0
            with open(archive_path, "wb") as out:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    total += len(chunk)
                    if total > WORKSPACE_MAX_ARCHIVE_BYTES:
                        logger.warning("[workspace] Tarball exceeded size limit while streaming")
                        return False
                    out.write(chunk)

        extract_tarball(archive_path, workspace)
        return True

    except (requests.RequestException, tarfile.TarError, OSError) as e:
        logger.warning("[workspace] Tarball download/extract failed: %s", e)
        return False
    finally:
        if os.path.exists(archive_path):
            os.remove(archive_path)


def extract_tarball(archive_path: str, dest: str) -> int:
    """
    Safely extract a GitHub tarball into dest, stripping the top-level
    '<owner>-<repo>-<sha>/' directory. Returns the number of files written.
    Unsafe members (links, devices, absolute or escaping paths) are skipped.
    """
    written = 0
    with tarfile.open(archive_path, "r:*") as tar:
        for member in tar:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue  # the top-level directory itself
            rel = parts[1]

            if not (member.isfile() or member.isdir()):
                continue  # symlinks, hardlinks, devices, fifos

            target = safe_join(dest, rel)
            if target is None:
                logger.warning("[workspace] Skipping unsafe archive member: %s", member.name)
                continue

            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue

            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tar.extractfile(member)
            if src is None:
                continue
            with src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            written += 1
    return written


def _download_changed_files(
    owner: str, repo: str, commit_id: str, raw_files: list[dict], workspace: str
) -> int:
    from prpilot.tools.github_client import fetch_file_content

    written = 0
    for f in reviewable_files(raw_files):
        rel = f["filename"]
        target = safe_join(workspace, rel)
        if target is None:
            continue
        content = fetch_file_content(owner, repo, rel, commit_id)
        if content is None:
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as out:
            out.write(content)
        written += 1
    return written
