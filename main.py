"""
main.py — Backwards-compatible entrypoint.

Prefer `prpilot --pr-url ...` (installed console script) or
`python -m prpilot --pr-url ...`. This shim keeps `python main.py` working.
"""

from prpilot.cli import cli, parse_pr_url, run  # noqa: F401  (re-exported)

if __name__ == "__main__":
    cli()
