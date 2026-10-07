# How to log in, sync and export

Recipes for the everyday tasks. For a first run, start with [getting started](getting-started.md).
Every command is listed in the [CLI reference](reference/cli.md).

## Log in without typing the password

If you log in elsewhere and want to reuse the session, give the token and its API base
together (supplying only one is an error). Read the token from stdin, never from the
command line:

```bash
trackiwi login --token - --api-base <server>
pass show trackiwi/token | trackiwi login --token - --api-base <server>
```

On a terminal `--token -` prompts without echo; if stdin is a pipe or a file, the token is
read from it. Do not write `--token <token>`: the token would land in your shell history
and in `ps` output. The API base must be `https://`; anything else is rejected, because a
plain-HTTP base would send the bearer token in cleartext.

## Sync, and resume after an interruption

```bash
trackiwi sync          # fetch new positions
trackiwi sync --full   # start from the beginning instead of resuming
```

Sync is offset-based. Run it again after any interruption; there is no retry logic because
re-running is the retry. Everything fetched before a failure is already saved. Rows that
cannot be trusted (wrong field count, missing id, tracker, timestamp or coordinate,
non-finite coordinate, timestamp out of range) are skipped and counted on stderr, never
stored. The [sync sequence](architecture.rst) shows the exact flow.

If a sync fails with `trackiwi returned no new records past offset N`, the server did not
advance past the offset just requested; see the
[sync requirements](requirements.rst) and the
[`check-trackiwi-api` skill](https://github.com/ckeller42/trackiwi-client/blob/main/.claude/skills/check-trackiwi-api/SKILL.md)
for the live contract test.

## Export a route

```bash
trackiwi export --format gpx --from 2026-09-16 --to 2026-09-25 -o route.gpx
trackiwi export --format geojson --tracker 7 -o one-device.geojson
trackiwi export --format csv | head
```

- `--from` and `--to` are inclusive UTC days (`YYYY-MM-DD`); a `--to` before `--from`
  is an error.
- Without `--tracker`, one export covers every device and keeps them apart. See
  [export formats](reference/exports.md).
- `-o <path>` writes atomically and creates a new file mode `0600`. See
  [writing export files](reference/exports.md#writing-export-files).
- Piping into a reader that stops early (`| head`) exits `0` for `export` only.

On a machine that has never synced, `export` writes an empty result and does not create
the cache.

## Find out which way a vehicle points

```bash
trackiwi heading
trackiwi heading --tracker 7 --stale-after 900
```

`heading` reads the local cache only (run `sync` first) and prints one line per tracker:
id, degrees clockwise from true north, state (`moving`, `freshly_parked`, `stale` or
`unknown`), source (`bearing`, `course` or `none`), when it last moved (UTC) and for how many
seconds it has been parked. A parked heading is an estimate from the approach and assumes
the vehicle stopped nose-first. A vehicle that reversed into its spot points the other way
and nothing in the data can tell. See `trackiwi heading --help` for the full caveats.

## List the rest of the account

```bash
trackiwi tours
trackiwi alarms
trackiwi markers
trackiwi marker-categories
trackiwi shares
```

Each prints one tab-separated line per record, is read-only and is not cached.
`trackiwi alarms` and `trackiwi trackers` print no coordinates, but the API responses behind
them hold locations (alarm events, geofences). Treat them like the cache.

## Remove everything

```bash
trackiwi logout        # revokes server-side, removes the local token
trackiwi purge --yes   # deletes the cache and any -journal, -wal or -shm sidecar
```

`logout` always removes the local credentials. If it cannot revoke (no network, or a stored
`http://` base it refuses to send the token to), it says so on stderr and the token may stay
valid until you revoke it in the trackiwi app. `purge` never opens the database, so it works
on a corrupt file. A cache that is merely in use by another `trackiwi` is reported as locked
(wait and re-run), never as corrupt.
