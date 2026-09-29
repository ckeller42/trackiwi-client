"""Network access to the trackiwi API.

This is the only module that performs network I/O. It knows nothing about
SQLite or output formats.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import platform
import re
import urllib.request
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path
from typing import Any

from . import COLUMNS, _http
from . import AuthError as AuthError
from . import TrackiwiError as TrackiwiError

_FLOAT_COLUMNS = {"latitude", "longitude", "speed"}
_REQUIRED_COLUMNS = ("id", "tracker_id", "fix_at", "latitude", "longitude")

#: Beyond this, a value cannot be epoch seconds (it would be year 2286+), so
#: it must be milliseconds. The unit is not documented by trackiwi; see the
#: design spec, section 10.5.
_MILLISECOND_THRESHOLD = 10_000_000_000

#: Plausible range for `fix_at` *after* normalisation, in epoch seconds:
#: anything from 1970-01-01T00:00:01Z to 9999-12-31T23:59:59Z. The upper bound
#: is what `datetime.fromtimestamp` can represent, so a value beyond it would
#: pass parsing and the store and then crash every export (not only of that
#: row, but of any range containing it). If trackiwi ever emits microseconds,
#: `normalize_epoch` divides by 1000 only once and the value lands here.
_MIN_FIX_AT = 1
_MAX_FIX_AT = 253402300799


def normalize_epoch(value: int) -> int:
    """Return `value` as epoch seconds, accepting seconds or milliseconds.

    Implements :need:`REQ_FIX_AT_SECONDS`.

    The sync CSV's form, and only that: `parse_positions` has already called
    `int()` on the field by the time this runs. See :func:`epoch_from_iso` for
    the ISO 8601 form that `GET /api/v2/trackers` sends, and for why the two
    are separate functions.

    >>> normalize_epoch(1700000000)
    1700000000
    >>> normalize_epoch(1700000000000)
    1700000000
    """
    return value // 1000 if value > _MILLISECOND_THRESHOLD else value


def epoch_from_iso(value: str) -> int:
    """Return an ISO 8601 timestamp as epoch seconds.

    Implements :need:`REQ_FIX_AT_SECONDS`.

    `fix_at` has **two types on two endpoints**: an epoch integer in seconds
    in the sync CSV, and an ISO 8601 string inside `latest_positionlog` on
    `GET /api/v2/trackers` (`received_at` likewise). Both were verified live.

    This is a *sibling* of :func:`normalize_epoch`, not an extension of it,
    because the two forms differ in more than their input type:

    - A single `int | str` function would be ambiguous on its most likely bad
      input. `"1766663018"` is a string that is also an epoch, so a union-typed
      normaliser has to guess whether a digit string means "parse as ISO" or
      "coerce and treat as epoch" — and either guess is wrong somewhere. Two
      functions make the caller say which endpoint the value came from, which
      it always knows.
    - The failure handling is opposite. A bad CSV field must raise `ValueError`
      so `parse_positions` skips and counts the row (one bad row must not
      discard a sync page). There is no row to skip when reading a JSON field,
      so this converts to `TrackiwiError` at the module boundary instead, per
      the project's boundary-conversion rule.
    - `normalize_epoch` keeps exactly the contract its existing callers and
      tests rely on, with no widened signature to re-verify.

    `datetime.fromisoformat` handles the trailing `Z` from Python 3.11, which
    is this project's floor (checked on 3.11 itself, not inferred from the
    changelog), so no dependency and no hand-rolled parsing is needed.

    A value with no zone is read as **UTC**. `fromisoformat` returns a naive
    datetime there and naive `.timestamp()` quietly applies the *machine's*
    local zone, which would decode the same response to a different instant on
    a different machine and shift every exported track by the offset.

    The result is range-checked like the CSV path's, so a timestamp the
    exporters cannot render never reaches them.

    >>> epoch_from_iso("2023-11-14T22:13:20Z")
    1700000000
    >>> epoch_from_iso("2023-11-14T22:13:20+00:00")
    1700000000
    >>> epoch_from_iso("2023-11-14T22:13:20")  # naive value read as UTC
    1700000000
    """
    if not isinstance(value, str):
        raise TrackiwiError(f"expected an ISO 8601 timestamp, got {type(value).__name__}")
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise TrackiwiError(f"unparseable timestamp from trackiwi: {value!r}") from error
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    epoch = int(moment.timestamp())
    if not _MIN_FIX_AT <= epoch <= _MAX_FIX_AT:
        raise TrackiwiError(f"timestamp outside the exportable range: {value!r}")
    return epoch


def _coerce(column: str, raw: str) -> float | int | None:
    """Coerce one CSV field, rejecting a non-finite numeric value.

    Implements :need:`REQ_MALFORMED_SKIP`: a `ValueError` here lets
    :func:`parse_positions` skip and count the row.
    """
    raw = raw.strip()
    if raw == "":
        return None
    if column not in _FLOAT_COLUMNS:
        return int(raw)
    value = float(raw)
    if not math.isfinite(value):
        # `float()` accepts "nan"/"inf"/"-inf", and nothing downstream catches
        # them: `inf` yields schema-invalid GPX and invalid JSON, `nan` becomes
        # NULL on insert and raises on the NOT NULL constraint. A non-finite
        # coordinate is malformed data, so raise and let the caller skip and
        # count the row like any other malformed field.
        raise ValueError(f"non-finite value for {column}: {raw!r}")
    return value


def parse_positions(text: str) -> tuple[list[tuple[Any, ...]], int, int | None]:
    """Parse the sync endpoint's CSV body.

    Implements :need:`REQ_MALFORMED_SKIP`, :need:`REQ_FIX_AT_SECONDS` and
    :need:`REQ_SYNC_PAST_MALFORMED`.

    Returns `(rows, skipped, last_id)`. Rows are tuples in
    :data:`trackiwi.COLUMNS` order with `fix_at` normalised to epoch seconds.
    Malformed rows are skipped and counted rather than aborting the batch,
    matching the vendor client's behaviour: one bad row must not discard a
    whole sync page. `last_id` is the highest id on any line whose first field
    is an integer, skipped lines included, or ``None`` when there is none.

    >>> body = "1,7,1700000000,1,31.0,-41.0,12,0.0,0,0,-71,9,98,4120\\n"
    >>> rows, skipped, last_id = parse_positions(body)
    >>> skipped, last_id
    (0, 1)
    >>> rows[0][:3]
    (1, 7, 1700000000)
    >>> parse_positions("2,7,broken\\n")
    ([], 1, 2)
    >>> parse_positions("broken,row\\n")
    ([], 1, None)
    """
    rows: list[tuple[Any, ...]] = []
    skipped = 0
    ids: list[int] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # `split(",")` rather than the `csv` module: every one of the 14
        # columns is numeric, so the sync body can never contain a quoted or
        # embedded-comma field. Note the asymmetry with `export.to_csv`, which
        # does use `csv.writer` — an export is written for other tools, not to
        # be re-read here.
        fields = line.split(",")
        # Read before the row is judged: `sync` has to move past a malformed
        # row as well, or it is fetched and skipped again on every page.
        with contextlib.suppress(ValueError):
            ids.append(int(fields[0]))
        if len(fields) != len(COLUMNS):
            skipped += 1
            continue
        try:
            values = {c: _coerce(c, f) for c, f in zip(COLUMNS, fields, strict=True)}
        except ValueError:
            skipped += 1
            continue
        if any(values[c] is None for c in _REQUIRED_COLUMNS):
            skipped += 1
            continue
        # `fix_at` is in `_REQUIRED_COLUMNS`, so the check just above guarantees
        # it is not None here; `_coerce` returns an `int` for it (not in
        # `_FLOAT_COLUMNS`). mypy cannot see through the `any(...)` generator, so
        # this narrows the value it already knows is present.
        fix_at_raw = values["fix_at"]
        assert fix_at_raw is not None
        fix_at = normalize_epoch(int(fix_at_raw))
        if not _MIN_FIX_AT <= fix_at <= _MAX_FIX_AT:
            skipped += 1
            continue
        values["fix_at"] = fix_at
        rows.append(tuple(values[c] for c in COLUMNS))
    return rows, skipped, max(ids, default=None)


WEBSITE = "https://www.trackiwi.com"
APP_NAME = "trackiwi"
APP_VERSION = "0.0.0"
DEFAULT_TIMEOUT = 30
SYNC_TIMEOUT = 60


def default_config_path() -> Path:
    """Return the credentials file path, honouring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "config.json"


