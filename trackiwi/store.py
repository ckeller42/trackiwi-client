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
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        os.chmod(self.path, 0o600)
        self._conn.executescript(_SCHEMA)
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
