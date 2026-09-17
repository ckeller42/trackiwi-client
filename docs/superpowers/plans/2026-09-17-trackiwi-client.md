# trackiwi-client Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Python CLI and library that pulls a personal trackiwi account's GPS positions into a local SQLite cache and exports them as GPX, GeoJSON or CSV.

**Architecture:** Four modules with one responsibility each. `client.py` is the only module doing network I/O; `store.py` is the only module touching SQLite; `export.py` is pure (rows in, text out); `cli.py` wires them together. Sync is incremental and offset-based, which makes it resumable — so there is deliberately no retry logic.

**Tech Stack:** Python ≥3.11, standard library only at runtime (`urllib`, `json`, `csv`, `sqlite3`, `argparse`, `getpass`, `xml.etree`). Dev-only: `pytest`, `ruff`, `pre-commit`.

**Spec:** `docs/superpowers/specs/2026-09-17-trackiwi-client-design.md`

## Global Constraints

- **Zero runtime dependencies.** `[project].dependencies` stays empty. Dev tools live in `[project.optional-dependencies].dev`.
- **Python floor `>=3.11`**, CI matrix `3.11` and `3.13`. The machine's `/usr/bin/python3` is 3.9.6 (Xcode); use MacPorts `/opt/local/bin/python3.13`.
- **Read-only against the API.** The only state-changing call permitted anywhere is `DELETE /api/v2/session` in `logout`. No `share create`, no writes to tours/markers/alarms.
- **No private data in the repository, ever.** Fixtures are synthetic: invented coordinates, invented IDs. There is no record-from-live mode.
- **Ignore the `trackiwi-app-command` response header.** Never act on server-delivered commands.
- **Never log or print the token**, not even in debug output.
- **Secrets on disk:** `~/.config/trackiwi/config.json` mode `0600`, directory `0700`. Cache `~/.local/share/trackiwi/positions.db` mode `0600`, directory `0700`. Respect `XDG_CONFIG_HOME` / `XDG_DATA_HOME`.
- **API base is never hardcoded** — it comes from the login response's `server` field. Only `https://www.trackiwi.com` (the auth host) is a constant.
- `fix_at` is normalised to **epoch seconds at parse time**, so no other module needs unit-handling logic.

---

### Task 1: Scaffolding, pre-commit and the private-data guard

The guard comes first because §7.2 of the spec must hold from the first code commit, not be retrofitted.

**Files:**
- Create: `pyproject.toml`
- Create: `tools/check_no_private_data.py`
- Create: `tools/ci.sh`
- Create: `.pre-commit-config.yaml`
- Create: `trackiwi/__init__.py`
- Test: `tests/test_check_no_private_data.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `tools/check_no_private_data.py::check_paths(paths: Iterable[str]) -> list[str]` returning human-readable violation strings, empty when clean. `trackiwi.COLUMNS: tuple[str, ...]` — the shared column vocabulary used by Tasks 2, 3 and 4.

- [ ] **Step 1: Create the virtualenv and install dev tools**

```bash
cd ~/src/trackiwi-client
/opt/local/bin/python3.13 -m venv .venv
.venv/bin/pip install -U pip pytest ruff pre-commit
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "trackiwi"
version = "0.1.0"
description = "Read-only client for a personal trackiwi GPS account"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.6", "pre-commit>=3.7"]

[project.scripts]
trackiwi = "trackiwi.cli:main"

[tool.setuptools.packages.find]
include = ["trackiwi*"]

[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["live: hits the real trackiwi API; requires TRACKIWI_LIVE=1"]
addopts = "-m 'not live'"
```

- [ ] **Step 3: Write `trackiwi/__init__.py`**

`COLUMNS` lives here because all three of `client`, `store` and `export` need the same vocabulary; putting it in any one of them would force the other two to import from a peer.

```python
"""Read-only client for a personal trackiwi GPS account."""

__version__ = "0.1.0"

#: Column order of the sync CSV and of the local `positions` table.
COLUMNS = (
    "id",
    "tracker_id",
    "fix_at",
    "fix_timezone",
    "latitude",
    "longitude",
    "altitude",
    "speed",
    "course",
    "distance",
    "rssi",
    "sat",
    "battery",
    "voltage",
)
```

- [ ] **Step 4: Write the failing test for the guard**

Create `tests/test_check_no_private_data.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from check_no_private_data import check_paths  # noqa: E402


def _write(tmp_path, rel, text="hello\n"):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return rel


def test_clean_files_pass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "trackiwi/client.py", "x = 1\n")
    assert check_paths([rel]) == []


def test_database_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "positions.db")
    assert any("database" in p for p in check_paths([rel]))


def test_track_export_outside_fixtures_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "route.gpx", "<gpx/>\n")
    assert check_paths([rel]) != []


def test_synthetic_fixture_is_allowed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "tests/fixtures/synthetic-positions.csv", "1,2\n")
    assert check_paths([rel]) == []


def test_credential_file_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = _write(tmp_path, "config.json", "{}\n")
    assert check_paths([rel]) != []


def test_token_shaped_string_is_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = "a" * 44
    rel = _write(tmp_path, "notes.md", f"token = {secret}\n")
    assert any("token-shaped" in p for p in check_paths([rel]))


def test_allow_secret_marker_suppresses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    secret = "a" * 44
    rel = _write(tmp_path, "notes.md", f"token = {secret}  # allow-secret\n")
    assert check_paths([rel]) == []
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_check_no_private_data.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_no_private_data'`

- [ ] **Step 6: Write `tools/check_no_private_data.py`**

```python
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
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_check_no_private_data.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 8: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: check-added-large-files
      - id: check-merge-conflict
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: detect-private-key
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: no-private-data
        name: no private data in repository
        entry: python3 tools/check_no_private_data.py
        language: system
        pass_filenames: true
```

- [ ] **Step 9: Resolve the pinned revisions and install the hooks**

`pre-commit autoupdate` replaces the `rev:` values above with whatever is current, so the committed file carries real pins rather than the placeholders written by hand.

```bash
.venv/bin/pre-commit autoupdate
.venv/bin/pre-commit install
.venv/bin/pre-commit run --all-files
```

Expected: hooks may reformat files on the first run. Re-run until it reports all hooks passing.

- [ ] **Step 10: Write `tools/ci.sh`**

```bash
#!/usr/bin/env bash
# Local gate. Mirrors CI exactly — if this passes, CI passes.
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pre-commit run --all-files
.venv/bin/pytest -v
```

