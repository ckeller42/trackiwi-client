# CLI reference

`trackiwi <command>`, or `python3 -m trackiwi <command>` from a clone. `trackiwi <command>
--help` is authoritative for options. Source: `trackiwi/cli.py`
(`build_parser`).

## Commands

| Command | Reads | Writes | What it does |
|---|---|---|---|
| `login [--email E] [--token T --api-base URL]` | stdin, terminal | config file | Authenticate and store the session. `--token -` reads the token from stdin. `--token` and `--api-base` must be given together. |
| `logout` | config file | removes config file | Revoke the session on the server, then remove the local credentials. |
| `trackers` | API | nothing | List devices: id, name. Not cached. |
| `tours` | API | nothing | List recorded tours: id, name, tracker id, start, end. Not cached. |
| `alarms` | API | nothing | List alarms: id, tracker id, type, acknowledged, recorded at. Prints no coordinates. Not cached. |
| `markers` | API | nothing | List markers: id, name (field names unverified). Not cached. |
| `marker-categories` | API | nothing | List marker categories: id, name, colour. Not cached. |
| `shares` | API | nothing | List active share links: id, name (field names unverified). Cannot create or revoke one. Not cached. |
| `sync [--full]` | API | cache | Fetch new positions into the cache; `--full` restarts from the beginning. |
| `export --format F [--from D] [--to D] [--tracker N] [-o PATH]` | cache | stdout or file | Export cached positions. See [export formats](exports.md). |
| `heading [--tracker N] [--stale-after SECONDS]` | cache | nothing | Estimate each vehicle's heading from the cache. Offline. Default `--stale-after` 3600. |
| `influx check` | InfluxDB | nothing | Probe the configured InfluxDB target: connectivity, version, auth, bucket. |
| `influx push` | cache | InfluxDB, `mirror_state` | Send cached positions not yet mirrored. |
| `ingest` | API, cache | cache, InfluxDB | `sync`, refresh tracker names, then `influx push`. What the timer runs. |
| `purge --yes` | nothing | removes cache | Delete the position cache and its `-journal`, `-wal`, `-shm` sidecars without opening it. |

`export` and `purge` never create the cache. `export` takes `--format` (required, `gpx`,
`geojson` or `csv`). `--from` and `--to` are inclusive UTC days, `YYYY-MM-DD`.

The tab-separated listings print a field the API does not send as `-`. Timestamps in the
listings are printed as the API sends them (ISO 8601), not as epoch seconds.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Error: network, API, bad arguments to the tool's own logic, an interrupted run, or a dead output stream (except in `export`, where an early-closing reader is success). |
| `2` | Authentication required: not logged in, or the session was rejected. |

`argparse` also exits `2` for its own usage errors (an unknown flag, a missing `--format`), so
a `2` alone does not prove an authentication failure. Check stderr: it says
`authentication required: ...` for the real thing.

## Files and environment

| What | Default location | Notes |
|---|---|---|
| Session | `~/.config/trackiwi/config.json` | Mode `0600` in a `0700` directory. Honours `XDG_CONFIG_HOME`. Holds the API base, token and user id, never the password. |
| Position cache | `~/.local/share/trackiwi/positions.db` | SQLite, mode `0600` in a `0700` directory. Honours `XDG_DATA_HOME`. Holds `positions`, `mirror_state`, `tracker_names`. |
| InfluxDB target | `~/.config/trackiwi/influx.toml` | Mode `0600`. Template: `examples/influx.example.toml`. |
| `TRACKIWI_INFLUX_<KEY>` | environment | Overrides the same key in `influx.toml` (`url`, `version`, `org`, `bucket`, `database`, `username`, `token_env`, `token_file`, `password_file`). Precedence: environment, then file, then default. |
| `TRACKIWI_INFLUX_TOKEN` | environment | The InfluxDB 2.x token, unless `token_env` names another variable. A `token_file` is the alternative. `TRACKIWI_INFLUX_PASSWORD` or `password_file` for 1.x. |
| `TRACKIWI_INGEST_INTERVAL` | compose environment | Seconds between runs of the ingest container (default 600). |

The requirements behind these rules are in the [requirements](../requirements.rst); the
[units table](units.md) explains the position fields.
