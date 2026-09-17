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
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

from . import COLUMNS, __version__
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
    """Return `value` as epoch seconds, accepting seconds or milliseconds."""
    return value // 1000 if value > _MILLISECOND_THRESHOLD else value


def _coerce(column: str, raw: str) -> float | int | None:
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


def parse_positions(text: str) -> tuple[list[tuple], int]:
    """Parse the sync endpoint's CSV body.

    Returns `(rows, skipped)`. Rows are tuples in :data:`trackiwi.COLUMNS`
    order with `fix_at` normalised to epoch seconds. Malformed rows are
    skipped and counted rather than aborting the batch, matching the vendor
    client's behaviour: one bad row must not discard a whole sync page.
    """
    rows: list[tuple] = []
    skipped = 0
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
        fix_at = normalize_epoch(int(values["fix_at"]))
        if not _MIN_FIX_AT <= fix_at <= _MAX_FIX_AT:
            skipped += 1
            continue
        values["fix_at"] = fix_at
        rows.append(tuple(values[c] for c in COLUMNS))
    return rows, skipped


WEBSITE = "https://www.trackiwi.com"
APP_NAME = "trackiwi"
APP_VERSION = "0.0.0"
USER_AGENT = f"trackiwi-client/{__version__} (+python-urllib)"
DEFAULT_TIMEOUT = 30
SYNC_TIMEOUT = 60


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "config.json"


def _header(headers: dict, name: str) -> str | None:
    """Look up a response header case-insensitively.

    `_request` returns `dict(response.headers)`, which loses the
    case-insensitivity real HTTP headers have, so a lookup here must not
    assume the server sent any particular casing.
    """
    lname = name.lower()
    for key, value in headers.items():
        if key.lower() == lname:
            return value
    return None


#: Anything that looks like a bearer credential in server-controlled text.
_BEARER_RE = re.compile(r"Bearer\s+\S+", re.IGNORECASE)


def _redact(text: str, token: str | None = None) -> str:
    """Strip bearer credentials from text that is about to be shown.

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

    `token`, when given, is this session's own token, scrubbed from the body
    in addition to the generic `Bearer ...` pattern. `_check` is module-level
    and so has no `self` to read it from.
    """
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

    Nothing validated the scheme before, and the value arrives from two
    untrusted-ish places: the login response's `server` field and whatever
    `--api-base` (or a config file) says. An `http://` value would send
    `Authorization: Bearer <token>` in cleartext on every request; this also
    catches a typo'd `--api-base`.
    """
    if not api_base.lower().startswith("https://"):
        raise TrackiwiError(f"the API base must start with https:// — got {api_base!r}")
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
        opener=None,
    ) -> None:
        self.api_base = _require_https(api_base) if api_base else None
        self.token = token
        self.user_id = user_id
        self.opener = opener or urllib.request.urlopen
        self.config_path = default_config_path()

    @property
    def authenticated(self) -> bool:
        return bool(self.token and self.api_base)

    @classmethod
    def load(cls, opener=None) -> Client:
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
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TrackiwiError(
                f"config file is corrupt ({path}) — run 'trackiwi login' again"
            ) from error
        if not isinstance(data, dict):
            raise TrackiwiError(f"config file is corrupt ({path}) — run 'trackiwi login' again")
        return cls(
            api_base=data.get("api_base"),
            token=data.get("token"),
            user_id=data.get("user_id"),
            opener=opener,
        )

    def save(self) -> None:
        """Persist the session owner-only. The password is never stored.

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
        body: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        authed: bool = False,
    ) -> tuple[int, dict, bytes]:
        headers = {
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
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
        try:
            with self.opener(request, timeout=timeout) as response:
                # The API may return a `trackiwi-app-command` header, which the
                # official client executes. We deliberately ignore it.
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()
        except urllib.error.URLError as error:
            raise TrackiwiError(f"network error: {error.reason}") from error

    def _api(
        self, method: str, path: str, body: dict | None = None, timeout: int = DEFAULT_TIMEOUT
    ) -> tuple[int, dict, bytes]:
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

    def login(self, email: str, password: str) -> dict:
        status, _, body = self._request(
            "POST", f"{WEBSITE}/api/login", body={"email": email, "password": password}
        )
        _check(status, body, self.token)
        data = _decode_json(body)
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
        try:
            status, _, _ = self._api("GET", "/api/v2/session")
        except AuthError:
            return False
        return status < 400

    def logout(self) -> bool:
        """Revoke server-side first; a local delete alone leaves a live token.

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
        self.config_path.unlink(missing_ok=True)
        self.token = None
        self.api_base = None
        self.user_id = None
        return revoked

    def trackers(self) -> list[dict]:
        """List the account's trackers.

        The response envelope is not documented; both a bare list and a
        `{"data": [...]}` wrapper are accepted (design spec, section 10.3).
        Anything else is not a list of trackers and must not be handed to
        the caller as if it were one.
        """
        status, _, body = self._api("GET", "/api/v2/trackers")
        _check(status, body, self.token)
        data = _decode_json(body)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        raise TrackiwiError("unexpected trackers response from trackiwi")

    def sync(self, offset: int | None = None) -> Iterator[tuple[list[tuple], int, int | None]]:
        """Yield `(rows, skipped, total)` batches until the server runs dry.

        Offset-based and therefore resumable: if this fails part-way, simply
        running it again continues from the highest id already stored. That is
        why there is no retry logic anywhere in this client.

        Two failure shapes are distinguished from ordinary end-of-data:

        - A page that parses to zero rows but skipped one or more malformed
          ones is not "no more data" — it is a parse failure. Continuing
          would leave the stored offset stuck forever, silently re-fetching
          and re-failing on the same page, so this raises instead. Only a
          page with nothing to skip either (a genuinely empty response) ends
          the loop normally.
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
                raw_total = _header(headers, "trackiwi-position-count")
                if raw_total:
                    try:
                        total = int(raw_total)
                    except ValueError:
                        total = None
            rows, skipped = parse_positions(body.decode("utf-8", "replace"))
            if not rows:
                if skipped:
                    raise TrackiwiError(
                        f"sync page at offset {requested_offset!r} was unparseable "
                        f"({skipped} row(s) skipped, none usable)"
                    )
                return
            yield rows, skipped, total
            offset = max(row[0] for row in rows)
            if requested_offset is not None and offset <= requested_offset:
                raise TrackiwiError(
                    f"trackiwi returned no new records past offset {requested_offset}"
                )
