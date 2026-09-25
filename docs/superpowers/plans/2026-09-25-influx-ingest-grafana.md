# InfluxDB Ingest + Grafana Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror the trackiwi position history (backfill and ongoing) from the local SQLite cache into InfluxDB, and ship a portable Flux Grafana dashboard plus Docker Compose and systemd deployment templates. None of it may carry deployment-specific configuration.

**Architecture:** Data flows in two stages: `trackiwi sync` fills the existing SQLite cache, then a new mirror step writes new rows to InfluxDB as line protocol. A `mirror_state` table records how far each target has been mirrored, and it advances only after InfluxDB acknowledges a batch. Two new modules divide the work: `lineprotocol.py` is pure formatting with no I/O, and `influx.py` is the only module that talks to InfluxDB (config, version detection, writes, checks, mirror loop). Deployment templates live under `deploy/`, and the dashboard is Flux-only JSON parameterised by variables.

**Tech Stack:** Python ≥ 3.11 standard library only (`urllib`, `gzip`, `tomllib`, `json`, `sqlite3`, `base64`); pytest; InfluxDB 2.x/1.x HTTP APIs; Grafana ≥ 10 (geomap route layer); Docker Compose; systemd user units.

**Spec:** `docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md` (builds on `docs/superpowers/specs/2026-09-17-trackiwi-client-design.md`).

## Global Constraints

- **Zero runtime dependencies.** The package imports only the standard library. `tests/test_invariants.py::test_package_imports_only_stdlib` enforces this for every `trackiwi/*.py`.
- **Module boundaries:**
  - `client.py` is the only module that talks to trackiwi.
  - `influx.py` is the only module that talks to InfluxDB.
  - `store.py` is the only module that touches SQLite.
  - `lineprotocol.py` does no I/O.
  - `influx.py` and `client.py` never import each other.
  - Shared exceptions (`TrackiwiError`, `AuthError`) come from `trackiwi/__init__.py`.
- **Read-only against trackiwi:** the only state-changing trackiwi request stays `DELETE /api/v2/session`. The only InfluxDB writes are `trackiwi_position` points to the one configured target.
- **Secrets:** the InfluxDB token and password are never printed or logged. Any server-controlled text is redacted before it is shown. `influx.toml` is `0600`.
- **No private data in the repo:** every example and fixture uses placeholders or synthetic values. Never commit a real host, IP, token, tracker id, tracker name or coordinate. The existing guard blocks any filename starting with `.env.`, so the committed env template is **`deploy/example.env`**. This deliberately replaces the spec's `deploy/.env.example`; Task 7 updates the spec.
- **`.gitignore` ignores `*.json`.** The dashboard needs the negation `!deploy/grafana-dashboards/*.json` (Task 8).
- **mypy strict:** every new test module must be added to the `module = [...]` list of the `[[tool.mypy.overrides]]` block in `pyproject.toml`, or strict mode rejects its unannotated test functions.
- **Traceability gate:** every new `req` in `docs/requirements.rst` needs a `test` with `:verifies:` in `docs/traceability.rst`, added in the same task, or `sphinx-build -W` fails. Implementing code references its requirement with ``:need:`REQ_…` `` in the docstring.
- **Docstrings:** every public function, class and method gets a docstring (interrogate `fail-under = 100`).
- **Gates stay green after every task:** `./tools/ci.sh` (pre-commit, mypy, pytest + coverage ≥ 95, doctests, sphinx `-W`, interrogate).
- **Format before the gate:** run `.venv/bin/ruff format <changed .py files> && .venv/bin/ruff check --fix <changed .py files>` before `./tools/ci.sh`. The code in this plan is written for correctness, not wrapped to the 100-column limit, and ruff-format rewraps it. If E501 still fires on a string literal, split the literal. Don't add `noqa`.
- Commit messages end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX
  ```

## Review Focus

Five inputs the spec implies but doesn't spell out, most likely first. Each is pinned by a test in its owning task:

1. **A tracker name containing spaces, commas, `=` or a newline** (the default app name has a space). Without escaping, InfluxDB rejects the whole batch with 400. Expected: correct escaping, newline replaced by a space → Task 1.
2. **A row whose optional columns are NULL** (altitude, speed, battery, … missing). Expected: those fields are omitted, never written as `None`, and the line stays valid → Task 1.
3. **Nothing new since the last push**, or an empty cache. Expected: `push` sends zero requests, reports 0, exits 0 → Task 5.
4. **A URL written with a trailing slash** (`http://host:8086/`). Expected: no `//write` double slash → Task 3.
5. **A token file ending in a newline** (what `echo … > file` produces). Expected: the newline is stripped, not sent inside the `Authorization` header → Task 3.

---

## File Structure

| File | Responsibility |
|---|---|
| `trackiwi/lineprotocol.py` (new) | Pure: one cache row → one line-protocol string (escaping, unit normalisation) |
| `trackiwi/influx.py` (new) | `InfluxConfig` + `load_config`, `InfluxWriter` (version detection, `write`, `check`, `target_key`), `mirror()` loop |
| `trackiwi/store.py` (modify) | Add `mirror_state` + `tracker_names` tables and their accessors, plus `rows_after` |
| `trackiwi/cli.py` (modify) | `influx check`, `influx push`, `ingest`; factor the sync body into `_sync_into_cache` |
| `trackiwi/__init__.py` (modify) | Update the module docstring for the rescoped read-only rule |
| `examples/influx.example.toml` (new) | Placeholder config |
| `deploy/docker-compose.yml`, `deploy/example.env`, `deploy/ingest/Dockerfile`, `deploy/grafana-provisioning/{datasources,dashboards}/trackiwi.yaml` (new) | Turnkey stack |
| `deploy/systemd/trackiwi-ingest.{service,timer}` (new) | Bring-your-own-InfluxDB route |
| `deploy/grafana-dashboards/trackiwi.json` (new) | Portable Flux dashboard |
| `.dockerignore` (new) | Keeps private data and the venv out of the image |
| `tests/test_lineprotocol.py`, `test_store_mirror.py`, `test_influx_config.py`, `test_influx_writer.py`, `test_influx_mirror.py`, `test_cli_influx.py`, `test_deploy.py`, `test_dashboard.py` (new) | One per unit |
| `docs/requirements.rst`, `docs/traceability.rst`, `.gitignore`, `.pre-commit-config.yaml`, `pyproject.toml`, `README.md`, `CLAUDE.md`, `llms.txt` (modify) | Traceability, hygiene, docs |

---

### Task 1: Line protocol formatting

**Files:**
- Create: `trackiwi/lineprotocol.py`
- Create: `tests/test_lineprotocol.py`
- Modify: `pyproject.toml` (mypy overrides list), `docs/requirements.rst`, `docs/traceability.rst`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `MEASUREMENT: str = "trackiwi_position"`
  - `format_point(row: Row, tracker_name: str | None) -> str`, where `Row` is any object supporting `row["<column>"]` (a `sqlite3.Row` or a `dict`) with the keys in `trackiwi.COLUMNS`.

- [ ] **Step 1: Write the failing tests**

`tests/test_lineprotocol.py`:

```python
"""Line-protocol formatting: escaping, units, NULL handling (REQ_LINEPROTOCOL_UNITS)."""

import math

import pytest

from trackiwi.lineprotocol import MEASUREMENT, format_point


def _row(**overrides):
    row = {
        "id": 1,
        "tracker_id": 7,
        "fix_at": 1700000000,
        "fix_timezone": 1,
        "latitude": 31.0,
        "longitude": -41.0,
        "altitude": 12,
        "speed": 0.0,
        "course": 90,
        "distance": 150,
        "rssi": 5,
        "sat": 9,
        "battery": 99,
        "voltage": 1287,
    }
    row.update(overrides)
    return row


def test_full_row_formats_with_natural_units():
    line = format_point(_row(), "Bus")
    assert line == (
        "trackiwi_position,tracker_id=7,tracker_name=Bus "
        "lat=31.0,lon=-41.0,altitude_m=12.0,speed_kmh=0.0,course_deg=90.0,"
        "distance_m=1.5,voltage_v=12.87,battery_pct=99i,satellites=9i,"
        "gnss_quality=5i,fix_flag=1i 1700000000"
    )


def test_measurement_name_is_fixed():
    assert MEASUREMENT == "trackiwi_position"
    assert format_point(_row(), "Bus").startswith("trackiwi_position,")


def test_tag_value_escapes_space_comma_equals():
    line = format_point(_row(), "My Bus, v=2")
    assert ",tracker_name=My\\ Bus\\,\\ v\\=2 " in line


def test_newline_in_tag_value_becomes_space():
    line = format_point(_row(), "My\nBus")
    assert ",tracker_name=My\\ Bus " in line
    assert "\n" not in line


def test_missing_or_empty_name_falls_back_to_tracker_id():
    assert ",tracker_name=7 " in format_point(_row(), None)
    assert ",tracker_name=7 " in format_point(_row(), "")


def test_null_optional_columns_are_omitted():
    row = _row(
        altitude=None, speed=None, course=None, distance=None,
        rssi=None, sat=None, battery=None, voltage=None, fix_timezone=None,
    )
    line = format_point(row, "Bus")
    assert line == "trackiwi_position,tracker_id=7,tracker_name=Bus lat=31.0,lon=-41.0 1700000000"
    assert "None" not in line


def test_non_finite_value_is_rejected():
    with pytest.raises(ValueError):
        format_point(_row(latitude=math.inf), "Bus")
    with pytest.raises(ValueError):
        format_point(_row(speed=math.nan), "Bus")


def test_integer_fields_carry_the_i_suffix_and_floats_do_not():
    line = format_point(_row(), "Bus")
    fields = line.split(" ")[1].split(",")
    assert "battery_pct=99i" in fields
    assert "voltage_v=12.87" in fields
```

- [ ] **Step 2: Register the test module with mypy and run the tests to verify they fail**

In `pyproject.toml`, add `"test_lineprotocol",` to the `module = [...]` list of the `[[tool.mypy.overrides]]` block. Keep the list alphabetical, so it goes after `"test_invariants",`.

Run: `.venv/bin/pytest tests/test_lineprotocol.py -v`
Expected: collection error with `ModuleNotFoundError: No module named 'trackiwi.lineprotocol'`.

- [ ] **Step 3: Implement `trackiwi/lineprotocol.py`**

```python
"""Convert cached position rows to InfluxDB line protocol.

Pure: no I/O. Implements :need:`REQ_LINEPROTOCOL_UNITS` — fields are written
in natural units (distance cm → m, voltage cV → V, per the units verified
against the live API) so every consumer reads metres and volts, and the two
columns trackiwi misnames are renamed for what they are (``rssi`` is a 0–5
GNSS quality scale; ``fix_timezone`` is a 0/1 flag, not a timezone).

>>> row = {"id": 1, "tracker_id": 7, "fix_at": 1700000000, "fix_timezone": 1,
...        "latitude": 31.0, "longitude": -41.0, "altitude": 12, "speed": 0.0,
...        "course": 90, "distance": 150, "rssi": 5, "sat": 9, "battery": 99,
...        "voltage": 1287}
>>> head, fields, timestamp = format_point(row, "Bus").split(" ")
>>> head
'trackiwi_position,tracker_id=7,tracker_name=Bus'
>>> fields.split(",")[:3]
['lat=31.0', 'lon=-41.0', 'altitude_m=12.0']
>>> "distance_m=1.5" in fields, "voltage_v=12.87" in fields, "battery_pct=99i" in fields
(True, True, True)
>>> timestamp
'1700000000'
"""

from __future__ import annotations

import math
from typing import Any, Protocol

MEASUREMENT = "trackiwi_position"


class Row(Protocol):
    """Anything indexable by column name: a ``sqlite3.Row`` or a ``dict``."""

    def __getitem__(self, key: str, /) -> Any: ...


#: (field name, source column, divisor or None, "f" for float or "i" for integer)
#: Order is the order written; it is fixed so lines are deterministic.
_FIELDS: tuple[tuple[str, str, float | None, str], ...] = (
    ("lat", "latitude", None, "f"),
    ("lon", "longitude", None, "f"),
    ("altitude_m", "altitude", None, "f"),
    ("speed_kmh", "speed", None, "f"),
    ("course_deg", "course", None, "f"),
    ("distance_m", "distance", 100.0, "f"),
    ("voltage_v", "voltage", 100.0, "f"),
    ("battery_pct", "battery", None, "i"),
    ("satellites", "sat", None, "i"),
    ("gnss_quality", "rssi", None, "i"),
    ("fix_flag", "fix_timezone", None, "i"),
)


def _escape_tag(value: str) -> str:
    """Escape a tag key or value: comma, equals sign and space; newline → space."""
    value = value.replace("\r", " ").replace("\n", " ")
    return value.replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def _format_field(value: Any, kind: str, divisor: float | None) -> str:
    """Format one field value, rejecting non-finite numbers."""
    number = float(value)
    if divisor is not None:
        number = number / divisor
    if not math.isfinite(number):
        raise ValueError(f"non-finite field value: {value!r}")
    if kind == "i":
        return f"{int(number)}i"
    return repr(number)


def format_point(row: Row, tracker_name: str | None) -> str:
    """Return one line-protocol line for a cached position row.

    `tracker_name` becomes the ``tracker_name`` tag; when it is missing or
    empty the tracker id is used instead, because InfluxDB rejects an empty
    tag value. NULL optional columns are omitted rather than written.
    Raises ``ValueError`` for a non-finite value.
    """
    tracker_id = int(row["tracker_id"])
    name = tracker_name if tracker_name else str(tracker_id)
    tags = f"tracker_id={tracker_id},tracker_name={_escape_tag(name)}"
    fields = []
    for field, column, divisor, kind in _FIELDS:
        value = row[column]
        if value is None:
            continue
        fields.append(f"{field}={_format_field(value, kind, divisor)}")
    return f"{MEASUREMENT},{tags} {','.join(fields)} {int(row['fix_at'])}"
```

