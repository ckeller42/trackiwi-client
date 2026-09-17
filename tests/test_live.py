"""Live contract test. Requires a real session and TRACKIWI_LIVE=1.

Run with:  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v

It prints what it learns so the design spec's open questions (section 10) can
be answered from real data. It asserts only shape, never content, and must
never print the token.
"""

import os

import pytest

from trackiwi import COLUMNS
from trackiwi.client import Client

pytestmark = pytest.mark.skipif(
    os.environ.get("TRACKIWI_LIVE") != "1", reason="set TRACKIWI_LIVE=1 to run"
)


@pytest.mark.live
def test_live_contract():
    client = Client.load()
    assert client.authenticated, "run 'trackiwi login' first"

    trackers = client.trackers()
    assert isinstance(trackers, list) and trackers
    print("\ntracker keys:", sorted(trackers[0]))

    rows, skipped, total = next(iter(client.sync()))
    assert rows and len(rows[0]) == len(COLUMNS)
    sample = dict(zip(COLUMNS, rows[0], strict=True))

    print("position-count header:", total)
    print("skipped rows in first page:", skipped)
    # Answers open questions 10.5 and 10.6: a plausible recent epoch-second
    # value confirms the unit; the rest shows the magnitude of each field.
    for field in ("fix_at", "fix_timezone", "speed", "altitude", "distance"):
        print(f"{field} = {sample[field]!r}")
    assert 1_600_000_000 < sample["fix_at"] < 2_000_000_000, "fix_at is not epoch seconds"
