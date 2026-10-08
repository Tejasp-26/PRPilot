"""
tools/eslint_runner.py — Runs ESLint linter on JS/TS files and parses JSON output.

SECURITY: ESLint executes the repository's config file (eslint.config.js is
arbitrary JavaScript). The Style Agent only calls this when
PRPILOT_TRUST_REPO_CODE is enabled — see config.TRUST_REPO_CODE.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from prpilot.config import ESLINT_TIMEOUT

logger = logging.getLogger(__name__)

_CONFIG_FILENAMES = [
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
    "eslint.config.ts", "eslint.config.mts", "eslint.config.cts",
    ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.yaml", ".eslintrc.yml",
    ".eslintrc.json", ".eslintrc",
]


def has_eslint_config(dir_path: str = ".", stop_at: str | None = None) -> bool:
    """
    Check if an ESLint configuration exists in dir_path or its parents.

    Args:
        dir_path: Directory to start searching from.
        stop_at:  Do not search above this directory (the workspace root),
                  so configs on the host machine are never picked up.
    """
    current_dir = os.path.abspath(dir_path)
    stop = os.path.abspath(stop_at) if stop_at else None

    while True:
        for name in _CONFIG_FILENAMES:
            if os.path.exists(os.path.join(current_dir, name)):
                return True

        # Check package.json for eslintConfig
        package_json = os.path.join(current_dir, "package.json")
        if os.path.exists(package_json):
            try:
                with open(package_json, errors="replace") as f:
                    if "eslintConfig" in json.load(f):
                        return True
            except Exception:
                pass

        parent_dir = os.path.dirname(current_dir)
        if parent_dir == current_dir or (stop and current_dir == stop):
            break
        current_dir = parent_dir

    return False


def run_eslint(file_path: str, cwd: str | None = None) -> list[dict] | None:
    """
    Run eslint via npx --format json on the specified file.

    Args:
        file_path: Repo-relative path of the file to lint.
        cwd:       Workspace directory containing the checkout (repo root).

    Returns:
        None if ESLint is unavailable, unconfigured or crashed (caller should
        fall back), otherwise a list of dicts (empty = clean), each containing:
        - file, line, column, code (rule id), message, fix_suggestion
    """
    base = cwd or "."
    # 1. Guard: Check if eslint config exists (inside the workspace only)
    if not has_eslint_config(os.path.join(base, os.path.dirname(file_path)), stop_at=base):
        logger.info("[eslint_runner] No ESLint configuration found for %s.", file_path)
        return None

    # 2. Check if npx is available
    npx_bin = shutil.which("npx")
    if not npx_bin:
        logger.warning("[eslint_runner] npx executable not found on PATH.")
        return None

    logger.info("[eslint_runner] Running ESLint via npx on %s", file_path)

    # 3. Execute ESLint. --yes stops npx from waiting on an install prompt.
    cmd = [npx_bin, "--yes", "eslint", "--format", "json", file_path]
    try:
        # Exit codes: 0 = clean, 1 = lint problems, 2 = config/runtime error.
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=ESLINT_TIMEOUT,
            shell=os.name == "nt",  # Use shell on Windows for npx
        )
    except subprocess.TimeoutExpired:
        logger.error("[eslint_runner] Timeout running ESLint on %s", file_path)
        return None
    except Exception as e:
        logger.error("[eslint_runner] Error executing ESLint on %s: %s", file_path, e)
        return None

    if result.returncode >= 2:
        logger.warning(
            "[eslint_runner] ESLint failed on %s (exit %d): %s",
            file_path,
            result.returncode,
            (result.stderr or "")[:300],
        )
        return None

    stdout = result.stdout.strip()
    if not stdout:
        return []

    # 4. Parse JSON output
    try:
        results = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.warning(
            "[eslint_runner] Failed to parse ESLint JSON output. stdout: %s, error: %s",
            stdout[:200],
            e,
        )
        return None

    parsed_violations = []
    # ESLint output is an array of file results
    for file_result in results:
        for msg_item in file_result.get("messages", []):
            code = msg_item.get("ruleId") or "parse-error"
            msg = msg_item.get("message", "")

            fix_suggestion = "Apply automatic fix (eslint --fix)" if msg_item.get("fix") else None

            parsed_violations.append({
                "file": file_path,
                "line": msg_item.get("line"),
                "column": msg_item.get("column"),
                "code": code,
                "message": f"[{code}] {msg}",
                "fix_suggestion": fix_suggestion,
            })

    logger.info("[eslint_runner] Found %d violations on %s", len(parsed_violations), file_path)
    return parsed_violations
