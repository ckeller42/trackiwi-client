"""Command-line interface.

Wires the client, the store and the exporters together and owns all user
interaction. The password is read with `getpass`, never echoed, never logged
and never written to disk.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .client import AuthError, Client, TrackiwiError
from .export import FORMATS
from .store import Store, default_db_path


def _epoch(date_text: str, end_of_day: bool = False) -> int:
    """Parse YYYY-MM-DD as an inclusive UTC bound."""
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as error:
        raise TrackiwiError(f"invalid date {date_text!r}, expected YYYY-MM-DD") from error
    if end_of_day:
        day = day.replace(hour=23, minute=59, second=59)
    return int(day.timestamp())


def _write_atomically(path: str, text: str) -> None:
    """Write `text` to `path` without ever leaving a truncated file behind.

    Renders to a temporary file in the destination's own directory, then
    `os.replace()`s it over the target. `os.replace` is atomic on the same
    filesystem, so a failure never clobbers a good previous export with a
    partial one. Any `OSError` (bad path, permissions, a directory where a
    file was expected, ...) becomes a `TrackiwiError` so `main()` reports it
    cleanly instead of leaking a traceback.
    """
    target = Path(path)
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, target)
    except OSError as error:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise TrackiwiError(f"could not write {path}: {error}") from error


def cmd_login(args: argparse.Namespace) -> int:
    if bool(args.token) != bool(args.api_base):
        raise TrackiwiError("--token and --api-base must be given together")
    if args.token and args.api_base:
        client = Client(api_base=args.api_base, token=args.token)
        client.save()
        print("Session stored. Run 'trackiwi trackers' to verify it.")
        return 0
    email = args.email or input("trackiwi email: ").strip()
    password = getpass.getpass("trackiwi password: ")
    user = Client().login(email, password)
    print(f"Logged in as user {user.get('id')}.")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    Client.load().logout()
    print("Session revoked and local credentials removed.")
    return 0


def cmd_trackers(_: argparse.Namespace) -> int:
    for tracker in Client.load().trackers():
        print(f"{tracker.get('id')}\t{tracker.get('name', '(unnamed)')}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    client = Client.load()
    if not client.authenticated:
        # Checked before Store() is ever opened: otherwise an unauthenticated
        # run leaves behind an empty cache directory and database file.
        raise AuthError("not logged in — run 'trackiwi login'")
    with Store() as store:
        offset = None if args.full else store.max_id()
        written = skipped_total = 0
        try:
            for rows, skipped, total in client.sync(offset=offset):
                written += store.upsert(rows)
                skipped_total += skipped
                suffix = f" of {total}" if total else ""
                print(f"\rsynced {written}{suffix} positions", end="", file=sys.stderr)
        finally:
            # Always close the \r-progress line, success or failure, so a
            # later error message never gets appended to a partial line.
            print(file=sys.stderr)
        if skipped_total:
            print(f"skipped {skipped_total} malformed rows", file=sys.stderr)
        print(f"{written} new positions, {store.count()} cached in total")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    start = _epoch(args.start) if args.start else None
    end = _epoch(args.end, end_of_day=True) if args.end else None
    if start is not None and end is not None and start > end:
        raise TrackiwiError("--from date is after --to date")
    with Store() as store:
        rows = store.query(tracker_id=args.tracker, start=start, end=end)
    text = FORMATS[args.format](rows)
    if args.output:
        _write_atomically(args.output, text)
        print(f"wrote {len(rows)} positions to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    if not args.yes:
        raise TrackiwiError(f"this deletes {default_db_path()} — pass --yes to confirm")
    with Store() as store:
        store.purge()
    print("Local position cache deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trackiwi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="authenticate and store the session")
    login.add_argument("--email")
    login.add_argument("--token", help="use an existing token instead of a password")
    login.add_argument("--api-base", help="API base that goes with --token")
    login.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="revoke the session").set_defaults(func=cmd_logout)
    sub.add_parser("trackers", help="list trackers").set_defaults(func=cmd_trackers)

    sync = sub.add_parser("sync", help="fetch new positions into the local cache")
    sync.add_argument("--full", action="store_true", help="restart from the beginning")
    sync.set_defaults(func=cmd_sync)

    export = sub.add_parser("export", help="export cached positions")
    export.add_argument("--format", choices=sorted(FORMATS), required=True)
    export.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    export.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    export.add_argument("--tracker", type=int, help="limit to one tracker id")
    export.add_argument("-o", "--output", help="write to a file instead of stdout")
    export.set_defaults(func=cmd_export)

    purge = sub.add_parser("purge", help="delete the local position cache")
    purge.add_argument("--yes", action="store_true", help="confirm deletion")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthError as error:
        print(f"authentication required: {error}", file=sys.stderr)
        print("Run 'trackiwi login'.", file=sys.stderr)
        return 2
    except TrackiwiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
