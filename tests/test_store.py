import os
import sqlite3
import stat
from pathlib import Path

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


def test_upsert_counts_only_newly_inserted_rows(store):
    """`upsert` used to return `len(rows)`, which `cmd_sync` reported as "N new
    positions" — so `sync --full` over an unchanged cache claimed every
    re-fetched row was new."""
    assert store.upsert([row(1), row(2)]) == 2
    assert store.upsert([row(1), row(2), row(3)]) == 1
    assert store.upsert([row(1)]) == 0


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


def test_a_constraint_violating_row_is_ignored_not_the_whole_batch(tmp_path):
    """INSERT OR IGNORE: a row that violates a constraint (a NULL latitude that
    somehow reached the store) is skipped and not counted, and does not abort
    the valid rows in the same batch (#32)."""
    path = tmp_path / "positions.db"
    ok = (1, 7, 1758000000, 120, 31.0, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)
    null = (2, 7, 1758000060, 120, None, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)
    with Store(path) as s:
        assert s.upsert([ok, null]) == 1
    with Store(path) as s:
        assert s.count() == 1


def test_create_false_on_a_missing_cache_reads_empty_without_creating_it(tmp_path):
    """A read-only command opens with create=False: a never-created cache reads
    empty and the file is not brought into being (#32)."""
    path = tmp_path / "positions.db"
    with Store(path, create=False) as s:
        assert s.count() == 0
        assert s.query() == []
        assert s.max_id() is None
    assert not path.exists()


def test_an_already_stored_position_is_never_rewritten(tmp_path):
    """INSERT OR IGNORE: a re-fetched row keeps its stored value, so a point the
    mirror already sent cannot change under it (#32)."""
    path = tmp_path / "positions.db"
    original = (1, 7, 1758000000, 120, 31.0, -41.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)
    changed = (1, 7, 1758000000, 120, 99.0, -99.0, 12, 0.0, 0, 0, -71, 9, 98, 4120)
    with Store(path) as s:
        assert s.upsert([original]) == 1
        assert s.upsert([changed]) == 0  # same id: ignored, not replaced
        assert s.query()[0]["latitude"] == 31.0


def test_database_is_created_owner_only_without_relying_on_chmod(tmp_path, monkeypatch):
    """The cache used to be created by `sqlite3.connect` at the umask default
    (0644 under the usual umask, 0666 under a permissive one) and narrowed
    only afterwards. With `os.chmod` disabled, only a create-time mode can
    produce 0600."""
    monkeypatch.setattr(os, "chmod", lambda *args, **kwargs: None)
    previous = os.umask(0)
    try:
        path = tmp_path / "sub" / "positions.db"
        with Store(path) as s:
            s.upsert([row(1)])
    finally:
        os.umask(previous)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


def test_corrupt_database_becomes_a_trackiwierror_naming_the_way_out(tmp_path):
    """A non-database file at the cache path used to raise
    `sqlite3.DatabaseError`, which `main()` does not catch: export, sync and
    purge all died with a traceback that gave no hint how to recover."""
    from trackiwi.client import TrackiwiError

    path = tmp_path / "positions.db"
    path.write_bytes(b"not a database, just some bytes\n" * 8)
    with pytest.raises(TrackiwiError, match="purge"), Store(path):
        pass  # pragma: no cover - __enter__ raises


def test_permissions_are_self_healed(tmp_path):
    path = tmp_path / "positions.db"
    path.touch()
    os.chmod(path, 0o644)
    with Store(path) as s:
        s.upsert([row(1)])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# --- A locked cache is not a corrupt cache (N-1) ---


@pytest.fixture
def impatient_sqlite(monkeypatch):
    """Make every `sqlite3.connect` give up on a lock at once, not after 5 s.

    Only the wait is shortened; the error raised is the real
    `sqlite3.OperationalError: database is locked`.
    """
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        kwargs["timeout"] = 0.05
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)


def test_a_locked_database_is_not_reported_as_corrupt(tmp_path, impatient_sqlite):
    """A concurrent `trackiwi` holding a write lock used to be reported as
    `local cache is corrupt — run 'trackiwi purge --yes'`, so following the
    tool's own advice deleted an intact movement history."""
    from trackiwi import TrackiwiError

    path = tmp_path / "positions.db"
    with Store(path) as first:
        first.upsert([row(1)])
    holder = sqlite3.connect(path)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(TrackiwiError) as excinfo, Store(path):
            pass  # pragma: no cover - __enter__ raises
    finally:
        holder.rollback()
        holder.close()
    message = str(excinfo.value)
    assert "purge" not in message
    assert "locked" in message
    with Store(path) as reopened:
        assert reopened.count() == 1


def test_corrupt_database_message_keeps_purge_and_names_the_sqlite_detail(tmp_path):
    from trackiwi import TrackiwiError

    path = tmp_path / "positions.db"
    path.write_bytes(b"not a database, just some bytes\n" * 8)
    with pytest.raises(TrackiwiError) as excinfo, Store(path):
        pass  # pragma: no cover - __enter__ raises
    message = str(excinfo.value)
    assert "purge" in message
    assert "not a database" in message


# --- A lock or filesystem error AFTER open is also converted (#20) ----------


def test_a_lock_during_a_write_is_reported_not_a_traceback(tmp_path, impatient_sqlite):
    """An overlapping `trackiwi` can lock the cache after `__enter__` passed;
    the write must become the 'in use' error, not a raw OperationalError."""
    from trackiwi import TrackiwiError

    path = tmp_path / "positions.db"
    with Store(path) as store:
        holder = sqlite3.connect(path)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            with pytest.raises(TrackiwiError) as excinfo:
                store.upsert([row(1)])
        finally:
            holder.rollback()
            holder.close()
    message = str(excinfo.value)
    assert "locked" in message or "busy" in message
    assert "purge" not in message


def test_a_filesystem_error_opening_the_cache_is_converted(tmp_path, monkeypatch):
    """mkdir on a read-only mount used to raise a raw PermissionError (#20)."""
    from trackiwi import TrackiwiError

    def boom(*args, **kwargs):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(TrackiwiError, match="cache"), Store(tmp_path / "sub" / "positions.db"):
        pass  # pragma: no cover - __enter__ raises


# --- The library-level purge must make the same promise as the CLI (N-5) ---


def test_purge_removes_the_sidecar_files(tmp_path):
    """A `-wal` file can hold committed position rows, so `Store.purge()` has
    to remove the sidecars the CLI's `purge` removes."""
    path = tmp_path / "positions.db"
    store = Store(path)
    with store as opened:
        opened.upsert([row(1)])
    for suffix in ("-journal", "-wal", "-shm"):
        path.with_name(path.name + suffix).write_bytes(b"position rows\n")
    store.purge()
    assert list(tmp_path.iterdir()) == []
