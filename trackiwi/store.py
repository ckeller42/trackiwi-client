"""Local SQLite cache of positions.

This is the only module that touches the database. It performs no network I/O.
Open failures surface as `TrackiwiError`, never raw `sqlite3` exceptions.

The cache is a complete movement history, so the file is created owner-only
inside an owner-only directory.
"""

from __future__ import annotations

import functools
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar, cast

from . import COLUMNS, TrackiwiError

_P = ParamSpec("_P")
_R = TypeVar("_R")


def _converts_sqlite_errors(
    method: Callable[Concatenate[Store, _P], _R],
) -> Callable[Concatenate[Store, _P], _R]:
    """Turn a `sqlite3.Error` from a Store method into a `TrackiwiError`.

    Only `__enter__` converted before; a lock or I/O error during any later
    operation — an overlapping ingest, corruption found on a read, a commit —
    escaped `main()` as a traceback (#20). `_open_failure` still distinguishes
    a lock (wait and re-run) from corruption (purge).
    """

    @functools.wraps(method)
    def wrapper(self: Store, *args: _P.args, **kwargs: _P.kwargs) -> _R:
        try:
            return method(self, *args, **kwargs)
        except (sqlite3.IntegrityError, sqlite3.ProgrammingError):
            # A bad row or a misused query is a caller/bug error, not a locked
            # or corrupt cache — let it surface as itself, not "run purge".
            raise
        except sqlite3.Error as error:
            raise self._open_failure(error) from error

    # functools.wraps + Concatenate confuses mypy's return inference; the
    # wrapper has the declared signature.
    return cast("Callable[Concatenate[Store, _P], _R]", wrapper)


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
CREATE TABLE IF NOT EXISTS mirror_state (
  target  TEXT PRIMARY KEY,
  last_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracker_names (
  tracker_id INTEGER PRIMARY KEY,
  name       TEXT NOT NULL
);
"""


#: Suffixes SQLite appends for its rollback journal and WAL; they can hold rows.
SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def default_db_path() -> Path:
    """Return the cache path, honouring ``XDG_DATA_HOME``."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "trackiwi" / "positions.db"


def cache_files(path: Path) -> tuple[Path, ...]:
    """Every file that can hold cached positions for the database at `path`.

    Shared by `trackiwi purge` and `Store.purge()`.
    """
    return (path, *(path.with_name(path.name + suffix) for suffix in SIDECAR_SUFFIXES))


class Store:
    """Owner-only SQLite cache of position rows."""

    def __init__(self, path: Path | str | None = None, *, create: bool = True) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.create = create
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Store:
        """Open the cache owner-only, distinguishing a lock from corruption.

        Implements :need:`REQ_CACHE_MODE_0600` (0600 file in a 0700 directory,
        self-healing a widened mode) and :need:`REQ_PURGE_NOT_ON_LOCK` (a
        locked cache is reported as transient, never as corruption to purge),
        the latter via :meth:`_open_failure`.

        With ``create=False`` a missing cache is **not** created: an in-memory
        empty database answers every read, so a read-only command (``export``,
        ``heading``) never brings the file into being.
        """
        if not self.create and not self.path.exists():
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            self._conn = conn
            return self
        # Create at 0600 before sqlite3 can create it at the umask default (no
        # chmod window). O_CREAT's mode applies to new files only; the chmod
        # below heals an existing one. mkdir/chmod are inside the guard too: a
        # read-only mount (a container) otherwise raised a raw OSError (#20).
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.path.parent, 0o700)
            os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise TrackiwiError(f"cannot open local cache ({self.path}): {error}") from error
        # timeout=30 so a timer-driven ingest waits out a manual sync's write
        # lock instead of failing at the 5 s default (#20).
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            # Close the connection before converting to TrackiwiError.
            conn.close()
            raise self._open_failure(error) from error
        self._conn = conn
        return self

    def _open_failure(self, error: sqlite3.Error) -> TrackiwiError:
        """Map an open failure to a user-facing error.

        A lock is transient (wait and re-run, never purge); anything else is
        corruption and gets the purge advice. The lock check comes first
        because `OperationalError` subclasses `DatabaseError`.
        """
        detail = str(error)
        if isinstance(error, sqlite3.OperationalError) and (
            "locked" in detail.lower() or "busy" in detail.lower()
        ):
            return TrackiwiError(
                f"local cache is in use ({self.path}): {detail} — another trackiwi "
                "process may be running; wait for it to finish and re-run"
            )
        return TrackiwiError(
            f"local cache is corrupt ({self.path}): {detail} — run 'trackiwi purge --yes'"
        )

    def __exit__(self, exc_type: type[BaseException] | None, *exc: object) -> None:
        if self._conn is not None:
            try:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
            except sqlite3.Error as error:
                self._conn.close()
                self._conn = None
                # Only convert a clean-exit commit failure; a rollback failing
                # while already unwinding must not mask the original error.
                if exc_type is None:
                    raise self._open_failure(error) from error
                return
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """The open connection, or raise if used outside a ``with`` block."""
        if self._conn is None:
            raise RuntimeError("Store must be used as a context manager")
        return self._conn

    @_converts_sqlite_errors
    def upsert(self, rows: Iterable[tuple[Any, ...]]) -> int:
        """Insert new `rows`, returning how many were *newly inserted*.

        ``INSERT OR IGNORE``: positions are immutable fixes keyed by the
        vendor's own id, so an id already stored is kept unchanged — a
        re-fetched row (e.g. from ``sync --full``) is never rewritten, which
        could otherwise change a point the mirror has already sent. The
        inserted count comes straight from ``cursor.rowcount`` (a row that
        violates a constraint is ignored, not stored, and not counted).

        Implements :need:`REQ_CACHE_NO_RESENT_FIX`: a row matching a cached
        fix (tracker, time, place) under another id is ignored too; the
        ``(tracker_id, fix_at)`` index keeps that lookup cheap.
        """
        new_row = ", ".join(f"? AS {column}" for column in COLUMNS)
        sql = (
            f"INSERT OR IGNORE INTO positions ({','.join(COLUMNS)}) "
            f"SELECT * FROM (SELECT {new_row}) AS new WHERE NOT EXISTS ("
            "SELECT 1 FROM positions AS p WHERE p.tracker_id = new.tracker_id "
            "AND p.fix_at = new.fix_at AND p.latitude = new.latitude "
            "AND p.longitude = new.longitude)"
        )
        cursor = self.conn.executemany(sql, list(rows))
        self.conn.commit()
        return cursor.rowcount

    @_converts_sqlite_errors
    def max_id(self) -> int | None:
        """Return the highest stored position id, or ``None`` when empty."""
        highest: int | None = self.conn.execute("SELECT MAX(id) FROM positions").fetchone()[0]
        return highest

    @_converts_sqlite_errors
    def count(self) -> int:
        """Return the number of cached positions."""
        total: int = self.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        return total

    @_converts_sqlite_errors
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

    @_converts_sqlite_errors
    def mirror_position(self, target: str) -> int:
        """Return the highest id already mirrored to `target`, or 0 if none.

        Part of :need:`REQ_MIRROR_RESUME`: the mirror resumes from here.
        """
        row = self.conn.execute(
            "SELECT last_id FROM mirror_state WHERE target = ?", (target,)
        ).fetchone()
        last: int = row[0] if row is not None else 0
        return last

    @_converts_sqlite_errors
    def set_mirror_position(self, target: str, last_id: int) -> None:
        """Record that every row up to `last_id` reached `target`, and commit.

        Commits immediately so a later failure cannot roll back acknowledged progress.
        """
        self.conn.execute(
            "INSERT OR REPLACE INTO mirror_state (target, last_id) VALUES (?, ?)",
            (target, last_id),
        )
        self.conn.commit()

    @_converts_sqlite_errors
    def rows_after(self, last_id: int, limit: int) -> list[sqlite3.Row]:
        """Return up to `limit` rows with ``id > last_id``, ordered by id."""
        return list(
            self.conn.execute(
                "SELECT * FROM positions WHERE id > ? ORDER BY id LIMIT ?",
                (last_id, limit),
            )
        )

    @_converts_sqlite_errors
    def set_tracker_names(self, names: Mapping[int, str]) -> None:
        """Pin each tracker's first real name; upgrade a fallback to a real one.

        ``tracker_name`` is part of the InfluxDB series key, so replacing a real
        name when a tracker is renamed would split its history into two series
        and defeat idempotent re-mirroring (#24, :need:`REQ_MIRROR_IDEMPOTENT`).
        The first real name is therefore kept; a later, different name is
        ignored. The one exception is the ``tracker <id>`` fallback from
        `name_unnamed_trackers`, which a real name still replaces
        (:need:`REQ_MIRROR_FALLBACK_NAME`).
        """
        self.conn.executemany(
            "INSERT INTO tracker_names (tracker_id, name) VALUES (?, ?) "
            "ON CONFLICT(tracker_id) DO UPDATE SET name = excluded.name "
            "WHERE name = 'tracker ' || tracker_id",
            list(names.items()),
        )
        self.conn.commit()

    @_converts_sqlite_errors
    def name_unnamed_trackers(self) -> None:
        """Give every cached tracker that has no stored name ``tracker <id>``.

        Implements :need:`REQ_MIRROR_FALLBACK_NAME`. Never overwrites a stored
        name; `set_tracker_names` may replace a fallback.
        """
        self.conn.execute(
            "INSERT OR IGNORE INTO tracker_names (tracker_id, name) "
            "SELECT DISTINCT tracker_id, 'tracker ' || tracker_id FROM positions"
        )
        self.conn.commit()

    @_converts_sqlite_errors
    def tracker_names(self) -> dict[int, str]:
        """Return every stored tracker id → display name."""
        return {
            int(tracker_id): str(name)
            for tracker_id, name in self.conn.execute("SELECT tracker_id, name FROM tracker_names")
        }

    def purge(self) -> None:
        """Close and delete the cache, including SQLite's sidecar files.

        Implements :need:`REQ_PURGE_DELETES`.

        Library twin of `trackiwi purge` (same `cache_files`). The CLI does not
        call this: it unlinks without opening, so a corrupt cache still goes.
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        for target in cache_files(self.path):
            target.unlink(missing_ok=True)