Then: `chmod +x tools/ci.sh && ./tools/ci.sh`
Expected: all hooks pass, tests pass.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml .pre-commit-config.yaml tools trackiwi tests
git commit -m "feat: scaffolding, pre-commit gate and private-data guard"
```

---

### Task 2: Parse the sync CSV

**Files:**
- Create: `trackiwi/client.py`
- Create: `tests/fixtures/synthetic-positions.csv`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `trackiwi.COLUMNS` from Task 1.
- Produces:
  - `trackiwi.client.TrackiwiError(Exception)` with attribute `status: int | None`
  - `trackiwi.client.AuthError(TrackiwiError)`
  - `trackiwi.client.normalize_epoch(value: int) -> int`
  - `trackiwi.client.parse_positions(text: str) -> tuple[list[tuple], int]` — returns `(rows, skipped_count)`; each row is a 14-tuple in `COLUMNS` order with `fix_at` already normalised to epoch seconds.

- [ ] **Step 1: Create the synthetic fixture**

Coordinates are mid-Atlantic on purpose — they correspond to no real place. Create `tests/fixtures/synthetic-positions.csv`:

```
1001,7,1758000000,120,31.000000,-41.000000,12,0.0,0,0,-71,9,98,4120
1002,7,1758000060,120,31.001000,-41.001000,14,11.5,90,85,-70,10,98,4118
1003,7,1758000120,120,31.002000,-41.002000,15,12.0,91,170,-69,11,97,4117
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_parse.py`:

```python
from pathlib import Path

from trackiwi import COLUMNS
from trackiwi.client import normalize_epoch, parse_positions

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic-positions.csv"


def test_parses_all_rows_from_fixture():
    rows, skipped = parse_positions(FIXTURE.read_text(encoding="utf-8"))
    assert len(rows) == 3
    assert skipped == 0
    assert len(rows[0]) == len(COLUMNS)


