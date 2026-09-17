"""Local SQLite cache of positions.

This is the only module that touches the database. It performs no network I/O.
The single import from `client.py` is the `TrackiwiError` type: the design spec
(section 4) deliberately keeps one exception pair instead of a hierarchy, and a
corrupt cache has to be reportable as a user-facing error rather than as a raw
`sqlite3` exception.

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
from .client import TrackiwiError

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
            raise TrackiwiError(
                f"local cache is corrupt ({self.path}) — run 'trackiwi purge --yes'"
            ) from error
        self._conn = conn
        return self

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
        if self._conn is None:
            raise RuntimeError("Store must be used as a context manager")
        return self._conn

    def upsert(self, rows: Iterable[tuple]) -> int:
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
