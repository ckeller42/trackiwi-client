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
