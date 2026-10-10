"""Mirror resume invariant (REQ_MIRROR_RESUME) and idempotence (REQ_MIRROR_IDEMPOTENT)."""

import contextlib

import pytest
from conftest import FakeOpener, FakeResponse

from trackiwi import TrackiwiError
from trackiwi.influx import InfluxConfig, InfluxWriter, mirror
from trackiwi.store import Store


def _row(pid, tracker=7):
    return (pid, tracker, 1700000000 + pid, 1, 31.0, -41.0, 12, 0.0, 90, 150, 5, 9, 99, 1287)


@contextlib.contextmanager
def _cache(tmp_path):
    """A store whose tracker 7 has a name, as after any successful `ingest`."""
    with Store(tmp_path / "p.db") as store:
        store.set_tracker_names({7: "Bus"})
        yield store


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
    with _cache(tmp_path) as store:
        assert mirror(store, writer, batch_size=2) == 0
    assert writer.batches == []


def test_backfill_sends_everything_in_batches(tmp_path):
    writer = FakeWriter()
    with _cache(tmp_path) as store:
        store.upsert([_row(i) for i in range(1, 6)])
        assert mirror(store, writer, batch_size=2) == 5
        assert store.mirror_position(writer.target_key()) == 5
    assert [_ids(b) for b in writer.batches] == [[1, 2], [3, 4], [5]]


def test_nothing_new_since_last_push_sends_nothing(tmp_path):
    with _cache(tmp_path) as store:
        store.upsert([_row(1), _row(2)])
        mirror(store, FakeWriter(), batch_size=10)
        second = FakeWriter()
        assert mirror(store, second, batch_size=10) == 0
    assert second.batches == []


def test_failed_batch_does_not_advance_state(tmp_path):
    with _cache(tmp_path) as store:
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
    with _cache(tmp_path) as store:
        store.upsert([_row(1), _row(2)])
        mirror(store, FakeWriter(), batch_size=10)
        store.upsert([_row(3)])
        later = FakeWriter()
        assert mirror(store, later, batch_size=10) == 1
    assert [_ids(b) for b in later.batches] == [[3]]


def test_resending_produces_identical_lines(tmp_path):
    """Idempotence: same rows and names → byte-identical points (same series + timestamp)."""
    with _cache(tmp_path) as store:
        store.upsert([_row(1), _row(2)])
        first = FakeWriter()
        mirror(store, first, batch_size=10)
        store.set_mirror_position(first.target_key(), 0)
        again = FakeWriter()
        mirror(store, again, batch_size=10)
    assert first.batches == again.batches
    assert ",tracker_name=Bus " in first.batches[0][0]


# --- No placeholder tags: stop before a tracker without a name (review I1) --


def test_rows_without_a_stored_name_stop_before_their_batch(tmp_path):
    """REQ_MIRROR_IDEMPOTENT: a point is only ever tagged with the real name.

    Writing ``tracker_name=<id>`` and later ``tracker_name=Bus`` for the same
    tracker would split its history and let one row land under two tags.
    """
    with _cache(tmp_path) as store:
        store.upsert([_row(1), _row(2), _row(3, tracker=8), _row(4)])
        writer = FakeWriter()
        with pytest.raises(TrackiwiError, match="no name known for tracker 8") as excinfo:
            mirror(store, writer, batch_size=2)
        assert "trackiwi ingest" in str(excinfo.value)
        # The first batch (ids 1-2) keeps its progress; nothing of batch 2 went out.
        assert store.mirror_position(writer.target_key()) == 2
        assert [_ids(b) for b in writer.batches] == [[1, 2]]
        store.set_tracker_names({8: "Van"})
        rerun = FakeWriter()
        assert mirror(store, rerun, batch_size=2) == 2
    assert all(
        ",tracker_name=" in line and ",tracker_name=8 " not in line for line in rerun.batches[0]
    )


def test_a_non_finite_legacy_row_is_skipped_not_fatal(tmp_path, capsys):
    """A legacy row with a non-finite value must not wedge the mirror (#22).

    Rows from before the parse-time checks can hold inf/nan. `format_point`
    rejects them; `mirror` skips and counts the row (REQ_MALFORMED_SKIP) and
    still advances past it, rather than crashing out of `main()`.
    """
    import math

    bad = _row(2)
    bad = bad[:7] + (math.inf,) + bad[8:]  # speed = inf
    with _cache(tmp_path) as store:
        store.upsert([_row(1), bad, _row(3)])
        writer = FakeWriter()
        assert mirror(store, writer, batch_size=10) == 2
        assert store.mirror_position(writer.target_key()) == 3
    assert _ids(writer.batches[0]) == [1, 3]
    assert "2" in capsys.readouterr().err


def test_no_names_at_all_sends_nothing(tmp_path):
    with Store(tmp_path / "p.db") as store:
        store.upsert([_row(1)])
        writer = FakeWriter()
        with pytest.raises(TrackiwiError, match="no name known for tracker 7"):
            mirror(store, writer)
        assert store.mirror_position(writer.target_key()) == 0
    assert writer.batches == []


# --- Retention drops are acknowledged, not a permanent stall (review I3) ----


def test_a_batch_beyond_retention_advances_the_position(tmp_path, capsys):
    """REQ_MIRROR_RESUME: InfluxDB accepted the batch; the dropped points can never be stored."""
    body = (
        b'{"code":"unprocessable entity",'
        b'"message":"partial write: points beyond retention policy dropped=2"}'
    )
    opener = FakeOpener(FakeResponse(body, status=422), FakeResponse(b"", status=204))
    writer = InfluxWriter(
        InfluxConfig(
            url="http://influx.example.invalid:8086",
            version=2,
            org="home",
            bucket="trackiwi",
            database=None,
            username=None,
            token="tok",
            password=None,
            token_env="TRACKIWI_INFLUX_TOKEN",
        ),
        opener=opener,
    )
    with _cache(tmp_path) as store:
        store.upsert([_row(i) for i in range(1, 5)])
        assert mirror(store, writer, batch_size=2) == 4
        assert store.mirror_position(writer.target_key()) == 4
    assert len(opener.calls) == 2
    assert "beyond retention policy dropped=2" in capsys.readouterr().err
