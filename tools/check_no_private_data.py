#!/usr/bin/env python3
"""Block private data from entering this repository.

Run by pre-commit against staged files. Rules are deliberately boring: hard
path and extension bans, not coordinate heuristics, which would fail open the
moment a fixture moved. See the design spec, section 7.2.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path

DENY_SUFFIXES = {".db", ".db-journal", ".sqlite", ".sqlite3"}
TRACK_SUFFIXES = {".gpx", ".geojson", ".kml", ".csv", ".xml"}
# Credential files by exact name. `influx.toml` holds the InfluxDB target and
# the *paths* of its token/password files; `.token`/`.password` are those files
# in bare dotfile form. Their committed templates are `influx.example.toml` and
# `example.env`, which none of these match.
DENY_NAMES = {"config.json", ".env", "influx.toml", ".token", ".password"}
# Credential files by extension, at any depth: `influx.token`, `grafana.password`.
CREDENTIAL_SUFFIXES = {".token", ".password"}
ALLOWED_TRACK_DIR = "tests/fixtures"
ALLOWED_TRACK_BASENAME_PREFIX = "synthetic-"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".cfg", ".ini", ".sh"}
SECRET_RE = re.compile(r"\b(?:[A-Fa-f0-9]{32,}|[A-Za-z0-9+/]{40,}={0,2})\b")
SELF = "tools/check_no_private_data.py"
SQLITE_HEADER = b"SQLite format 3\x00"


#: A GeoJSON document identifies itself exactly, by its own top-level `type`
#: discriminator (RFC 7946) — no coordinate heuristics involved. This is the
#: same class of exact check as the SQLite header sniff below, and it is what
#: closes the `export --format geojson -o positions.json` hole: `.json` is the
#: natural name for a GeoJSON file, and it used to pass all three layers.
GEOJSON_TYPES = {
    "FeatureCollection",
    "Feature",
    "GeometryCollection",
    "Point",
    "LineString",
    "MultiPoint",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}


def _has_sqlite_header(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SQLITE_HEADER)) == SQLITE_HEADER
    except OSError:
        return False


def _is_geojson(path: str) -> bool:
    """True only for a parseable JSON object carrying a GeoJSON `type`.

    Unparseable JSON — a malformed config, a fixture, a lockfile — is not a
    GeoJSON export: it falls through to the existing secret scan rather than
    erroring out. `json.JSONDecodeError` is a `ValueError`, which keeps this
    working on the Python 3.9 the pre-commit hook runs with.
    """
    try:
        with open(path, "rb") as handle:
            data = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, dict) and data.get("type") in GEOJSON_TYPES


def check_paths(paths: Iterable[str]) -> list[str]:
    """Return a human-readable violation per offending path; empty when clean."""
    problems: list[str] = []
    for raw in paths:
        path = raw.replace("\\", "/")
        name = Path(path).name
        suffix = Path(path).suffix.lower()
        parent = str(Path(path).parent).replace("\\", "/")

        if suffix in DENY_SUFFIXES:
            problems.append(f"{path}: database files may never be committed")
            continue
        if _has_sqlite_header(path):
            problems.append(
                f"{path}: database file (sqlite header detected) may never be committed"
            )
            continue
        is_track = suffix in TRACK_SUFFIXES or (suffix == ".json" and _is_geojson(path))
        if is_track and not (
            parent == ALLOWED_TRACK_DIR and name.startswith(ALLOWED_TRACK_BASENAME_PREFIX)
        ):
            problems.append(
                f"{path}: track exports may only live directly under {ALLOWED_TRACK_DIR}/ "
                f"with a '{ALLOWED_TRACK_BASENAME_PREFIX}' basename"
            )
            continue
        if name in DENY_NAMES or name.startswith(".env.") or suffix in CREDENTIAL_SUFFIXES:
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
