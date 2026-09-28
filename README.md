# trackiwi-client

[![CI](https://github.com/ckeller42/trackiwi-client/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/trackiwi-client/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Read-only Python client for pulling positions out of a personal
[trackiwi](https://www.trackiwi.com) account: sync them into a local SQLite
cache, export them as GPX, GeoJSON or CSV.

Unofficial and unaffiliated. trackiwi publishes no API; this talks to the
private API its own app uses, which can change without notice.

## Install

`python3` on macOS is 3.9; this needs 3.11+. If you installed Python via
MacPorts, use its interpreter directly. The recommended path is a virtualenv:

```bash
/opt/local/bin/python3.13 -m venv .venv
.venv/bin/pip install .
```

There are zero runtime dependencies, so nothing is downloaded to resolve — the
client is standard library only.

### Run without installing

Because there is nothing to install, you can skip the step above and run the
tool straight from a clone — this is the low-friction path:

```bash
python3.13 -m trackiwi --help          # run from the clone directory
python3.13 -m trackiwi trackers
```

`python -m trackiwi` behaves exactly like the installed `trackiwi` command.

If you want an isolated `trackiwi` on your `PATH` without managing the venv
yourself, `pipx install .` works too.

## Use

```bash
trackiwi login                 # prompts for email and password
trackiwi login --email you@example.com   # prompts for the password only
trackiwi trackers               # list your devices
trackiwi sync                   # fetch new positions (resumable)
trackiwi sync --full            # restart from the beginning instead of resuming
trackiwi export --format gpx --from 2026-09-16 --to 2026-09-25 -o route.gpx
trackiwi heading                 # which way is each vehicle pointing? (see below)
trackiwi logout                  # revokes the session server-side
trackiwi purge --yes             # delete the local cache
```

### Listing the rest of your account

```bash
trackiwi tours                  # recorded tours
trackiwi alarms                 # alarms — see the warning below
trackiwi markers                # markers
trackiwi marker-categories      # marker categories
trackiwi shares                 # active share links
```

Each prints one tab-separated line per record, like `trackers` does, so the
output pipes into `cut`, `sort` or `awk`. A field the API does not send prints
as `-`. These are live reads and are **not** cached: the local database is for
positions only, and none of these commands creates or touches it.

**`trackiwi alarms` output is location history.** Every alarm record carries an
`event` object embedding latitude and longitude, so the alarm list says where
the vehicle was each time an alarm fired — which for a theft or geofence alarm
is precisely the locations you would want to protect. The command deliberately
prints only id, tracker, type, acknowledged and timestamp, so merely checking
what fired does not put coordinates into your scrollback; but the API response
itself holds them, so treat it like the cache. `trackiwi alarms --help` repeats
this.

**`trackiwi trackers` output also contains location data**, which the name does
not suggest: each tracker record carries an `alarm_configuration` with a
geofence `lat`/`long`/`radius` — usually where the vehicle is kept — and the
latest fix. The command itself prints only id and name.

`markers` and `shares` have an **unverified record shape**: both endpoints
answered with an empty list on the account this was checked against, so their
field names are an expectation rather than a confirmed contract. Their
`--help` says so.

All five are read-only, like the rest of the tool. `shares` lists share links;
it cannot create or revoke one.

Timestamps in these listings are printed exactly as the API sends them, which
for these endpoints is an ISO 8601 string — *not* the epoch integer the
position sync uses. Both forms occur in this API and mean the same kind of
thing; see the units note below.

### Which way is the vehicle pointing?

```bash
trackiwi heading                       # one line per tracker, from the cache
trackiwi heading --tracker 7 --stale-after 900
```

The feed has exactly one directional field, `course`, and it is GPS
course-over-ground: the direction of *travel*. While the vehicle moves it is
the heading. Once parked it drifts, and reading it at zero speed as "which way
the van points" is wrong most of the time. There is no compass in the data, so
a parked heading can only be *inferred* from the approach. `heading` does that
and says how much to trust it, printing tab-separated: tracker id, degrees
clockwise from true north, state, source, when it last moved (UTC) and for how
many seconds it has been parked.

- **`moving`**: the latest fix has `speed > 0`; the heading is its `course`.
- **`freshly_parked`**: stationary, and the last movement was within
  `--stale-after` seconds (default 3600). The heading is the direction of
  approach — the great-circle bearing between the last two moving fixes
  (`source` `bearing`), which beats the raw `course` at low speed. With only
  one moving fix, or two at the same spot, it falls back to that fix's
  `course` (`source` `course`).
- **`stale`**: the same estimate, but the last movement is older than
  `--stale-after`. Still printed; trust it less.
- **`unknown`**: no fix has ever shown movement. Nothing to estimate from.

**Caveats.** The parked heading assumes the vehicle stopped nose-first in its
direction of travel. A vehicle that reversed into its spot points the
*opposite* way and nothing in the data can tell you so. GPS alone cannot sense
a stationary vehicle's true heading; every value is an estimate. The command is
offline and read-only: it reads the local cache and never the API (run `sync`
first), never creates the cache, and prints no coordinates. The same logic is
available as `trackiwi.heading.estimate_heading()` for library use.

### Field units

Worth knowing before you do arithmetic on an export, because the field names
do not say it and the vendor documents nothing:

- **`distance` is centimetres**, and it is a **per-fix delta, not an
  odometer**. It is reported by the device from its own consecutive readings,
  so it can disagree slightly with the distance you would compute from two
  stored coordinates when a fix was dropped or GPS jittered.
- **`voltage` is centivolts** — `1303` means 13.03 V.
- **`fix_timezone` is an integer offset**, not a zone name. Its exact
  interpretation is unconfirmed; nothing in this tool depends on it, because
  every timestamp is handled and exported in UTC.
- **`speed` and `altitude` units are unconfirmed.** They are passed through
  unchanged rather than converted to something that might be wrong.

`--from`/`--to` are inclusive UTC calendar days (`YYYY-MM-DD`); giving a `--to`
before `--from` is rejected with an error rather than silently returning
nothing. `--format` is required and one of `gpx`, `geojson` or `csv`; add
`--tracker <id>` to limit an export to one device.

Without `--tracker`, an export covers every device and **keeps them apart**:
GPX gets one `<trk>` per tracker (named `tracker <id>`), GeoJSON one `Feature`
per tracker with its `tracker_id` in `properties`, and CSV has the column
anyway. Merging two devices into one track would produce a route that jumps
between them, which renders as a plausible-looking line and so cannot be
spotted afterwards.

`export` and `purge` never create the cache: on a machine that has never
synced, `export` writes an empty result and `purge` does nothing. `purge`
deletes the database without opening it, so it works even when the file is
corrupt, and it removes any `-journal`/`-wal`/`-shm` sidecar left behind by a
crashed write.

A cache that is merely **in use** by another `trackiwi` is reported as such —
`local cache is in use (...): database is locked` — and the advice is to wait
and re-run. That message never mentions `purge`, because nothing is wrong with
the data. Only a genuinely corrupt cache (`local cache is corrupt (...)`) tells
you to run `purge --yes`, which there is the only remedy.

Piping an export into a reader that stops early (`trackiwi export --format csv
| head`) exits `0`: the reader took what it wanted. Every other command treats
a dead output stream as the failure it is — `trackiwi sync 2>&1 | head -1`
exits non-zero, because a sync that stopped after its first page has not
finished. Re-run it; it resumes.

The API base must be `https://` — both `--api-base` and the server address the
login response hands back are rejected otherwise, since a plain-HTTP base
would send the bearer token in cleartext. The same check applies to the value
stored in `~/.config/trackiwi/config.json`, so a hand-edited or migrated
config with an `http://` base fails every authenticated command with
`the API base must start with https:// — ... run 'trackiwi login' again`.
`logout` is the exception: it cannot revoke over such a base (that would send
the token in cleartext), so it removes the local credentials and warns that
the token may still be live and has to be revoked in the app.

Rows that cannot be trusted are skipped and counted rather than stored: a row
with the wrong number of fields, a missing id/tracker/timestamp/coordinate, a
non-numeric or non-finite coordinate, or a timestamp outside the representable
range. `sync` reports the count on stderr.

`sync` is incremental and offset-based, so if it is interrupted, just run it
again — it resumes from the highest position already stored. There is no
retry logic precisely because re-running is the retry. Two things make it
stop loudly instead of quietly limping on: a page whose rows are all
unparseable, and a server offset that fails to advance past the one just
requested. Either way, everything fetched before the failure is already
saved, so re-running continues from where it stopped — nothing is lost or
re-fetched from scratch.

If you would rather not type your password, log in elsewhere and reuse the
session (both flags are required together — supplying only one is an error).
Pass `--token -` to read the token from stdin, which is the recommended form —
see [Security](#security) for why:

```bash
trackiwi login --token - --api-base <server>     # prompts, or reads a pipe
pass show trackiwi/token | trackiwi login --token - --api-base <server>
```

On a terminal `--token -` prompts without echoing; if stdin is a pipe or a
file, the token is read from it.

### Exit codes

- `0` — success
- `1` — error (network, API, bad arguments to the tool's own logic)
- `2` — authentication required (not logged in, or the session was rejected)

Caveat for anything scripting against this: `argparse` also exits `2` for its
own usage errors — an unknown flag, a missing required `--format`. So an exit
code of `2` alone does not prove an authentication failure; check stderr,
which distinguishes `authentication required: ...` from argparse's own usage
message.

### Writing export files

`export -o <path>` writes atomically (via a temp file plus rename), which has
two consequences worth knowing:

- Writing to a path that does not exist yet creates it mode `0600`, because
  an export contains a vehicle's movement history and defaults to the same
  owner-only protection as the cache. Re-exporting over an existing file
  **preserves whatever mode that file already had** — it does not reset it.
- The destination is symlink-followed: exporting onto a symlink updates the
  file it points to and leaves the symlink itself intact.
- A writable *directory* is required, not merely a writable file: the temp
  file is created next to the target. Exporting onto a writable file inside a
  read-only directory therefore fails with a clean error — and leaves the
  previous export untouched — where a plain `open(path, "w")` would have
  succeeded.

## InfluxDB & Grafana

`trackiwi ingest` syncs from trackiwi into the local cache, then mirrors every
position not yet sent into InfluxDB (measurement `trackiwi_position`). The cache
is the buffer: if InfluxDB or the network is down, positions wait in the cache
and the next run catches up. Re-sending is harmless (same point, same
timestamp), and the first run is the full backfill.

```bash
trackiwi influx check   # connectivity, version, auth — read-only
trackiwi influx push    # send cached positions not yet mirrored
trackiwi ingest         # sync + push; what the timer/container runs
```

### Option A — turnkey stack (Docker Compose)

1. Copy the template and replace every `changeme` value, then start InfluxDB
   alone so it runs its first-time setup (org, bucket, operator token):

   ```bash
   cp deploy/example.env deploy/.env
   docker compose -f deploy/docker-compose.yml up -d influxdb
   ```

2. Create the token the ingest container uses. It is **not** the operator
   token (`INFLUXDB_TOKEN`), which can read and delete every bucket; it is
   scoped to the trackiwi bucket: **write** for `push`, plus **read** so that
   `trackiwi influx check` can confirm the bucket exists — `check` asks
   `GET /api/v2/buckets`, which lists only the buckets the token may read, so
   with a write-only token it reports the bucket as not found and exits 1.
   The container's `influx` CLI was signed in as the operator by the setup
   step. `home` and `trackiwi` are the org and bucket names from
   `deploy/.env`. The helper is a shell function rather than a variable so
   the snippet works in zsh (the macOS default) as well as bash:

   ```bash
   stack_exec() { docker compose -f deploy/docker-compose.yml exec influxdb "$@"; }
   BUCKET_ID=$(stack_exec influx bucket list --name trackiwi --hide-headers | awk '{print $1}')
   stack_exec influx auth create --org home --description "trackiwi ingest" \
       --write-bucket "$BUCKET_ID" --read-bucket "$BUCKET_ID"
   ```

   The `Permissions` column of the output must show exactly one `read:` and
   one `write:` entry, both ending in the bucket id.

   Put the printed token into `deploy/.env` as `INFLUXDB_INGEST_TOKEN`. The
   ingest container never sees the operator token; only Grafana's provisioned
   datasource still uses it.

3. Give the ingest container a trackiwi session. It lives on the
   `trackiwi-state` volume, so this is done once. Either log in inside the
   container:

   ```bash
   docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi login
   ```

   or, if this machine is already logged in (`trackiwi trackers` works), copy
   that session into the volume instead of entering the password again:

   ```bash
   docker compose -f deploy/docker-compose.yml run --rm -T ingest \
       sh -c 'umask 077; mkdir -p ~/.config/trackiwi; cat > ~/.config/trackiwi/config.json' \
       < ~/.config/trackiwi/config.json
   ```

   Both sessions then share one token: `trackiwi logout` on either side revokes
   it for both.

4. Start the rest of the stack and verify the InfluxDB side once:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi influx check
   ```

   The first ingest run backfills the whole history; `docker compose -f
   deploy/docker-compose.yml logs ingest` ends with `pushed N positions to
   InfluxDB`. A Flux `count()` over the bucket may come out slightly below the
   cache's row count: the tracker occasionally reports two fixes with the same
   timestamp, and InfluxDB keeps one point per tracker and timestamp.

Grafana runs at <http://localhost:3000> with the **trackiwi** dashboard already
provisioned. The ingest container runs every `TRACKIWI_INGEST_INTERVAL` seconds.

#### Exposing the stack to the LAN

The published ports bind to `127.0.0.1`: InfluxDB (8086) and Grafana (3000)
answer only on the machine running the stack, and the ingest container reaches
InfluxDB over the compose network, not through a published port. To open
Grafana to other devices on your LAN, set its bind address in `deploy/.env`
and recreate the stack:

```bash
# deploy/.env — 0.0.0.0 is every interface; the address of one interface
# limits it to that network
GRAFANA_BIND_ADDRESS=0.0.0.0
```

```bash
docker compose -f deploy/docker-compose.yml up -d
```

`INFLUXDB_BIND_ADDRESS` does the same for InfluxDB, which is only needed when
something *outside* the stack writes to or queries it. Both services speak
plain HTTP: expose them on a network you trust, and put a TLS reverse proxy in
front for anything beyond that. Behind those ports is the vehicle's complete
movement history, guarded by nothing more than Grafana's admin password and
the InfluxDB tokens.

### Option B — your own InfluxDB (systemd timer)

1. Install with `pipx install .` (or `pip install --user .` where your system
   allows it), so the command lands in `~/.local/bin/trackiwi`, which is where
   the unit's `ExecStart` looks. If you installed it anywhere else (the `.venv`
   from [Install](#install), say), edit `ExecStart` to that path.
2. Copy `examples/influx.example.toml` to `~/.config/trackiwi/influx.toml` (mode 0600) and fill it in.
3. Give the timer the token. A systemd user unit does **not** see variables
   exported in your shell rc, so put the token in
   `~/.config/trackiwi/influx.env` (mode 0600), which the unit reads if it
   exists — or name a file with `token_file` in `influx.toml`. Never put the
   token in `influx.toml` itself.

   ```bash
   # ~/.config/trackiwi/influx.env — one VAR=value per line, no `export`
   TRACKIWI_INFLUX_TOKEN=changeme-influx-token
   # the variable token_env names, if you changed it; for 1.x:
   # TRACKIWI_INFLUX_PASSWORD=changeme-influx-password
   ```

4. Run `trackiwi ingest` once by hand while trackiwi is reachable: `influx push`
   only sends positions whose tracker name is already known (it never writes
   the tracker id as a stand-in name), and `ingest` is what stores the names.
   `trackiwi influx check` verifies the InfluxDB side.
5. Install the user timer from `deploy/systemd/` (instructions in the unit file).
6. Import `deploy/grafana-dashboards/trackiwi.json` into Grafana. Pick your InfluxDB datasource and set the **bucket** variable.

The mirror never follows an HTTP redirect: if `url` sits behind a proxy or login
gate that answers with a 3xx, `push` stops with an error naming the redirect
target, and you set `url` to the final address. Points older than the bucket's
retention are dropped by InfluxDB; `push` prints a warning and moves on.

The dashboard is **Flux**, so it requires InfluxDB 2.x or InfluxDB Cloud, and
Grafana 10 or later. The ingest itself also writes to InfluxDB 1.x (`version = 1`,
`database = …`), but 1.x users need their own dashboard.

The two map panels use the **OpenStreetMap** basemap (`osm-standard`), which needs no API
key. Grafana's default basemap (CARTO) now shows "API KEY REQUIRED" tiles instead of a map.

The **Route** panel downsamples rather than fetching every raw fix: it takes the
last `lat` and `lon` in each `v.windowPeriod` window (`aggregateWindow(every:
v.windowPeriod, fn: last)`) before pivoting them into points, so every plotted
point is a real fix, and the panel sets `maxDataPoints` to 20000 so the window
stays fine-grained over long time ranges. Without that, Grafana truncated the
raw series and cut long routes short. Zoomed out far enough, a route is drawn
from fewer fixes than the cache holds; narrow the time range to see them all.

## Security

> ⚠️ **The token is stored in plaintext — read this if the tracker is in a
> vehicle you use.** The session token lives at
> `~/.config/trackiwi/config.json` (mode `0600`, but readable by any process
> running as you and **swept into Time Machine and cloud backups**). It grants
> **live vehicle location, not just history**, so it is the single most
> sensitive artifact this tool touches. Your password is never stored. Remove
> the token with **`trackiwi logout`** (revokes the session server-side, then
> clears the local copy); delete the movement cache with **`trackiwi purge
> --yes`**. Moving the token into the macOS Keychain is the recommended
> upgrade — see below.

**The local cache is the most sensitive thing this tool creates.**
`~/.local/share/trackiwi/positions.db` is a complete movement history of a
vehicle: where it is kept, daily patterns, and when it is away. It is created
mode `0600` in a `0700` directory, but **it will be swept into Time Machine and
any cloud backup**. Delete it with `trackiwi purge --yes` when you no longer
need it.

**The token grants live location, not just history.** It is stored in
`~/.config/trackiwi/config.json` mode `0600`. That file is readable by any
process running as you, and is captured in backups as plaintext. Moving it to
the macOS Keychain would be a real improvement and is the recommended upgrade.
Your password is never stored.

**Never pass the token as a command-line argument.** `trackiwi login --token
<token> ...` writes the token verbatim into your shell history file
(`~/.zsh_history`, `~/.bash_history`) — plaintext, long-lived, and swept into
the same Time Machine and cloud backups warned about above — and makes it
visible in `argv` (`ps -ww`) to every process running as you for the lifetime
of the command. This is the same credential that grants live location. Use
`trackiwi login --token - --api-base <server>` instead: stdin touches neither
the history file nor `argv`, and on a terminal the prompt does not echo.

`logout` revokes the session server-side before deleting the local copy;
deleting a local copy of a still-valid token would be fake security. If revocation fails (network or server error), the local credentials are still removed and the token may remain valid until revoked in the app. The same applies when the stored API base is unusable (an `http://` value in a hand-edited config): `logout` still removes the credentials rather than leaving a token that cannot be deleted with the tool, and says on stderr that it must be revoked in the app.

**Never share a trackiwi URL containing a `token=` parameter.** Their app
accepts `?token=...&apibase=...` for auto-login, so such a link hands over full
account access, including live location.

This client is read-only. It cannot reconfigure your tracker, disarm alarms, or
publish a share link — by construction, so that a bug cannot do those things
either.

## Development

Install with the dev extras (an editable install, plus the test and lint
tooling), then run the gate:

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install   # commit hooks + pre-push (mypy, tests)
./tools/ci.sh     # the same gates CI runs: pre-commit, strict mypy, pytest with coverage, …
```

`.pre-commit-config.yaml` pins every lint and scan tool (ruff, gitleaks,
detect-secrets, markdownlint-cli2, actionlint) and CI runs exactly that, so
local and CI results agree.

No private data may enter this repository. A pre-commit hook blocks databases,
track exports outside `tests/fixtures/synthetic-*`, credential files
(`config.json`, `.env*`, `influx.toml`, `*.token`, `*.password` and their
bare-dotfile forms — the committed templates are `influx.example.toml` and
`example.env`) and token-shaped strings; `.gitignore` and CI enforce the same
rules independently.
Test fixtures are synthetic: invented coordinates, invented IDs.

### Requirements & traceability

Every project requirement is a first-class `sphinx-needs` object under `docs/`
(`docs/requirements.rst`), each traced to the test(s) that verify it
(`docs/traceability.rst`). The docs build **fails** if any requirement has no
verifying test, so it runs as a CI gate — only its GitHub Pages hosting is
deferred until the repo is public. Build it locally with:

```bash
.venv/bin/sphinx-build -b html -W docs docs/_build/html
```

`./tools/ci.sh` also runs this build, the pure-function doctests
(`pytest --doctest-modules trackiwi`), strict `mypy` over the package, tools
and tests, and the `interrogate` docstring-coverage gate, alongside the test
suite and coverage.

## License

MIT — see [LICENSE](LICENSE).