- [ ] **Step 4: Run the tests and the doctest to verify they pass**

Run: `.venv/bin/pytest tests/test_lineprotocol.py -v && .venv/bin/pytest --doctest-modules trackiwi/lineprotocol.py -q`
Expected: 8 passed, then 1 passed.

- [ ] **Step 5: Add the requirement and its traceability link**

Append to `docs/requirements.rst`, after the last existing `req` in the file:

```rst
.. req:: Positions are written to InfluxDB in natural units
   :id: REQ_LINEPROTOCOL_UNITS
   :tags: influx, data

   Each cached row becomes one ``trackiwi_position`` line-protocol point with
   tags ``tracker_id`` and ``tracker_name`` and fields in natural units
   (distance in metres, voltage in volts). NULL optional columns are omitted,
   tag values are escaped, and non-finite values are rejected. (Influx spec §4.)
```

Append to `docs/traceability.rst`:

```rst
.. test:: Line protocol uses natural units and escapes tags
   :id: TEST_LINEPROTOCOL_UNITS
   :verifies: REQ_LINEPROTOCOL_UNITS

   ``tests/test_lineprotocol.py::test_full_row_formats_with_natural_units``,
   ``tests/test_lineprotocol.py::test_tag_value_escapes_space_comma_equals``,
   ``tests/test_lineprotocol.py::test_null_optional_columns_are_omitted``,
   ``tests/test_lineprotocol.py::test_non_finite_value_is_rejected``
```

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes. Coverage stays ≥ 95. `sphinx-build` reports `req_without_test: passed`.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/lineprotocol.py tests/test_lineprotocol.py pyproject.toml docs/requirements.rst docs/traceability.rst
git commit -m "feat: format cached positions as InfluxDB line protocol

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 2: Store — mirror state, tracker names, rows after an id

**Files:**
- Modify: `trackiwi/store.py` (`_SCHEMA` and new methods on `Store`)
- Create: `tests/test_store_mirror.py`
- Modify: `pyproject.toml` (mypy overrides list)

**Interfaces:**
- Consumes: `Store` as it exists (context manager, `upsert`, `conn`).
- Produces (all methods on `Store`, used inside `with Store(path) as store:`):
  - `mirror_position(target: str) -> int`: the last mirrored id for `target`, or `0` if the target has never been mirrored.
  - `set_mirror_position(target: str, last_id: int) -> None`: records the position and commits immediately.
  - `rows_after(last_id: int, limit: int) -> list[sqlite3.Row]`: rows with `id > last_id`, ordered by `id`, at most `limit`.
  - `set_tracker_names(names: Mapping[int, str]) -> None`: upserts, then commits.
  - `tracker_names() -> dict[int, str]`

- [ ] **Step 1: Write the failing tests**

`tests/test_store_mirror.py`:

```python
"""Mirror bookkeeping in the cache: state per target, names, id-ordered paging."""

from trackiwi.store import Store


def _row(pid, tracker=7, fix_at=1700000000):
    return (pid, tracker, fix_at + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


def test_mirror_position_defaults_to_zero(tmp_path):
    with Store(tmp_path / "p.db") as store:
        assert store.mirror_position("target-a") == 0


def test_mirror_position_is_per_target_and_persists(tmp_path):
    path = tmp_path / "p.db"
    with Store(path) as store:
        store.set_mirror_position("target-a", 42)
        store.set_mirror_position("target-b", 7)
    with Store(path) as store:
        assert store.mirror_position("target-a") == 42
        assert store.mirror_position("target-b") == 7


def test_rows_after_pages_in_id_order(tmp_path):
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(3), _row(1), _row(2), _row(4)])
        assert [r["id"] for r in store.rows_after(0, 2)] == [1, 2]
        assert [r["id"] for r in store.rows_after(2, 10)] == [3, 4]
        assert store.rows_after(4, 10) == []


def test_tracker_names_round_trip_and_update(tmp_path):
    with Store(tmp_path / "p.db") as store:
        assert store.tracker_names() == {}
        store.set_tracker_names({7: "Bus", 8: "Car"})
        store.set_tracker_names({7: "Van"})
        assert store.tracker_names() == {7: "Van", 8: "Car"}


def test_existing_cache_gains_the_new_tables(tmp_path):
    """A cache created before this feature (no new tables) upgrades on open, keeping its rows."""
    import sqlite3

    path = tmp_path / "p.db"
    with Store(path) as store:
        store.upsert([_row(1)])
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE mirror_state")
    conn.execute("DROP TABLE tracker_names")
    conn.commit()
    conn.close()
    with Store(path) as store:
        assert store.mirror_position("t") == 0
        assert store.tracker_names() == {}
        assert store.count() == 1
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_store_mirror",` to the mypy overrides `module` list in `pyproject.toml`, after `"test_store",`.

Run: `.venv/bin/pytest tests/test_store_mirror.py -v`
Expected: FAIL with `AttributeError: 'Store' object has no attribute 'mirror_position'`.

- [ ] **Step 3: Extend the schema and add the methods**

In `trackiwi/store.py`, append these two tables to the `_SCHEMA` string, just before its closing `"""`:

```sql
CREATE TABLE IF NOT EXISTS mirror_state (
  target  TEXT PRIMARY KEY,
  last_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracker_names (
  tracker_id INTEGER PRIMARY KEY,
  name       TEXT NOT NULL
);
```

Add `from collections.abc import Iterable, Mapping`, replacing the existing `from collections.abc import Iterable` line. Then add these methods to `Store`, directly after `query`:

```python
    def mirror_position(self, target: str) -> int:
        """Return the highest id already mirrored to `target`, or 0 if none.

        Part of :need:`REQ_MIRROR_RESUME`: the mirror resumes from here.
        """
        row = self.conn.execute(
            "SELECT last_id FROM mirror_state WHERE target = ?", (target,)
        ).fetchone()
        last: int = row[0] if row is not None else 0
        return last

    def set_mirror_position(self, target: str, last_id: int) -> None:
        """Record that every row up to `last_id` reached `target`, and commit.

        Committed immediately so that a later failure in the same run cannot
        roll back progress that InfluxDB has already acknowledged.
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO mirror_state (target, last_id) VALUES (?, ?)",
            (target, last_id),
        )
        self.conn.commit()

    def rows_after(self, last_id: int, limit: int) -> list[sqlite3.Row]:
        """Return up to `limit` rows with ``id > last_id``, ordered by id."""
        return list(
            self.conn.execute(
                "SELECT * FROM positions WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, limit),
            )
        )

    def set_tracker_names(self, names: Mapping[int, str]) -> None:
        """Store the display name of each tracker id, replacing old names."""
        self.conn.executemany(
            "INSERT OR REPLACE INTO tracker_names (tracker_id, name) VALUES (?, ?)",
            list(names.items()),
        )
        self.conn.commit()

    def tracker_names(self) -> dict[int, str]:
        """Return every stored tracker id → display name."""
        return {
            int(tracker_id): str(name)
            for tracker_id, name in self.conn.execute(
                "SELECT tracker_id, name FROM tracker_names"
            )
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_store_mirror.py tests/test_store.py -v`
Expected: all pass, including the existing store tests.

- [ ] **Step 5: Record the names table in the spec**

In `docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md` §5.1, append this paragraph:

"The cache also gains a `tracker_names` table (`tracker_id INTEGER PRIMARY KEY, name TEXT NOT NULL`), refreshed by `ingest` from `trackers()`. `push` reads names from it rather than from the network. The tag value must be the same every time a row is sent, or re-sending would create a second series instead of overwriting the first, so the name has to be stored rather than fetched per push."

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/store.py tests/test_store_mirror.py pyproject.toml docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md
git commit -m "feat: track mirror progress and tracker names in the cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 3: InfluxDB configuration

**Files:**
- Create: `trackiwi/influx.py` (config part only; later tasks extend this file)
- Create: `tests/test_influx_config.py`
- Modify: `pyproject.toml` (mypy overrides), `docs/requirements.rst` (extend `REQ_CONFIG_MODE_0600`), `docs/traceability.rst` (extend `TEST_CONFIG_MODE_0600`)

**Interfaces:**
- Consumes: `TrackiwiError` from `trackiwi`.
- Produces:
  - `default_influx_config_path() -> Path`
  - `@dataclass(frozen=True) class InfluxConfig`. Fields:
    - `url: str`
    - `version: int | None` (`None` means auto-detect)
    - `org: str | None`, `bucket: str | None`, `database: str | None`, `username: str | None`
    - `token: str | None`, `password: str | None`, `token_env: str`
  - `load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> InfluxConfig`

- [ ] **Step 1: Write the failing tests**

`tests/test_influx_config.py`:

```python
"""InfluxDB target configuration: file < environment, token sources, 0600."""

import os
import stat

import pytest

from trackiwi import TrackiwiError
from trackiwi.influx import default_influx_config_path, load_config


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_default_path_honours_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_influx_config_path() == tmp_path / "trackiwi" / "influx.toml"


def test_file_values_load(tmp_path):
    path = _write(
        tmp_path / "influx.toml",
        'url = "http://localhost:8086"\nversion = 2\norg = "home"\nbucket = "trackiwi"\n',
    )
    cfg = load_config(path, env={"TRACKIWI_INFLUX_TOKEN": "tok"})
    assert cfg.url == "http://localhost:8086"
    assert cfg.version == 2
    assert (cfg.org, cfg.bucket, cfg.token) == ("home", "trackiwi", "tok")


def test_environment_overrides_file(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://file:8086"\nbucket = "a"\n')
    cfg = load_config(path, env={"TRACKIWI_INFLUX_URL": "http://env:8086", "TRACKIWI_INFLUX_BUCKET": "b"})
    assert cfg.url == "http://env:8086"
    assert cfg.bucket == "b"


def test_environment_alone_is_enough(tmp_path):
    cfg = load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://h:8086"})
    assert cfg.url == "http://h:8086"
    assert cfg.version is None


def test_trailing_slash_is_removed_from_url(tmp_path):
    cfg = load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://h:8086/"})
    assert cfg.url == "http://h:8086"


def test_version_values(tmp_path):
    for raw, expected in (("auto", None), ("1", 1), ("2", 2)):
        env = {"TRACKIWI_INFLUX_URL": "http://h", "TRACKIWI_INFLUX_VERSION": raw}
        assert load_config(tmp_path / "missing.toml", env=env).version == expected
    with pytest.raises(TrackiwiError, match="version"):
        load_config(tmp_path / "missing.toml", env={"TRACKIWI_INFLUX_URL": "http://h", "TRACKIWI_INFLUX_VERSION": "3"})


def test_missing_url_names_the_key_and_file(tmp_path):
    with pytest.raises(TrackiwiError, match="url.*TRACKIWI_INFLUX_URL"):
        load_config(tmp_path / "missing.toml", env={})


def test_unknown_key_is_rejected(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\ntoken = "plaintext"\n')
    with pytest.raises(TrackiwiError, match="unknown key 'token'"):
        load_config(path, env={})


def test_invalid_toml_is_a_clear_error(tmp_path):
    path = _write(tmp_path / "influx.toml", "url = \n")
    with pytest.raises(TrackiwiError, match="not valid TOML"):
        load_config(path, env={})


def test_token_env_names_the_variable_holding_the_token(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\ntoken_env = "INFLUXDB_TOKEN"\n')
    cfg = load_config(path, env={"INFLUXDB_TOKEN": "from-other-var"})
    assert cfg.token == "from-other-var"
    assert cfg.token_env == "INFLUXDB_TOKEN"


def test_token_file_is_read_and_trailing_newline_stripped(tmp_path):
    token_file = _write(tmp_path / "influx.token", "tok-from-file\n")
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\ntoken_file = "{token_file}"\n')
    assert load_config(path, env={}).token == "tok-from-file"


def test_token_env_wins_over_token_file(tmp_path):
    token_file = _write(tmp_path / "influx.token", "from-file")
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\ntoken_file = "{token_file}"\n')
    assert load_config(path, env={"TRACKIWI_INFLUX_TOKEN": "from-env"}).token == "from-env"


def test_missing_token_file_names_the_file(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\ntoken_file = "/nonexistent/influx.token"\n')
    with pytest.raises(TrackiwiError, match="/nonexistent/influx.token"):
        load_config(path, env={})


def test_password_file_is_read(tmp_path):
    pw = _write(tmp_path / "influx.password", "s3cret\n")
    path = _write(tmp_path / "influx.toml", f'url = "http://h"\nversion = 1\ndatabase = "d"\npassword_file = "{pw}"\n')
    assert load_config(path, env={}).password == "s3cret"  # pragma: allowlist secret


def test_load_narrows_a_widened_config_file(tmp_path):
    path = _write(tmp_path / "influx.toml", 'url = "http://h"\n')
    os.chmod(path, 0o644)
    load_config(path, env={})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_influx_config",` to the mypy overrides list in `pyproject.toml`, after `"test_export",`.

