# InfluxDB ingest + Grafana dashboard — design

**Status:** approved design, pending spec review
**Date:** 2026-09-25
**Builds on:** `2026-09-17-trackiwi-client-design.md` (the client spec — its
requirements, security posture and module boundaries remain binding except
where this document explicitly amends them)

## 1. Purpose

Get trackiwi position and telemetry data into InfluxDB and show it in Grafana:

- **Ingest:** a one-time backfill of the full position history, then ongoing
  capture of new fixes.
- **Dashboard:** one Grafana dashboard covering both current status and history,
  checked into this repository as a reusable template.
- **Portability:** everything is generic and driven by configuration. The
  author's own deployment (a Raspberry Pi called `buspi`) is just one user of
  the setup. Nothing in the repository depends on it, and anyone with a trackiwi
  account can run the same thing against their own InfluxDB and Grafana.

### Success criteria

1. A fresh user can clone the repo, fill in `deploy/.env` from
   `deploy/.env.example`, run `docker compose up`, and see their own trackiwi
   data in a provisioned Grafana dashboard. No host, token, tracker id or
   location from the author's deployment appears anywhere in the repository.
2. A user who already runs InfluxDB (1.x or 2.x) can instead point the ingest at
   it with `~/.config/trackiwi/influx.toml` and a systemd timer, then import the
   dashboard JSON.
3. The ingest survives long connectivity gaps. Losing the network or InfluxDB
   for hours or days loses no data; the next successful run catches up.
4. Re-running the ingest, including a full backfill, never creates duplicate
   points.

### Non-goals

