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
