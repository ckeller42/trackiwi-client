# trackiwi-client — Design

**Date:** 2026-09-17
**Status:** Approved design, pending implementation plan

## 1. Purpose

A standalone Python client for reading data out of a personal
[trackiwi](https://www.trackiwi.com) account. trackiwi is a German GPS tracker
product aimed at campervans. It publishes no API documentation and ships no SDK,
so this client is built against the private API its own web/mobile app uses.

The client exists to get *your own data out of your own account*: which trackers
exist, and where they have been.

### Non-goals

- **No trip-planning coupling.** The client knows nothing about any trip, route
  plan or itinerary. It emits standard GPS formats; what consumes them is not
  this project's concern.
- **No write access.** See §7. The only state-changing call is `logout`, which
  revokes the client's own session.
- **No support for other people's accounts**, multi-tenancy, or a hosted service.
- **Not a reimplementation of the trackiwi app.** Tours, markers, alarms and
  shares are deliberately out of scope for v1.

## 2. Prior art

Checked 2026-09-17: no client exists.

| Source | Result |
| --- | --- |
| GitHub repository search (`trackiwi`) | 0 results |
| PyPI (`trackiwi`, `pytrackiwi`, `trackiwi-api`, `python-trackiwi`) | all 404 |
| npm registry | 0 results |
| Web, forums, Home Assistant community | no hits |

The vendor's GitHub organisation [`trackiwi`](https://github.com/trackiwi)
exists (created 2024-11-01) but contains only three **forks** of Elixir
libraries it consumes internally (`pigeon`, `geocalc`, `kadabra`). This is the
only public signal about their stack: the backend is Elixir/Phoenix. No SDK, no
API documentation, no community reverse-engineering exists.

This client would therefore be the first. It also means there is no published
schema to rely on and no deprecation policy — the API can change without notice.

## 3. API reference (reverse-engineered)

Derived by reading the web app's JavaScript bundles at `app.trackiwi.com`
(`index-B-l6q9QX.js`, `LoginPage-BN7o2iI5.js`). **Read-only inspection of
publicly served files; no authenticated requests were made.** The mobile apps
are Capacitor wrappers around the same bundle, so they carry no additional
information.

Everything in this section is *observed*, not *documented*. Treat it as a
starting point that must be verified against live responses, not as a contract.

### 3.1 Hosts

| Role | Host |
| --- | --- |
| Website / auth | `https://www.trackiwi.com` |
| API | value of `server` from the login response — **never hardcode** |
| Share links | `https://share.trackiwi.com` |

The login response carries the API base. The client must store and use it.

### 3.2 Authentication

`POST {website}/api/login`

```json
{"email": "...", "password": "..."}
```

Request headers observed: `Accept: application/json`, `App-Name: trackiwi`,
`App-Version`, `User-Platform`, `User-Device`, `User-OS`, `User-Timezone`
(minutes, from `Date.getTimezoneOffset()`).

Success (200) returns `{server, token, user}`. `user` carries at least `id`,
`is_tester`, `early_access_features[]`.

Status semantics, taken from the app's own error handling:

| Status | Meaning |
| --- | --- |
| 200 | Success |
| 401, 412 | Invalid credentials |
| 409 | A distinct account state requiring user action; exact meaning unknown (§10) |
| 503 | Maintenance |
| other | Unknown error |

Authenticated requests use `Authorization: Bearer <token>` against the `server`
base.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v2/session` | Validate token; returns `{token, user}` |
| `DELETE /api/v2/session` | Revoke the session (server-side logout) |
| `POST {website}/api/login/refresh` | Body `{user_id}`; headers `Trackiwi-Backend-Authorization: Bearer <token>` and `Trackiwi-User-Id` |

The app additionally accepts an auto-login via query parameters
`?token=<token>&apibase=<server>`, confirming that a session consists of exactly
those two values. This enables a password-free auth path (§6.1) — and is also a
weakness in the product (§7.4).

### 3.3 Positions (the data that matters)

`POST {server}/api/v2/trackers/sync`

Body is `{"initial_sync": true}` on the first call, then `{"offset": <highest id
seen>}` on each subsequent call. The app uses a 60 s timeout and loops until a
response yields zero rows.

The response body is **CSV text**, not JSON: one record per line, comma
separated, no header row. Columns, in order:

```
id, tracker_id, fix_at, fix_timezone, latitude, longitude,
altitude, speed, course, distance, rssi, sat, battery, voltage
```

Response header `trackiwi-position-count` carries the total record count and is
used for progress reporting on the initial sync.

The app **skips any row whose field count does not match** the expected 14. This
client mirrors that behaviour rather than failing the whole batch.

*Observed inconsistency:* the app's local SQLite schema also declares a `motion`
column, but the sync column list does not include it. The CSV column list above
is authoritative for parsing; `motion` is ignored.

### 3.4 Endpoints not used in v1

`GET /api/v2/trackers` **is** used. The following exist but are out of scope:
`/api/v2/tours`, `/api/v2/markers`, `/api/v2/marker_categories`,
`/api/v2/alarms`, `/api/v2/shares`, `PUT /api/v2/session/push_token`, and
`GET {website}/api/share/{id}/getlink`.

### 3.5 Server-driven command channel

Responses may carry a `trackiwi-app-command` header, which the official client
**executes**. This client must ignore that header entirely. Executing commands
delivered by a server we do not control is not acceptable in a tool that has
filesystem access.

### 3.6 Verified against the live API (2026-09-17)

Everything above this subsection is *static analysis* of the vendor's app
bundle. Everything in it was **observed in a real response** from a real
account on 2026-09-17, and therefore supersedes the inferences above where the
two disagree.

Scope note: this subsection records that the five read-only list endpoints of
§3.4 (`/api/v2/tours`, `/api/v2/markers`, `/api/v2/marker_categories`,
`/api/v2/alarms`, `/api/v2/shares`) are now **implemented, read-only**. That
supersedes their listing as out of scope in §3.4 and in §1's non-goals, which
have not been rewritten here because this subsection's edit was the only one
authorised. The write/mutation routes in §3.4 remain out of scope permanently
(see §7.5).

1. **`distance` is centimetres, and it is a per-fix delta — not an odometer.**
   The field divided by the haversine distance between consecutive fixes is
   100.00 across 12 consecutive samples (99.98 … 100.03). One sample read
   80.88, which says the value is reported by the *device* from its own
   consecutive readings rather than derived server-side from the stored
   coordinates — so it can legitimately disagree with a two-point calculation
   when a fix is dropped or GPS jitters. Do not treat it as a cumulative
   total, and do not expect it to reconcile exactly with the coordinates.
2. **`voltage` is centivolts.** A reading of `1303` is 13.03 V — a charging
   12 V system. Same hundredths convention as `distance`.
3. **`fix_timezone` is an INTEGER, not a string.** Observed value: `1`. It is
   never a zone name such as `"UTC"`. The parser already required this (the
   field goes through `int()`) and the cache schema in §5 already declares the
   column `INTEGER`, so nothing needed changing; it is now pinned by a test
   that walks a row parse → store → CSV export.
4. **`fix_at` has two different types on two endpoints.**
   - In the **sync CSV**: an epoch integer in **seconds** (observed
     `1766663018`). This resolves §10.5's unit question.
   - On **`GET /api/v2/trackers`**, inside `latest_positionlog`: an **ISO 8601
     string** (observed `'2026-09-17T19:19:59Z'`). `received_at` is likewise an
     ISO string.

   The client therefore has two converters, not one: `normalize_epoch` for the
   numeric form and `epoch_from_iso` for the ISO form. They are deliberately
   separate rather than one `int | str` function, because a digit string such
   as `"1766663018"` is ambiguous between the two and no caller actually needs
   the ambiguity — each one knows which endpoint it read.
5. **A sync page is 20,000 rows.** The account observed held 62,361 positions,
   so an initial sync is four pages.
6. **`GET /api/v2/session`** returns `{"token": ..., "user": {...}}`, where
   `user` holds only `id`, `created_at` and `updated_at` — **no email
   address**. §3.2's claim that `user` carries `is_tester` and
   `early_access_features[]` is the *login* response's shape, not this one.
7. **Tracker records carry `alarm_configuration`**, which includes a geofence
   alarm with `lat`/`long`/`radius`. **`GET /api/v2/trackers` output therefore
   contains location data, not merely device metadata** — a geofence centre is
   usually where the vehicle is kept. It must be treated with the care §7.1
   demands of the position cache, not as an inventory listing.
8. **Tracker records also carry** `installed_at`, `membership_valid`,
   `membership_ends_at` and `prunable_at`. The last reads as the vendor's own
   data-retention horizon for the account's history, which is an argument for
   keeping a local cache rather than relying on the server.

Also observed, and the reason the `alarms` command carries a privacy warning:
each alarm record's `event` object embeds `latitude`/`longitude`, so
`GET /api/v2/alarms` is itself a **location history** — and for a theft or
geofence alarm, precisely the locations worth protecting.

`markers` and `shares` both answered 200 with an **empty list** on the account
checked, so their element shape remains **unverified**. The client returns the
parsed list and promises no field for them.

## 4. Architecture

Four modules, no runtime dependencies. Python standard library only: `urllib`,
`json`, `csv`, `sqlite3`, `argparse`, `getpass`, `xml.etree`.

```
trackiwi/
  __init__.py
  client.py   auth, HTTP plumbing, config load/save, trackers, sync
  store.py    local SQLite position cache
  export.py   Position rows -> GPX / GeoJSON / CSV
  cli.py      argparse entry point
```

Boundaries:

- `client.py` is the only module that performs network I/O. It knows nothing
  about SQLite or output formats.
- `store.py` is the only module that touches the database. It takes and returns
  plain rows; it performs no network I/O.
- `export.py` is pure: rows in, bytes out. No I/O beyond writing the target file.
- `cli.py` wires the three together and owns all user interaction.

Deliberately absent, having been judged speculative: a dataclass layer
(`sqlite3.Row` is already mapping-like), a separate config module (~15 lines,
folded into `client.py`), a multi-class exception hierarchy, and retry/backoff
machinery (§8).

### 4.1 Data flow

```
sync:    API (CSV) -> parse -> SQLite cache
export:  SQLite (filtered by tracker + date range) -> GPX / GeoJSON / CSV
```

The API is contacted only by `login`, `logout`, `trackers` and `sync`. Every
query and export runs locally and offline.

## 5. Local cache

Incremental sync into SQLite at `~/.local/share/trackiwi/positions.db`
(`XDG_DATA_HOME` respected), mirroring how the vendor's own client behaves.

Rationale: a year of tracking is easily 100k+ points. Re-fetching the full
history for every export would be slow, offline-hostile, and needlessly
expensive for a small vendor's infrastructure.

Schema:

```sql
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
```

`id` is the server's own identifier, so re-syncing is idempotent
(`INSERT OR REPLACE`). The sync offset is `SELECT MAX(id)`, requiring no
separate bookkeeping table.

The database file is created with mode `0600` inside a `0700` directory.

## 6. CLI

```
trackiwi login [--email ADDR] [--token TOKEN --api-base URL]
trackiwi logout
trackiwi trackers
trackiwi sync [--full]
trackiwi export --format gpx|geojson|csv [--from DATE] [--to DATE]
               [--tracker ID] [-o FILE]
trackiwi purge
```

- `login` prompts for the password via `getpass`; it is never echoed, never
  logged, and never written to disk.
- `login --token/--api-base` stores a session obtained elsewhere, so the
  password need never be handled at all.
- `logout` calls `DELETE /api/v2/session` to revoke server-side, *then* deletes
  the local session. A local delete alone would leave a valid token in
  circulation.
- `sync` is incremental by default; `--full` restarts from `initial_sync`.
- `export` writes to stdout unless `-o` is given. Dates are `YYYY-MM-DD`,
  **both bounds inclusive**, interpreted as UTC calendar days. Omitting
  `--from`/`--to` means unbounded in that direction; omitting `--tracker` exports
  every tracker.
- `purge` deletes the local cache database.

## 7. Security

The tracker is fitted to the owner's vehicle. This raises the stakes above
those of a typical API client and drives several decisions.

### 7.1 The cache is the most sensitive artefact

The position history is a complete movement log of a physical vehicle: home
address, daily patterns, and — most sensitively — *when the vehicle is away from
home*. It is more sensitive than the token, because a token can be revoked and a
leaked history cannot.

Requirements:

- Cache lives under `~/.local/share/trackiwi/`, **never inside the repository**.
- Database file `0600`, containing directory `0700`.
- `trackiwi purge` must actually delete it.
- The README must state plainly that this file will be swept into Time Machine
  and any cloud backup, because that is not obvious.

### 7.2 No private data in the repository, ever

Test fixtures are **synthetic only**: invented coordinates in an uninhabited
area, invented tracker IDs. There is deliberately **no record-from-live mode**;
an earlier draft proposed one and it was withdrawn, because it would have
written real home coordinates into a repository intended to become public.
Git history is permanent, so this must hold from the first commit rather than
being cleaned up later.

Enforced by three independent layers (§9.2).

### 7.3 Token handling

The token grants **real-time location of the vehicle**, not merely historical
data.

For now it is stored in `~/.config/trackiwi/config.json`, mode `0600`
(`XDG_CONFIG_HOME` respected), alongside the non-secret API base and user id.
The password is never stored.

**Accepted risk, to be stated in the README:** a `0600` file is readable by any
process running as the user — every install script, every compromised
development tool — and is captured in file-level backups as plaintext. The
macOS Keychain would be materially better and remains the recommended upgrade;
it was consciously deferred, not overlooked.

Mitigations in scope now: never log the token; redact it in any debug output;
revoke server-side on `logout`.

### 7.4 Weaknesses observed in the product itself

Not this client's to fix, but worth recording for the eventual conversation
with the vendor:

1. **Token-in-URL auto-login.** `?token=...&apibase=...` places a bearer token
   in a URL, where it reaches browser history, server access logs and `Referer`
   headers. Anyone receiving such a link gains full account access including
   live vehicle location. trackiwi URLs containing a `token` parameter must
   never be shared or pasted.
2. **Token lifetime and revocation scope are unknown.** The presence of
   `DELETE /api/v2/session` implies revocation exists, but not its granularity.
3. **The `trackiwi-app-command` response header** lets the server direct client
   behaviour (§3.5). This client ignores it.

### 7.5 Read-only by construction

The client issues no call that can alter tracker configuration, disarm alarms,
or publish data. This is a safety property, not merely a scope decision: a bug
must not be able to disable theft protection on the vehicle.

`share create` was considered and **rejected**: per trackiwi's own FAQ, anyone
holding a share link can see the shared data, so a defect or mistaken default
could publish the vehicle's live position to a public URL, quite possibly
unnoticed. The benefit did not justify that failure mode.

### 7.6 Availability

A misbehaving client could get the account rate-limited or suspended, which
would mean losing tracking on the vehicle. Incremental sync and the absence of
automatic retry loops (§8) are partly safety measures, not only good manners.

## 8. Error handling

Two exception types: `TrackiwiError` (base, carrying `status`) and `AuthError`
(401/412), so callers can distinguish "log in again" from everything else.
Status-specific detail — 409 account action, 503 maintenance, 429 rate limit —
is surfaced as a clear message rather than as distinct classes, because no
caller needs to branch on them programmatically.

**No retry or backoff logic.** `sync` is offset-based and therefore inherently
resumable: re-running the command *is* the retry, and it resumes exactly where
it stopped. Adding a retry loop would duplicate that property in a way that is
harder to reason about and riskier for the vendor's servers. Failures print a
clear message and a non-zero exit code.

Malformed CSV rows are skipped with a counted warning, matching the vendor
client's behaviour; a single bad row must not discard a whole batch.

The `User-Agent` honestly identifies this client. The `App-Name` and
`App-Version` headers the API expects are sent, but the client does not
otherwise impersonate the official app.

## 9. Testing and quality gates

### 9.1 Tests

- Unit tests run **fully offline** against synthetic fixtures in
  `tests/fixtures/synthetic-*`.
- CSV parsing: well-formed rows, short rows, over-long rows, empty body,
  non-numeric fields.
- Golden-file tests for GPX and GeoJSON output.
- Store tests: idempotent re-insert, offset computation, date-range filtering.
- One live contract test marked `@pytest.mark.live`, skipped unless
  `TRACKIWI_LIVE=1`, so CI never needs credentials.

### 9.2 Pre-commit

The `pre-commit` framework as a **development-only** dependency; the installed
package keeps zero runtime dependencies.

`.pre-commit-config.yaml` (all revisions pinned):

- `ruff` — lint and format
- `pre-commit-hooks` — `check-added-large-files`, `check-merge-conflict`,
  `end-of-file-fixer`, `trailing-whitespace`
- `detect-private-key`
- `no-private-data` — a local hook, `tools/check_no_private_data.py`, failing
  the commit on: any `*.db` / `*.sqlite*`; any `*.gpx` / `*.geojson` / `*.csv`
  outside `tests/fixtures/synthetic-*`; `config.json`, `.env`, or any path under
  `.trackiwi/`; and token-shaped long hex/base64 strings in staged content.

The hook uses hard path and extension rules rather than coordinate-range
heuristics, which would fail open as soon as a fixture moved.

Three layers, because `--no-verify` exists:

1. `.gitignore` covering the same patterns, present in the first commit
2. the pre-commit hook
3. CI running `pre-commit run --all-files`, so a bypassed hook still fails the
   build

`tools/ci.sh` runs `pre-commit run --all-files` and `pytest`, mirroring CI
exactly.

## 10. Open questions

Resolved during implementation against live responses, not guessed. Items
marked **ANSWERED** were settled on 2026-09-17; see §3.6 for the evidence.
They are kept rather than deleted, so that a later reader can see what was
once unknown and on what basis it was closed.

1. The exact meaning of HTTP **409** on login. **Still open** — not reproduced
   on a healthy account, and deliberately not provoked.
2. Token lifetime, and whether `/api/login/refresh` is required in practice.
   **Still open.**
3. The JSON shape of `GET /api/v2/trackers`. **ANSWERED: a bare JSON list**,
   not a `{"data": [...]}` envelope — as for all five of the other read-only
   list endpoints. The client nevertheless still accepts the envelope as a
   fallback: this is one account on one day against an undocumented API with
   no deprecation policy (§2), so the branch stays. Record *contents* are in
   §3.6, items 7 and 8, and they include location data.
4. Whether `trackiwi-position-count` is returned on incremental syncs or only on
   the initial one. **Still open** — the header was present on the sync
   observed, but that does not distinguish the two cases. The client already
   treats it as optional.
5. The unit of `fix_at`. **ANSWERED: epoch seconds** in the sync CSV (observed
   `1766663018`) — and, separately, an **ISO 8601 string** inside
   `latest_positionlog` on `GET /api/v2/trackers` (observed
   `'2026-09-17T19:19:59Z'`), which the question did not anticipate. See §3.6
   item 4. `fix_timezone`'s **type** is also ANSWERED: an integer, observed
   `1`, never a string (§3.6 item 3). Its exact **semantics** remain open —
   `1` is consistent with hours (CET) and inconsistent with the minutes guess
   in the original question, but a single sample from a single zone cannot
   distinguish "hours" from "an index" or "something else". Nothing in the
   client depends on the interpretation: the value is stored and exported
   verbatim, and all timestamps are handled in UTC.
6. The units of `speed` (km/h or m/s), `altitude` (m) and `distance`.
   **`distance` is ANSWERED: centimetres, and a per-fix delta rather than an
   odometer** (§3.6 item 1). **`voltage` is ANSWERED: centivolts** (§3.6 item
   2), which was not part of the original question. `speed` and `altitude`
   remain **open**: no ground truth was available to calibrate them against,
   and the exporters do not depend on either (GPX carries `altitude` through
   as-is and omits `speed`).
7. Whether the sync `offset` parameter is **exclusive**. **Still open, and
   deliberately left so** — see CLAUDE.md, "Known unknown: offset semantics in
   `sync`". `Client.sync()` assumes exclusivity, which is inferred from the
   vendor's own web app rather than measured. A third-party claim that this
   was confirmed live arrived during implementation but could not be verified
   here (no live request was made, per the implementation brief), and it cited
   real position ids that must not enter this repository, so the question is
   recorded as open. The strict-monotonicity guard in `sync` stays.

## 11. Repository and CI

`~/src/trackiwi-client`, GitHub, **private initially**. The owner intends to
contact trackiwi before making it public.

Set up per the `github-project-setup` house style:

- `pyproject.toml`, `ruff`, `pytest`
- Staged CI with a top-level least-privilege `permissions: {contents: read}`
  block; a two-version matrix (floor and current Python); version-independent
  checks (`pre-commit run --all-files`) run **once** on the target version, not
  across the matrix
- CodeRabbit via a committed `.coderabbit.yaml`; the app installation at
  <https://github.com/apps/coderabbitai> is a manual browser step
- Branch protection on `main`: PR required, `required_approving_review_count: 0`
  (a solo owner cannot approve their own PR), `enforce_admins: true`,
  `required_conversation_resolution: true` so unresolved CodeRabbit findings
  block merge
- Dependabot, plus the `workflow_run`-triggered auto-merge workflow for
  minor/patch bumps only
- `CLAUDE.md` at the repository root recording the PR workflow, the gate
  command, and the no-private-data rule
- Badge row: CI and language now; the licence badge is added together with the
  licence itself, which is chosen when the repository is published rather than
  while it is private

Sphinx documentation and GitHub Pages are **deferred**: Pages on a private
repository requires a paid plan. Both are added when the repository is
published. Until then the README is the documentation.
