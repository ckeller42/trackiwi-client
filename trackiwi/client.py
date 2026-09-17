"""Network access to the trackiwi API.

This is the only module that performs network I/O. It knows nothing about
SQLite or output formats.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import urllib.error
import urllib.request
from pathlib import Path

from . import COLUMNS, __version__

_FLOAT_COLUMNS = {"latitude", "longitude", "speed"}
_REQUIRED_COLUMNS = ("id", "tracker_id", "fix_at", "latitude", "longitude")

#: Beyond this, a value cannot be epoch seconds (it would be year 2286+), so
#: it must be milliseconds. The unit is not documented by trackiwi; see the
#: design spec, section 10.5.
_MILLISECOND_THRESHOLD = 10_000_000_000


class TrackiwiError(Exception):
    """Any failure talking to trackiwi."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(TrackiwiError):
    """Credentials were rejected, or the session expired."""


def normalize_epoch(value: int) -> int:
    """Return `value` as epoch seconds, accepting seconds or milliseconds."""
    return value // 1000 if value > _MILLISECOND_THRESHOLD else value


def _coerce(column: str, raw: str) -> float | int | None:
    raw = raw.strip()
    if raw == "":
        return None
    return float(raw) if column in _FLOAT_COLUMNS else int(raw)


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
        values["fix_at"] = normalize_epoch(int(values["fix_at"]))
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


def _check(status: int, body: bytes) -> None:
    """Raise the right error for a non-2xx status, per the app's own handling."""
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
    detail = body[:200].decode("utf-8", "replace")
    raise TrackiwiError(f"API error {status}: {detail}", status=status)


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
        self.api_base = api_base.rstrip("/") if api_base else None
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
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            api_base=data.get("api_base"),
            token=data.get("token"),
            user_id=data.get("user_id"),
            opener=opener,
        )

    def save(self) -> None:
        """Persist the session owner-only. The password is never stored."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.config_path.parent, 0o700)
        payload = {"api_base": self.api_base, "token": self.token, "user_id": self.user_id}
        self.config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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
        return self._request(
            method, f"{self.api_base}{path}", body=body, timeout=timeout, authed=True
        )

    def login(self, email: str, password: str) -> dict:
        status, _, body = self._request(
            "POST", f"{WEBSITE}/api/login", body={"email": email, "password": password}
        )
        _check(status, body)
        data = json.loads(body)
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
        self.api_base = server.rstrip("/")
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

    def logout(self) -> None:
        """Revoke server-side first; a local delete alone leaves a live token."""
        if self.authenticated:
            with contextlib.suppress(TrackiwiError):
                self._api("DELETE", "/api/v2/session")
        self.config_path.unlink(missing_ok=True)
        self.token = None
        self.api_base = None
        self.user_id = None