#: Anything that looks like a bearer credential in server-controlled text.
_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def _redact(text: str, token: str | None = None) -> str:
    """Strip bearer credentials from text that is about to be shown.

    Implements :need:`REQ_TOKEN_NEVER_LOGGED`.

    Spec section 7.3 requires the token to be redacted in any output. The one
    place a token can plausibly re-enter output is an error body: proxies and
    API gateways echo request details, including request headers, into 4xx/5xx
    responses, and `_check` interpolates the first 200 bytes of the body into a
    message the CLI prints to stderr — from where it reaches scrollback,
    `script` captures, CI logs and bug reports.
    """
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    if token:
        text = text.replace(token, "<redacted>")
    return text


def _check(status: int, body: bytes, token: str | None = None) -> None:
    """Raise the right error for a non-2xx status, per the app's own handling.

    Implements :need:`REQ_TOKEN_NEVER_LOGGED`: the interpolated body is redacted.
    Implements :need:`REQ_NO_REDIRECTS`: a 3xx raises.

    `token`, when given, is this session's own token, scrubbed from the body
    in addition to the generic `Bearer ...` pattern. `_check` is module-level
    and so has no `self` to read it from.
    """
    if 300 <= status < 400:
        # Never success and never "no more data": see `_http.no_redirect_opener`.
        raise TrackiwiError(
            f"trackiwi answered HTTP {status}, a redirect; redirects are not followed",
            status=status,
        )
    if status < 400:
        return
    if status in (401, 412):
        raise AuthError("invalid credentials or expired session", status=status)
    if status == 409:
        raise TrackiwiError(
            "the account needs attention — open the trackiwi app and check", status=status
        )
    if status == 429:
        raise TrackiwiError("rate limited by trackiwi — wait, then re-run", status=status)
    if status == 503:
        raise TrackiwiError("trackiwi is in maintenance — try again later", status=status)
    detail = _redact(body[:200].decode("utf-8", "replace"), token)
    raise TrackiwiError(f"API error {status}: {detail}", status=status)