Run: `.venv/bin/pytest tests/test_influx_config.py -v`
Expected: collection error with `ModuleNotFoundError: No module named 'trackiwi.influx'`.

- [ ] **Step 3: Implement the config part of `trackiwi/influx.py`**

```python
"""Write cached positions to InfluxDB.

The only module that talks to InfluxDB. It never imports `client.py`, and
`client.py` never imports it. Configuration lives in
``~/.config/trackiwi/influx.toml`` and every key can be overridden by an
environment variable named ``TRACKIWI_INFLUX_<KEY>``. Secrets are never
stored in the TOML file: the token comes from the variable named by
``token_env`` or from ``token_file``.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import TrackiwiError

ENV_PREFIX = "TRACKIWI_INFLUX_"
DEFAULT_TOKEN_ENV = "TRACKIWI_INFLUX_TOKEN"
_KEYS = (
    "url",
    "version",
    "org",
    "bucket",
    "database",
    "username",
    "token_env",
    "token_file",
    "password_file",
)


def default_influx_config_path() -> Path:
    """Return the InfluxDB config path, honouring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "influx.toml"


@dataclass(frozen=True)
class InfluxConfig:
    """A resolved InfluxDB target. `version` None means auto-detect."""

    url: str
    version: int | None
    org: str | None
    bucket: str | None
    database: str | None
    username: str | None
    token: str | None
    password: str | None
    token_env: str


def _read_file(path: Path) -> dict[str, Any]:
    """Read the TOML file, narrowing a widened mode first (REQ_CONFIG_MODE_0600)."""
    if not path.exists():
        return {}
    # A config widened by a restore or a copy is narrowed back, mirroring the
    # trackiwi session file. Failing to chmod must not stop the load.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise TrackiwiError(f"{path} is not valid TOML: {error}") from error
    except OSError as error:
        raise TrackiwiError(f"cannot read {path}: {error}") from error
    for key in data:
        if key not in _KEYS:
            raise TrackiwiError(
                f"{path}: unknown key '{key}' (allowed: {', '.join(_KEYS)}); "
                "secrets belong in token_file or an environment variable"
            )
    return data


def _read_secret(path_text: str, what: str) -> str:
    """Read a secret from a file, stripping surrounding whitespace."""
    path = Path(path_text).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TrackiwiError(f"cannot read {what} file {path}: {error.strerror}") from error


def _parse_version(raw: Any) -> int | None:
    """Map the configured version to 1, 2, or None (auto)."""
    text = str(raw).strip().lower()
    if text in ("", "auto"):
        return None
    if text in ("1", "2"):
        return int(text)
    raise TrackiwiError(f"invalid InfluxDB version {raw!r}: use \"auto\", 1 or 2")


def load_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> InfluxConfig:
    """Resolve the InfluxDB target from the file and the environment.

    Precedence: environment > file > default. Implements
    :need:`REQ_CONFIG_MODE_0600` for ``influx.toml`` (see `_read_file`).
    """
    path = path if path is not None else default_influx_config_path()
    env = env if env is not None else os.environ
    merged: dict[str, Any] = dict(_read_file(path))
    for key in _KEYS:
        value = env.get(ENV_PREFIX + key.upper())
        if value is not None and value != "":
            merged[key] = value
    url = str(merged.get("url", "")).strip().rstrip("/")
    if not url:
        raise TrackiwiError(
            f"InfluxDB 'url' is not configured: set it in {path} or TRACKIWI_INFLUX_URL"
        )
    token_env = str(merged.get("token_env") or DEFAULT_TOKEN_ENV)
    token = env.get(token_env) or None
    if token is None and merged.get("token_file"):
        token = _read_secret(str(merged["token_file"]), "token")
    password = env.get(ENV_PREFIX + "PASSWORD") or None
    if password is None and merged.get("password_file"):
        password = _read_secret(str(merged["password_file"]), "password")

    def text(key: str) -> str | None:
        value = merged.get(key)
        return str(value) if value not in (None, "") else None

    return InfluxConfig(
        url=url,
        version=_parse_version(merged.get("version", "auto")),
        org=text("org"),
        bucket=text("bucket"),
        database=text("database"),
        username=text("username"),
        token=token.strip() if token else None,
        password=password,
        token_env=token_env,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_influx_config.py -v`
Expected: 15 passed.

- [ ] **Step 5: Extend the 0600 requirement and its trace**

In `docs/requirements.rst`, change the body of `REQ_CONFIG_MODE_0600` to:

```rst
   The credentials file and the InfluxDB target config (``influx.toml``) are
   kept at mode 0600, and a widened mode is narrowed back on load.
   (Design spec §7.3; influx spec §5.6.)
```

In `docs/traceability.rst`, change the node-id list of `TEST_CONFIG_MODE_0600` to:

```rst
   ``tests/test_client_auth.py::test_config_is_created_owner_only_without_relying_on_chmod``,
   ``tests/test_client_auth.py::test_load_self_heals_a_widened_config``,
   ``tests/test_influx_config.py::test_load_narrows_a_widened_config_file``
```

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes. `test_package_imports_only_stdlib` still passes (`tomllib` is stdlib).

- [ ] **Step 7: Commit**

```bash
git add trackiwi/influx.py tests/test_influx_config.py pyproject.toml docs/requirements.rst docs/traceability.rst
git commit -m "feat: load the InfluxDB target from influx.toml and the environment

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 4: InfluxDB writer — version detection, write, check, redaction

**Files:**
- Modify: `trackiwi/influx.py` (append the writer)
- Modify: `trackiwi/__init__.py` (module docstring)
- Create: `tests/test_influx_writer.py`
- Modify: `pyproject.toml` (mypy overrides), `docs/requirements.rst`, `docs/traceability.rst`

**Interfaces:**
- Consumes: `InfluxConfig` (Task 3); `tests/conftest.py`'s `FakeOpener` and `FakeResponse`.
- Produces:
  - `class InfluxWriter`, constructed as `InfluxWriter(config: InfluxConfig, opener: Callable[..., Any] | None = None)`, with:
    - `version: int` (property; detects on first use)
    - `detect_version() -> int`
    - `write(lines: Sequence[str]) -> None` (raises `TrackiwiError` on failure)
    - `check() -> tuple[bool, list[str]]`
    - `target_key() -> str`
  - Module constant `WRITE_TIMEOUT = 60`

- [ ] **Step 1: Write the failing tests**

`tests/test_influx_writer.py`:

```python
"""InfluxDB writer: version detection, exact write requests, errors, redaction."""

import base64
import gzip
import json
import urllib.error
import urllib.parse

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi import TrackiwiError
from trackiwi.influx import InfluxConfig, InfluxWriter


def _cfg(**over):
    base = dict(
        url="http://influx.example.invalid:8086", version=2, org="home", bucket="trackiwi",
        database=None, username=None, token="tok-secret", password=None,
        token_env="TRACKIWI_INFLUX_TOKEN",
    )
    base.update(over)
    return InfluxConfig(**base)


def test_influx_and_client_do_not_import_each_other():
    """Module boundary: influx.py talks to InfluxDB, client.py to trackiwi, never both."""
    import ast
    import pathlib

    import trackiwi

    pkg = pathlib.Path(trackiwi.__file__).parent
    for source, other in (("influx.py", "client"), ("client.py", "influx")):
        tree = ast.parse((pkg / source).read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert other not in imported and f"trackiwi.{other}" not in imported, source


def test_detects_v2_from_ping_header():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"X-Influxdb-Version": "v2.7.10"}))
    writer = InfluxWriter(_cfg(version=None), opener=opener)
    assert writer.version == 2
    assert opener.calls[0].full_url == "http://influx.example.invalid:8086/ping"


def test_detects_v1_from_ping_header():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"x-influxdb-version": "1.8.10"}))
    assert InfluxWriter(_cfg(version=None), opener=opener).version == 1


def test_cloud_ping_header_means_v2():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={"X-Influxdb-Version": "Cloud"}))
    assert InfluxWriter(_cfg(version=None), opener=opener).version == 2


def test_undetectable_version_asks_for_explicit_setting():
    opener = FakeOpener(FakeResponse(b"", status=204, headers={}))
    with pytest.raises(TrackiwiError, match="version = 1 or 2"):
        InfluxWriter(_cfg(version=None), opener=opener).detect_version()


def test_explicit_version_skips_ping():
    opener = FakeOpener()
    assert InfluxWriter(_cfg(version=2), opener=opener).version == 2
    assert opener.calls == []


def test_v2_write_request_is_exact():
    opener = FakeOpener(FakeResponse(b"", status=204))
    InfluxWriter(_cfg(), opener=opener).write(["m,t=1 f=1.0 1", "m,t=1 f=2.0 2"])
    req = opener.calls[0]
    parsed = urllib.parse.urlsplit(req.full_url)
    assert req.method == "POST"
    assert parsed.path == "/api/v2/write"
    assert urllib.parse.parse_qs(parsed.query) == {
        "org": ["home"], "bucket": ["trackiwi"], "precision": ["s"],
    }
    assert req.get_header("Authorization") == "Token tok-secret"
    assert req.get_header("Content-encoding") == "gzip"
    assert gzip.decompress(req.data).decode() == "m,t=1 f=1.0 1\nm,t=1 f=2.0 2"


def test_v1_write_request_is_exact_with_basic_auth():
    opener = FakeOpener(FakeResponse(b"", status=204))
    cfg = _cfg(version=1, database="trackiwi", username="u", password="p", token=None)
    InfluxWriter(cfg, opener=opener).write(["m f=1.0 1"])
    req = opener.calls[0]
    parsed = urllib.parse.urlsplit(req.full_url)
    assert parsed.path == "/write"
    assert urllib.parse.parse_qs(parsed.query) == {"db": ["trackiwi"], "precision": ["s"]}
    assert req.get_header("Authorization") == "Basic " + base64.b64encode(b"u:p").decode()


def test_200_and_204_are_both_success():
    for status in (200, 204):
        InfluxWriter(_cfg(), opener=FakeOpener(FakeResponse(b"", status=status))).write(["m f=1.0 1"])


def test_empty_batch_sends_nothing():
    opener = FakeOpener()
    InfluxWriter(_cfg(), opener=opener).write([])
    assert opener.calls == []


def test_v2_without_token_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="TRACKIWI_INFLUX_TOKEN"):
        InfluxWriter(_cfg(token=None), opener=FakeOpener()).write(["m f=1.0 1"])


def test_v2_without_bucket_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="bucket"):
        InfluxWriter(_cfg(bucket=None), opener=FakeOpener()).write(["m f=1.0 1"])


