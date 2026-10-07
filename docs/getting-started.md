# Getting started

A first run, start to finish: install the client, log in, pull your positions into a
local cache and write a GPX track. It takes a few minutes. Afterwards, the
[how-to guides](howto-sync-and-export.md) cover the everyday tasks and the
[reference](reference/cli.md) lists every command.

```{note}
This is an unofficial client. trackiwi publishes no API; the tool uses the private one
its own app uses, which can change without notice. It is read-only: the only request
that changes anything on the trackiwi side is the logout that revokes your session.
```

## 1. Install

You need Python 3.11 or newer and nothing else: the package has no runtime
dependencies. Check with `python3 --version`. If your system Python is older (older macOS,
for one), install a newer one and call it explicitly, for example `python3.13`.

```bash
pipx install git+https://github.com/ckeller42/trackiwi-client
trackiwi --help
```

There is nothing to build, so you can also run straight from a clone with
`python3 -m trackiwi --help`. It behaves exactly like the installed `trackiwi` command.

## 2. Log in

```bash
trackiwi login
```

It asks for your trackiwi email and password. The password is used once and never stored.
What is stored is the session token, in `~/.config/trackiwi/config.json` with mode
`0600`. That token grants live vehicle location, so read
[the security notes](https://github.com/ckeller42/trackiwi-client#security) once.

Check that it works:

```bash
trackiwi trackers
```

You get one tab-separated line per device: id and name.

## 3. Sync

```bash
trackiwi sync
```

The first run fetches your whole history into `~/.local/share/trackiwi/positions.db`
(mode `0600`). Every later run fetches only what is new. If a sync is interrupted, run it
again: it resumes from the highest position already stored.

## 4. Export

```bash
trackiwi export --format gpx --from 2026-09-16 --to 2026-09-25 -o route.gpx
```

Open `route.gpx` in any GPX viewer. `--format` is `gpx`, `geojson` or `csv`; the dates
are inclusive UTC calendar days. With several devices, each keeps its own track.

## 5. Clean up when you are done

```bash
trackiwi logout        # revokes the session on the server, then deletes the local token
trackiwi purge --yes   # deletes the position cache
```

## Where next

- Everyday tasks: [sync and export](howto-sync-and-export.md), and
  [ingest into InfluxDB and Grafana](howto-ingest.md).
- Look something up: [CLI](reference/cli.md), [units](reference/units.md),
  [export formats](reference/exports.md).
- Why it is built this way: [architecture](architecture.md).
