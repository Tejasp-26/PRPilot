"""
tools/go_vet_runner.py — Runs staticcheck or go vet on Go files and parses compiler-like text output.

SECURITY: both tools compile the repository's packages (and may download
modules). The Style Agent only calls this when PRPILOT_TRUST_REPO_CODE is
enabled — see config.TRUST_REPO_CODE.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess

from prpilot.config import GO_VET_TIMEOUT, STATICCHECK_TIMEOUT

logger = logging.getLogger(__name__)

# file.go:10:2: message      /  file.go:10: message
_LINE_PATTERN = re.compile(r"^(.+?\.go):(\d+):(?:(\d+):)?\s*(.*)$")
# staticcheck appends the check id: "message (SA5011)"; some formats prefix it: "SA5011: message"
_STATICCHECK_CODE_SUFFIX = re.compile(r"\(((?:SA|S|ST|QF|U)\d+)\)\s*$")
_STATICCHECK_CODE_PREFIX = re.compile(r"^((?:SA|S|ST|QF|U)\d+):\s*")


def _norm(path: str) -> str:
    return os.path.normpath(path).replace(os.sep, "/")


def run_go_vet(file_path: str, cwd: str | None = None) -> list[dict] | None:
    """
    Run staticcheck (preferred) or go vet on the Go package containing the file,
    then filter results to only include findings for the given file_path.

    Args:
        file_path: Repo-relative path of the Go file.
        cwd:       Workspace directory containing the checkout (repo root).

    Returns:
        None if no Go toolchain is available or the tool failed to run,
        otherwise a list of dicts (empty = clean), each containing keys:
        - file, line, column, code (e.g. 'SA5011' or 'govet'),
          tool ('staticcheck' | 'go_vet'), message, fix_suggestion
    """
    package = "./" + _norm(os.path.dirname(file_path) or ".")

    # 1. Decide which tool to run
    staticcheck_bin = shutil.which("staticcheck")
    go_bin = shutil.which("go")

    if staticcheck_bin:
        tool_name, cmd, timeout = "staticcheck", [staticcheck_bin, package], STATICCHECK_TIMEOUT
    elif go_bin:
        tool_name, cmd, timeout = "go_vet", [go_bin, "vet", package], GO_VET_TIMEOUT
    else:
        logger.warning("[go_vet_runner] Neither staticcheck nor go command found on PATH.")
        return None

    logger.info("[go_vet_runner] Running %s on %s (for file %s)", tool_name, package, file_path)

    # 2. Execute the command
    try:
        # Both go vet and staticcheck exit with non-zero when findings are present.
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.error("[go_vet_runner] Timeout running %s on %s", tool_name, package)
        return None
    except Exception as e:
        logger.error("[go_vet_runner] Error executing %s on %s: %s", tool_name, package, e)
        return None

    # go vet and staticcheck print issues to stderr or stdout.
    output = result.stdout + "\n" + result.stderr
    target = _norm(file_path)

    parsed_violations = []
    for raw_line in output.splitlines():
        match = _LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue

        file_found, line_str, col_str, msg = match.groups()
        # Output paths are relative to cwd (possibly with ./) or absolute.
        found = _norm(os.path.relpath(file_found, cwd) if os.path.isabs(file_found) and cwd else file_found)
        if found != target:
            continue

        if tool_name == "staticcheck":
            code_match = _STATICCHECK_CODE_SUFFIX.search(msg) or _STATICCHECK_CODE_PREFIX.match(msg)
            code = code_match.group(1) if code_match else "staticcheck"
        else:
            code = "govet"

        parsed_violations.append({
            "file": file_path,
            "line": int(line_str),
            "column": int(col_str) if col_str else None,
            "code": code,
            "tool": tool_name,
            "message": msg if code in msg else f"[{code}] {msg}",
            "fix_suggestion": None,
        })

    if not parsed_violations and result.returncode != 0 and "can't load package" in output.lower():
        logger.warning("[go_vet_runner] %s could not load package %s", tool_name, package)
        return None

    logger.info(
        "[go_vet_runner] Tool %s found %d violations for %s",
        tool_name,
        len(parsed_violations),
        file_path,
    )
    return parsed_violations