def test_v1_without_database_is_a_clear_error():
    with pytest.raises(TrackiwiError, match="database"):
        InfluxWriter(_cfg(version=1, database=None), opener=FakeOpener()).write(["m f=1.0 1"])


@pytest.mark.parametrize(
    ("status", "pattern"),
    [(401, "rejected the token"), (403, "rejected the token"), (404, "bucket 'trackiwi'"), (500, "500")],
)
def test_error_statuses_map_to_clear_messages(status, pattern):
    opener = FakeOpener(FakeResponse(b"boom", status=status))
    with pytest.raises(TrackiwiError, match=pattern):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])


def test_unreachable_server_is_a_clear_error():
    opener = FakeOpener(urllib.error.URLError("connection refused"))
    with pytest.raises(TrackiwiError, match="cannot reach InfluxDB"):
        InfluxWriter(_cfg(), opener=opener).write(["m f=1.0 1"])


def test_token_and_password_are_redacted_from_error_bodies():
    body = b"echo: Authorization: Token tok-secret and Basic dTpw and pw=hunter2"
    opener = FakeOpener(FakeResponse(body, status=500))
    cfg = _cfg(password="hunter2")
    with pytest.raises(TrackiwiError) as excinfo:
        InfluxWriter(cfg, opener=opener).write(["m f=1.0 1"])
    message = str(excinfo.value)
    assert "tok-secret" not in message
    assert "hunter2" not in message
    assert "dTpw" not in message
    assert "<redacted>" in message


def test_only_write_endpoints_receive_posts():
    """REQ_INFLUX_WRITE_SCOPE: writes go only to /api/v2/write or /write."""
    for cfg, path in ((_cfg(), "/api/v2/write"), (_cfg(version=1, database="d", token=None), "/write")):
        opener = FakeOpener(FakeResponse(b"", status=204))
        InfluxWriter(cfg, opener=opener).write(["trackiwi_position,tracker_id=1 lat=1.0 1"])
        assert [(r.method, urllib.parse.urlsplit(r.full_url).path) for r in opener.calls] == [("POST", path)]


def test_target_key_distinguishes_targets():
    a = InfluxWriter(_cfg(), opener=FakeOpener()).target_key()
    b = InfluxWriter(_cfg(bucket="other"), opener=FakeOpener()).target_key()
    c = InfluxWriter(_cfg(version=1, database="trackiwi", token=None), opener=FakeOpener()).target_key()
    assert len({a, b, c}) == 3
    assert "tok-secret" not in a


def test_check_v2_bucket_found():
    body = json.dumps({"buckets": [{"name": "trackiwi"}]}).encode()
    opener = FakeOpener(FakeResponse(body, status=200))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert ok
    assert any("bucket 'trackiwi' found" in line for line in lines)
    assert all(r.method == "GET" for r in opener.calls)


def test_check_v2_bucket_missing():
    opener = FakeOpener(FakeResponse(json.dumps({"buckets": []}).encode(), status=200))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert not ok
    assert any("not found" in line for line in lines)


def test_check_v2_write_only_token_is_ok():
    opener = FakeOpener(FakeResponse(b"", status=403))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert ok
    assert any("not verifiable" in line for line in lines)


def test_check_v2_rejected_token():
    opener = FakeOpener(FakeResponse(b"", status=401))
    ok, lines = InfluxWriter(_cfg(), opener=opener).check()
    assert not ok
    assert any("rejected" in line for line in lines)


def test_check_v1_database_present():
    body = json.dumps({"results": [{"series": [{"values": [["_internal"], ["trackiwi"]]}]}]}).encode()
    opener = FakeOpener(FakeResponse(body, status=200))
    ok, lines = InfluxWriter(_cfg(version=1, database="trackiwi", token=None), opener=opener).check()
    assert ok
    assert any("database 'trackiwi' found" in line for line in lines)
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_influx_writer",` to the mypy overrides list, after `"test_influx_config",`.

Run: `.venv/bin/pytest tests/test_influx_writer.py -v`
Expected: FAIL with `ImportError: cannot import name 'InfluxWriter'`.

- [ ] **Step 3: Implement the writer**

Change the imports at the top of `trackiwi/influx.py` to:

```python
import base64
import contextlib
import gzip
import json
import os
import re
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import TrackiwiError, __version__
```

Append to `trackiwi/influx.py`:

```python
WRITE_TIMEOUT = 60
USER_AGENT = f"trackiwi-client/{__version__} (+python-urllib)"
_AUTH_RE = re.compile(r"\b(Token|Bearer|Basic)\s+\S+", re.IGNORECASE)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup on a plain dict of headers."""
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


class InfluxWriter:
    """Talks to one InfluxDB target: detect the version, write, check.

    Implements :need:`REQ_INFLUX_WRITE_SCOPE` (the only writes are POSTs of
    line protocol to the configured target's write endpoint) and
    :need:`REQ_INFLUX_TOKEN_REDACT` (credentials are scrubbed from every
    message built from server-controlled text). `opener` exists so tests can
    inject a fake transport, exactly as in `client.Client`.
    """

    def __init__(
        self, config: InfluxConfig, opener: Callable[..., Any] | None = None
    ) -> None:
        self.config = config
        self.opener = opener or urllib.request.urlopen
        self._version = config.version

    # -- transport -----------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Scrub the token, the password and any auth header value."""
        text = _AUTH_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
        for secret in (self.config.token, self.config.password):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _auth_headers(self) -> dict[str, str]:
        """Authorization header for the detected version, if credentials exist."""
        if self.version == 2 and self.config.token:
            return {"Authorization": f"Token {self.config.token}"}
        if self.version == 1 and self.config.username:
            raw = f"{self.config.username}:{self.config.password or ''}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Perform one request and return ``(status, headers, body)``."""
        url = self.config.url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            data=data,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            method=method,
        )
        try:
            with self.opener(request, timeout=WRITE_TIMEOUT) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers or {}), error.read()
        except urllib.error.URLError as error:
            raise TrackiwiError(
                f"cannot reach InfluxDB at {self.config.url}: {error.reason}"
            ) from error

    # -- version -------------------------------------------------------------

    def detect_version(self) -> int:
        """Read the major version from the ``X-Influxdb-Version`` header of ``/ping``."""
        _, headers, _ = self._request("GET", "/ping")
        raw = (_header(headers, "X-Influxdb-Version") or "").strip()
        text = raw.lstrip("vV")
        if text[:1] in ("1", "2"):
            return int(text[0])
        if raw.lower().startswith("cloud"):
            return 2
        raise TrackiwiError(
            f"could not detect the InfluxDB version at {self.config.url} "
            f"(X-Influxdb-Version: {raw or 'missing'}); set version = 1 or 2 in influx.toml"
        )

    @property
    def version(self) -> int:
        """The target's major version, detected on first use unless configured."""
        if self._version is None:
            self._version = self.detect_version()
        return self._version

    def target_key(self) -> str:
        """Stable identity of this target for mirror bookkeeping; no secrets."""
        if self.version == 2:
            return f"{self.config.url}|v2|{self.config.org}/{self.config.bucket}"
        return f"{self.config.url}|v1|{self.config.database}"

    # -- write ---------------------------------------------------------------

    def _write_endpoint(self) -> tuple[str, dict[str, str]]:
        """Path and query for a write, validating the settings it needs."""
        cfg = self.config
        if self.version == 2:
            if not cfg.token:
                raise TrackiwiError(
                    f"InfluxDB 2.x needs a token: set ${cfg.token_env} or token_file"
                )
            if not cfg.org or not cfg.bucket:
                raise TrackiwiError("InfluxDB 2.x needs 'org' and 'bucket' configured")
            return "/api/v2/write", {"org": cfg.org, "bucket": cfg.bucket, "precision": "s"}
        if not cfg.database:
            raise TrackiwiError("InfluxDB 1.x needs 'database' configured")
        return "/write", {"db": cfg.database, "precision": "s"}

    def _target_name(self) -> str:
        """Human name of the write destination, for error messages."""
        if self.version == 2:
            return f"bucket '{self.config.bucket}' in org '{self.config.org}'"
        return f"database '{self.config.database}'"

    def write(self, lines: Sequence[str]) -> None:
        """POST one gzipped batch of line protocol; raise on any non-2xx."""
        if not lines:
            return
        path, query = self._write_endpoint()
        body = gzip.compress("\n".join(lines).encode("utf-8"))
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Encoding": "gzip",
            **self._auth_headers(),
        }
        status, _, response = self._request("POST", path, query=query, data=body, headers=headers)
        if 200 <= status < 300:
            return
        if status in (401, 403):
            raise TrackiwiError(
                f"InfluxDB rejected the token or credentials (HTTP {status}) "
                f"for {self._target_name()}"
            )
        if status == 404:
            raise TrackiwiError(f"InfluxDB has no {self._target_name()} (HTTP 404)")
        detail = self._redact(response[:200].decode("utf-8", "replace"))
        raise TrackiwiError(f"InfluxDB write failed (HTTP {status}): {detail}")

    # -- check ---------------------------------------------------------------

    def check(self) -> tuple[bool, list[str]]:
        """Probe connectivity, version and auth. Read-only: writes nothing."""
        lines = [f"InfluxDB {self.version}.x at {self.config.url}"]
        if self.version == 2:
            status, _, body = self._request(
                "GET",
                "/api/v2/buckets",
                query={"org": self.config.org or "", "name": self.config.bucket or ""},
                headers=self._auth_headers(),
            )
            if status == 401:
                return False, [*lines, "token rejected (HTTP 401)"]
            if status == 403:
                return True, [*lines, "auth OK, bucket not verifiable (write-only token)"]
            if status != 200:
                detail = self._redact(body[:200].decode("utf-8", "replace"))
                return False, [*lines, f"bucket lookup failed (HTTP {status}): {detail}"]
            found = json.loads(body or b"{}").get("buckets") or []
            if any(b.get("name") == self.config.bucket for b in found):
                return True, [*lines, f"bucket '{self.config.bucket}' found"]
            return False, [*lines, f"bucket '{self.config.bucket}' not found"]
        status, _, body = self._request(
            "GET", "/query", query={"q": "SHOW DATABASES"}, headers=self._auth_headers()
        )
        if status in (401, 403):
            return False, [*lines, f"credentials rejected (HTTP {status})"]
        if status != 200:
            detail = self._redact(body[:200].decode("utf-8", "replace"))
            return False, [*lines, f"database lookup failed (HTTP {status}): {detail}"]
        series = (json.loads(body).get("results") or [{}])[0].get("series") or [{}]
        names = {row[0] for row in series[0].get("values") or []}
        if self.config.database in names:
            return True, [*lines, f"database '{self.config.database}' found"]
        return False, [*lines, f"database '{self.config.database}' not found"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_influx_writer.py -v`
Expected: all pass.

- [ ] **Step 5: Rescope `REQ_READONLY`, add the two new requirements, update the package docstring**

In `docs/requirements.rst`, change the body of `REQ_READONLY` to:

```rst
   Against the **trackiwi API**, the only state-changing request the client
   may issue is ``DELETE /api/v2/session`` (logout). No call may alter
   trackers, alarms, tours, markers or shares. (Design spec §7.5; rescoped by
   influx spec §3.2 — writes to the user's own InfluxDB are bounded
   separately by REQ_INFLUX_WRITE_SCOPE.)
```

Append to `docs/requirements.rst`:

```rst
.. req:: InfluxDB writes are bounded to one measurement and one target
   :id: REQ_INFLUX_WRITE_SCOPE
   :tags: safety, influx

   The only writes to InfluxDB are POSTs of ``trackiwi_position`` line
   protocol to the configured target's write endpoint (``/api/v2/write`` or
   ``/write``). No deletes, no schema or bucket management. (Influx spec §3.2.)

.. req:: InfluxDB credentials are never shown
   :id: REQ_INFLUX_TOKEN_REDACT
   :tags: privacy, security, influx

   The InfluxDB token and password never appear in output: every message
   built from server-controlled text is redacted first. (Influx spec §5.6.)
```

Append to `docs/traceability.rst`:

```rst
.. test:: Only the write endpoint receives POSTs
   :id: TEST_INFLUX_WRITE_SCOPE
   :verifies: REQ_INFLUX_WRITE_SCOPE

   ``tests/test_influx_writer.py::test_only_write_endpoints_receive_posts``,
   ``tests/test_influx_writer.py::test_v2_write_request_is_exact``

.. test:: InfluxDB credentials are redacted from errors
   :id: TEST_INFLUX_TOKEN_REDACT
   :verifies: REQ_INFLUX_TOKEN_REDACT

   ``tests/test_influx_writer.py::test_token_and_password_are_redacted_from_error_bodies``
```

