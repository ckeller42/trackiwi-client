# trackiwi-client

[![CI](https://github.com/ckeller42/trackiwi-client/actions/workflows/ci.yml/badge.svg)](https://github.com/ckeller42/trackiwi-client/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

Read-only Python client for pulling positions out of a personal
[trackiwi](https://www.trackiwi.com) account: sync them into a local SQLite
cache, export them as GPX, GeoJSON or CSV.

Unofficial and unaffiliated. trackiwi publishes no API; this talks to the
private API its own app uses, which can change without notice.

## Install

The system `python3` on macOS is 3.9; this needs 3.11+. If you installed
Python via MacPorts, use its interpreter directly:

```bash
/opt/local/bin/python3.13 -m venv .venv
.venv/bin/pip install -e .
```

No runtime dependencies are installed — the client is standard library only.

## Use

```bash
trackiwi login                 # prompts for email and password
trackiwi trackers               # list your devices
trackiwi sync                   # fetch new positions (resumable)
trackiwi export --format gpx --from 2026-09-16 --to 2026-09-25 -o route.gpx
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

## Security

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

```bash
./tools/ci.sh     # the same gates CI runs
```

No private data may enter this repository. A pre-commit hook blocks databases,
track exports outside `tests/fixtures/synthetic-*`, credential files and
token-shaped strings; `.gitignore` and CI enforce the same rules independently.
Test fixtures are synthetic: invented coordinates, invented IDs.
