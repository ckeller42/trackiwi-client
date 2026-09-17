#!/usr/bin/env python3
"""Block private data from entering this repository.

Run by pre-commit against staged files. Rules are deliberately boring: hard
path and extension bans, not coordinate heuristics, which would fail open the
moment a fixture moved. See the design spec, section 7.2.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

DENY_SUFFIXES = {".db", ".db-journal", ".sqlite", ".sqlite3"}
TRACK_SUFFIXES = {".gpx", ".geojson", ".kml", ".csv"}
DENY_NAMES = {"config.json", ".env"}
ALLOWED_TRACK_PREFIX = "tests/fixtures/synthetic-"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh"}
SECRET_RE = re.compile(r"\b(?:[A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{40,}={0,2})\b")
SELF = "tools/check_no_private_data.py"


def check_paths(paths: Iterable[str]) -> list[str]:
    """Return a human-readable violation per offending path; empty when clean."""
    problems: list[str] = []
    for raw in paths:
        path = raw.replace("\\", "/")
        name = Path(path).name
        suffix = Path(path).suffix.lower()

        if suffix in DENY_SUFFIXES:
            problems.append(f"{path}: database files may never be committed")
            continue
        if suffix in TRACK_SUFFIXES and not path.startswith(ALLOWED_TRACK_PREFIX):
            problems.append(f"{path}: track exports may only live under {ALLOWED_TRACK_PREFIX}*")
            continue
        if name in DENY_NAMES or name.startswith(".env."):
            problems.append(f"{path}: credential file")
            continue
        if ".trackiwi/" in path:
            problems.append(f"{path}: path is inside a .trackiwi/ data directory")
            continue
        if path == SELF or suffix not in TEXT_SUFFIXES:
            continue

        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if "allow-secret" in line:
                continue
            match = SECRET_RE.search(line)
            if match:
                problems.append(f"{path}:{lineno}: token-shaped string {match.group(0)[:8]}...")
                break
    return problems


def main(argv: list[str] | None = None) -> int:
    problems = check_paths(sys.argv[1:] if argv is None else argv)
    for problem in problems:
        print(f"BLOCKED {problem}", file=sys.stderr)
    if problems:
        print(
            "\nNo private data may enter this repository (design spec, section 7.2).",
            file=sys.stderr,
        )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
