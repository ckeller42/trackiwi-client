# How to ingest into InfluxDB and Grafana on your own host

`trackiwi ingest` syncs from trackiwi into the local cache, then mirrors every position not
yet sent into InfluxDB (measurement `trackiwi_position`). You run it on a timer on any host
you own. This page uses placeholders only: hostnames, tokens and paths are yours to fill in.
How a specific machine is deployed lives in the repository that deploys that machine, never
in this one.

Before you start, [install and log in](getting-started.md) on the host that will run the
timer. The way the pieces fit is on the [architecture page](architecture.rst).

## What ingest does

```bash
trackiwi influx check   # connectivity, version, auth. Read-only.
trackiwi influx push    # send cached positions not yet mirrored
trackiwi ingest         # sync, refresh tracker names, then push: what a timer runs
```

The cache is the buffer. If InfluxDB or the network is down, positions wait in the cache and
the next run catches up. Re-sending is harmless (the same point with the same timestamp
overwrites), and the first run is the full backfill. The three steps fail independently: the
push runs even if the sync failed, and the first trackiwi-side error decides the exit code.

## Option A: the turnkey stack (Docker Compose)

`deploy/docker-compose.yml` starts InfluxDB 2, Grafana with the trackiwi dashboard provisioned,
and an ingest container that runs `trackiwi ingest` every `TRACKIWI_INGEST_INTERVAL` seconds.

1. Copy the template, replace every `changeme` value, start InfluxDB alone for its first-time
   setup:

   ```bash
   cp deploy/example.env deploy/.env
   docker compose -f deploy/docker-compose.yml up -d influxdb
   ```

2. Create the ingest token. It is not the operator token: it is scoped to the trackiwi bucket
   with one `read` and one `write` entry (read so that `influx check` can see the bucket). The
   exact `influx auth create` call is in the
   [README, Option A step 2](https://github.com/ckeller42/trackiwi-client#option-a--turnkey-stack-docker-compose).
   Put the printed token into `deploy/.env` as `INFLUXDB_INGEST_TOKEN`.
3. Give the ingest container a trackiwi session (once, it lives on the `trackiwi-state` volume):

   ```bash
   docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi login
   ```

4. Start everything and verify once:

   ```bash
   docker compose -f deploy/docker-compose.yml up -d
   docker compose -f deploy/docker-compose.yml run --rm ingest trackiwi influx check
   ```

   The log of the ingest service ends with `pushed N positions to InfluxDB`. Grafana is at
   `http://localhost:3000`.

Published ports bind to `127.0.0.1` unless you set `GRAFANA_BIND_ADDRESS` or
`INFLUXDB_BIND_ADDRESS` in `deploy/.env`. Both services speak plain HTTP, so open them only on
a network you trust. Behind them is the vehicle's complete movement history.

## Option B: your own InfluxDB and a systemd timer

1. Install so the command lands in `~/.local/bin/trackiwi`, where the unit's `ExecStart` looks:
   `pipx install .` (edit `ExecStart` if you installed elsewhere).
2. Copy `examples/influx.example.toml` to `~/.config/trackiwi/influx.toml` (mode `0600`) and
   fill in `url`, `org` and `bucket`. Every key can be overridden by `TRACKIWI_INFLUX_<KEY>`
   in the environment. For InfluxDB 1.x set `version = 1` and `database`.
3. Give the timer the token. A systemd user unit does not see variables from your shell rc: put
   it in `~/.config/trackiwi/influx.env` (mode `0600`) as `TRACKIWI_INFLUX_TOKEN=<token>`, or
   name a file with `token_file` in `influx.toml`. Never put the token in `influx.toml` itself.
4. Run `trackiwi ingest` once by hand while trackiwi is reachable. `influx push` only sends
   positions whose tracker name is already known, and `ingest` is what stores the names.
   `trackiwi influx check` verifies the InfluxDB side.
5. Install the user timer (it runs every 10 minutes):

   ```bash
   cp deploy/systemd/trackiwi-ingest.* ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now trackiwi-ingest.timer
   ```

6. Import `deploy/grafana-dashboards/trackiwi.json` into Grafana, pick your InfluxDB
   datasource and set the `bucket` variable.

Check a run with `systemctl --user start trackiwi-ingest.service` and
`journalctl --user -u trackiwi-ingest -n 5`. A healthy run ends with
`pushed N positions to InfluxDB`.

## Things worth knowing

- The mirror never follows an HTTP redirect. If `url` sits behind a proxy or a login gate
  that answers 3xx, `push` stops with an error naming the target: set `url` to the final address.
- Points older than the bucket's retention are dropped by InfluxDB. `push` prints a warning and
  moves on.
- The dashboard is Flux, so it needs InfluxDB 2.x or Cloud and Grafana 10 or later. Its map
  panels use the OpenStreetMap basemap, which needs no API key.
- A `count()` over the bucket can come out slightly below the cache's row count: the tracker
  sometimes reports two fixes with the same timestamp, and InfluxDB keeps one point per tracker
  and timestamp.
- To roll out a new release on a host, install the new wheel over the old one and run one
  `trackiwi ingest` by hand. The cache schema is stable across releases.
