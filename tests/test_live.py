"""Live contract test. Requires a real session and TRACKIWI_LIVE=1.

Run with:  TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v tests/test_live.py

One test per endpoint family the client calls. It prints what it learns so
drift in the undocumented API (and the design spec's open questions, section
10) can be read from the output. It asserts only shape, never content: it
prints types, key names, counts and ratios — never coordinates, names, alarm
events, geofence values or the token. Read-only: `GET`s and the sync `POST`.
"""

import json
import os
import statistics

import pytest

from trackiwi import COLUMNS
from trackiwi.cli import READ_COMMANDS
from trackiwi.client import SYNC_TIMEOUT, Client, _check, epoch_from_iso, parse_positions

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("TRACKIWI_LIVE") != "1", reason="set TRACKIWI_LIVE=1 to run"),
]

#: Client method -> keys the CLI prints for it. `trackers` is the one list
#: command outside `READ_COMMANDS`.
PRINTED_KEYS = {"trackers": ("id", "name")} | {
    method: fields for _, method, fields, _ in READ_COMMANDS
}


def _client():
    client = Client.load()
    assert client.authenticated, "run 'trackiwi login' first"
    return client


def _raw(client, method, path, body=None, timeout=30):
    """One authenticated request; `_check` raises on any 3xx/4xx/5xx."""
    status, headers, payload = client._api(method, path, body=body, timeout=timeout)
    _check(status, payload, client.token)
    assert 200 <= status < 300, f"{method} {path} answered HTTP {status}"
    return headers, payload


def test_live_session():
    client = _client()
    _raw(client, "GET", "/api/v2/session")


@pytest.mark.parametrize("method", sorted(PRINTED_KEYS))
def test_live_list_endpoint(method):
    client = _client()
    # The client method unwraps the envelope, so only the raw body can say
    # what the wire shape is.
    _, body = _raw(client, "GET", f"/api/v2/{method}")
    raw = json.loads(body)
    if isinstance(raw, dict):
        print(f"\n{method} envelope: object with keys {sorted(raw)}")
    else:
        print(f"\n{method} envelope: bare {type(raw).__name__}")

    records = getattr(client, method)()
    assert isinstance(records, list)
    print(f"{method}: {len(records)} record(s)")
    if not records:
        print(f"{method}: empty, element shape unverified")
        return
    assert all(isinstance(r, dict) for r in records)
    in_all = set.intersection(*(set(r) for r in records))
    in_any = set.union(*(set(r) for r in records))
    expected = set(PRINTED_KEYS[method])
    print(f"{method} keys: {sorted(in_any)}")
    print(f"{method} keys the CLI does not print: {sorted(in_any - expected)}")
    assert not expected - in_all, f"{method}: missing keys {sorted(expected - in_all)}"


def test_live_tracker_timestamps():
    logs = [t.get("latest_positionlog") for t in _client().trackers()]
    print(f"\nlatest_positionlog types: {sorted({type(log).__name__ for log in logs})}")
    logs = [log for log in logs if isinstance(log, dict)]
    if not logs:
        pytest.skip("no tracker has a latest_positionlog")
    print("latest_positionlog keys:", sorted(set.union(*(set(log) for log in logs))))
    for log in logs:
        for field in ("fix_at", "received_at"):
            assert isinstance(log.get(field), str), f"{field} is {type(log.get(field)).__name__}"
            assert 1_600_000_000 < epoch_from_iso(log[field]) < 2_000_000_000


def test_live_sync():
    client = _client()
    path = "/api/v2/trackers/sync"
    headers, body = _raw(client, "POST", path, {"initial_sync": True}, SYNC_TIMEOUT)
    lines = [line for line in body.decode().splitlines() if line.strip()]
    if not lines:
        pytest.skip("no positions recorded for this account yet")

    print("\nposition-count header parses to int:", int(headers["trackiwi-position-count"]) >= 0)
    print("columns per line:", sorted({len(line.split(",")) for line in lines}))
    assert {len(line.split(",")) for line in lines} == {len(COLUMNS)}

    rows, skipped, last_id = parse_positions(body.decode())
    print(f"page 1: {len(rows)} rows, {skipped} skipped")
    assert rows and last_id is not None, f"first sync page had no usable row ({skipped} skipped)"
    assert skipped == 0, f"{skipped} rows the parser no longer understands"
    fixes = [dict(zip(COLUMNS, row, strict=True)) for row in rows]
    # Raw CSV, not the normalised rows: the wire unit is the question.
    raw_fix_at = [int(line.split(",")[COLUMNS.index("fix_at")]) for line in lines]
    assert all(1_600_000_000 < v < 2_000_000_000 for v in raw_fix_at), "fix_at not epoch seconds"
    assert all(isinstance(f["fix_timezone"], int) for f in fixes)

    # Unit plausibility without printing a value: `distance` is centimetres
    # per fix (verified), so distance / 100 / dt is the mean speed in m/s.
    # A median ratio near 3.6 means `speed` is km/h, near 1 m/s, near 1.94 kn.
    fixes.sort(key=lambda f: (f["tracker_id"], f["fix_at"]))
    ratios = [
        b["speed"] / (b["distance"] / 100 / (b["fix_at"] - a["fix_at"]))
        for a, b in zip(fixes, fixes[1:], strict=False)
        if a["tracker_id"] == b["tracker_id"]
        and 0 < b["fix_at"] - a["fix_at"] <= 120
        and b["speed"]
        and b["distance"]
    ]
    if ratios:
        median = statistics.median(ratios)
        print(f"speed / (distance-derived m/s): median {median:.2f}, n={len(ratios)}")
    print("altitude types:", sorted({type(f["altitude"]).__name__ for f in fixes}))

    # Offset is exclusive: asking past the highest id must not return it again.
    # An empty page 2 proves it too (inclusive would return the boundary row).
    _, body2 = _raw(client, "POST", path, {"offset": last_id}, SYNC_TIMEOUT)
    ids2 = [int(line.split(",")[0]) for line in body2.decode().splitlines() if line.strip()]
    print(f"page 2 past the max id of page 1: {len(ids2)} rows")
    assert all(i > last_id for i in ids2), "offset is not exclusive"
