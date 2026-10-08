"""
tools/semgrep_runner.py — Runs Semgrep locally on a list of file paths.
Supports auto configuration and strict security audit config (for escalation).

Semgrep only reads files, so it is safe to run on untrusted PR code.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from prpilot.config import SEMGREP_SEVERITY_MAP, SEMGREP_TIMEOUT

logger = logging.getLogger(__name__)


def run_semgrep(
    file_paths: list[str],
    strict: bool = False,
    cwd: str | None = None,
) -> list[dict] | None:
    """
    Run semgrep on the specified files.

    Args:
        file_paths: Repo-relative file paths to scan.
        strict:     If True (escalation), adds --config=p/security-audit on top
                    of --config=auto, so the strict run is a superset.
        cwd:        Workspace directory containing the checkout (repo root).

    Returns:
        None if Semgrep is unavailable or failed (caller should fall back),
        otherwise a list of dicts (empty = clean), each containing keys:
        - file: repo-relative file path
        - line: 1-indexed line number (int)
        - code: check ID (str)
        - message: rule description (str)
        - severity: mapped severity string (critical/high/medium/low/info)
    """
    if not file_paths:
        return []

    # 1. Check if Semgrep is installed
    semgrep_bin = shutil.which("semgrep")
    if not semgrep_bin:
        logger.warning("[semgrep_runner] Semgrep executable not found on PATH.")
        return None

    configs = ["auto", "p/security-audit"] if strict else ["auto"]
    logger.info("[semgrep_runner] Running semgrep (config=%s) on %d files", "+".join(configs), len(file_paths))

    # 2. Prepare command
    cmd = [semgrep_bin, "scan", "--json", "--quiet"]
    cmd.extend(f"--config={c}" for c in configs)
    cmd.extend(file_paths)

    try:
        # Semgrep returns:
        # - 0: ran successfully, no findings
        # - 1: ran successfully, findings found
        # - 2+: error occurred
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=SEMGREP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("[semgrep_runner] Timeout running Semgrep on files: %s", file_paths)
        return None
    except Exception as e:
        logger.error("[semgrep_runner] Error executing Semgrep: %s", e)
        return None

    # 3. Handle errors
    stdout = result.stdout.strip()
    if result.returncode >= 2:
        logger.warning(
            "[semgrep_runner] Semgrep exited with error code %d. stderr: %s",
            result.returncode,
            result.stderr[:500],
        )
        # We can still try parsing stdout if it contains valid JSON
        if not stdout:
            return None

    if not stdout:
        return []

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        logger.warning(
            "[semgrep_runner] Failed to parse Semgrep JSON output. error: %s",
            e,
        )
        return None

    parsed_findings = []
    for r in data.get("results", []):
        path = os.path.normpath(r.get("path", "")).replace(os.sep, "/")
        line = r.get("start", {}).get("line")
        check_id = r.get("check_id", "unknown")

        extra = r.get("extra", {})
        msg = extra.get("message", "")
        semgrep_sev = (extra.get("severity") or "UNKNOWN").upper()
        severity = SEMGREP_SEVERITY_MAP.get(semgrep_sev, SEMGREP_SEVERITY_MAP["UNKNOWN"])

        parsed_findings.append({
            "file": path,
            "line": line,
            "code": check_id,
            "message": msg,
            "severity": severity,
        })

    logger.info("[semgrep_runner] Found %d findings across files", len(parsed_findings))
    return parsed_findings