def _require_https(api_base: str) -> str:
    """Return `api_base` without its trailing slash, insisting on https.

    Implements :need:`REQ_API_BASE_HTTPS`.

    Nothing validated the scheme before, and the value arrives from two
    untrusted-ish places: the login response's `server` field and whatever
    `--api-base` (or a config file) says. An `http://` value would send
    `Authorization: Bearer <token>` in cleartext on every request; this also
    catches a typo'd `--api-base`.

    The message names the way out, because a config file holding such a value
    fails *every* authenticated command and the user needs to know what to do
    about it — see `Client.load` and `cmd_logout`.
    """
    if not api_base.lower().startswith("https://"):
        raise TrackiwiError(
            f"the API base must start with https:// — got {api_base!r}; run 'trackiwi login' again"
        )
    return api_base.rstrip("/")


def _decode_json(body: bytes) -> object:
    """Parse a response body, converting a non-JSON body at the boundary.

    A WAF, captive portal or API gateway can answer 200 with an HTML page;
    `json.loads` then raises `json.JSONDecodeError`, which is not a
    `TrackiwiError` and used to escape `main()` as a traceback. The message
    never includes the body, because such a page can echo the request's own
    `Authorization` header back at us.
    """
    try:
        return json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise TrackiwiError("unexpected response from trackiwi") from error