In `trackiwi/__init__.py`, change the docstring's middle sentence to:

```text
Implements :need:`REQ_ZERO_DEPS` (standard library only, no runtime
dependencies), :need:`REQ_READONLY` (against the trackiwi API the only
state-changing request is ``DELETE /api/v2/session``; InfluxDB writes are
bounded by :need:`REQ_INFLUX_WRITE_SCOPE`) and :need:`REQ_NO_PRIVATE_DATA`
(no private data is ever committed).
```

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/influx.py trackiwi/__init__.py tests/test_influx_writer.py pyproject.toml docs/requirements.rst docs/traceability.rst
git commit -m "feat: InfluxDB writer with version detection, check and redaction

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 5: Mirror loop with the resume invariant

**Files:**
- Modify: `trackiwi/influx.py` (append `mirror` and `BATCH_SIZE`)
- Create: `tests/test_influx_mirror.py`
- Modify: `pyproject.toml` (mypy overrides), `docs/requirements.rst`, `docs/traceability.rst`

**Interfaces:**
- Consumes: `Store.mirror_position`, `set_mirror_position`, `rows_after`, `tracker_names` (Task 2); `format_point` (Task 1); `InfluxWriter.write`, `target_key` (Task 4).
- Produces:
  - `BATCH_SIZE = 5000`
  - `mirror(store: Store, writer: InfluxWriter, batch_size: int = BATCH_SIZE) -> int`: returns the number of points sent. It raises `TrackiwiError` from the first failed batch, having kept all earlier progress.

- [ ] **Step 1: Write the failing tests**

`tests/test_influx_mirror.py`:

```python
"""Mirror resume invariant (REQ_MIRROR_RESUME) and idempotence (REQ_MIRROR_IDEMPOTENT)."""

import pytest

from trackiwi import TrackiwiError
from trackiwi.influx import mirror
from trackiwi.store import Store


def _row(pid):
    return (pid, 7, 1700000000 + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


class FakeWriter:
    """Records batches; fails on the batch numbers listed in `fail_on`."""

    def __init__(self, fail_on=()):
        self.batches = []
        self.fail_on = set(fail_on)

    def target_key(self):
        return "http://x|v2|home/trackiwi"

    def write(self, lines):
        number = len(self.batches) + 1
        self.batches.append(list(lines))
        if number in self.fail_on:
            raise TrackiwiError("InfluxDB write failed (HTTP 500)")


def _ids(lines):
    return [int(line.rsplit(" ", 1)[1]) - 1700000000 for line in lines]


def test_empty_cache_sends_nothing(tmp_path):
    writer = FakeWriter()
    with Store(tmp_path / "p.db") as store:
        assert mirror(store, writer, batch_size=2) == 0
    assert writer.batches == []


def test_backfill_sends_everything_in_batches(tmp_path):
    writer = FakeWriter()
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(i) for i in range(1, 6)])
        assert mirror(store, writer, batch_size=2) == 5
        assert store.mirror_position(writer.target_key()) == 5
    assert [_ids(b) for b in writer.batches] == [[1, 2], [3, 4], [5]]


def test_nothing_new_since_last_push_sends_nothing(tmp_path):
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(1), _row(2)])
        mirror(store, FakeWriter(), batch_size=10)
        second = FakeWriter()
        assert mirror(store, second, batch_size=10) == 0
    assert second.batches == []


def test_failed_batch_does_not_advance_state(tmp_path):
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(i) for i in range(1, 7)])
        failing = FakeWriter(fail_on={2})
        with pytest.raises(TrackiwiError):
            mirror(store, failing, batch_size=2)
        # Batch 1 (ids 1-2) was acknowledged; batch 2 was not.
        assert store.mirror_position(failing.target_key()) == 2
        rerun = FakeWriter()
        assert mirror(store, rerun, batch_size=2) == 4
    assert [_ids(b) for b in rerun.batches] == [[3, 4], [5, 6]]


def test_only_new_rows_are_sent_after_more_sync(tmp_path):
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(1), _row(2)])
        mirror(store, FakeWriter(), batch_size=10)
        store.upsert([_row(3)])
        later = FakeWriter()
        assert mirror(store, later, batch_size=10) == 1
    assert [_ids(b) for b in later.batches] == [[3]]


def test_resending_produces_identical_lines(tmp_path):
    """Idempotence: same rows and names → byte-identical points (same series + timestamp)."""
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(1), _row(2)])
        store.set_tracker_names({7: "Bus"})
        first = FakeWriter()
        mirror(store, first, batch_size=10)
        store.set_mirror_position(first.target_key(), 0)
        again = FakeWriter()
        mirror(store, again, batch_size=10)
    assert first.batches == again.batches
    assert ",tracker_name=Bus " in first.batches[0][0]
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_influx_mirror",` to the mypy overrides list, after `"test_influx_config",`.

Run: `.venv/bin/pytest tests/test_influx_mirror.py -v`
Expected: FAIL with `ImportError: cannot import name 'mirror'`.

- [ ] **Step 3: Implement `mirror`**

Add this import to `trackiwi/influx.py`, below `from . import TrackiwiError, __version__`:

```python
from .lineprotocol import format_point
from .store import Store
```

Append to `trackiwi/influx.py`:

```python
BATCH_SIZE = 5000


def mirror(store: Store, writer: InfluxWriter, batch_size: int = BATCH_SIZE) -> int:
    """Send every cached row not yet mirrored to the writer's target.

    Implements :need:`REQ_MIRROR_RESUME`: the stored position advances only
    after a batch is acknowledged, so it never moves past a row InfluxDB has
    not accepted, and a failure leaves earlier batches recorded. Implements
    :need:`REQ_MIRROR_IDEMPOTENT`: a row always becomes the same point (same
    tags, same timestamp), which InfluxDB overwrites rather than duplicates,
    so re-sending is always safe. Returns the number of points sent; the first
    failed batch re-raises its `TrackiwiError`.
    """
    target = writer.target_key()
    last = store.mirror_position(target)
    names = store.tracker_names()
    sent = 0
    while True:
        rows = store.rows_after(last, batch_size)
        if not rows:
            return sent
        lines = [format_point(row, names.get(int(row["tracker_id"]))) for row in rows]
        writer.write(lines)
        last = int(rows[-1]["id"])
        store.set_mirror_position(target, last)
        sent += len(rows)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_influx_mirror.py -v`
Expected: 6 passed.

- [ ] **Step 5: Add the requirements and traces**

Append to `docs/requirements.rst`:

```rst
.. req:: The mirror never advances past an unacknowledged row
   :id: REQ_MIRROR_RESUME
   :tags: integrity, influx

   The per-target mirror position advances only after InfluxDB acknowledges a
   batch. A failure leaves earlier batches recorded and the failed batch and
   everything after it for the next run. (Influx spec §5.2.)

.. req:: Re-sending positions never creates duplicates
   :id: REQ_MIRROR_IDEMPOTENT
   :tags: integrity, influx

   A cached row always becomes the same point — same measurement, tags and
   timestamp — so re-sending (including a full backfill) overwrites rather
   than duplicates. (Influx spec §4.)
```

Append to `docs/traceability.rst`:

```rst
.. test:: A failed batch does not advance the mirror
   :id: TEST_MIRROR_RESUME
   :verifies: REQ_MIRROR_RESUME

   ``tests/test_influx_mirror.py::test_failed_batch_does_not_advance_state``,
   ``tests/test_influx_mirror.py::test_only_new_rows_are_sent_after_more_sync``

.. test:: Re-sending produces identical points
   :id: TEST_MIRROR_IDEMPOTENT
   :verifies: REQ_MIRROR_IDEMPOTENT

   ``tests/test_influx_mirror.py::test_resending_produces_identical_lines``
```

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/influx.py tests/test_influx_mirror.py pyproject.toml docs/requirements.rst docs/traceability.rst
git commit -m "feat: mirror cached positions to InfluxDB, resuming after failures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 6: CLI — `influx check`, `influx push`, `ingest`

**Files:**
- Modify: `trackiwi/cli.py`
- Create: `tests/test_cli_influx.py`
- Modify: `pyproject.toml` (mypy overrides)

**Interfaces:**
- Consumes:
  - `load_config`, `InfluxWriter`, `mirror` from `trackiwi.influx`
  - `Store.set_tracker_names` (Task 2)
  - the existing `Client.load`, `Client.sync`, `Client.trackers`
- Produces:
  - Commands `trackiwi influx check`, `trackiwi influx push`, `trackiwi ingest`
  - `cli.py` functions: `cmd_influx_check`, `cmd_influx_push`, `cmd_ingest`, and `_sync_into_cache(client: Client, full: bool) -> None`. `cmd_sync` now delegates to `_sync_into_cache`.

- [ ] **Step 1: Write the failing tests**

`tests/test_cli_influx.py`:

```python
"""CLI wiring for InfluxDB: check, push, and ingest (push runs even if sync fails)."""

import pytest

from trackiwi import AuthError, TrackiwiError, cli
from trackiwi.store import Store


def _row(pid):
    return (pid, 7, 1700000000 + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("TRACKIWI_INFLUX_URL", "http://influx.example.invalid:8086")
    monkeypatch.setenv("TRACKIWI_INFLUX_VERSION", "2")
    monkeypatch.setenv("TRACKIWI_INFLUX_ORG", "home")
    monkeypatch.setenv("TRACKIWI_INFLUX_BUCKET", "trackiwi")
    monkeypatch.setenv("TRACKIWI_INFLUX_TOKEN", "tok")
    return tmp_path


class FakeWriter:
    instances = []

    def __init__(self, config, opener=None):
        self.config = config
        self.batches = []
        FakeWriter.instances.append(self)

    def target_key(self):
        return "k"

    def write(self, lines):
        self.batches.append(list(lines))

    def check(self):
        return True, ["InfluxDB 2.x at x", "bucket 'trackiwi' found"]


@pytest.fixture(autouse=True)
def fake_writer(monkeypatch):
    FakeWriter.instances = []
    monkeypatch.setattr("trackiwi.cli.InfluxWriter", FakeWriter)


def _stub_client(batches=None, trackers=None, sync_error=None, authenticated=True):
    class Stub:
        @classmethod
        def load(cls):
            return cls()

        @property
        def authenticated(self):
            return authenticated

        def sync(self, offset=None):
            yield from batches or []
            if sync_error:
                raise sync_error

        def trackers(self):
            return trackers or []

    return Stub


def test_check_prints_report_and_exits_zero(env, capsys):
    assert cli.main(["influx", "check"]) == 0
    assert "bucket 'trackiwi' found" in capsys.readouterr().out


def test_check_exits_one_when_not_ok(env, monkeypatch):
    monkeypatch.setattr(FakeWriter, "check", lambda self: (False, ["bucket 'trackiwi' not found"]))
    assert cli.main(["influx", "check"]) == 1


def test_push_without_cache_is_a_noop(env, capsys):
    assert cli.main(["influx", "push"]) == 0
    assert "nothing cached" in capsys.readouterr().out
    assert not (env / "data" / "trackiwi" / "positions.db").exists()


def test_push_sends_cached_rows(env, capsys):
    with Store() as store:
        store.upsert([_row(1), _row(2)])
    assert cli.main(["influx", "push"]) == 0
    assert len(FakeWriter.instances[0].batches[0]) == 2
    assert "pushed 2 positions" in capsys.readouterr().out


def test_push_with_bad_config_exits_one(env, monkeypatch, capsys):
    monkeypatch.delenv("TRACKIWI_INFLUX_URL")
    assert cli.main(["influx", "push"]) == 1
    assert "TRACKIWI_INFLUX_URL" in capsys.readouterr().err


def test_ingest_syncs_names_and_pushes(env, monkeypatch):
    monkeypatch.setattr(
        "trackiwi.cli.Client",
        _stub_client(batches=[([_row(1), _row(2)], 0, 2)], trackers=[{"id": 7, "name": "Bus"}]),
    )
    assert cli.main(["ingest"]) == 0
    lines = FakeWriter.instances[0].batches[0]
    assert len(lines) == 2
    assert ",tracker_name=Bus " in lines[0]


def test_ingest_pushes_cached_rows_even_when_sync_fails(env, monkeypatch, capsys):
    with Store() as store:
        store.upsert([_row(1)])
    monkeypatch.setattr(
        "trackiwi.cli.Client", _stub_client(sync_error=TrackiwiError("network error: down"))
    )
    assert cli.main(["ingest"]) == 1
    assert len(FakeWriter.instances[0].batches[0]) == 1
    assert "network error" in capsys.readouterr().err


def test_ingest_auth_failure_still_pushes_and_exits_two(env, monkeypatch):
    with Store() as store:
        store.upsert([_row(1)])
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(authenticated=False))
    assert cli.main(["ingest"]) == 2
    assert len(FakeWriter.instances[0].batches[0]) == 1


def test_sync_command_still_works(env, monkeypatch, capsys):
    monkeypatch.setattr("trackiwi.cli.Client", _stub_client(batches=[([_row(1)], 0, 1)]))
    assert cli.main(["sync"]) == 0
    assert "1 new positions" in capsys.readouterr().out


def test_auth_error_type_is_preserved():
    assert issubclass(AuthError, TrackiwiError)
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_cli_influx",` to the mypy overrides list, after `"test_cli",`.

