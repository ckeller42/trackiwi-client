# Security

This is an **unofficial, unaffiliated** client for trackiwi. It is not endorsed
by or connected to the vendor, and it talks to a private API that can change
without notice.

## What is sensitive

The two things worth protecting are the **session token** and the **local
position cache** — both amount to the live location and movement history of a
real vehicle. The README's [Security](README.md#security) section explains where
they live, why they matter (the token grants live location, not just history),
and how to remove them (`trackiwi logout`, `trackiwi purge --yes`). That is not
duplicated here.

If you use the InfluxDB mirror (`trackiwi influx push` / `trackiwi ingest`), two
more things join that list:

- The **InfluxDB token** (or 1.x password). It is never stored in
  `influx.toml`; it comes from an environment variable (`TRACKIWI_INFLUX_TOKEN`
  by default, or the variable `token_env` names), from the file `token_file`
  names, or — for the systemd timer — from `~/.config/trackiwi/influx.env`
  (mode `0600`). Protect it like any other credential.
- The **positions copied into InfluxDB**. The mirror sends the cached movement
  history to the configured bucket, so that bucket (and any Grafana reading it)
  holds the same data as the local cache and deserves the same care. Purging
  the local cache does not remove what was already mirrored.

## Read-only by construction

The client issues no request that can change trackiwi account state, except
the `DELETE /api/v2/session` that `logout` uses to revoke its own session. It
cannot reconfigure a tracker, disarm an alarm, or publish a share link — by
design, so that a bug cannot do those things either.

The only other writes go to InfluxDB, and only as `trackiwi_position` line
protocol to the one configured target (`REQ_INFLUX_WRITE_SCOPE`): no deletes,
no bucket management, no other measurements.

## Reporting a concern

The repository is private for now. If you have access, open an issue. There is
no bug-bounty program and no response-time commitment — this is a personal tool
maintained on a best-effort basis.