class Client:
    """Read-only access to a trackiwi account.

    The API base is never hardcoded: it comes from the login response's
    `server` field. `opener` exists so tests can inject a fake transport.
    """

    def __init__(
        self,
        api_base: str | None = None,
        token: str | None = None,
        user_id: int | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.api_base = _require_https(api_base) if api_base else None
        self.token = token
        self.user_id = user_id
        self.opener = opener or _http.no_redirect_opener().open
        self.config_path = default_config_path()

    @property
    def authenticated(self) -> bool:
        """True when both a token and an API base are present."""
        return bool(self.token and self.api_base)

    @classmethod
    def load(cls, opener: Callable[..., Any] | None = None) -> Client:
        """Load the stored session, self-healing a widened config file.

        Implements :need:`REQ_CONFIG_MODE_0600`: a config file whose mode was
        widened after login is narrowed back to 0600 here.
        """
        path = default_config_path()
        if not path.exists():
            return cls(opener=opener)
        # Steady-state self-heal, mirroring Store.__enter__: a config file
        # widened after login (restored from a backup, copied with `cp`, synced
        # by a dotfile manager) is narrowed back here, since `save()` normally
        # runs only once. A chmod we are not allowed to perform must not stop
        # the session from loading.
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as error:
            # Not corruption: a permission-denied read, a directory in the way
            # or an I/O error says something different about what is wrong, and
            # collapsing it into the corrupt-config message dropped the errno
            # that identifies it. The recovery is the same, because `save()`
            # chmods the parent directory back to 0700 first.
            raise TrackiwiError(
                f"cannot read the config file ({path}): {error} — run 'trackiwi login' again"
            ) from error
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrackiwiError(
                f"config file is corrupt ({path}) — run 'trackiwi login' again"
            ) from error
        corrupt = TrackiwiError(f"config file is corrupt ({path}) — run 'trackiwi login' again")
        if not isinstance(data, dict):
            raise corrupt
        api_base = data.get("api_base")
        # The values are validated, not only the top-level document: a
        # non-string `api_base` reached `_require_https`, whose `.lower()`
        # raised an uncaught `AttributeError` — a raw traceback one line past
        # the check added to prevent exactly that. `None` is legitimate and
        # means "not logged in".
        if not isinstance(api_base, (str, type(None))):
            raise corrupt
        return cls(
            api_base=api_base,
            token=data.get("token"),
            user_id=data.get("user_id"),
            opener=opener,
        )

    @classmethod
    def forget_local_session(cls) -> bool:
        """Delete the stored credentials, returning whether there were any.

        Does not need a *loadable* config, which is the point: the token grants
        live vehicle location, so it must stay removable even when the config
        file holds a value that `Client.load` refuses (an `http://` API base,
        say). `cmd_logout` uses this as its fallback.

        Implements :need:`REQ_TOKEN_REMOVABLE`.
        """
        path = default_config_path()
        existed = path.exists()
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            raise TrackiwiError(f"could not delete {path}: {error}") from error
        return existed

    def save(self) -> None:
        """Persist the session owner-only. The password is never stored.

        Implements :need:`REQ_CONFIG_MODE_0600`.

        The directory and the file are created *at* their final mode, not
        widened-then-narrowed: `write_text` plus a follow-up `chmod` left a
        window (0644 under the usual umask, 0666 under a permissive one) in
        which another local process could open the token file and keep the
        descriptor — a later chmod does not revoke an open fd.
        """
        self.config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.config_path.parent, 0o700)
        payload = {"api_base": self.api_base, "token": self.token, "user_id": self.user_id}
        text = json.dumps(payload, indent=2) + "\n"
        fd = os.open(self.config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        # O_CREAT's mode applies to a new file only, so an existing config
        # file (or one created under a narrower umask) still gets chmod'ed.
        os.chmod(self.config_path, 0o600)

    def _request(
        self,
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        authed: bool = False,
    ) -> tuple[int, Message, bytes]:
        """Perform one HTTP request and return `(status, headers, body)`.

        Implements :need:`REQ_IGNORE_APP_COMMAND`: any `trackiwi-app-command`
        response header is returned unread and never acted on. Every transport
        failure, including a timeout or reset while reading the response,
        becomes a `TrackiwiError` ("network error: …").
        """
        headers = {
            "Accept": "application/json",
            "User-Agent": _http.USER_AGENT,
            "App-Name": APP_NAME,
            "App-Version": APP_VERSION,
            "User-Platform": "python",
            "User-Device": "trackiwi-client",
            "User-OS": platform.platform(),
            "User-Timezone": "0",
        }
        if authed:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # The API may return a `trackiwi-app-command` header, which the
        # official client executes. We deliberately ignore it.
        return _http.send(self.opener, request, timeout, "network error")

    def _api(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> tuple[int, Message, bytes]:
        if not self.authenticated:
            raise AuthError("not logged in — run 'trackiwi login'")
        try:
            return self._request(
                method, f"{self.api_base}{path}", body=body, timeout=timeout, authed=True
            )
        except TrackiwiError as error:
            # Belt and braces: every authenticated failure that surfaces from
            # below is scrubbed of this session's token before it travels any
            # further, whatever produced the message.
            message = _redact(str(error), self.token)
            if message == str(error):
                raise
            raise type(error)(message, status=error.status) from None

    def login(self, email: str, password: str) -> dict[str, Any]:
        """Authenticate and store the session.

        Implements :need:`REQ_API_BASE_FROM_LOGIN`: the API base is taken from
        the login response's ``server`` field, and :need:`REQ_API_BASE_HTTPS`
        via :func:`_require_https` before it is stored.
        """
        status, _, body = self._request(
            "POST", f"{WEBSITE}/api/login", body={"email": email, "password": password}
        )
        _check(status, body, self.token)
        data = _decode_json(body)
        # `_decode_json` returns `object` (a JSON value can be any shape); a
        # non-object body would previously fall into the `except TypeError`
        # below via the failed subscription. Narrowing here keeps that same
        # outcome while letting the field reads type-check.
        if not isinstance(data, dict):
            raise TrackiwiError("unexpected login response from trackiwi")
        try:
            server = data["server"]
            token = data["token"]
            user = data["user"]
            user_id = user["id"]
            if (
                not isinstance(server, str)
                or not isinstance(token, str)
                or not isinstance(user, dict)
            ):
                raise TypeError
        except (KeyError, TypeError) as error:
            # Never include the raw body here — a token could be in it.
            raise TrackiwiError("unexpected login response from trackiwi") from error
        self.api_base = _require_https(server)
        self.token = token
        self.user_id = user_id
        self.save()
        return user

    def session_ok(self) -> bool:
        """Return whether the stored session is still accepted by the server."""
        try:
            status, _, _ = self._api("GET", "/api/v2/session")
        except AuthError:
            return False
        return status < 300

    def logout(self) -> bool:
        """Revoke server-side first; a local delete alone leaves a live token.

        Implements :need:`REQ_LOGOUT_REVOKES` and :need:`REQ_READONLY` (the one
        state-changing request this client makes, ``DELETE /api/v2/session``).

        Returns `True` if server-side revocation succeeded; `False` if the
        attempt failed (network error, 5xx, etc.). The local state (token,
        api_base, user_id, config file) is always cleared regardless, since
        the caller must decide whether to warn the user that the token may
        remain valid server-side.
        """
        revoked = True
        if self.authenticated:
            try:
                status, _, body = self._api("DELETE", "/api/v2/session")
                _check(status, body, self.token)
            except TrackiwiError:
                revoked = False
        self.forget_local_session()
        self.token = None
        self.api_base = None
        self.user_id = None
        return revoked

    def _get_list(self, path: str, what: str) -> list[Any]:
        """GET `path` and return the list of records it answers with.

        The one implementation behind all six read-only list endpoints. It was
        `trackers()`'s body; five more endpoints needed exactly the same four
        steps (GET, `_check`, decode, insist on a list), and six copies of it
        would have been six places for the envelope handling to drift apart.

        A payload that is not a list raises rather than being passed through.
        Handing back a dict or a string would move the failure to whichever
        caller first indexes it, by which point nothing says which endpoint
        produced it — hence `what` in the message.

        The `{"data": [...]}` envelope is still accepted alongside a bare list.
        The live check saw a bare list from every one of the six, but that is
        one account on one day against an undocumented API with no deprecation
        policy (design spec, section 2), and the vendor's own bundle reads both
        shapes. One extra branch is cheaper than a command that dies on a
        re-wrapped response.
        """
        status, _, body = self._api("GET", path)
        _check(status, body, self.token)
        data = _decode_json(body)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            inner = data.get("data")
            if isinstance(inner, list):
                return inner
        raise TrackiwiError(f"unexpected {what} response from trackiwi")

    def trackers(self) -> list[dict[str, Any]]:
        """List the account's trackers.

        **This output contains location data, not only device metadata.** Each
        record carries an `alarm_configuration` whose geofence alarm holds
        `lat`/`long`/`radius` — i.e. a place the owner cares about, typically
        where the vehicle is kept — and a `latest_positionlog` with the current
        fix. Treat the return value with the same care as the position cache
        (design spec, section 7.1), not as an inventory listing.

        Records also carry `installed_at`, `membership_valid`,
        `membership_ends_at` and `prunable_at`; the last of these reads as the
        vendor's own data-retention horizon for the account's history.

        Timestamps here are **ISO 8601 strings**, unlike the epoch integers in
        the sync CSV — `latest_positionlog.fix_at` and `received_at` both are.
        Use :func:`epoch_from_iso` on them.
        """
        return self._get_list("/api/v2/trackers", "trackers")

    def tours(self) -> list[dict[str, Any]]:
        """List the account's tours.

        Verified live: a bare JSON list whose records carry `color`,
        `ended_at`, `id`, `name`, `started_at` and `tracker_id`.
        """
        return self._get_list("/api/v2/tours", "tours")

    def markers(self) -> list[dict[str, Any]]:
        """List the account's markers.

        **The element shape is unverified.** The endpoint answered 200 with an
        empty list on the account it was checked against, so nothing is known
        about a marker record beyond the fact that the response is a bare list.
        Records are returned exactly as parsed; no field is promised.
        """
        return self._get_list("/api/v2/markers", "markers")

    def marker_categories(self) -> list[dict[str, Any]]:
        """List the account's marker categories.

        Verified live: a bare JSON list whose records carry `color`, `id` and
        `name`.
        """
        return self._get_list("/api/v2/marker_categories", "marker categories")

    def alarms(self) -> list[dict[str, Any]]:
        """List the account's alarms.

        Verified live: a bare JSON list whose records carry `acknowledged`,
        `alarm_type`, `event`, `id`, `inserted_at` and `tracker_id`.

        **This output is a location history.** Each record's `event` object
        embeds `latitude`/`longitude`, so an alarm list says where the vehicle
        was every time an alarm fired — which for a theft or geofence alarm is
        precisely the interesting places. It is not "just" a list of alerts.

        Nothing here acknowledges, clears or tests an alarm: this client is
        read-only by construction (design spec, section 7.5).
        """
        return self._get_list("/api/v2/alarms", "alarms")

    def shares(self) -> list[dict[str, Any]]:
        """List the account's active shares.

        **The element shape is unverified**, for the same reason as
        :meth:`markers`: the endpoint answered 200 with an empty list on the
        account it was checked against. Records are returned exactly as parsed.

        Read-only: this lists shares, it cannot create or revoke one. `share
        create` was considered and rejected (design spec, section 7.5), because
        anyone holding a share link can see the vehicle's position.
        """
        return self._get_list("/api/v2/shares", "shares")

    # `POST /api/v2/session/test_alarm` exists in trackiwi's API and is
    # deliberately NOT implemented, here or anywhere else. It fires a real
    # alarm on a real vehicle, which is both a state change (forbidden by the
    # read-only rule that allows only `DELETE /api/v2/session`) and a physical
    # event in the owner's life. There is no safe way to exercise it and no
    # read-only use for it. The same goes for the item/mutation routes the app
    # uses to create, update and delete tours, markers, marker categories,
    # shares and trackers (the trailing-slash variants of the paths above), and
    # for `PUT /api/v2/session/push_token`.

    def sync(
        self, offset: int | None = None
    ) -> Iterator[tuple[list[tuple[Any, ...]], int, int | None]]:
        """Yield `(rows, skipped, total)` batches until the server runs dry.

        Implements :need:`REQ_SYNC_RESUME`, :need:`REQ_SYNC_OFFSET_EXCLUSIVE`,
        :need:`REQ_SYNC_FAIL_LOUD` and :need:`REQ_SYNC_PAST_MALFORMED`.

        Offset-based and therefore resumable: if this fails part-way, simply
        running it again continues from the highest id already stored. That is
        why there is no retry logic anywhere in this client.

        The offset advances to the highest id on the page, malformed rows
        included, so `rows` can be empty in a batch that only skipped: a
        malformed row at the newest end of the data is passed, not re-fetched
        until a newer good row happens to follow it.

        Two failure shapes are distinguished from ordinary end-of-data:

        - A page on which no line has a readable id but one or more were
          skipped is not "no more data" — it is a parse failure with no next
          offset to go to. Continuing would silently re-fetch and re-fail on
          the same page, so this raises instead. Only a page with nothing to
          skip either (a genuinely empty response) ends the loop normally.
        - If the server ever returns a page whose highest id does not exceed
          the offset just requested (a duplicate, a stale cache, an
          off-by-one on their side), advancing by that id would spin
          forever re-requesting the same data. This raises rather than loop.
        """
        total: int | None = None
        while True:
            requested_offset = offset
            payload = {"initial_sync": True} if offset is None else {"offset": offset}
            status, headers, body = self._api(
                "POST", "/api/v2/trackers/sync", body=payload, timeout=SYNC_TIMEOUT
            )
            _check(status, body, self.token)
            if total is None:
                raw_total = headers.get("trackiwi-position-count")
                if raw_total:
                    try:
                        total = int(raw_total)
                    except ValueError:
                        total = None
            rows, skipped, last_id = parse_positions(body.decode("utf-8", "replace"))
            if last_id is None:
                if skipped:
                    raise TrackiwiError(
                        f"sync page at offset {requested_offset!r} was unparseable "
                        f"({skipped} row(s) skipped, none with a readable id)"
                    )
                return
            yield rows, skipped, total
            offset = last_id
            if requested_offset is not None and offset <= requested_offset:
                raise TrackiwiError(
                    f"trackiwi returned no new records past offset {requested_offset}"
                )
