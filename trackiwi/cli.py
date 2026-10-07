"""Command-line interface.

Wires the client, the store and the exporters together and owns all user
interaction. The password is read with `getpass`, never echoed, never logged
and never written to disk.
"""

from __future__ import annotations

import argparse
import contextlib
import getpass
import os
import stat
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .client import AuthError, Client, TrackiwiError
from .export import FORMATS
from .heading import DEFAULT_STALE_AFTER, Heading, estimate_headings
from .influx import InfluxWriter, load_config, mirror
from .store import Store, cache_files, default_db_path


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

    Implements :need:`REQ_EXPORT_ATOMIC`.

    Renders to a temporary file in the destination's own directory, then
    `os.replace()`s it over the target. `os.replace` is atomic on the same
    filesystem, so a failure never clobbers a good previous export with a
    partial one.

    If the destination is a symlink, the symlink is followed and its target
    is updated in place, leaving the symlink intact. If the destination
    exists, its mode is preserved on the replacement file. New files default
    to 0600 for security (consistent with the cache database).

    Any `OSError` (bad path, permissions, a directory where a file was
    expected, ...) becomes a `TrackiwiError` so `main()` reports it cleanly
    instead of leaking a traceback.
    """
    # Resolve the destination, following symlinks to their real path.
    real_target = Path(os.path.realpath(path))
    tmp_path: Path | None = None
    try:
        # Create temp file in the same directory as the real target.
        fd, tmp_name = tempfile.mkstemp(
            dir=real_target.parent, prefix=f".{real_target.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)

        # If the destination exists, preserve its mode.
        # New files stay at 0600 (mkstemp default); updated files keep their chosen mode.
        if real_target.exists():
            mode = stat.S_IMODE(os.stat(real_target).st_mode)
            os.chmod(tmp_path, mode)

        os.replace(tmp_path, real_target)
    except OSError as error:
        with contextlib.suppress(OSError):
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        raise TrackiwiError(f"could not write {path}: {error}") from error


def _silence_stderr() -> None:
    """Point fd 2 at /dev/null so the interpreter's exit flush stays quiet.

    Without this, a write to a closed pipe makes CPython print "Exception
    ignored in: <_io.TextIOWrapper name='<stdout>'>" while flushing on exit,
    after the command has already decided what to do about it (the idiom from
    the Python docs' note on SIGPIPE). Under `capsys` the `fileno()` call
    raises `io.UnsupportedOperation`, which is both an `OSError` and a
    `ValueError` and is deliberately swallowed here.
    """
    with contextlib.suppress(OSError, ValueError):
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stderr.fileno())


def _read_token() -> str:
    """Read a token for `--token -`, without it ever reaching argv.

    A token given as `--token <value>` is written verbatim into the shell
    history file and is visible in `ps -ww` to every process running as the
    same user, for the lifetime of the command. Reading it from stdin touches
    neither; on a terminal it is prompted for without echo.
    """
    token = getpass.getpass("trackiwi token: ") if sys.stdin.isatty() else sys.stdin.read()
    token = token.strip()
    if not token:
        raise TrackiwiError("no token supplied on stdin")
    return token


def cmd_login(args: argparse.Namespace) -> int:
    """Authenticate (or store a supplied token) and save the session."""
    if bool(args.token) != bool(args.api_base):
        raise TrackiwiError("--token and --api-base must be given together")
    if args.token and args.api_base:
        token = _read_token() if args.token == "-" else args.token
        client = Client(api_base=args.api_base, token=token)
        client.save()
        print("Session stored. Run 'trackiwi trackers' to verify it.")
        return 0
    try:
        email = args.email or input("trackiwi email: ").strip()
    except EOFError as error:
        raise TrackiwiError("no email given and stdin is closed — pass --email") from error
    password = getpass.getpass("trackiwi password: ")
    user = Client().login(email, password)
    print(f"Logged in as user {user.get('id')}.")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    """Revoke the session and remove local credentials.

    Implements :need:`REQ_LOGOUT_REVOKES`: revokes server-side when it can and,
    when it cannot, still removes the local credentials and warns that the
    token may remain valid. Implements :need:`REQ_TOKEN_REMOVABLE`: a config
    file that cannot be loaded is deleted all the same.
    """
    try:
        client = Client.load()
    except TrackiwiError as error:
        # Whatever makes the config unloadable (a non-https `api_base`,
        # truncated JSON, a wrong type) must not keep the token on disk: it
        # grants live vehicle location and `logout` is the only way to remove
        # it (spec section 7.3). Revoking over a non-https base is not an
        # option — sending the token in cleartext is the very thing
        # `_require_https` prevents — so the local credential goes, loudly.
        Client.forget_local_session()
        print(
            f"Could not revoke the session: {error}\n"
            "Local credentials have been removed, but the token may still be valid.\n"
            "Revoke it in the trackiwi app.",
            file=sys.stderr,
        )
        return 0
    revoked = client.logout()
    if revoked:
        print("Session revoked and local credentials removed.")
    else:
        print(
            "Could not revoke the session (network or server error).\n"
            "Local credentials have been removed, but the token may still be valid.\n"
            "Revoke it in the trackiwi app.",
            file=sys.stderr,
        )
    return 0


def _cell(value: object) -> str:
    """Render one field for a tab-separated listing.

    A missing field prints as `-` rather than `None`, and a tab or newline
    inside a server-supplied string is replaced with a space: a tour named
    with an embedded tab would otherwise shift every following column, which
    silently corrupts `cut -f`-style downstream use.
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    text = str(value)
    for char in ("\t", "\r", "\n"):
        text = text.replace(char, " ")
    return text


def _print_rows(records: list[Any], fields: tuple[str, ...]) -> None:
    """Print one tab-separated line per record, in `fields` order.

    `.get` throughout, deliberately: this is an undocumented API with no
    deprecation policy (design spec, section 2), and two of the five endpoints
    have an element shape that was never observed at all, so a renamed or
    absent field must degrade to `-` rather than end the listing in a
    `KeyError` traceback.
    """
    for record in records:
        if not isinstance(record, dict):
            # The endpoint promised a list of records; an element that is not
            # one still prints rather than aborting the whole listing.
            print(_cell(record))
            continue
        print("\t".join(_cell(record.get(field)) for field in fields))


def _read_command(method: str, fields: tuple[str, ...]) -> Callable[[argparse.Namespace], int]:
    """Build a `cmd_*` for one read-only list endpoint.

    `Client.load` is a **classmethod returning a Client**, so the call below is
    `Client.load().<method>()` — not an instance method on an already-built
    client.
    """

    def command(_: argparse.Namespace) -> int:
        _print_rows(getattr(Client.load(), method)(), fields)
        return 0

    return command


#: `(subcommand, Client method, printed fields, help text)`.
#:
#: None of these is cached in SQLite. They are small live reads and the cache
#: exists for positions only; adding tables for them would widen the most
#: sensitive artefact this tool creates (design spec, section 7.1) for no gain.
READ_COMMANDS = (
    ("trackers", "trackers", ("id", "name"), "list trackers"),
    (
        "tours",
        "tours",
        ("id", "name", "tracker_id", "started_at", "ended_at"),
        "list recorded tours",
    ),
    (
        "alarms",
        "alarms",
        ("id", "tracker_id", "alarm_type", "acknowledged", "inserted_at"),
        "list alarms — WARNING: this is location history, see --help",
    ),
    (
        "markers",
        "markers",
        ("id", "name"),
        "list markers (field names unverified — see --help)",
    ),
    (
        "marker-categories",
        "marker_categories",
        ("id", "name", "color"),
        "list marker categories",
    ),
    (
        "shares",
        "shares",
        ("id", "name"),
        "list active shares (field names unverified — see --help)",
    ),
)

#: Longer `--help` text, where a warning has room to be read.
READ_DESCRIPTIONS = {
    "trackers": (
        "List the account's trackers, one per line: id and name.\n\n"
        "PRIVACY WARNING: a tracker record holds location data: the alarm "
        "geofence (latitude, longitude, radius, usually where the vehicle is "
        "kept) and the latest fix. This command prints only id and name, but "
        "the underlying API response holds the rest, so treat it with the same "
        "care as the position cache."
    ),
    "alarms": (
        "List the account's alarms, one per line: id, tracker, type, "
        "whether it was acknowledged, and when it was recorded.\n\n"
        "PRIVACY WARNING: an alarm list is itself a location history. Every "
        "alarm record carries an 'event' object embedding latitude and "
        "longitude, so this endpoint says where the vehicle was each time an "
        "alarm fired — which, for a theft or geofence alarm, is precisely the "
        "locations worth protecting. This command prints the fields above and "
        "deliberately does not print those coordinates, but the underlying "
        "records hold them, so treat the API response with the same care as "
        "the position cache.\n\n"
        "Read-only: nothing here acknowledges, clears or tests an alarm."
    ),
    "markers": (
        "List the account's markers, one per line.\n\n"
        "The element shape of this endpoint is UNVERIFIED: it answered with an "
        "empty list on the account it was checked against, so 'id' and 'name' "
        "are expectations rather than a confirmed contract. A field that does "
        "not exist prints as '-'."
    ),
    "shares": (
        "List the account's active shares, one per line.\n\n"
        "The element shape of this endpoint is UNVERIFIED: it answered with an "
        "empty list on the account it was checked against, so 'id' and 'name' "
        "are expectations rather than a confirmed contract. A field that does "
        "not exist prints as '-'.\n\n"
        "Read-only: this lists shares, it cannot create or revoke one. Anyone "
        "holding a share link can see the vehicle's position."
    ),
    "tours": (
        "List recorded tours, one per line: id, name, tracker, start and end.\n\n"
        "'started_at' and 'ended_at' are printed exactly as the API sends "
        "them, which for this endpoint is an ISO 8601 string, not the epoch "
        "integer the sync CSV uses."
    ),
}


HEADING_DESCRIPTION = (
    "Estimate which way each tracked vehicle is pointing, from the cached "
    "positions. One tab-separated line per tracker: id, heading in degrees "
    "clockwise from true north, state, source, when it last moved (UTC) and "
    "for how many seconds it has been parked.\n\n"
    "STATE is the confidence flag. 'moving': the latest fix has speed > 0 and "
    "the heading is its GPS course. 'freshly_parked': the vehicle is "
    "stationary and last moved within --stale-after seconds; the heading is "
    "the direction it was travelling as it came to rest. 'stale': the same "
    "estimate, but the last movement is older than --stale-after. 'unknown': "
    "no fix has ever shown movement, so there is nothing to estimate from.\n\n"
    "SOURCE is 'bearing' when the parked heading is the great-circle bearing "
    "between the last two moving positions (preferred: the raw course field "
    "is noisy at low speed), 'course' when only the raw field was available "
    "(a single moving fix, or two at the same spot), 'none' with no heading.\n\n"
    "CAVEATS: the parked heading assumes the vehicle stopped nose-first in "
    "its direction of travel. A vehicle that reversed into its spot points "
    "the OPPOSITE way, and nothing in the data can reveal that. GPS alone "
    "cannot sense a stationary vehicle's true heading; every value printed "
    "here is an estimate.\n\n"
    "Read-only and offline: reads the local cache only, never the API. Run "
    "'trackiwi sync' first to have current positions."
)


def _sync_into_cache(client: Client, full: bool) -> None:
    """Fetch new positions into the local cache, reporting progress."""
    if not client.authenticated:
        # Checked before Store() is ever opened: otherwise an unauthenticated
        # run leaves behind an empty cache directory and database file.
        raise AuthError("not logged in — run 'trackiwi login'")
    with Store() as store:
        offset = None if full else store.max_id()
        # `fetched` drives the progress line, because that is what the server's
        # total is comparable with; `written` counts rows that were actually
        # new, which is what gets reported at the end.
        fetched = written = skipped_total = 0
        try:
            for rows, skipped, total in client.sync(offset=offset):
                written += store.upsert(rows)
                fetched += len(rows)
                skipped_total += skipped
                suffix = f" of {total}" if total else ""
                print(f"\rsynced {fetched}{suffix} positions", end="", file=sys.stderr)
        finally:
            # Always close the \r-progress line, success or failure, so a
            # later error message never gets appended to a partial line.
            print(file=sys.stderr)
        if skipped_total:
            print(f"skipped {skipped_total} malformed rows", file=sys.stderr)
        print(f"{written} new positions, {store.count()} cached in total")


def cmd_sync(args: argparse.Namespace) -> int:
    """Fetch new positions into the local cache, reporting progress."""
    _sync_into_cache(Client.load(), full=args.full)
    return 0


def cmd_influx_check(_: argparse.Namespace) -> int:
    """Probe the configured InfluxDB target and print a report. Writes nothing."""
    ok, lines = InfluxWriter(load_config()).check()
    for line in lines:
        print(line)
    return 0 if ok else 1


def _push() -> int:
    """Mirror new cached rows to InfluxDB and return how many were sent."""
    writer = InfluxWriter(load_config())
    if not default_db_path().exists():
        # Never create the cache as a side effect: it is a movement history.
        print("nothing cached yet — run 'trackiwi sync' first")
        return 0
    with Store() as store:
        sent = mirror(store, writer)
    print(f"pushed {sent} positions to InfluxDB")
    return sent


def cmd_influx_push(_: argparse.Namespace) -> int:
    """Mirror cached positions not yet sent to the configured InfluxDB."""
    _push()
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Sync from trackiwi, refresh tracker names, then push to InfluxDB.

    The three steps fail independently. The push runs even when the sync or
    the name refresh fails: the cache is the buffer, and whatever is already
    in it should still reach InfluxDB. Each failure is reported on its own
    ("sync failed" / "tracker names not refreshed"). The first trackiwi-side
    error is re-raised afterwards so the exit code still reports it (2 for
    auth, 1 else); when the push fails too, its error is printed and the
    trackiwi-side error still decides the exit code.
    """
    trackiwi_error: TrackiwiError | None = None
    client: Client | None = None
    try:
        client = Client.load()
        _sync_into_cache(client, full=False)
    except TrackiwiError as error:
        trackiwi_error = error
        print(f"sync failed: {error}", file=sys.stderr)
    # Names are refreshed whether or not the sync got through: without them
    # `mirror` refuses to push. A cached tracker the API does not name (empty
    # name, or gone from the account) gets a stable fallback, but only here,
    # after `trackers()` answered: otherwise one such tracker would stall the
    # mirror for every tracker.
    if client is not None and not isinstance(trackiwi_error, AuthError):
        try:
            trackers = client.trackers()
            names = {int(t["id"]): str(t.get("name") or "") for t in trackers if "id" in t}
            with Store() as store:
                store.set_tracker_names({k: v for k, v in names.items() if v})
                store.name_unnamed_trackers()
        except TrackiwiError as error:
            trackiwi_error = trackiwi_error or error
            print(f"tracker names not refreshed: {error}", file=sys.stderr)
    try:
        _push()
    except TrackiwiError as error:
        if trackiwi_error is None:
            raise
        print(f"push failed: {error}", file=sys.stderr)
    if trackiwi_error is not None:
        raise trackiwi_error
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export cached positions to stdout or a file in the chosen format."""
    start = _epoch(args.start) if args.start else None
    end = _epoch(args.end, end_of_day=True) if args.end else None
    if start is not None and end is not None and start > end:
        raise TrackiwiError("--from date is after --to date")
    if default_db_path().exists():
        with Store() as store:
            rows = store.query(tracker_id=args.tracker, start=start, end=end)
    else:
        # Never create the cache as a side effect of a read-only command: the
        # file is a movement history, and an export has nothing to put in it.
        rows = []
    try:
        text = FORMATS[args.format](rows)
    except (ValueError, OverflowError, OSError) as error:
        # The exporters are pure, so they raise plain exceptions: a `fix_at`
        # outside `datetime`'s range, or a non-finite coordinate, cached before
        # those were rejected at parse time. Convert at this boundary rather
        # than letting it escape main() as a traceback. Both schema-bearing
        # formats raise on a non-finite coordinate (GPX since `lat="inf"` is
        # not a valid `xsd:decimal`, GeoJSON since `Infinity` is not valid
        # JSON); `csv` is a raw dump of the cache and passes the value on.
        raise TrackiwiError(f"cached data cannot be exported: {error}") from error
    if args.output:
        _write_atomically(args.output, text)
        print(f"wrote {len(rows)} positions to {args.output}", file=sys.stderr)
        return 0
    try:
        sys.stdout.write(text)
    except BrokenPipeError:
        # `trackiwi export --format csv | head`: the reader is gone, which is
        # not an error — the export is the payload and the reader took what it
        # wanted. This is the *only* place a dead pipe means success; handling
        # it for the whole dispatch made `trackiwi sync 2>&1 | head -1` report
        # a sync that stopped after its first page as exit 0.
        _silence_stderr()
    return 0


def _heading_line(tracker_id: object, heading: Heading) -> str:
    """Render one tracker's estimate as a tab-separated line.

    Columns: tracker id, degrees (one decimal, ``-`` when unknown), state,
    source, when the vehicle last moved (UTC, ``-`` when never), and how long
    it has been parked in seconds (``-`` while moving or unknown). Nothing
    here is a coordinate: the heading says which way the vehicle points, not
    where it is.
    """
    degrees = "-" if heading.degrees is None else f"{heading.degrees:.1f}"
    moved_at = (
        "-"
        if heading.moved_at is None
        else datetime.fromtimestamp(heading.moved_at, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    return "\t".join(
        (
            _cell(tracker_id),
            degrees,
            heading.state,
            heading.source,
            moved_at,
            _cell(heading.parked_for),
        )
    )


def cmd_heading(args: argparse.Namespace) -> int:
    """Print each tracker's estimated heading and confidence state.

    Implements :need:`REQ_HEADING_ESTIMATE` and :need:`REQ_HEADING_STATE`.

    Pure arithmetic over the cache: no request is made, and like `export` the
    command never creates the cache as a side effect. Without a cache there
    is nothing to estimate; with ``--tracker`` an id with no rows still prints
    an ``unknown`` line, so a script reading the output sees a row per
    requested tracker.
    """
    if args.stale_after < 0:
        raise TrackiwiError("--stale-after must be zero or more seconds")
    if default_db_path().exists():
        with Store() as store:
            rows = store.query(tracker_id=args.tracker)
    else:
        rows = []
    headings = estimate_headings(rows, stale_after=args.stale_after)
    if args.tracker is not None and args.tracker not in headings:
        headings[args.tracker] = Heading(None, "unknown", "none", None, None)
    if not headings:
        print("nothing cached yet — run 'trackiwi sync' first", file=sys.stderr)
        return 0
    for tracker_id, heading in headings.items():
        print(_heading_line(tracker_id, heading))
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Delete the local position cache, including SQLite sidecars.

    Implements :need:`REQ_PURGE_DELETES`: unlinks the cache and its sidecars
    without opening the database, so a corrupt cache can still be removed.
    """
    path = default_db_path()
    if not args.yes:
        raise TrackiwiError(f"this deletes {path} — pass --yes to confirm")
    # Unlink without opening the database. Opening it first meant a corrupt
    # cache raised in `Store.__enter__` before the delete ever happened —
    # failing in exactly the case where a user most wants the movement history
    # gone (spec section 7.1) — and on a machine that had never synced it
    # created the file just to delete it again.
    # The sidecar files are deleted too: a journal left behind by a crashed
    # write holds position rows just like the database does. `cache_files` is
    # shared with `Store.purge()` so the two cannot drift apart again.
    for target in cache_files(path):
        try:
            target.unlink(missing_ok=True)
        except OSError as error:
            raise TrackiwiError(f"could not delete {target}: {error}") from error
    print("Local position cache deleted.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for every subcommand."""
    parser = argparse.ArgumentParser(prog="trackiwi", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="authenticate and store the session")
    login.add_argument("--email")
    login.add_argument(
        "--token",
        help="use an existing token instead of a password; '-' reads it from stdin, "
        "which keeps it out of the shell history and out of argv",
    )
    login.add_argument("--api-base", help="API base that goes with --token")
    login.set_defaults(func=cmd_login)

    sub.add_parser("logout", help="revoke the session").set_defaults(func=cmd_logout)

    for name, method, fields, help_text in READ_COMMANDS:
        read = sub.add_parser(
            name,
            help=help_text,
            description=READ_DESCRIPTIONS.get(name),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        read.set_defaults(func=_read_command(method, fields))

    sync = sub.add_parser("sync", help="fetch new positions into the local cache")
    sync.add_argument("--full", action="store_true", help="restart from the beginning")
    sync.set_defaults(func=cmd_sync)

    influx = sub.add_parser("influx", help="write cached positions to InfluxDB")
    influx_sub = influx.add_subparsers(dest="influx_command", required=True)
    influx_sub.add_parser(
        "check", help="probe the configured InfluxDB target (read-only)"
    ).set_defaults(func=cmd_influx_check)
    influx_sub.add_parser("push", help="send cached positions not yet mirrored").set_defaults(
        func=cmd_influx_push
    )

    sub.add_parser(
        "ingest", help="sync from trackiwi, then push to InfluxDB (for timers)"
    ).set_defaults(func=cmd_ingest)

    export = sub.add_parser("export", help="export cached positions")
    export.add_argument("--format", choices=sorted(FORMATS), required=True)
    export.add_argument("--from", dest="start", metavar="YYYY-MM-DD")
    export.add_argument("--to", dest="end", metavar="YYYY-MM-DD")
    export.add_argument("--tracker", type=int, help="limit to one tracker id")
    export.add_argument("-o", "--output", help="write to a file instead of stdout")
    export.set_defaults(func=cmd_export)

    heading = sub.add_parser(
        "heading",
        help="estimate which way each vehicle is pointing (from the cache)",
        description=HEADING_DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    heading.add_argument("--tracker", type=int, help="limit to one tracker id")
    heading.add_argument(
        "--stale-after",
        type=int,
        default=DEFAULT_STALE_AFTER,
        metavar="SECONDS",
        help="seconds since the last movement after which a parked estimate is "
        f"reported as 'stale' (default {DEFAULT_STALE_AFTER})",
    )
    heading.set_defaults(func=cmd_heading)

    purge = sub.add_parser("purge", help="delete the local position cache")
    purge.add_argument("--yes", action="store_true", help="confirm deletion")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch a command and map the project's exceptions to exit codes."""
    args = build_parser().parse_args(argv)
    try:
        # `args.func` is an argparse attribute, so it is `Any`; the local pins
        # the exit code every `cmd_*`/`command` returns back to `int`.
        exit_code: int = args.func(args)
        return exit_code
    except AuthError as error:
        print(f"authentication required: {error}", file=sys.stderr)
        print("Run 'trackiwi login'.", file=sys.stderr)
        return 2
    except TrackiwiError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # Reaching here means a dead pipe somewhere that is *not* the export
        # payload — `cmd_sync`'s progress line on a closed stderr, say, which
        # `trackiwi sync 2>&1 | head -1` produces. The command did not finish,
        # so it must not claim success: returning 0 made a wrapper's
        # `trackiwi sync && trackiwi export` run on a partial sync. Nothing is
        # printed because the stream to print on is what just failed.
        _silence_stderr()
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