Run: `.venv/bin/pytest tests/test_cli_influx.py -v`
Expected: FAIL. `monkeypatch.setattr("trackiwi.cli.InfluxWriter", …)` raises `AttributeError: <module 'trackiwi.cli'> has no attribute 'InfluxWriter'`.

- [ ] **Step 3: Implement the commands**

In `trackiwi/cli.py`, add below the existing `from .store import …` line:

```python
from .influx import InfluxWriter, load_config, mirror
```

Replace the whole body of `cmd_sync`, from its docstring to its `return 0`, with:

```python
def _sync_into_cache(client: Client, full: bool) -> None:
    """Fetch new positions into the local cache, reporting progress."""
    if not client.authenticated:
        # Checked before Store() is ever opened: otherwise an unauthenticated
        # run leaves behind an empty cache directory and database file.
        raise AuthError("not logged in — run 'trackiwi login'")
    with Store() as store:
        offset = None if full else store.max_id()
        # `fetched` drives the progress line, because that is what the server's
        # total is comparable with; `written` counts rows that were actually
        # new, which is what gets reported at the end.
        fetched = written = skipped_total = 0
        try:
            for rows, skipped, total in client.sync(offset=offset):
                written += store.upsert(rows)
                fetched += len(rows)
                skipped_total += skipped
                suffix = f" of {total}" if total else ""
                print(f"\rsynced {fetched}{suffix} positions", end="", file=sys.stderr)
        finally:
            # Always close the \r-progress line, success or failure, so a
            # later error message never gets appended to a partial line.
            print(file=sys.stderr)
        if skipped_total:
            print(f"skipped {skipped_total} malformed rows", file=sys.stderr)
        print(f"{written} new positions, {store.count()} cached in total")


def cmd_sync(args: argparse.Namespace) -> int:
    """Fetch new positions into the local cache, reporting progress."""
    _sync_into_cache(Client.load(), full=args.full)
    return 0
```

Add after `cmd_sync`:

```python
def cmd_influx_check(_: argparse.Namespace) -> int:
    """Probe the configured InfluxDB target and print a report. Writes nothing."""
    ok, lines = InfluxWriter(load_config()).check()
    for line in lines:
        print(line)
    return 0 if ok else 1


def _push() -> int:
    """Mirror new cached rows to InfluxDB and return how many were sent."""
    writer = InfluxWriter(load_config())
    if not default_db_path().exists():
        # Never create the cache as a side effect: it is a movement history.
        print("nothing cached yet — run 'trackiwi sync' first")
        return 0
    with Store() as store:
        sent = mirror(store, writer)
    print(f"pushed {sent} positions to InfluxDB")
    return sent


def cmd_influx_push(_: argparse.Namespace) -> int:
    """Mirror cached positions not yet sent to the configured InfluxDB."""
    _push()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Sync from trackiwi, refresh tracker names, then push to InfluxDB.

    The push runs even when the sync fails: the cache is the buffer, and
    whatever is already in it should still reach InfluxDB. The sync's error is
    re-raised afterwards so the exit code still reports it (2 for auth, 1 else).
    """
    sync_error: TrackiwiError | None = None
    try:
        client = Client.load()
        _sync_into_cache(client, full=False)
        names = {int(t["id"]): str(t.get("name") or "") for t in client.trackers() if "id" in t}
        with Store() as store:
            store.set_tracker_names({k: v for k, v in names.items() if v})
    except TrackiwiError as error:
        sync_error = error
        print(f"sync failed: {error}", file=sys.stderr)
    _push()
    if sync_error is not None:
        raise sync_error
    return 0
```

In `build_parser`, add after the `sync` parser block:

```python
    influx = sub.add_parser("influx", help="write cached positions to InfluxDB")
    influx_sub = influx.add_subparsers(dest="influx_command", required=True)
    influx_sub.add_parser(
        "check", help="probe the configured InfluxDB target (read-only)"
    ).set_defaults(func=cmd_influx_check)
    influx_sub.add_parser(
        "push", help="send cached positions not yet mirrored"
    ).set_defaults(func=cmd_influx_push)

    sub.add_parser(
        "ingest", help="sync from trackiwi, then push to InfluxDB (for timers)"
    ).set_defaults(func=cmd_ingest)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cli_influx.py tests/test_cli.py -v`
Expected: all pass, including the existing CLI tests (`cmd_sync` still behaves identically).

- [ ] **Step 5: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes. If coverage drops below 95, the uncovered lines are in `influx.py`'s `check()` v1 error branches: add a `test_check_v1_credentials_rejected` (FakeResponse status 401 → `(False, …"credentials rejected"…)`) to `tests/test_influx_writer.py` and rerun.

- [ ] **Step 6: Align the spec's exit code**

In the influx spec §5.5 table, change the last row's exit-code cell from `1` to `1 (2 if the sync failed on authentication, matching every other command)`.

- [ ] **Step 7: Commit**

