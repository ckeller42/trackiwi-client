"""Live contract test. Requires a real session and TRACKIWI_LIVE=1.

Run with:  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v

It prints what it learns so the design spec's open questions (section 10) can
be answered from real data. It asserts only shape, never content, and must
never print the token.
"""

import json
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

    # trackers() already unwraps the envelope, so it can't tell us what the
    # raw shape actually was. Go through the same authenticated request path
    # it uses, but look at the raw JSON before unwrapping — that's the only
    # way to answer the open envelope question. Never print field values,
    # only the type and, for an object, its top-level keys.
    _, _, body = client._api("GET", "/api/v2/trackers")
    raw = json.loads(body)
    if isinstance(raw, dict):
        print("\ntrackers envelope: object with keys", sorted(raw.keys()))
    else:
        print("\ntrackers envelope: bare", type(raw).__name__)

    trackers = client.trackers()
    assert isinstance(trackers, list) and trackers
    print("tracker keys:", sorted(trackers[0]))

    try:
        rows, skipped, total = next(iter(client.sync()))
    except StopIteration:
        pytest.skip("no positions recorded for this account yet")

    assert rows and len(rows[0]) == len(COLUMNS)
    sample = dict(zip(COLUMNS, rows[0], strict=True))

    print("position-count header:", total)
    print("skipped rows in first page:", skipped)
    # Answers open questions 10.5 and 10.6: a plausible recent epoch-second
    # value confirms the unit; the rest shows the magnitude of each field.
    for field in ("fix_at", "fix_timezone", "speed", "altitude", "distance"):
        print(f"{field} = {sample[field]!r}")
    assert 1_600_000_000 < sample["fix_at"] < 2_000_000_000, "fix_at is not epoch seconds"
