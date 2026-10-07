---
name: check-trackiwi-api
description: Use when the trackiwi API may have changed, when sync/ingest starts failing with shape errors ("unexpected ... response", skipped malformed rows, "no new records past offset"), or before a release. Runs the opt-in live contract test and says how to read it.
---

# Check the client against the live trackiwi API

```bash
TRACKIWI_LIVE=1 .venv/bin/pytest -m live -s -v tests/test_live.py
```

Needs the existing session in `~/.config/trackiwi/config.json`. No `.venv`:
`/opt/local/bin/python3.13 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.

## Hard rules

- **Read-only.** Only the GETs the client already uses, `GET /api/v2/session`
  and `POST /api/v2/trackers/sync` (a read). Never `test_alarm`, never a
  trailing-slash mutation route, never `PUT /api/v2/session/push_token`.
- **No reverse engineering.** Do not read or diff the vendor's web-app
  JavaScript.
- **No real data anywhere** — code, tests, commits, issues, PRs, reports.
  Print and report only types, key names, counts, units and plausibility
  verdicts. Never coordinates, tracker names, alarm events, geofence values,
  email or the token. The `trackers` response *is* location data.
- **Session invalid (`AuthError` / 401)? Stop and report.** Never ask for,
  type or handle the password.

## Reading the result

| Test | Proves | A failure means |
| --- | --- | --- |
| `test_live_session` | `GET /api/v2/session` is 2xx | session expired (stop), or the route moved |
| `test_live_trackers_endpoint` | envelope type; the client returns a list; `id` and `name` exist | "assert" on keys: a field was renamed or dropped, `trackers` prints `-` |
| `test_live_tracker_timestamps` | `latest_positionlog.fix_at`/`received_at` are ISO 8601 strings | the type changed; `epoch_from_iso` callers break |
| `test_live_sync` | 14 CSV columns, `fix_at` in epoch seconds, count header is an int, offset is exclusive | sync/ingest/export are affected: P1 |

- Any 3xx surfaces as `TrackiwiError` "a redirect" in whichever test hit it.
- Printed lines are findings, not failures: `envelope:` (bare list vs object),
  `keys the CLI does not print` (new server fields), `empty, element shape
  unverified` (nothing learned), `speed / (distance-derived m/s)` (about 3.6
  reads as km/h, 1 as m/s, 1.94 as knots — plausibility, not proof).
- "offset is not exclusive": read "Offset semantics in `sync`" in `AGENTS.md`
  before touching anything.

## On drift

1. One GitHub issue per mismatch: endpoint, expected vs observed **shape**,
   labels `bug` plus `P1` (breaks sync/ingest/export) or `P2`. No real data.
2. Do not hot-fix the client in the PR that found it. The fix gets its own PR
   with a synthetic fixture reproducing the new shape.
3. Never claim a unit the output cannot prove.