def test_types_are_coerced():
    rows, _ = parse_positions("1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["id"] == 1001
    assert row["latitude"] == 31.5
    assert isinstance(row["latitude"], float)
    assert row["altitude"] == 12


def test_empty_optional_field_becomes_none():
    rows, _ = parse_positions("1001,7,1758000000,,31.5,-41.5,,,,,,,,\n")
    row = dict(zip(COLUMNS, rows[0], strict=True))
    assert row["fix_timezone"] is None
    assert row["voltage"] is None


def test_short_row_is_skipped_not_fatal():
    text = "1001,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n1002,7,broken\n"
    rows, skipped = parse_positions(text)
    assert len(rows) == 1
    assert skipped == 1


def test_overlong_row_is_skipped():
    rows, skipped = parse_positions("1," * 20 + "\n")
    assert rows == []
    assert skipped == 1


def test_row_missing_required_field_is_skipped():
    rows, skipped = parse_positions("1001,7,1758000000,120,,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_non_numeric_field_is_skipped():
    rows, skipped = parse_positions("x,7,1758000000,120,31.5,-41.5,12,0.0,0,0,-71,9,98,4120\n")
    assert rows == []
    assert skipped == 1


def test_blank_body_yields_nothing():
    assert parse_positions("") == ([], 0)
    assert parse_positions("\n\n") == ([], 0)


def test_milliseconds_are_normalised_to_seconds():
    assert normalize_epoch(1758000000) == 1758000000
    assert normalize_epoch(1758000000123) == 1758000000


def test_fix_at_is_normalised_during_parse():
    rows, _ = parse_positions("1,7,1758000000123,120,31.5,-41.5,1,0.0,0,0,-71,9,98,4120\n")
    assert dict(zip(COLUMNS, rows[0], strict=True))["fix_at"] == 1758000000
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackiwi.client'`

- [ ] **Step 4: Write `trackiwi/client.py`**

```python
"""Network access to the trackiwi API.

This is the only module that performs network I/O. It knows nothing about
SQLite or output formats.
"""

from __future__ import annotations

from . import COLUMNS

_FLOAT_COLUMNS = {"latitude", "longitude", "speed"}
_REQUIRED_COLUMNS = ("id", "tracker_id", "fix_at", "latitude", "longitude")

#: Beyond this, a value cannot be epoch seconds (it would be year 2286+), so
#: it must be milliseconds. The unit is not documented by trackiwi; see the
#: design spec, section 10.5.
_MILLISECOND_THRESHOLD = 10_000_000_000


class TrackiwiError(Exception):
    """Any failure talking to trackiwi."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(TrackiwiError):
    """Credentials were rejected, or the session expired."""


def normalize_epoch(value: int) -> int:
    """Return `value` as epoch seconds, accepting seconds or milliseconds."""
    return value // 1000 if value > _MILLISECOND_THRESHOLD else value


def _coerce(column: str, raw: str) -> float | int | None:
    raw = raw.strip()
    if raw == "":
        return None
    return float(raw) if column in _FLOAT_COLUMNS else int(raw)


def parse_positions(text: str) -> tuple[list[tuple], int]:
    """Parse the sync endpoint's CSV body.

    Returns `(rows, skipped)`. Rows are tuples in :data:`trackiwi.COLUMNS`
    order with `fix_at` normalised to epoch seconds. Malformed rows are
    skipped and counted rather than aborting the batch, matching the vendor
    client's behaviour: one bad row must not discard a whole sync page.
    """
    rows: list[tuple] = []
    skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = line.split(",")
        if len(fields) != len(COLUMNS):
            skipped += 1
            continue
        try:
            values = {c: _coerce(c, f) for c, f in zip(COLUMNS, fields, strict=True)}
        except ValueError:
            skipped += 1
            continue
        if any(values[c] is None for c in _REQUIRED_COLUMNS):
            skipped += 1
            continue
        values["fix_at"] = normalize_epoch(int(values["fix_at"]))
        rows.append(tuple(values[c] for c in COLUMNS))
    return rows, skipped
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_parse.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 6: Commit**

```bash
git add trackiwi/client.py tests/test_parse.py tests/fixtures/synthetic-positions.csv
git commit -m "feat: parse the sync CSV into typed position rows"
```

---

### Task 3: SQLite position cache

**Files:**
- Create: `trackiwi/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `trackiwi.COLUMNS` from Task 1.
- Produces:
  - `trackiwi.store.default_db_path() -> pathlib.Path`
  - `trackiwi.store.Store(path: Path | str | None = None)` — a context manager
  - `Store.upsert(rows: Iterable[tuple]) -> int` returning the number of rows written
  - `Store.max_id() -> int | None` — the sync offset
  - `Store.query(tracker_id: int | None = None, start: int | None = None, end: int | None = None) -> list[sqlite3.Row]` ordered by `fix_at`; `start`/`end` are inclusive epoch seconds
  - `Store.count() -> int`
  - `Store.purge() -> None` — delete the database file

- [ ] **Step 1: Write the failing tests**

Create `tests/test_store.py`:

```python
import os
import stat

import pytest

from trackiwi.store import Store, default_db_path


def row(pos_id, tracker_id=7, fix_at=1758000000, lat=31.0, lon=-41.0):
    return (pos_id, tracker_id, fix_at, 120, lat, lon, 12, 0.0, 0, 0, -71, 9, 98, 4120)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "positions.db") as s:
        yield s


def test_upsert_then_count(store):
    assert store.upsert([row(1), row(2)]) == 2
    assert store.count() == 2


def test_upsert_is_idempotent(store):
    store.upsert([row(1)])
    store.upsert([row(1)])
    assert store.count() == 1


def test_max_id_is_the_sync_offset(store):
    assert store.max_id() is None
    store.upsert([row(5), row(9), row(7)])
    assert store.max_id() == 9


def test_query_orders_by_time(store):
    store.upsert([row(2, fix_at=200), row(1, fix_at=100)])
    assert [r["fix_at"] for r in store.query()] == [100, 200]


def test_query_filters_by_tracker(store):
    store.upsert([row(1, tracker_id=7), row(2, tracker_id=8)])
    assert [r["id"] for r in store.query(tracker_id=8)] == [2]


def test_query_date_bounds_are_inclusive(store):
    store.upsert([row(1, fix_at=100), row(2, fix_at=200), row(3, fix_at=300)])
    assert [r["id"] for r in store.query(start=200, end=300)] == [2, 3]
    assert [r["id"] for r in store.query(start=200)] == [2, 3]
    assert [r["id"] for r in store.query(end=200)] == [1, 2]


def test_rows_are_mapping_like(store):
    store.upsert([row(1)])
    assert store.query()[0]["latitude"] == 31.0


def test_database_file_is_owner_only(tmp_path):
    path = tmp_path / "sub" / "positions.db"
    with Store(path) as s:
        s.upsert([row(1)])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_purge_removes_the_file(tmp_path):
    path = tmp_path / "positions.db"
    with Store(path) as s:
        s.upsert([row(1)])
        s.purge()
    assert not path.exists()


def test_default_path_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_db_path() == tmp_path / "trackiwi" / "positions.db"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackiwi.store'`

- [ ] **Step 3: Write `trackiwi/store.py`**

```python
"""Local SQLite cache of positions.

This is the only module that touches the database. It performs no network I/O.

The cache is a complete movement history of a physical vehicle, so the file is
created owner-only inside an owner-only directory. See the design spec,
section 7.1.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from . import COLUMNS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
  id           INTEGER PRIMARY KEY,
  tracker_id   INTEGER NOT NULL,
  fix_at       INTEGER NOT NULL,
  fix_timezone INTEGER,
  latitude     REAL NOT NULL,
  longitude    REAL NOT NULL,
  altitude     INTEGER,
  speed        REAL,
  course       INTEGER,
  distance     INTEGER,
  rssi         INTEGER,
  sat          INTEGER,
  battery      INTEGER,
  voltage      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_positions_tracker_time
  ON positions (tracker_id, fix_at);
"""


def default_db_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "trackiwi" / "positions.db"


class Store:
    """Owner-only SQLite cache of position rows."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Store:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        existed = self.path.exists()
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        if not existed:
            os.chmod(self.path, 0o600)
        self._conn.executescript(_SCHEMA)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Store must be used as a context manager")
        return self._conn

    def upsert(self, rows: Iterable[tuple]) -> int:
        placeholders = ",".join("?" * len(COLUMNS))
        sql = f"INSERT OR REPLACE INTO positions ({','.join(COLUMNS)}) VALUES ({placeholders})"
        rows = list(rows)
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return len(rows)

    def max_id(self) -> int | None:
        return self.conn.execute("SELECT MAX(id) FROM positions").fetchone()[0]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]

    def query(
        self,
        tracker_id: int | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> list[sqlite3.Row]:
        """Rows ordered by time. `start` and `end` are inclusive epoch seconds."""
        clauses: list[str] = []
        params: list[int] = []
        if tracker_id is not None:
            clauses.append("tracker_id = ?")
            params.append(tracker_id)
        if start is not None:
            clauses.append("fix_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("fix_at <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM positions{where} ORDER BY fix_at, id"
        return list(self.conn.execute(sql, params))

    def purge(self) -> None:
        """Close and delete the cache file."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self.path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_store.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add trackiwi/store.py tests/test_store.py
git commit -m "feat: owner-only SQLite position cache"
```

---

### Task 4: Exporters

**Files:**
- Create: `trackiwi/export.py`
- Test: `tests/test_export.py`

**Note on `xml.etree` and XXE:** automated tooling flags stdlib XML as unsafe and
suggests `defusedxml`. That applies to *parsing* untrusted input. This module only
*serialises* — it never parses. The single `ET.fromstring` call in the tests parses
output this code just produced. `defusedxml` would also break the zero-dependency
constraint, so stdlib `ElementTree` stays. Do not "fix" this later without re-reading
this note.

**Interfaces:**
- Consumes: `trackiwi.COLUMNS` from Task 1; rows shaped like `sqlite3.Row` from Task 3 (any mapping with the `COLUMNS` keys works).
- Produces:
  - `trackiwi.export.to_gpx(rows) -> str`
  - `trackiwi.export.to_geojson(rows) -> str`
  - `trackiwi.export.to_csv(rows) -> str`
  - `trackiwi.export.FORMATS: dict[str, Callable]` mapping `"gpx"`/`"geojson"`/`"csv"` to those functions — the CLI in Task 7 dispatches through this.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export.py`:

```python
import json
import xml.etree.ElementTree as ET

from trackiwi import COLUMNS
from trackiwi.export import FORMATS, to_csv, to_geojson, to_gpx


def row(pos_id=1, fix_at=1758000000, lat=31.0, lon=-41.0, altitude=12):
    values = (pos_id, 7, fix_at, 120, lat, lon, altitude, 0.0, 0, 0, -71, 9, 98, 4120)
    return dict(zip(COLUMNS, values, strict=True))


def test_gpx_is_well_formed_with_points():
    xml = to_gpx([row(1), row(2, fix_at=1758000060, lat=31.001)])
    tree = ET.fromstring(xml)
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    points = tree.findall(".//g:trkpt", ns)
    assert len(points) == 2
    assert points[0].attrib["lat"] == "31.000000"
    assert points[0].attrib["lon"] == "-41.000000"


def test_gpx_writes_utc_timestamp_and_elevation():
    xml = to_gpx([row(fix_at=1758000000)])
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    tree = ET.fromstring(xml)
    assert tree.find(".//g:trkpt/g:time", ns).text.endswith("Z")
    assert tree.find(".//g:trkpt/g:ele", ns).text == "12"


def test_gpx_omits_elevation_when_absent():
    xml = to_gpx([row(altitude=None)])
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    assert ET.fromstring(xml).find(".//g:trkpt/g:ele", ns) is None


def test_gpx_with_no_rows_is_still_valid():
    ET.fromstring(to_gpx([]))


def test_geojson_is_a_linestring_in_lon_lat_order():
    data = json.loads(to_geojson([row(1), row(2, lon=-41.5)]))
    geometry = data["features"][0]["geometry"]
    assert geometry["type"] == "LineString"
    assert geometry["coordinates"][0] == [-41.0, 31.0]
    assert geometry["coordinates"][1][0] == -41.5


def test_geojson_with_no_rows_has_no_features():
    assert json.loads(to_geojson([]))["features"] == []


def test_csv_has_header_and_rows():
    lines = to_csv([row(1), row(2)]).strip().splitlines()
    assert lines[0] == ",".join(COLUMNS)
    assert len(lines) == 3


def test_formats_registry_exposes_all_three():
    assert set(FORMATS) == {"gpx", "geojson", "csv"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackiwi.export'`

- [ ] **Step 3: Write `trackiwi/export.py`**

```python
"""Convert position rows into standard GPS formats.

Pure: rows in, text out. No I/O. `fix_at` is already epoch seconds by the time
rows reach here, normalised at parse time.
"""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from . import COLUMNS, __version__

GPX_NS = "http://www.topografix.com/GPX/1/1"


def _iso(fix_at: int) -> str:
    return datetime.fromtimestamp(fix_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_gpx(rows: Sequence) -> str:
    """Render rows as a single GPX 1.1 track segment."""
    gpx = ET.Element(
        "gpx",
        {"version": "1.1", "creator": f"trackiwi-client/{__version__}", "xmlns": GPX_NS},
    )
    trk = ET.SubElement(gpx, "trk")
    ET.SubElement(trk, "name").text = "trackiwi export"
    seg = ET.SubElement(trk, "trkseg")
    for row in rows:
        point = ET.SubElement(
            seg,
            "trkpt",
            {"lat": f"{row['latitude']:.6f}", "lon": f"{row['longitude']:.6f}"},
        )
        if row["altitude"] is not None:
            ET.SubElement(point, "ele").text = str(row["altitude"])
        ET.SubElement(point, "time").text = _iso(row["fix_at"])
    body = ET.tostring(gpx, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n{body}\n'


def to_geojson(rows: Sequence) -> str:
    """Render rows as a FeatureCollection holding one LineString."""
    coordinates = [[row["longitude"], row["latitude"]] for row in rows]
    features = []
    if coordinates:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": coordinates},
                "properties": {
                    "point_count": len(coordinates),
                    "start_time": _iso(rows[0]["fix_at"]),
                    "end_time": _iso(rows[-1]["fix_at"]),
                },
            }
        )
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n"


def to_csv(rows: Sequence) -> str:
    """Render rows as CSV with a header, in :data:`trackiwi.COLUMNS` order."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in COLUMNS])
    return buffer.getvalue()


FORMATS: dict[str, Callable[[Sequence], str]] = {
    "gpx": to_gpx,
    "geojson": to_geojson,
    "csv": to_csv,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_export.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add trackiwi/export.py tests/test_export.py
git commit -m "feat: GPX, GeoJSON and CSV exporters"
```

---

### Task 5: Config, HTTP transport and authentication

**Files:**
- Modify: `trackiwi/client.py` (append; keep Task 2's contents)
- Test: `tests/test_client_auth.py`

**Interfaces:**
- Consumes: `TrackiwiError`, `AuthError`, `parse_positions` from Task 2.
- Produces:
  - `trackiwi.client.WEBSITE: str` — `"https://www.trackiwi.com"`
  - `trackiwi.client.default_config_path() -> pathlib.Path`
  - `trackiwi.client.Client(api_base=None, token=None, user_id=None, opener=None)` — `opener` defaults to `urllib.request.urlopen` and exists so tests can inject a fake
  - `Client.load(opener=None) -> Client` (classmethod) — read the saved session
  - `Client.save() -> None` — write `config.json` mode 0600
  - `Client.login(email: str, password: str) -> dict` — returns the `user` object
  - `Client.logout() -> None` — revoke server-side, then delete local config
  - `Client.session_ok() -> bool`
  - `Client.authenticated: bool` (property)

- [ ] **Step 1: Write the shared test doubles**

These live in `tests/conftest.py` so Task 6 can reuse them without importing
across test modules. Create `tests/conftest.py`:

```python
import io


class FakeResponse(io.BytesIO):
    """Stand-in for an HTTP response returned by urlopen."""

    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    """Stand-in for urllib.request.urlopen that records the requests it got."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_client_auth.py`:

```python
import json
import os
import stat

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import AuthError, Client, TrackiwiError, default_config_path


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def login_response():
    body = json.dumps(
        {"server": "https://api.example.invalid/", "token": "tok", "user": {"id": 42}}
    ).encode()
    return FakeResponse(body)


def test_login_stores_server_token_and_user():
    opener = FakeOpener(login_response())
    client = Client(opener=opener)
    user = client.login("a@example.invalid", "pw")
    assert user["id"] == 42
    assert client.api_base == "https://api.example.invalid"
    assert client.token == "tok"
    assert client.user_id == 42


def test_login_posts_email_and_password_to_the_website():
    opener = FakeOpener(login_response())
    Client(opener=opener).login("a@example.invalid", "pw")
    request = opener.calls[0]
    assert request.full_url == "https://www.trackiwi.com/api/login"
    assert json.loads(request.data) == {"email": "a@example.invalid", "password": "pw"}


def test_login_saves_config_owner_only():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    path = default_config_path()
    assert json.loads(path.read_text())["token"] == "tok"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_load_restores_a_saved_session():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    restored = Client.load()
    assert restored.token == "tok"
    assert restored.authenticated is True


def test_load_without_config_is_unauthenticated():
    assert Client.load().authenticated is False


@pytest.mark.parametrize("status", [401, 412])
def test_bad_credentials_raise_autherror(status):
    opener = FakeOpener(FakeResponse(b'{"message":"nope"}', status=status))
    with pytest.raises(AuthError):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_409_explains_account_needs_attention():
    opener = FakeOpener(FakeResponse(b"", status=409))
    with pytest.raises(TrackiwiError, match="attention"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_503_reports_maintenance():
    opener = FakeOpener(FakeResponse(b"", status=503))
    with pytest.raises(TrackiwiError, match="maintenance"):
        Client(opener=opener).login("a@example.invalid", "pw")


def test_logout_revokes_then_deletes_config():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=204))
    client = Client.load(opener=opener)
    client.logout()
    assert opener.calls[0].get_method() == "DELETE"
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/session"
    assert not default_config_path().exists()


def test_logout_clears_local_state_even_if_revoke_fails():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=500))
    Client.load(opener=opener).logout()
    assert not default_config_path().exists()


def test_authenticated_requests_send_bearer_token():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b'{"token":"tok","user":{"id":42}}'))
    assert Client.load(opener=opener).session_ok() is True
    assert opener.calls[0].get_header("Authorization") == "Bearer tok"


def test_session_ok_is_false_when_rejected():
    Client(opener=FakeOpener(login_response())).login("a@example.invalid", "pw")
    opener = FakeOpener(FakeResponse(b"", status=401))
    assert Client.load(opener=opener).session_ok() is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_client_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'Client'`

- [ ] **Step 4: Append the transport and auth code to `trackiwi/client.py`**

Add these imports at the top of the file, after the existing `from __future__` line:

```python
import json
import os
import platform
import urllib.error
import urllib.request
from pathlib import Path
```

and extend the existing `from . import COLUMNS` line to:

```python
from . import COLUMNS, __version__
```

Then append to the end of the module:

```python
WEBSITE = "https://www.trackiwi.com"
APP_NAME = "trackiwi"
APP_VERSION = "0.0.0"
USER_AGENT = f"trackiwi-client/{__version__} (+python-urllib)"
DEFAULT_TIMEOUT = 30
SYNC_TIMEOUT = 60


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "config.json"


def _check(status: int, body: bytes) -> None:
    """Raise the right error for a non-2xx status, per the app's own handling."""
    if status < 400:
        return
    if status in (401, 412):
        raise AuthError("invalid credentials or expired session", status=status)
    if status == 409:
        raise TrackiwiError(
            "the account needs attention — open the trackiwi app and check", status=status
        )
    if status == 429:
        raise TrackiwiError("rate limited by trackiwi — wait, then re-run", status=status)
    if status == 503:
        raise TrackiwiError("trackiwi is in maintenance — try again later", status=status)
    detail = body[:200].decode("utf-8", "replace")
    raise TrackiwiError(f"API error {status}: {detail}", status=status)


class Client:
    """Read-only access to a trackiwi account.

    The API base is never hardcoded: it comes from the login response's
    `server` field. `opener` exists so tests can inject a fake transport.
    """

    def __init__(
        self,
        api_base: str | None = None,
        token: str | None = None,
        user_id: int | None = None,
        opener=None,
    ) -> None:
        self.api_base = api_base.rstrip("/") if api_base else None
        self.token = token
        self.user_id = user_id
        self.opener = opener or urllib.request.urlopen
        self.config_path = default_config_path()

    @property
    def authenticated(self) -> bool:
        return bool(self.token and self.api_base)

    @classmethod
    def load(cls, opener=None) -> Client:
        path = default_config_path()
        if not path.exists():
            return cls(opener=opener)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            api_base=data.get("api_base"),
            token=data.get("token"),
            user_id=data.get("user_id"),
            opener=opener,
        )

    def save(self) -> None:
        """Persist the session owner-only. The password is never stored."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_path.parent, 0o700)
        payload = {"api_base": self.api_base, "token": self.token, "user_id": self.user_id}
        self.config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(self.config_path, 0o600)

    def _request(
        self,
        method: str,
        url: str,
        body: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        authed: bool = False,
    ) -> tuple[int, dict, bytes]:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "App-Name": APP_NAME,
            "App-Version": APP_VERSION,
            "User-Platform": "python",
            "User-Device": "trackiwi-client",
            "User-OS": platform.platform(),
            "User-Timezone": "0",
        }
        if authed:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=timeout) as response:
                # The API may return a `trackiwi-app-command` header, which the
                # official client executes. We deliberately ignore it.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()
        except urllib.error.URLError as error:
            raise TrackiwiError(f"network error: {error.reason}") from error

    def _api(
        self, method: str, path: str, body: dict | None = None, timeout: int = DEFAULT_TIMEOUT
    ) -> tuple[int, dict, bytes]:
        if not self.authenticated:
            raise AuthError("not logged in — run 'trackiwi login'")
        return self._request(
            method, f"{self.api_base}{path}", body=body, timeout=timeout, authed=True
        )

    def login(self, email: str, password: str) -> dict:
        status, _, body = self._request(
            "POST", f"{WEBSITE}/api/login", body={"email": email, "password": password}
        )
        _check(status, body)
        data = json.loads(body)
        self.api_base = data["server"].rstrip("/")
        self.token = data["token"]
        self.user_id = data["user"]["id"]
        self.save()
        return data["user"]

    def session_ok(self) -> bool:
        try:
            status, _, _ = self._api("GET", "/api/v2/session")
        except TrackiwiError:
            return False
        return status < 400

    def logout(self) -> None:
        """Revoke server-side first; a local delete alone leaves a live token."""
        if self.authenticated:
            try:
                self._api("DELETE", "/api/v2/session")
            except TrackiwiError:
                pass
        self.config_path.unlink(missing_ok=True)
        self.token = None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_client_auth.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 6: Commit**

```bash
git add trackiwi/client.py tests/conftest.py tests/test_client_auth.py
git commit -m "feat: config storage, HTTP transport and authentication"
```

---

### Task 6: Trackers and incremental sync

**Files:**
- Modify: `trackiwi/client.py` (append to the `Client` class)
- Test: `tests/test_client_sync.py`

**Interfaces:**
- Consumes: `Client`, `_check`, `parse_positions`, `SYNC_TIMEOUT` from Tasks 2 and 5.
- Produces:
  - `Client.trackers() -> list[dict]`
  - `Client.sync(offset: int | None = None) -> Iterator[tuple[list[tuple], int, int | None]]` — yields `(rows, skipped, total)` per batch and stops when a batch is empty. `offset=None` requests `initial_sync`; otherwise it sends `{"offset": offset}`. `total` comes from the `trackiwi-position-count` header when present.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_client_sync.py`:

```python
import json

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi.client import Client

PAGE_1 = b"1,7,1758000000,120,31.0,-41.0,12,0.0,0,0,-71,9,98,4120\n"
PAGE_2 = b"2,7,1758000060,120,31.1,-41.1,12,0.0,0,0,-71,9,98,4120\n"


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))


def client(*responses):
    return Client(
        api_base="https://api.example.invalid",
        token="tok",
        user_id=42,
        opener=FakeOpener(*responses),
    )


def test_sync_pages_until_empty():
    c = client(
        FakeResponse(PAGE_1, headers={"trackiwi-position-count": "2"}),
        FakeResponse(PAGE_2),
        FakeResponse(b""),
    )
    batches = list(c.sync())
    assert [rows[0][0] for rows, _, _ in batches] == [1, 2]


def test_first_call_requests_initial_sync_then_offsets():
    opener = FakeOpener(FakeResponse(PAGE_1), FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync())
    assert json.loads(opener.calls[0].data) == {"initial_sync": True}
    assert json.loads(opener.calls[1].data) == {"offset": 1}


def test_resuming_from_an_offset_skips_initial_sync():
    opener = FakeOpener(FakeResponse(b""))
    c = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(c.sync(offset=99))
    assert json.loads(opener.calls[0].data) == {"offset": 99}


def test_total_comes_from_the_count_header():
    c = client(FakeResponse(PAGE_1, headers={"trackiwi-position-count": "7"}), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total == 7


def test_total_is_none_without_the_header():
    c = client(FakeResponse(PAGE_1), FakeResponse(b""))
    _, _, total = next(iter(c.sync()))
    assert total is None


def test_malformed_rows_are_counted_not_fatal():
    c = client(FakeResponse(PAGE_1 + b"broken,row\n"), FakeResponse(b""))
    _, skipped, _ = next(iter(c.sync()))
    assert skipped == 1


def test_sync_posts_to_the_api_base():
    opener = FakeOpener(FakeResponse(b""))
    client = Client(api_base="https://api.example.invalid", token="tok", opener=opener)
    list(client.sync())
    assert opener.calls[0].full_url == "https://api.example.invalid/api/v2/trackers/sync"


def test_trackers_accepts_a_bare_list():
    c = client(FakeResponse(b'[{"id":7,"name":"Bus"}]'))
    assert c.trackers()[0]["name"] == "Bus"


def test_trackers_accepts_a_data_envelope():
    c = client(FakeResponse(b'{"data":[{"id":7,"name":"Bus"}]}'))
    assert c.trackers()[0]["id"] == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_client_sync.py -v`
Expected: FAIL — `AttributeError: 'Client' object has no attribute 'sync'`

- [ ] **Step 3: Append `trackers` and `sync` to the `Client` class**

Add to the top of `trackiwi/client.py`'s import block:

```python
from collections.abc import Iterator
```

Then append these two methods inside `class Client`:

```python
    def trackers(self) -> list[dict]:
        """List the account's trackers.

        The response envelope is not documented; both a bare list and a
        `{"data": [...]}` wrapper are accepted (design spec, section 10.3).
        """
        status, _, body = self._api("GET", "/api/v2/trackers")
        _check(status, body)
        data = json.loads(body)
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return data

    def sync(self, offset: int | None = None) -> Iterator[tuple[list[tuple], int, int | None]]:
        """Yield `(rows, skipped, total)` batches until the server runs dry.

        Offset-based and therefore resumable: if this fails part-way, simply
        running it again continues from the highest id already stored. That is
        why there is no retry logic anywhere in this client.
        """
        total: int | None = None
        while True:
            payload = {"initial_sync": True} if offset is None else {"offset": offset}
            status, headers, body = self._api(
                "POST", "/api/v2/trackers/sync", body=payload, timeout=SYNC_TIMEOUT
            )
            _check(status, body)
            if total is None:
                raw_total = headers.get("trackiwi-position-count")
                total = int(raw_total) if raw_total else None
            rows, skipped = parse_positions(body.decode("utf-8", "replace"))
            if not rows:
                return
            yield rows, skipped, total
            offset = max(row[0] for row in rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_client_sync.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Write the live contract test**

This is how the spec's open questions (section 10) get answered against reality
instead of guessed. It never runs in CI. Create `tests/test_live.py`:

```python
"""Live contract test. Requires a real session and TRACKIWI_LIVE=1.

Run with:  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v

It prints what it learns so the design spec's open questions (section 10) can
be answered from real data. It asserts only shape, never content, and must
never print the token.
"""

import os

import pytest

from trackiwi import COLUMNS
from trackiwi.client import Client

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACKIWI_LIVE") != "1", reason="set TRACKIWI_LIVE=1 to run"
)


@pytest.mark.live
def test_live_contract():
    client = Client.load()
    assert client.authenticated, "run 'trackiwi login' first"

    trackers = client.trackers()
    assert isinstance(trackers, list) and trackers
    print("\ntracker keys:", sorted(trackers[0]))

    rows, skipped, total = next(iter(client.sync()))
    assert rows and len(rows[0]) == len(COLUMNS)
    sample = dict(zip(COLUMNS, rows[0], strict=True))

    print("position-count header:", total)
    print("skipped rows in first page:", skipped)
    # Answers open questions 10.5 and 10.6: a plausible recent epoch-second
    # value confirms the unit; the rest shows the magnitude of each field.
    for field in ("fix_at", "fix_timezone", "speed", "altitude", "distance"):
        print(f"{field} = {sample[field]!r}")
    assert 1_600_000_000 < sample["fix_at"] < 2_000_000_000, "fix_at is not epoch seconds"
```

- [ ] **Step 6: Run the whole suite and the gate**

Run: `./tools/ci.sh`
Expected: all hooks and all tests pass; the live test is skipped.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/client.py tests/test_client_sync.py tests/test_live.py
git commit -m "feat: tracker listing and resumable incremental sync"
```

---

### Task 7: Command-line interface

**Files:**
- Create: `trackiwi/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Client`, `TrackiwiError`, `AuthError` (Tasks 2/5/6); `Store`, `default_db_path` (Task 3); `FORMATS` (Task 4).
- Produces: `trackiwi.cli.main(argv: list[str] | None = None) -> int` — the console-script entry point declared in `pyproject.toml`. Exit codes: `0` success, `1` general error, `2` authentication required.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
import json

import pytest

from trackiwi.cli import main
from trackiwi.store import Store


def row(pos_id=1, fix_at=1758000000):
    return (pos_id, 7, fix_at, 120, 31.0, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return tmp_path


def seed_cache():
    with Store() as store:
        store.upsert([row(1), row(2, fix_at=1758000060)])


def test_export_writes_gpx_to_stdout(capsys):
    seed_cache()
    assert main(["export", "--format", "gpx"]) == 0
    assert "<trkpt" in capsys.readouterr().out


def test_export_writes_to_a_file(tmp_path):
    seed_cache()
    target = tmp_path / "out.geojson"
    assert main(["export", "--format", "geojson", "-o", str(target)]) == 0
    assert json.loads(target.read_text())["features"][0]["geometry"]["type"] == "LineString"


def test_export_filters_by_date(capsys):
    seed_cache()
    assert main(["export", "--format", "csv", "--from", "2000-01-01", "--to", "2000-01-02"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 1  # header only


def test_export_rejects_a_bad_date(capsys):
    seed_cache()
    assert main(["export", "--format", "csv", "--from", "not-a-date"]) == 1
    assert "date" in capsys.readouterr().err.lower()


def test_commands_requiring_auth_exit_2(capsys):
    assert main(["trackers"]) == 2
    assert "login" in capsys.readouterr().err


def test_purge_deletes_the_cache(capsys):
    seed_cache()
    from trackiwi.store import default_db_path

    assert default_db_path().exists()
    assert main(["purge", "--yes"]) == 0
    assert not default_db_path().exists()


def test_purge_without_confirmation_refuses(capsys):
    seed_cache()
    assert main(["purge"]) == 1
    assert "--yes" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trackiwi.cli'`

- [ ] **Step 3: Write `trackiwi/cli.py`**

```python
"""Command-line interface.

Wires the client, the store and the exporters together and owns all user
interaction. The password is read with `getpass`, never echoed, never logged
and never written to disk.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import UTC, datetime

from .client import AuthError, Client, TrackiwiError
from .export import FORMATS
from .store import Store, default_db_path


def _epoch(date_text: str, end_of_day: bool = False) -> int:
    """Parse YYYY-MM-DD as an inclusive UTC bound."""
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise TrackiwiError(f"invalid date {date_text!r}, expected YYYY-MM-DD") from error
    if end_of_day:
        day = day.replace(hour=23, minute=59, second=59)
    return int(day.timestamp())


def cmd_login(args: argparse.Namespace) -> int:
    if args.token and args.api_base:
        client = Client(api_base=args.api_base, token=args.token)
        client.save()
        print("Session stored. Run 'trackiwi trackers' to verify it.")
        return 0
    email = args.email or input("trackiwi email: ").strip()
    password = getpass.getpass("trackiwi password: ")
    user = Client().login(email, password)
    print(f"Logged in as user {user.get('id')}.")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    Client.load().logout()
    print("Session revoked and local credentials removed.")
    return 0


def cmd_trackers(_: argparse.Namespace) -> int:
    for tracker in Client.load().trackers():
        print(f"{tracker.get('id')}\t{tracker.get('name', '(unnamed)')}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    client = Client.load()
    with Store() as store:
        offset = None if args.full else store.max_id()
        written = skipped_total = 0
        for rows, skipped, total in client.sync(offset=offset):
            written += store.upsert(rows)
            skipped_total += skipped
            suffix = f" of {total}" if total else ""
            print(f"\rsynced {written}{suffix} positions", end="", file=sys.stderr)
        print(file=sys.stderr)
        if skipped_total:
            print(f"skipped {skipped_total} malformed rows", file=sys.stderr)
        print(f"{written} new positions, {store.count()} cached in total")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    start = _epoch(args.start) if args.start else None
    end = _epoch(args.end, end_of_day=True) if args.end else None
    with Store() as store:
        rows = store.query(tracker_id=args.tracker, start=start, end=end)
    text = FORMATS[args.format](rows)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"wrote {len(rows)} positions to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if not args.yes:
        raise TrackiwiError(f"this deletes {default_db_path()} — pass --yes to confirm")
    with Store() as store:
        store.purge()
    print("Local position cache deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trackiwi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="authenticate and store the session")
    login.add_argument("--email")
    login.add_argument("--token", help="use an existing token instead of a password")
    login.add_argument("--api-base", help="API base that goes with --token")
    login.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="revoke the session").set_defaults(func=cmd_logout)
    sub.add_parser("trackers", help="list trackers").set_defaults(func=cmd_trackers)

    sync = sub.add_parser("sync", help="fetch new positions into the local cache")
    sync.add_argument("--full", action="store_true", help="restart from the beginning")
    sync.set_defaults(func=cmd_sync)

    export = sub.add_parser("export", help="export cached positions")
    export.add_argument("--format", choices=sorted(FORMATS), required=True)
    export.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    export.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    export.add_argument("--tracker", type=int, help="limit to one tracker id")
    export.add_argument("-o", "--output", help="write to a file instead of stdout")
    export.set_defaults(func=cmd_export)

    purge = sub.add_parser("purge", help="delete the local position cache")
    purge.add_argument("--yes", action="store_true", help="confirm deletion")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthError as error:
        print(f"authentication required: {error}", file=sys.stderr)
        print("Run 'trackiwi login'.", file=sys.stderr)
        return 2
    except TrackiwiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Verify the console script works**

```bash
.venv/bin/pip install -e .
.venv/bin/trackiwi --help
.venv/bin/trackiwi export --help
```

Expected: both print help without a traceback.

- [ ] **Step 6: Run the gate and commit**

```bash
./tools/ci.sh
git add trackiwi/cli.py tests/test_cli.py
git commit -m "feat: command-line interface"
```

---

### Task 8: Documentation

**Files:**
- Create: `README.md`
- Create: `CLAUDE.md`

**Interfaces:**
- Consumes: the CLI surface from Task 7.
- Produces: nothing importable.

- [ ] **Step 1: Write `README.md`**

The security section is not decoration — the accepted risks in the spec (sections 7.1 and 7.3) are only acceptable if they are written down where the user will see them.

````markdown
# trackiwi-client

Read-only Python client for pulling positions out of a personal
[trackiwi](https://www.trackiwi.com) account: sync them into a local SQLite
cache, export them as GPX, GeoJSON or CSV.

Unofficial and unaffiliated. trackiwi publishes no API; this talks to the
private API its own app uses, which can change without notice.

## Install

The system `python3` on macOS is 3.9; this needs 3.11+.

```bash
/opt/local/bin/python3.13 -m venv .venv
.venv/bin/pip install -e .
```

## Use

```bash
trackiwi login                 # prompts for email and password
trackiwi trackers              # list your devices
trackiwi sync                  # fetch new positions (resumable)
trackiwi export --format gpx --from 2026-09-16 --to 2026-09-25 -o route.gpx
trackiwi logout                # revokes the session server-side
trackiwi purge --yes           # delete the local cache
```

`sync` is incremental and offset-based, so if it is interrupted, just run it
again — it resumes from the last position stored. There is no retry logic
precisely because re-running is the retry.

If you would rather not type your password, log in elsewhere and reuse the
session:

```bash
trackiwi login --token <token> --api-base <server>
```

## Security

**The local cache is the most sensitive thing this tool creates.**
`~/.local/share/trackiwi/positions.db` is a complete movement history of a
vehicle: where it is kept, daily patterns, and when it is away. It is created
mode `0600` in a `0700` directory, but **it will be swept into Time Machine and
any cloud backup**. Delete it with `trackiwi purge --yes` when you no longer
need it.

**The token grants live location, not just history.** It is stored in
`~/.config/trackiwi/config.json` mode `0600`. That file is readable by any
process running as you, and is captured in backups as plaintext. Moving it to
the macOS Keychain would be a real improvement and is the recommended upgrade.
Your password is never stored.

`logout` revokes the session server-side before deleting the local copy;
deleting a local copy of a still-valid token would be fake security.

**Never share a trackiwi URL containing a `token=` parameter.** Their app
accepts `?token=...&apibase=...` for auto-login, so such a link hands over full
account access, including live location.

This client is read-only. It cannot reconfigure your tracker, disarm alarms, or
publish a share link — by construction, so that a bug cannot do those things
either.

## Development

```bash
./tools/ci.sh     # the same gates CI runs
```

No private data may enter this repository. A pre-commit hook blocks databases,
track exports outside `tests/fixtures/synthetic-*`, credential files and
token-shaped strings; `.gitignore` and CI enforce the same rules independently.
Test fixtures are synthetic: invented coordinates, invented IDs.
````

- [ ] **Step 2: Write `CLAUDE.md`**

```markdown
# CLAUDE.md

Unofficial read-only client for the trackiwi GPS API. Spec:
`docs/superpowers/specs/2026-09-17-trackiwi-client-design.md`.

## Rules

- **Zero runtime dependencies.** Standard library only. Dev tools go in
  `[project.optional-dependencies].dev`.
- **Read-only.** The only state-changing call allowed is `DELETE /api/v2/session`
  in `logout`. Never add writes to tours, markers, alarms or shares.
- **No private data in this repo, ever.** Fixtures are synthetic. There is no
  record-from-live mode, deliberately.
- **Never log or print the token.**
- **Never hardcode the API base** — it comes from the login response's `server`.
- **Ignore the `trackiwi-app-command` response header.** Never act on it.
- No retry logic: `sync` is offset-based and resumable, so re-running is the retry.

## Workflow

- Branch, then PR into `main`. Branch protection requires a PR; CodeRabbit
  reviews it. Do not self-merge past unresolved CodeRabbit threads.
- Commit prefixes: `feat:`, `fix:`, `docs:`, `test:`, `chore:`.
- Local gate: `./tools/ci.sh` — runs `pre-commit run --all-files` and `pytest`,
  mirroring CI exactly.

## Gotchas

- System `/usr/bin/python3` is 3.9.6 (Xcode); this project needs 3.11+. Use
  `/opt/local/bin/python3.13` (MacPorts).
- `pytest` skips live tests by default (`addopts = -m 'not live'`). Run them
  with `TRACKIWI_LIVE=1 pytest -m live`.
- `fix_at` is normalised to epoch seconds at parse time. Nothing downstream
  should handle milliseconds.
```

- [ ] **Step 3: Verify the gate still passes and commit**

```bash
./tools/ci.sh
git add README.md CLAUDE.md
git commit -m "docs: README and CLAUDE.md"
```

---

### Task 9: Publish to GitHub with CI, CodeRabbit and branch protection

**Files:**
- Create: `.github/workflows/ci.yml`, `.coderabbit.yaml`, `.github/dependabot.yml`, `.github/workflows/dependabot-auto-merge.yml` (all generated by the skill below)

**Interfaces:**
- Consumes: the finished package and `tools/ci.sh` from Tasks 1–8.
- Produces: a private GitHub repository with green CI.

**This task must run in the main session, not a subagent** — the setup skill conducts an interview with the user, and a subagent cannot reach them.

- [ ] **Step 1: Invoke the setup skill**

Use the `github-project-setup` skill. It inspects the repo first, then interviews once. Answer its interview with the decisions already made here:

- Project type: Python, `requires-python >=3.11`; matrix `3.11` and `3.13`
- Docs: **no** Sphinx/Pages for now — Pages on a private repo needs a paid plan; add both when the repo is published
- CI stages: lint + test (`pre-commit run --all-files` once on 3.13, `pytest` across the matrix)
- CodeRabbit: **yes**
- Visibility: **private**; licence deferred until publication
- Badge row: CI + language (no licence badge yet)
- Branch protection: **yes** — solo repo, so `required_approving_review_count: 0`, `enforce_admins: true`, `required_conversation_resolution: true`
- Dependabot: **yes**, with the `workflow_run`-triggered auto-merge for minor/patch only

- [ ] **Step 2: Verify the CI workflow runs `pre-commit`**

The private-data guard is only a real backstop if CI runs it, because `git commit --no-verify` bypasses the local hook. Confirm the generated `.github/workflows/ci.yml` contains a step running `pre-commit run --all-files`, and that the workflow has a top-level `permissions: {contents: read}` block. Add them if the template omitted either.

- [ ] **Step 3: Watch the first run go green**

```bash
gh run list --limit 1
gh run watch
```

Expected: `completed / success`. The task is not done until this is observed, not assumed.

- [ ] **Step 4: Report remaining manual steps**

Tell the user explicitly that installing the CodeRabbit app at
<https://github.com/apps/coderabbitai> is a browser step that cannot be
automated, and that the repository is private until they choose to publish.

---

## Completion checklist

- [ ] `./tools/ci.sh` passes from a clean checkout
- [ ] `git log` shows one commit per task
- [ ] No `.db`, `.gpx`, `.geojson` or `.csv` file exists in the repository outside `tests/fixtures/synthetic-*`
- [ ] `grep -ri "token" --include="*.py" trackiwi/` shows no logging or printing of the token value
- [ ] First CI run observed green
- [ ] Spec section 10's open questions answered against live responses, and the spec updated with the findings
