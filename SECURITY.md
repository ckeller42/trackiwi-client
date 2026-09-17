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

## Read-only by construction

The client issues no request that can change account state, except the
`DELETE /api/v2/session` that `logout` uses to revoke its own session. It cannot
reconfigure a tracker, disarm an alarm, or publish a share link — by design, so
that a bug cannot do those things either.

## Reporting a concern

The repository is private for now. If you have access, open an issue. There is
no bug-bounty program and no response-time commitment — this is a personal tool
maintained on a best-effort basis.