```bash
git add trackiwi/cli.py tests/test_cli_influx.py pyproject.toml docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md
git commit -m "feat: influx check/push and ingest commands

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 7: Deployment templates (compose, provisioning, systemd, example config)

**Files:**
- Create:
  - `examples/influx.example.toml`
  - `deploy/example.env`
  - `deploy/docker-compose.yml`
  - `deploy/ingest/Dockerfile`
  - `deploy/grafana-provisioning/datasources/trackiwi.yaml`
  - `deploy/grafana-provisioning/dashboards/trackiwi.yaml`
  - `deploy/systemd/trackiwi-ingest.service`
  - `deploy/systemd/trackiwi-ingest.timer`
  - `.dockerignore`
  - `tests/test_deploy.py`
- Modify: `.pre-commit-config.yaml` (add `check-yaml`), `pyproject.toml` (mypy overrides), `docs/requirements.rst`, `docs/traceability.rst`, the influx spec (§6/§7 env-file name)

**Interfaces:**
- Consumes: the command names (`trackiwi ingest`, `trackiwi login`) and the `TRACKIWI_INFLUX_*` variables from Tasks 3 and 6.
- Produces: files that Task 8's dashboard is provisioned into (`/var/lib/grafana/dashboards`), and the variable names `INFLUXDB_ORG`, `INFLUXDB_BUCKET`, `INFLUXDB_TOKEN` that the provisioning uses.

- [ ] **Step 1: Write the failing tests**

`tests/test_deploy.py`:

```python
"""Deploy templates are portable: placeholders only, every variable defined (REQ_PORTABLE_CONFIG)."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
EXAMPLES = ROOT / "examples"
# A user's real deploy/.env is git-ignored and must not be scanned as a template.
TEMPLATE_FILES = sorted(
    p
    for base in (DEPLOY, EXAMPLES)
    for p in base.rglob("*")
    if p.is_file() and p.name != ".env"
)

_IPV4 = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b")
_COORD = re.compile(r"-?\d{1,3}\.\d{5,}")
# Credential-like: 32+ chars mixing lower case, upper case and a digit. The
# mix requirement keeps UPPER_SNAKE variable names such as
# DOCKER_INFLUXDB_INIT_ADMIN_TOKEN (exactly 32 chars) from matching.
_LONG_SECRET = re.compile(
    r"(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*\d)"
    r"\b[A-Za-z0-9+/_-]{32,}={0,2}"
)


def _env_keys():
    keys = {}
    for line in (DEPLOY / "example.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            keys[key] = value
    return keys


def test_template_files_exist():
    names = {p.relative_to(ROOT).as_posix() for p in TEMPLATE_FILES}
    for required in (
        "deploy/docker-compose.yml",
        "deploy/example.env",
        "deploy/ingest/Dockerfile",
        "deploy/grafana-provisioning/datasources/trackiwi.yaml",
        "deploy/grafana-provisioning/dashboards/trackiwi.yaml",
        "deploy/systemd/trackiwi-ingest.service",
        "deploy/systemd/trackiwi-ingest.timer",
        "examples/influx.example.toml",
    ):
        assert required in names


def test_every_compose_variable_is_in_example_env():
    compose = (DEPLOY / "docker-compose.yml").read_text()
    used = {m.group(1) for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}", compose)}
    defaulted = {m.group(1) for m in re.finditer(r"\$\{([A-Z_][A-Z0-9_]*):-[^}]*\}", compose)}
    missing = used - defaulted - set(_env_keys())
    assert not missing, f"compose uses variables absent from example.env: {sorted(missing)}"


def test_provisioning_variables_are_passed_to_grafana():
    compose = (DEPLOY / "docker-compose.yml").read_text()
    for path in (DEPLOY / "grafana-provisioning").rglob("*.yaml"):
        for var in re.findall(r"\$([A-Z_][A-Z0-9_]*)", path.read_text()):
            assert f"{var}:" in compose, f"{path.name} uses ${var} but grafana is not given it"


def test_example_env_values_are_placeholders():
    allowed_literals = {"admin", "home", "trackiwi", "600"}
    for key, value in _env_keys().items():
        assert value.startswith("changeme") or value in allowed_literals, f"{key}={value}"


def test_no_real_values_in_templates():
    """REQ_PORTABLE_CONFIG: no IPs, coordinates or credential-looking strings."""
    for path in TEMPLATE_FILES:
        text = path.read_text(encoding="utf-8")
        ips = {ip for ip in _IPV4.findall(text) if ip not in ("127.0.0.1", "0.0.0.0")}
        assert not ips, f"{path}: IP addresses {ips}"
        assert not _COORD.search(text), f"{path}: coordinate-like number"
        assert not _LONG_SECRET.search(text), f"{path}: credential-like string"


def test_systemd_units_have_no_absolute_user_paths():
    for path in (DEPLOY / "systemd").iterdir():
        text = path.read_text()
        assert "/home/" not in text and "/Users/" not in text, path


def test_dockerignore_keeps_private_data_out_of_the_image():
    text = (ROOT / ".dockerignore").read_text()
    for pattern in (".venv", ".git", "*.db", "config.json", ".env", "deploy/.env"):
        assert pattern in text.splitlines(), pattern
```

- [ ] **Step 2: Register the test module and run the tests to verify they fail**

Add `"test_deploy",` to the mypy overrides list, after `"test_client_sync",`.

Run: `.venv/bin/pytest tests/test_deploy.py -v`
Expected: FAIL, because `deploy/example.env` does not exist.

- [ ] **Step 3: Create the template files**

`examples/influx.example.toml`:

```toml
# Copy to ~/.config/trackiwi/influx.toml (mode 0600) and adjust.
# Every key can be overridden by TRACKIWI_INFLUX_<KEY> in the environment.
url = "http://localhost:8086"
version = "auto"          # "auto" | 1 | 2

# InfluxDB 2.x / Cloud
org = "home"
bucket = "trackiwi"
# The token is never written here. Either name the environment variable
# that holds it (default TRACKIWI_INFLUX_TOKEN) ...
# token_env = "INFLUXDB_TOKEN"
# ... or point at a file that contains only the token:
# token_file = "~/.config/trackiwi/influx.token"

# InfluxDB 1.x
# database = "trackiwi"
# username = "trackiwi"
# password_file = "~/.config/trackiwi/influx.password"
```

`deploy/example.env`:

```sh
# Copy to deploy/.env and replace every "changeme" value.
# deploy/.env is git-ignored; this template holds placeholders only.

# InfluxDB 2 first-run setup (applies only while the volume is empty)
INFLUXDB_ADMIN_USER=admin
INFLUXDB_ADMIN_PASSWORD=changeme-admin-password
INFLUXDB_ORG=home
INFLUXDB_BUCKET=trackiwi
INFLUXDB_TOKEN=changeme-influx-token

GRAFANA_ADMIN_PASSWORD=changeme-grafana-password

# Seconds between ingest runs
TRACKIWI_INGEST_INTERVAL=600
```

`deploy/docker-compose.yml`:

```yaml
# Turnkey stack: InfluxDB 2 + Grafana + trackiwi ingest.
#   cp deploy/example.env deploy/.env      # then replace every changeme value
#   docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi login
#   docker compose -f deploy/docker-compose.yml up -d
# Grafana: http://localhost:3000 (admin / GRAFANA_ADMIN_PASSWORD)
services:
  influxdb:
    image: influxdb:2
    restart: unless-stopped
    environment:
      DOCKER_INFLUXDB_INIT_MODE: setup
      DOCKER_INFLUXDB_INIT_USERNAME: ${INFLUXDB_ADMIN_USER}
      DOCKER_INFLUXDB_INIT_PASSWORD: ${INFLUXDB_ADMIN_PASSWORD}
      DOCKER_INFLUXDB_INIT_ORG: ${INFLUXDB_ORG}
      DOCKER_INFLUXDB_INIT_BUCKET: ${INFLUXDB_BUCKET}
      DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: ${INFLUXDB_TOKEN}
    ports:
      - "8086:8086"
    volumes:
      - influxdb-data:/var/lib/influxdb2
      - influxdb-config:/etc/influxdb2

  grafana:
    image: grafana/grafana:11.3.0
    restart: unless-stopped
    depends_on:
      - influxdb
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
      INFLUXDB_ORG: ${INFLUXDB_ORG}
      INFLUXDB_BUCKET: ${INFLUXDB_BUCKET}
      INFLUXDB_TOKEN: ${INFLUXDB_TOKEN}
    ports:
      - "3000:3000"
    volumes:
      - ./grafana-provisioning:/etc/grafana/provisioning:ro
      - ./grafana-dashboards:/var/lib/grafana/dashboards:ro
      - grafana-data:/var/lib/grafana

  ingest:
    build:
      context: ..
      dockerfile: deploy/ingest/Dockerfile
    restart: unless-stopped
    depends_on:
      - influxdb
    environment:
      TRACKIWI_INFLUX_URL: http://influxdb:8086
      TRACKIWI_INFLUX_VERSION: "2"
      TRACKIWI_INFLUX_ORG: ${INFLUXDB_ORG}
      TRACKIWI_INFLUX_BUCKET: ${INFLUXDB_BUCKET}
      TRACKIWI_INFLUX_TOKEN: ${INFLUXDB_TOKEN}
      TRACKIWI_INGEST_INTERVAL: ${TRACKIWI_INGEST_INTERVAL:-600}
    volumes:
      # Holds ~/.config/trackiwi (session) and ~/.local/share/trackiwi (the
      # cache that buffers positions while InfluxDB is unreachable).
      - trackiwi-state:/home/trackiwi

volumes:
  influxdb-data:
  influxdb-config:
  grafana-data:
  trackiwi-state:
```

`deploy/ingest/Dockerfile`:

```dockerfile
# trackiwi ingest loop. Build context is the repository root (see docker-compose.yml).
FROM python:3.13-slim
RUN useradd --create-home --uid 10001 trackiwi
COPY . /src
RUN pip install --no-cache-dir /src && rm -rf /src
USER trackiwi
ENV HOME=/home/trackiwi
# A failed run (network down, InfluxDB restarting) must not stop the loop:
# the cache buffers positions and the next run catches up.
CMD ["sh", "-c", "while true; do trackiwi ingest || true; sleep \"${TRACKIWI_INGEST_INTERVAL:-600}\"; done"]
```

`deploy/grafana-provisioning/datasources/trackiwi.yaml`:

```yaml
# Flux datasource for the bundled InfluxDB. Values come from the container
# environment (see docker-compose.yml); nothing secret is committed here.
apiVersion: 1
datasources:
  - name: trackiwi
    type: influxdb
    access: proxy
    url: http://influxdb:8086
    isDefault: true
    jsonData:
      version: Flux
      organization: $INFLUXDB_ORG
      defaultBucket: $INFLUXDB_BUCKET
    secureJsonData:
      token: $INFLUXDB_TOKEN
```

`deploy/grafana-provisioning/dashboards/trackiwi.yaml`:

```yaml
apiVersion: 1
providers:
  - name: trackiwi
    folder: trackiwi
    type: file
    disableDeletion: false
    options:
      path: /var/lib/grafana/dashboards
```

`deploy/systemd/trackiwi-ingest.service`:

```ini
# User unit. Install: cp deploy/systemd/trackiwi-ingest.* ~/.config/systemd/user/
#                     systemctl --user daemon-reload
#                     systemctl --user enable --now trackiwi-ingest.timer
# Assumes `trackiwi` was installed with `pip install --user` or pipx
# (so it lives in ~/.local/bin) and ~/.config/trackiwi/influx.toml exists.
[Unit]
Description=trackiwi ingest (sync from trackiwi, push to InfluxDB)

[Service]
Type=oneshot
ExecStart=%h/.local/bin/trackiwi ingest
```

`deploy/systemd/trackiwi-ingest.timer`:

```ini
[Unit]
Description=Run trackiwi ingest every 10 minutes

[Timer]
OnCalendar=*:0/10
Persistent=true

[Install]
WantedBy=timers.target
```

`.dockerignore`:

```text
.venv
.git
.pytest_cache
.ruff_cache
.mypy_cache
docs/_build
*.db
*.db-journal
*.sqlite
*.sqlite3
config.json
.env
deploy/.env
.superpowers
```

- [ ] **Step 4: Validate YAML in pre-commit**

In `.pre-commit-config.yaml`, add `- id: check-yaml` to the `pre-commit-hooks` repo's `hooks:` list, after `- id: detect-private-key`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_deploy.py -v && .venv/bin/pre-commit run check-yaml --all-files`
Expected: 7 passed; `check yaml ... Passed`.

- [ ] **Step 6: Add the requirement, the trace, and the spec correction**

Append to `docs/requirements.rst`:

```rst
.. req:: Committed deployment templates carry no deployment's specifics
   :id: REQ_PORTABLE_CONFIG
   :tags: privacy, portability, influx

   No file under ``deploy/`` or ``examples/`` contains a hostname, IP
   address, credential, tracker id or coordinate from any real deployment;
   values are placeholders, and every variable the compose file uses is
   defined in ``deploy/example.env``. (Influx spec §6.)
```

Append to `docs/traceability.rst`:

```rst
.. test:: Deployment templates are placeholder-only and complete
   :id: TEST_PORTABLE_CONFIG
   :verifies: REQ_PORTABLE_CONFIG

   ``tests/test_deploy.py::test_no_real_values_in_templates``,
   ``tests/test_deploy.py::test_example_env_values_are_placeholders``,
   ``tests/test_deploy.py::test_every_compose_variable_is_in_example_env``
```

In `docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md`, replace every `deploy/.env.example` with `deploy/example.env`, and add this sentence at the end of §6's bullet about committed files: "(Named `example.env` because the private-data guard rejects any file whose name starts with `.env.`.)"

- [ ] **Step 7: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes. If `detect-secrets` flags a placeholder line (for example `DOCKER_INFLUXDB_INIT_ADMIN_TOKEN: ${INFLUXDB_TOKEN}`), append ` # pragma: allowlist secret` to **that line only**. It is a variable reference, not a secret. Never add a real value to `.secrets.baseline`.

- [ ] **Step 8: Commit**

```bash
git add examples deploy .dockerignore tests/test_deploy.py .pre-commit-config.yaml pyproject.toml docs/requirements.rst docs/traceability.rst docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md
git commit -m "feat: compose, Grafana provisioning and systemd templates for ingest

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 8: Portable Flux dashboard

**Files:**
- Create: `deploy/grafana-dashboards/trackiwi.json`
- Create: `tests/test_dashboard.py`
- Modify: `.gitignore`, `pyproject.toml` (mypy overrides), `docs/requirements.rst`, `docs/traceability.rst`, the influx spec (§8/§9 `v.bucket` → `"${bucket}"`)

**Interfaces:**
- Consumes: the schema from Task 1 (measurement `trackiwi_position`, tag `tracker_name`, fields `lat`, `lon`, `speed_kmh`, `distance_m`, `voltage_v`, `battery_pct`, `satellites`, `gnss_quality`) and the provisioning path from Task 7.
- Produces: the dashboard file (uid `trackiwi-overview`).

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard.py`:

```python
"""The Grafana dashboard is portable (REQ_DASHBOARD_PORTABLE)."""

import json
import pathlib
import re

PATH = pathlib.Path(__file__).resolve().parent.parent / "deploy" / "grafana-dashboards" / "trackiwi.json"


def _load():
    return json.loads(PATH.read_text(encoding="utf-8"))


def _panels(dashboard):
    return [p for p in dashboard["panels"] if p["type"] != "row"]


def test_three_rows_in_order():
    rows = [p["title"] for p in _load()["panels"] if p["type"] == "row"]
    assert rows == ["Status", "Travel", "Health"]


def test_required_panels_present():
    titles = {p["title"] for p in _panels(_load())}
    for required in (
        "Last seen", "Voltage now", "Battery now", "Satellites now", "GNSS quality now",
        "Current position", "Route", "Speed", "Distance per day",
        "Voltage", "Battery", "Satellites & GNSS quality",
    ):
        assert required in titles, required


def test_datasource_is_a_variable_everywhere():
    for panel in _panels(_load()):
        assert panel["datasource"] == {"type": "influxdb", "uid": "${DS_TRACKIWI}"}, panel["title"]
        for target in panel["targets"]:
            assert target["datasource"] == {"type": "influxdb", "uid": "${DS_TRACKIWI}"}


def test_every_query_is_flux_on_the_bucket_variable():
    for panel in _panels(_load()):
        for target in panel["targets"]:
            query = target["query"]
            assert 'from(bucket: "${bucket}")' in query, panel["title"]
            assert 'r._measurement == "trackiwi_position"' in query, panel["title"]
            assert 'r.tracker_name == "${tracker}"' in query, panel["title"]


def test_template_variables():
    variables = {v["name"]: v for v in _load()["templating"]["list"]}
    assert variables["DS_TRACKIWI"]["type"] == "datasource"
    assert variables["DS_TRACKIWI"]["query"] == "influxdb"
    assert variables["bucket"]["type"] == "textbox"
    assert variables["bucket"]["query"] == "trackiwi"
    assert variables["tracker"]["type"] == "query"
    assert 'schema.tagValues(bucket: "${bucket}", tag: "tracker_name"' in variables["tracker"]["query"]


def test_no_hardcoded_datasource_uids_or_real_values():
    text = PATH.read_text(encoding="utf-8")
    uids = set(re.findall(r'"uid":\s*"([^"]*)"', text))
    assert uids <= {"${DS_TRACKIWI}", "trackiwi-overview"}, uids
    assert not re.search(r"-?\d{1,3}\.\d{5,}", text), "coordinate-like number"
    assert "__inputs" not in text
```

- [ ] **Step 2: Register the test module, un-ignore the dashboard, and run the tests to verify they fail**

Add `"test_dashboard",` to the mypy overrides list, after `"test_cli_reads",`.

In `.gitignore`, add this line directly after `!tests/fixtures/synthetic-*`:

```text
!deploy/grafana-dashboards/*.json
```

Run: `.venv/bin/pytest tests/test_dashboard.py -v`
Expected: FAIL with `FileNotFoundError` for `trackiwi.json`.

- [ ] **Step 3: Create `deploy/grafana-dashboards/trackiwi.json`**

```json
{
  "uid": "trackiwi-overview",
  "title": "trackiwi",
  "description": "Position, travel and vehicle health from a trackiwi GPS tracker. Flux; InfluxDB 2.x / Cloud; Grafana 10+.",
  "tags": ["trackiwi"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 1,
  "editable": true,
  "graphTooltip": 1,
  "time": { "from": "now-7d", "to": "now" },
  "refresh": "5m",
  "templating": {
    "list": [
      {
        "name": "DS_TRACKIWI",
        "label": "InfluxDB (Flux)",
        "type": "datasource",
        "query": "influxdb",
        "current": {},
        "hide": 0
      },
      {
        "name": "bucket",
        "label": "Bucket",
        "type": "textbox",
        "query": "trackiwi",
        "current": { "text": "trackiwi", "value": "trackiwi" },
        "hide": 0
      },
      {
        "name": "tracker",
        "label": "Tracker",
        "type": "query",
        "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
        "query": "import \"influxdata/influxdb/schema\"\nschema.tagValues(bucket: \"${bucket}\", tag: \"tracker_name\", predicate: (r) => r._measurement == \"trackiwi_position\", start: -365d)",
        "refresh": 1,
        "current": {},
        "hide": 0
      }
    ]
  },
  "panels": [
    { "type": "row", "title": "Status", "collapsed": false, "gridPos": { "h": 1, "w": 24, "x": 0, "y": 0 }, "panels": [] },
    {
      "type": "stat", "title": "Last seen", "gridPos": { "h": 4, "w": 4, "x": 0, "y": 1 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "dateTimeFromNow" }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] }, "colorMode": "none" },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"lat\")\n  |> last()\n  |> map(fn: (r) => ({_time: r._time, _value: int(v: r._time) / 1000000}))"
        }
      ]
    },
    {
      "type": "stat", "title": "Voltage now", "gridPos": { "h": 4, "w": 4, "x": 4, "y": 1 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "volt", "decimals": 2 }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"voltage_v\")\n  |> last()"
        }
      ]
    },
    {
      "type": "stat", "title": "Battery now", "gridPos": { "h": 4, "w": 4, "x": 8, "y": 1 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "percent" }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"battery_pct\")\n  |> last()"
        }
      ]
    },
    {
      "type": "stat", "title": "Satellites now", "gridPos": { "h": 4, "w": 4, "x": 12, "y": 1 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "none" }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"satellites\")\n  |> last()"
        }
      ]
    },
    {
      "type": "stat", "title": "GNSS quality now", "gridPos": { "h": 4, "w": 4, "x": 16, "y": 1 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "none", "min": 0, "max": 5 }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"] } },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"gnss_quality\")\n  |> last()"
        }
      ]
    },
    {
      "type": "geomap", "title": "Current position", "gridPos": { "h": 10, "w": 24, "x": 0, "y": 5 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "options": {
        "view": { "id": "fit", "zoom": 13 },
        "layers": [
          { "type": "markers", "name": "Position", "location": { "mode": "coords", "latitude": "lat", "longitude": "lon" } }
        ]
      },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: -365d)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and (r._field == \"lat\" or r._field == \"lon\"))\n  |> last()\n  |> pivot(rowKey: [\"_time\"], columnKey: [\"_field\"], valueColumn: \"_value\")"
        }
      ]
    },
    { "type": "row", "title": "Travel", "collapsed": false, "gridPos": { "h": 1, "w": 24, "x": 0, "y": 15 }, "panels": [] },
    {
      "type": "geomap", "title": "Route", "gridPos": { "h": 12, "w": 24, "x": 0, "y": 16 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "options": {
        "view": { "id": "fit" },
        "layers": [
          { "type": "route", "name": "Route", "location": { "mode": "coords", "latitude": "lat", "longitude": "lon" } }
        ]
      },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and (r._field == \"lat\" or r._field == \"lon\"))\n  |> pivot(rowKey: [\"_time\"], columnKey: [\"_field\"], valueColumn: \"_value\")\n  |> keep(columns: [\"_time\", \"lat\", \"lon\"])"
        }
      ]
    },
    {
      "type": "timeseries", "title": "Speed", "gridPos": { "h": 8, "w": 12, "x": 0, "y": 28 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "velocitykmh" }, "overrides": [] },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"speed_kmh\")\n  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)"
        }
      ]
    },
    {
      "type": "timeseries", "title": "Distance per day", "gridPos": { "h": 8, "w": 12, "x": 12, "y": 28 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "lengthkm", "custom": { "drawStyle": "bars", "fillOpacity": 80 } }, "overrides": [] },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"distance_m\")\n  |> aggregateWindow(every: 1d, fn: sum, createEmpty: false)\n  |> map(fn: (r) => ({r with _value: r._value / 1000.0}))"
        }
      ]
    },
    { "type": "row", "title": "Health", "collapsed": false, "gridPos": { "h": 1, "w": 24, "x": 0, "y": 36 }, "panels": [] },
    {
      "type": "timeseries", "title": "Voltage", "gridPos": { "h": 8, "w": 12, "x": 0, "y": 37 },
      "description": "Thresholds suit a 12 V lead-acid/AGM starter battery; adjust for your battery chemistry.",
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": {
        "defaults": {
          "unit": "volt",
          "custom": { "thresholdsStyle": { "mode": "line+area" } },
          "thresholds": { "mode": "absolute", "steps": [
            { "color": "red", "value": null },
            { "color": "yellow", "value": 12.0 },
            { "color": "green", "value": 12.4 }
          ] }
        },
        "overrides": []
      },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"voltage_v\")\n  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)"
        }
      ]
    },
    {
      "type": "timeseries", "title": "Battery", "gridPos": { "h": 8, "w": 12, "x": 12, "y": 37 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "percent", "min": 0, "max": 100 }, "overrides": [] },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and r._field == \"battery_pct\")\n  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)"
        }
      ]
    },
    {
      "type": "timeseries", "title": "Satellites & GNSS quality", "gridPos": { "h": 8, "w": 24, "x": 0, "y": 45 },
      "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
      "fieldConfig": { "defaults": { "unit": "none" }, "overrides": [] },
      "targets": [
        {
          "refId": "A", "datasource": { "type": "influxdb", "uid": "${DS_TRACKIWI}" },
          "query": "from(bucket: \"${bucket}\")\n  |> range(start: v.timeRangeStart, stop: v.timeRangeStop)\n  |> filter(fn: (r) => r._measurement == \"trackiwi_position\" and r.tracker_name == \"${tracker}\" and (r._field == \"satellites\" or r._field == \"gnss_quality\"))\n  |> aggregateWindow(every: v.windowPeriod, fn: mean, createEmpty: false)"
        }
      ]
    }
  ]
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_dashboard.py -v && git check-ignore -v deploy/grafana-dashboards/trackiwi.json; echo "exit=$?"`
Expected: 6 passed; `exit=1` (meaning the file is **not** ignored).

- [ ] **Step 5: Add the requirement, the trace, and the spec correction**

Append to `docs/requirements.rst`:

```rst
.. req:: The dashboard is importable anywhere
   :id: REQ_DASHBOARD_PORTABLE
   :tags: portability, influx

   The committed Grafana dashboard references its datasource only through a
   ``DS_TRACKIWI`` variable, reads its bucket and tracker from dashboard
   variables, uses Flux throughout, and contains no hardcoded datasource UID
   or real value. (Influx spec §8.)
```

Append to `docs/traceability.rst`:

```rst
.. test:: Dashboard uses variables only and holds no real values
   :id: TEST_DASHBOARD_PORTABLE
   :verifies: REQ_DASHBOARD_PORTABLE

   ``tests/test_dashboard.py::test_datasource_is_a_variable_everywhere``,
   ``tests/test_dashboard.py::test_every_query_is_flux_on_the_bucket_variable``,
   ``tests/test_dashboard.py::test_no_hardcoded_datasource_uids_or_real_values``
```

In the influx spec, change §8's `tracker` variable query to `schema.tagValues(bucket: "${bucket}", tag: "tracker_name", predicate: (r) => r._measurement == "trackiwi_position")`, and change §9's dashboard test row from "uses `v.bucket`" to "uses `\"${bucket}\"`". Add one sentence to §8: "Custom dashboard variables are interpolated as `${name}` in Flux; only Grafana's built-ins (`v.timeRangeStart`, `v.timeRangeStop`, `v.windowPeriod`) exist as `v.*`."

- [ ] **Step 6: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes. `check-yaml` does not touch JSON. The private-data guard lets the dashboard through (it is not GeoJSON).

- [ ] **Step 7: Commit**

```bash
git add .gitignore deploy/grafana-dashboards/trackiwi.json tests/test_dashboard.py pyproject.toml docs/requirements.rst docs/traceability.rst docs/superpowers/specs/2026-09-25-influx-ingest-grafana-design.md
git commit -m "feat: portable Flux Grafana dashboard for trackiwi data

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `llms.txt`

**Interfaces:**
- Consumes: the commands, file names and variables from Tasks 3–8.
- Produces: user- and agent-facing documentation. Nothing downstream consumes it.

- [ ] **Step 1: Add the README section**

Add this section to `README.md`, directly before the existing `## Security` heading:

````markdown
## InfluxDB & Grafana

`trackiwi ingest` syncs from trackiwi into the local cache, then mirrors every
position not yet sent into InfluxDB (measurement `trackiwi_position`). The cache
is the buffer: if InfluxDB or the network is down, positions wait in the cache
and the next run catches up. Re-sending is harmless (same point, same
timestamp), and the first run is the full backfill.

```bash
trackiwi influx check   # connectivity, version, auth — read-only
trackiwi influx push    # send cached positions not yet mirrored
trackiwi ingest         # sync + push; what the timer/container runs
```

### Option A — turnkey stack (Docker Compose)

```bash
cp deploy/example.env deploy/.env        # replace every "changeme" value
docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi login
docker compose -f deploy/docker-compose.yml up -d
```

Grafana runs at <http://localhost:3000> with the **trackiwi** dashboard already
provisioned. The ingest container runs every `TRACKIWI_INGEST_INTERVAL` seconds.

### Option B — your own InfluxDB (systemd timer)

1. Copy `examples/influx.example.toml` to `~/.config/trackiwi/influx.toml` (mode 0600) and fill it in.
2. Put the token into the environment variable named by `token_env` (default `TRACKIWI_INFLUX_TOKEN`), or into the file named by `token_file`. Never put it in `influx.toml`.
3. Run `trackiwi influx check`.
4. Install the user timer from `deploy/systemd/` (instructions in the unit file).
5. Import `deploy/grafana-dashboards/trackiwi.json` into Grafana. Pick your InfluxDB datasource and set the **bucket** variable.

The dashboard is **Flux**, so it requires InfluxDB 2.x or InfluxDB Cloud, and
Grafana 10 or later. The ingest itself also writes to InfluxDB 1.x (`version = 1`,
`database = …`), but 1.x users need their own dashboard.
````

- [ ] **Step 2: Update `CLAUDE.md`**

Add these bullets to the `## Rules` list in `CLAUDE.md`, after the existing module-boundary bullet (the one about `store.py`/`client.py` imports):

```markdown
- **`influx.py` is the only module that talks to InfluxDB**, `lineprotocol.py`
  is pure. `influx.py` and `client.py` never import each other.
- **Read-only means read-only against trackiwi.** InfluxDB writes are allowed
  only as `trackiwi_position` line protocol to the one configured target
  (`REQ_INFLUX_WRITE_SCOPE`). Never add deletes, bucket management or other
  measurements.
- **Mirror invariant:** `mirror_state` advances only after InfluxDB
  acknowledges a batch (`REQ_MIRROR_RESUME`) — same discipline as `sync`.
- **Deployment specifics never enter this repo.** `deploy/` and `examples/`
  hold placeholders only (`REQ_PORTABLE_CONFIG`); a real deployment (e.g.
  buspi) is configured from its own repo, which consumes this package. The env
  template is `deploy/example.env` because the guard rejects `.env.*` names.
```

Add to the `## Gotchas` list in `CLAUDE.md`:

```markdown
- A **new test module** must be added to the `[[tool.mypy.overrides]]` `module`
  list in `pyproject.toml`, or strict mypy rejects its unannotated test functions.
- `*.json` is git-ignored (a GeoJSON export named `.json` must never be
  committed); the dashboard is re-included by `!deploy/grafana-dashboards/*.json`.
- Grafana Flux queries use `"${bucket}"` / `"${tracker}"` for dashboard
  variables; only built-ins (`v.timeRangeStart`, `v.windowPeriod`) are `v.*`.
```

- [ ] **Step 3: Update `llms.txt`**

Add to the module-map section of `llms.txt`:

```markdown
- `trackiwi/lineprotocol.py` — pure: cached row → InfluxDB line protocol (natural units)
- `trackiwi/influx.py` — InfluxDB target config, version detection, writes, `check`, and the resumable `mirror`
- `deploy/` — Docker Compose stack, Grafana provisioning + portable Flux dashboard, systemd timer
```

- [ ] **Step 4: Run the full gate**

Run: `./tools/ci.sh`
Expected: every stage passes.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md llms.txt
git commit -m "docs: InfluxDB ingest and Grafana dashboard usage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01Ge1FeKk9vZWN51yWbjF3hX"
```
