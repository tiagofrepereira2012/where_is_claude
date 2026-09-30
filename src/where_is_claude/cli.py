"""Command-line entry point for `where-is-claude`."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from where_is_claude import __version__
from where_is_claude.core import ClaudeInScreen, find_claude_sessions


def _shorten_home(path: str | None) -> str:
    if not path:
        return "?"
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def _render_table(results: list[ClaudeInScreen]) -> str:
    headers = ["SESSION", "STATE", "WINDOW", "PID", "CLAUDE SESSION", "DIRECTORY"]
    rows = [
        [
            r.session.id,
            r.session.state,
            r.window if r.window is not None else "?",
            str(r.pid),
            r.claude_session_name or "-",
            _shorten_home(r.cwd),
        ]
        for r in results
    ]
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(headers, widths)).rstrip()]
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip())
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="where-is-claude",
        description="List the GNU screen sessions that are running Claude Code.",
    )
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if shutil.which("screen") is None:
        print("where-is-claude: GNU screen is not installed or not on PATH.", file=sys.stderr)
        return 2

    results = find_claude_sessions()

    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
        return 0

    if not results:
        print("No screen sessions are running Claude.")
        return 0

    print(_render_table(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