- Heading/orientation display on the map. Parked `course` is unreliable (see
  issue #3), and a rotated marker would imply precision the data doesn't have.
- InfluxDB 3.x as a supported target. It is SQL-only. It may be documented later
  as a variant, but it is not built or tested here.
- Alerting rules (Grafana alerts, notifications). The dashboard shows thresholds
  visually but defines no alert rules in v1.
- Storing tours, alarms or markers in InfluxDB. Positions only.
- Writing to anything except the configured InfluxDB target.

## 2. Context and constraints

- The trackiwi client is read-only against the trackiwi API and has **zero
  runtime dependencies**. Both properties are preserved. Everything new uses the
  standard library only (`urllib`, `tomllib`, `gzip`, `json`).
- The deployment host has an **unreliable network link**. During design, buspi
  showed as "active" in Tailscale while dropping 100% of packets, and it was
  unreachable over SSH on three consecutive attempts. The design treats long
  offline periods as normal, not exceptional.
- The InfluxDB version on buspi is not yet known (the host was unreachable). The
  design therefore targets **1.x and 2.x equally** and detects the version at
  run time.

## 3. Architecture

Two stages. The local SQLite cache acts as a durable buffer between trackiwi and
InfluxDB:

```
trackiwi API ──sync──▶ SQLite cache ──push──▶ InfluxDB ──▶ Grafana
  (existing,             (+ mirror_state)       (new)
   resumable)
```

`trackiwi ingest` runs `sync` and then `influx push`. The first run is the
backfill; every later run carries only what's new. Backfill and ongoing capture
use the same code path.

Approaches considered and rejected:

- **Direct (trackiwi → InfluxDB, no cache).** There would be no buffer, so a
  poll whose write fails loses data, and resuming would need a version-specific
  InfluxDB query. Unsuitable for a host that goes offline often.
- **Telegraf or an InfluxDB task.** Adds a dependency and host-specific
  configuration, which conflicts with both the zero-dependency rule and the
  portability goal.

### 3.1 Module boundaries

| Module | Responsibility | Boundary rule |
|---|---|---|
| `trackiwi/lineprotocol.py` (new) | Convert rows to InfluxDB line protocol: escaping, unit normalisation, precision | **Pure**: no I/O. Doctested |
| `trackiwi/influx.py` (new) | Load the target config, detect the version via `/ping`, write batches over HTTP | **The only module that talks to InfluxDB** |
| `trackiwi/store.py` | Add a `mirror_state` table | Still the only module that touches SQLite |
| `trackiwi/client.py` | Unchanged | Still the only module that talks to trackiwi |
| `trackiwi/cli.py` | New commands `influx check`, `influx push`, `ingest` | Command layer only |

`influx.py` and `client.py` never import each other. Shared exceptions stay in
`trackiwi/__init__.py`.

### 3.2 Amendment to the read-only requirement

`REQ_READONLY` currently says the client's only state-changing request is
`DELETE /api/v2/session`. This design amends it as follows:

- **`REQ_READONLY` (rescoped):** against the **trackiwi API**, the only
  state-changing request is `DELETE /api/v2/session`. The wording changes but
  the guarantee does not: nothing can reconfigure the tracker, disarm an alarm,
  or publish a share.
- **`REQ_INFLUX_WRITE_SCOPE` (new):** the only writes to InfluxDB are line
  protocol points for the `trackiwi_position` measurement, sent to the one
  configured target (a 1.x database or a 2.x bucket). No deletes, no schema or
  bucket management, and no writes to any other measurement.

## 4. Data model

Measurement: `trackiwi_position`. Timestamp: the fix time in epoch seconds,
written with `precision=s`.

**Tags**

| Tag | Source | Note |
|---|---|---|
| `tracker_id` | `tracker_id` | Stable |
| `tracker_name` | `trackers()` name | Renaming a tracker in the app starts a new series for this tag. Accepted |

**Fields** (normalised to natural units on write)

| Field | Source column | Conversion | Type |
|---|---|---|---|
| `lat` | `latitude` | — | float |
| `lon` | `longitude` | — | float |
| `altitude_m` | `altitude` | — | float |
| `speed_kmh` | `speed` | already km/h (verified) | float |
| `course_deg` | `course` | — | float |
| `distance_m` | `distance` | cm ÷ 100 (verified) | float |
| `voltage_v` | `voltage` | cV ÷ 100 (verified) | float |
| `battery_pct` | `battery` | — | integer |
| `satellites` | `sat` | — | integer |
| `gnss_quality` | `rssi` | renamed: a 0–5 GNSS signal-quality scale (verified), not a cellular RSSI | integer |
| `fix_flag` | `fix_timezone` | renamed: a 0/1 flag, **not** a timezone. Meaning unconfirmed; stored raw | integer |

A point is identified by measurement + tag set + timestamp. InfluxDB overwrites
a point that has the same identity, so **every write is idempotent** and
re-sending rows is always safe.

Rows with non-finite or missing coordinates never reach the cache (the existing
`REQ_MALFORMED_SKIP` handles this), so `lineprotocol.py` may assume valid
inputs. It still rejects any non-finite value with `ValueError` as a second
layer of defence.

## 5. Ingest behaviour

### 5.1 Mirror state

`store.py` gains a `mirror_state` table: `target TEXT PRIMARY KEY,
last_id INTEGER NOT NULL`. `target` is a stable key derived from the config (URL
+ database/bucket), so pushing the same cache to two targets tracks them
separately.

### 5.2 `influx push`

1. Read cache rows with `id > last_id` for the target, ordered by `id`.
2. Send them in batches of up to 5,000 lines. Each batch is one gzipped POST.
3. **Only after a batch returns 2xx**, set `last_id` to the highest id in that
   batch and commit.
4. Stop at the first failure. Rows from earlier batches stay mirrored; the
   failed batch and everything after it are retried on the next run.

**Invariant (`REQ_MIRROR_RESUME`):** `last_id` never moves past a row that
InfluxDB has not acknowledged. This is the same rule `sync` already follows for
the trackiwi side.

### 5.3 Version handling

- `version = "auto"` (the default) issues `GET /ping` and reads the
  `X-Influxdb-Version` header, which both 1.x and 2.x send.
- **1.x** writes go to `POST {url}/write?db={database}&precision=s`, with
  optional basic auth (`username` and a password from a file or the environment).
- **2.x** writes go to `POST {url}/api/v2/write?org={org}&bucket={bucket}&precision=s`
  with `Authorization: Token …`.
- An explicit `version = 1` or `version = 2` skips detection.

### 5.4 `influx check`

Reports connectivity, the detected version, whether authentication works, and,
on 2.x, **whether a DBRP mapping exists for the bucket**. If the mapping is
missing, it prints the exact `influx v1 dbrp create …` command to create it. The
command is read-only: it probes and reports, and never creates anything.

### 5.5 Error handling

| Condition | Behaviour | Exit code |
|---|---|---|
| InfluxDB unreachable or timing out | Clear message; cache retains data; next run resumes | 1 |
| 401/403 | "InfluxDB rejected the token or credentials" | 1 |
| 404 on write (unknown database or bucket) | Names the configured database or bucket | 1 |
| Other 4xx/5xx | Status plus the **redacted** response body | 1 |
| Config missing or invalid | Names the file and the missing key | 1 |
| `sync` fails inside `ingest` | `push` still runs for whatever is already cached, then the command exits 1 | 1 |

There is no retry loop. Re-running is the retry, exactly as with `sync`.

### 5.6 Secrets

- The InfluxDB token and password are never logged or printed. Output passes
  through the same redaction as the trackiwi token (`REQ_INFLUX_TOKEN_REDACT`,
  new).
- `influx.toml` is created or checked at mode `0600`, and `load` narrows a
  widened mode, mirroring the session config (`REQ_CONFIG_MODE_0600` extended to
  cover it).

## 6. Configuration

`~/.config/trackiwi/influx.toml` (honours `XDG_CONFIG_HOME`):

```toml
url = "http://localhost:8086"
version = "auto"          # "auto" | 1 | 2

# 2.x
org = "home"
bucket = "trackiwi"
token_file = "~/.config/trackiwi/influx.token"   # or TRACKIWI_INFLUX_TOKEN

# 1.x
# database = "trackiwi"
# username = "trackiwi"
# password_file = "~/.config/trackiwi/influx.password"
```

- Every key can be overridden by an environment variable named
  `TRACKIWI_INFLUX_<KEY>`. Precedence: environment > file > default. Docker
  configures the ingest entirely through the environment.
- The repository commits only `examples/influx.example.toml` and
  `deploy/.env.example`, both containing placeholders. `influx.toml`,
  `influx.token`, `influx.password` and `deploy/.env` are git-ignored and covered
  by the private-data guard.

**`REQ_PORTABLE_CONFIG` (new):** no file under version control contains a
hostname, token, credential, tracker id or location from any real deployment. A
test enforces this for the committed example and template files.

## 7. Deployment (`deploy/`)

### 7.1 Docker Compose (turnkey)

`deploy/docker-compose.yml` defines three services:

- **`influxdb`** (`influxdb:2`): initialised from `DOCKER_INFLUXDB_INIT_*`
  variables in `.env`, with an init script that creates the DBRP mapping for
  InfluxQL.
- **`grafana`**: provisioned from `deploy/grafana/provisioning/`. This includes
  one InfluxQL datasource, with its token substituted from the environment
  rather than committed, and one dashboard provider that loads the committed
  dashboard.
- **`ingest`**: a Python slim image that installs this repository and runs
  `trackiwi ingest` every `TRACKIWI_INGEST_INTERVAL` seconds (default 600). The
  trackiwi session is created with `trackiwi login --token -` from environment
  variables, or read from a mounted config volume. The cache lives on a named
  volume, so the buffer survives container restarts.

### 7.2 Systemd timer (bring your own InfluxDB)

`deploy/systemd/trackiwi-ingest.service` and `trackiwi-ingest.timer` are
**user** units. They use `%h` and `ExecStart=%h/.local/bin/trackiwi ingest`,
with no hosts or absolute user paths, and run every 10 minutes with
`Persistent=true` so a missed run catches up after the machine wakes. This is
the buspi route: its existing InfluxDB, `influx.toml`, and the timer.

## 8. Dashboard

File: `deploy/grafana/dashboards/trackiwi.json`. It is both provisioned by the
compose stack and importable into any Grafana.

**Portability rules (`REQ_DASHBOARD_PORTABLE`, new):**

- The datasource is referenced through an input/variable (`${DS_TRACKIWI}`).
  There are no hardcoded datasource UIDs.
- The tracker is selected with a `tracker` template variable
  (`SHOW TAG VALUES FROM trackiwi_position WITH KEY = "tracker_name"`).
- The file contains no real tracker ids, names, coordinates or hostnames.
- All queries use InfluxQL, which works on 1.x and on 2.x with a DBRP mapping.

**Layout: three rows.**

1. **Status:** last seen (age of the latest point, with a staleness threshold),
   voltage now, battery now, satellites now, GNSS quality now, and a geomap
   showing the latest position.
2. **Travel:** a geomap route layer for the selected time range, speed over
   time, and distance per day (sum of `distance_m`, shown in km).
3. **Health:** voltage over time (low-voltage threshold band), battery over
   time, satellites and GNSS quality over time.

The dashboard targets Grafana 10 and later (the geomap route layer). The minimum
version is stated in the README.

## 9. Testing and traceability

All existing gates stay green: tests, coverage ≥ 95, mypy strict, interrogate
100%, sphinx `-W` traceability, detect-secrets, and the private-data guard.

| Area | Tests |
|---|---|
| `lineprotocol.py` | Escaping of tag keys/values and field keys (spaces, commas, `=`, quotes); units (cm→m, cV→V); integer suffix `i`; float formatting; rejection of non-finite values; doctests |
| `influx.py` writer | Fake opener: exact 1.x vs 2.x URLs, query parameters and auth headers; gzip body round-trip; error-status mapping (§5.5); redaction of the token in error messages |
| Version detection | `/ping` header parsing for 1.x and 2.x; explicit version skips `/ping` |
| Mirror resume | Batch 2 of 3 fails → `last_id` equals the end of batch 1; the rerun sends only batches 2 and 3; a duplicate rerun is harmless |
| Config | File < environment precedence; `token_file` and `~` expansion; missing key names the key; mode `0600` self-heal |
| CLI | `influx check` output for OK, missing DBRP, and auth failure; `ingest` runs `push` after a failed `sync` and exits 1 |
| Dashboard JSON | Parses; three rows and the required panels present; no hardcoded datasource UID; no real values (uses the same shapes as the private-data guard) |
| Deploy files | Compose and provisioning YAML parse; `.env.example` has placeholders for every variable the compose file references; systemd units contain no absolute user paths |

New requirements, each traced to a verifying test in `docs/requirements.rst`:
`REQ_INFLUX_WRITE_SCOPE`, `REQ_MIRROR_RESUME`, `REQ_MIRROR_IDEMPOTENT`,
`REQ_INFLUX_TOKEN_REDACT`, `REQ_PORTABLE_CONFIG`, `REQ_DASHBOARD_PORTABLE`,
`REQ_LINEPROTOCOL_UNITS`. `REQ_READONLY` is rescoped (§3.2), and
`REQ_CONFIG_MODE_0600` is extended to cover `influx.toml`.

No test contacts a real InfluxDB or Grafana, and none runs Docker. An optional
end-to-end check against the compose stack is documented in the README and
never runs in CI.

## 10. Documentation

- **README:** new "InfluxDB & Grafana" section describing both routes (compose
  and systemd/bring-your-own), the DBRP note for 2.x, and the minimum Grafana
  version.
- **`CLAUDE.md`:** the new module boundaries (`influx.py` is the only module
  talking to InfluxDB), the rescoped read-only rule, the mirror-resume
  invariant, and the rule that deployment specifics stay out of the repository.
- **`llms.txt`:** entries for `lineprotocol.py`, `influx.py` and `deploy/`.

## 11. Open questions

1. **buspi's InfluxDB version and whether Grafana is already installed there.**
   The host was unreachable during design. `trackiwi influx check` answers the
   version on first deploy. If buspi already runs Grafana, it imports the
   dashboard JSON; if not, the compose file's `grafana` service can run on its
   own there.
2. **The meaning of `fix_flag`.** It is stored raw. If its meaning is ever
   confirmed, it can be renamed or given dashboard use without a migration
   (renaming a field only affects new points).
