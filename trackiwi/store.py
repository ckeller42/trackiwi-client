"""Local SQLite cache of positions.

This is the only module that touches the database. It performs no network I/O.
`TrackiwiError` comes from `trackiwi/__init__.py`, the shared-contract module,
not from `client.py`: the design spec (section 4) deliberately keeps one
exception pair instead of a hierarchy, and a corrupt cache has to be
reportable as a user-facing error rather than as a raw `sqlite3` exception.

The cache is a complete movement history of a physical vehicle, so the file is
created owner-only inside an owner-only directory. See the design spec,
section 7.1.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import COLUMNS, TrackiwiError

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


#: Suffixes SQLite appends to the database file name for its rollback journal
#: and WAL bookkeeping. A journal or WAL left behind by a crashed write holds
#: position rows just like the database does, so deleting the history means
#: deleting these too.
SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


def default_db_path() -> Path:
    """Return the cache path, honouring ``XDG_DATA_HOME``."""
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "trackiwi" / "positions.db"


def cache_files(path: Path) -> tuple[Path, ...]:
    """Every file that can hold cached positions for the database at `path`.

    The single definition behind both `trackiwi purge` and `Store.purge()`:
    the two used to delete different sets of files while claiming to be
    equivalent, which made the library API the weaker privacy promise.
    """
    return (path, *(path.with_name(path.name + suffix) for suffix in SIDECAR_SUFFIXES))


class Store:
    """Owner-only SQLite cache of position rows."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> Store:
        """Open the cache owner-only, distinguishing a lock from corruption.

        Implements :need:`REQ_CACHE_MODE_0600` (0600 file in a 0700 directory,
        self-healing a widened mode) and :need:`REQ_PURGE_NOT_ON_LOCK` (a
        locked cache is reported as transient, never as corruption to purge),
        the latter via :meth:`_open_failure`.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        # Create the file at 0600 *before* sqlite3 can create it at the umask
        # default: the cache is a movement history, and a chmod after the fact
        # leaves a window in which another local process can open it and keep
        # the descriptor. The descriptor is closed immediately; sqlite3 opens
        # its own. O_CREAT's mode applies to a new file only, so the chmod
        # below still does the steady-state self-heal for an existing file.
        try:
            os.close(os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600))
            os.chmod(self.path, 0o600)
        except OSError as error:
            raise TrackiwiError(f"cannot open local cache ({self.path}): {error}") from error
        conn = sqlite3.connect(self.path)
        try:
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            # A corrupt or non-database file used to raise sqlite3.DatabaseError
            # here, which is not a TrackiwiError and escaped main() as a
            # traceback that gave no hint how to recover. Close the connection
            # on the way out so a short-lived CLI does not leak it either.
            conn.close()
            raise self._open_failure(error) from error
        self._conn = conn
        return self

    def _open_failure(self, error: sqlite3.Error) -> TrackiwiError:
        """Turn a failure to open the cache into the *right* user-facing error.

        The two conditions need opposite advice, and telling them apart is not
        cosmetic. A locked database is transient — another `trackiwi` process
        (or a hung one) holds a write lock — and the data is intact; a corrupt
        one is only recoverable by deleting it. Reporting a lock as corruption
        told the user to run `trackiwi purge --yes`, which deletes a complete
        movement history (spec section 7.1) that nothing was wrong with.
        `sqlite3.OperationalError` is a subclass of `sqlite3.DatabaseError`, so
        the lock check has to come first. The sqlite text is included either
        way: without it neither case could be diagnosed from the CLI output.
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
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """The open connection, or raise if used outside a ``with`` block."""
        if self._conn is None:
            raise RuntimeError("Store must be used as a context manager")
        return self._conn

    def upsert(self, rows: Iterable[tuple[Any, ...]]) -> int:
        """Insert or replace `rows`, returning how many were *newly inserted*.

        The count is a row-count delta rather than `len(rows)`, because
        `INSERT OR REPLACE` cannot distinguish an insert from a replace and the
        CLI reports this number to the user as "N new positions": returning the
        batch size made `sync --full` over an unchanged cache claim every
        re-fetched row was new. A delta also stays correct when a batch repeats
        an id within itself.
        """
        placeholders = ",".join("?" * len(COLUMNS))
        sql = f"INSERT OR REPLACE INTO positions ({','.join(COLUMNS)}) VALUES ({placeholders})"
        rows = list(rows)
        before = self.count()
        self.conn.executemany(sql, rows)
        self.conn.commit()
        return self.count() - before

    def max_id(self) -> int | None:
        """Return the highest stored position id, or ``None`` when empty."""
        # `fetchone()` and its columns are `Any` (sqlite3 has no row types); the
        # annotated local pins the boundary so the return stays honestly typed.
        highest: int | None = self.conn.execute("SELECT MAX(id) FROM positions").fetchone()[0]
        return highest

    def count(self) -> int:
        """Return the number of cached positions."""
        total: int = self.conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        return total

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
        """Close and delete the cache, including SQLite's sidecar files.

        Implements :need:`REQ_PURGE_DELETES`.

        The library-level equivalent of `trackiwi purge`, and equivalent in
        what it deletes as well as in name: both go through `cache_files`, so
        a library consumer gets the same guarantee as a CLI user. The CLI
        deliberately does *not* go through here: it unlinks the paths without
        opening the database, so that a corrupt cache can still be deleted.
        """
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        for target in cache_files(self.path):
            target.unlink(missing_ok=True)
