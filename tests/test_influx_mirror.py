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
