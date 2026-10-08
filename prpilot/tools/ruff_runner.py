"""
tools/ruff_runner.py — Runs Ruff linter on a file and parses JSON output.

Ruff only reads files (and the repo's pyproject/ruff.toml), so it is safe to
run on untrusted PR code.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from prpilot.config import RUFF_TIMEOUT

logger = logging.getLogger(__name__)


def run_ruff(file_path: str, cwd: str | None = None) -> list[dict] | None:
    """
    Run `ruff check --output-format json` on the specified file.

    Args:
        file_path: Repo-relative path of the file to lint.
        cwd:       Workspace directory containing the checkout (repo root).

    Returns:
        None if Ruff is unavailable or failed to run (caller should fall back),
        otherwise a list of dicts (empty = clean), each containing keys:
        - file: file path
        - line: 1-indexed line number (int)
        - column: 1-indexed column number (int)
        - code: rule code (str, e.g. 'E501')
        - message: lint error message (str)
        - fix_suggestion: optional suggestion text (str or None)
    """
    # 1. Check if ruff is available
    ruff_bin = shutil.which("ruff")
    if not ruff_bin:
        logger.warning("[ruff_runner] Ruff executable not found on PATH.")
        return None

    logger.info("[ruff_runner] Running ruff on %s", file_path)

    # 2. Execute Ruff. (`--format` was removed from Ruff; `--output-format` is current.)
    cmd = [ruff_bin, "check", "--output-format", "json", "--no-fix", file_path]
    try:
        # Exit codes: 0 = clean, 1 = violations found, 2 = Ruff itself failed.
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=RUFF_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("[ruff_runner] Timeout running Ruff on %s", file_path)
        return None
    except Exception as e:
        logger.error("[ruff_runner] Error executing Ruff on %s: %s", file_path, e)
        return None

    if result.returncode >= 2:
        logger.warning(
            "[ruff_runner] Ruff failed on %s (exit %d): %s",
            file_path,
            result.returncode,
            (result.stderr or "")[:300],
        )
        return None

    # 3. Parse JSON output
    stdout = result.stdout.strip()
    if not stdout:
        return []

    try:
        violations = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.warning(
            "[ruff_runner] Failed to parse Ruff JSON output. stdout: %s, error: %s",
            stdout[:200],
            e,
        )
        return None

    parsed_violations = []
    for v in violations:
        code = v.get("code") or "unknown"
        if code == "E902":
            # E902 = Ruff could not read the file (e.g. missing). Not a real finding.
            logger.warning("[ruff_runner] Ruff could not read %s: %s", file_path, v.get("message"))
            return None

        location = v.get("location", {})
        row = location.get("row")
        col = location.get("column")
        msg = v.get("message", "")

        # Ruff sometimes provides a fix suggestion object, let's extract standard details
        fix = v.get("fix")
        fix_suggestion = None
        if fix and isinstance(fix, dict):
            description = fix.get("message") or fix.get("description")
            if description:
                fix_suggestion = f"Apply auto-fix: {description}"

        parsed_violations.append({
            "file": file_path,
            "line": row,
            "column": col,
            "code": code,
            "message": f"[{code}] {msg}",
            "fix_suggestion": fix_suggestion,
        })

    logger.info("[ruff_runner] Found %d violations on %s", len(parsed_violations), file_path)
    return parsed_violations
