"""Write cached positions to InfluxDB.

The only module that talks to InfluxDB. It never imports `client.py`, and
`client.py` never imports it. Configuration lives in
``~/.config/trackiwi/influx.toml`` and every key can be overridden by an
environment variable named ``TRACKIWI_INFLUX_<KEY>``. Secrets are never
stored in the TOML file: the token comes from the variable named by
``token_env`` or from ``token_file``.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import json
import os
import re
import sys
import tomllib
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from typing import Any, Protocol

from . import TrackiwiError, _http
from .lineprotocol import format_point
from .store import Store

ENV_PREFIX = "TRACKIWI_INFLUX_"
DEFAULT_TOKEN_ENV = "TRACKIWI_INFLUX_TOKEN"
_KEYS = (
    "url",
    "version",
    "org",
    "bucket",
    "database",
    "username",
    "token_env",
    "token_file",
    "password_file",
)


def default_influx_config_path() -> Path:
    """Return the InfluxDB config path, honouring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "influx.toml"


@dataclass(frozen=True)
class InfluxConfig:
    """A resolved InfluxDB target. `version` None means auto-detect."""

    url: str
    version: int | None
    org: str | None
    bucket: str | None
    database: str | None
    username: str | None
    token: str | None
    password: str | None
    token_env: str


def _read_file(path: Path) -> dict[str, Any]:
    """Read the TOML file, narrowing a widened mode first (REQ_CONFIG_MODE_0600)."""
    if not path.exists():
        return {}
    # A config widened by a restore or a copy is narrowed back, mirroring the
    # trackiwi session file. Failing to chmod must not stop the load.
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise TrackiwiError(f"{path} is not valid TOML: {error}") from error
    except OSError as error:
        raise TrackiwiError(f"cannot read {path}: {error}") from error
    for key in data:
        if key not in _KEYS:
            raise TrackiwiError(
                f"{path}: unknown key '{key}' (allowed: {', '.join(_KEYS)}); "
                "secrets belong in token_file or an environment variable"
            )
    return data


def _read_secret(path_text: str, what: str) -> str:
    """Read a secret from a file, stripping surrounding whitespace."""
    path = Path(path_text).expanduser()
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise TrackiwiError(f"cannot read {what} file {path}: {error.strerror}") from error


def _parse_version(raw: Any) -> int | None:
    """Map the configured version to 1, 2, or None (auto)."""
    text = str(raw).strip().lower()
    if text in ("", "auto"):
        return None
    if text in ("1", "2"):
        return int(text)
    raise TrackiwiError(f'invalid InfluxDB version {raw!r}: use "auto", 1 or 2')


def load_config(path: Path | None = None, env: Mapping[str, str] | None = None) -> InfluxConfig:
    """Resolve the InfluxDB target from the file and the environment.

    Precedence: environment > file > default. Implements
    :need:`REQ_CONFIG_MODE_0600` for ``influx.toml`` (see `_read_file`).
    """
    path = path if path is not None else default_influx_config_path()
    env = env if env is not None else os.environ
    merged: dict[str, Any] = dict(_read_file(path))
    for key in _KEYS:
        value = env.get(ENV_PREFIX + key.upper())
        if value is not None and value != "":
            merged[key] = value
    url = str(merged.get("url", "")).strip().rstrip("/")
    if not url:
        raise TrackiwiError(
            f"InfluxDB 'url' is not configured: set it in {path} or TRACKIWI_INFLUX_URL"
        )
    token_env = str(merged.get("token_env") or DEFAULT_TOKEN_ENV)
    token = env.get(token_env) or None
    if token is None and merged.get("token_file"):
        token = _read_secret(str(merged["token_file"]), "token")
    password = env.get(ENV_PREFIX + "PASSWORD") or None
    if password is None and merged.get("password_file"):
        password = _read_secret(str(merged["password_file"]), "password")

    def text(key: str) -> str | None:
        value = merged.get(key)
        return str(value) if value not in (None, "") else None

    return InfluxConfig(
        url=url,
        version=_parse_version(merged.get("version", "auto")),
        org=text("org"),
        bucket=text("bucket"),
        database=text("database"),
        username=text("username"),
        token=token.strip() if token else None,
        password=password,
        token_env=token_env,
    )


WRITE_TIMEOUT = 60
_RETENTION_DROP = b"beyond retention policy"
_AUTH_RE = re.compile(r"\b(Token|Bearer|Basic)\s+\S+", re.IGNORECASE)


class InfluxWriter:
    """Talks to one InfluxDB target: detect the version, write, check.

    Implements :need:`REQ_INFLUX_WRITE_SCOPE` (the only writes are POSTs of
    line protocol to the configured target's write endpoint) and
    :need:`REQ_INFLUX_TOKEN_REDACT` (credentials are scrubbed from every
    message built from server-controlled text). Redirects are never
    followed, so a 3xx is an error rather than an acknowledgement and the
    credentials never travel to another address (:need:`REQ_MIRROR_RESUME`).
    `opener` exists so tests can inject a fake transport, exactly as in
    `client.Client`.
    """

    def __init__(self, config: InfluxConfig, opener: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.opener = opener or _http.no_redirect_opener().open
        self._version = config.version

    # -- transport -----------------------------------------------------------

    def _redact(self, text: str) -> str:
        """Scrub the token, the password and any auth header value."""
        text = _AUTH_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
        for secret in (self.config.token, self.config.password):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text

    def _auth_headers(self) -> dict[str, str]:
        """Authorization header for the detected version, if credentials exist."""
        if self.version == 2 and self.config.token:
            return {"Authorization": f"Token {self.config.token}"}
        if self.version == 1 and self.config.username:
            raw = f"{self.config.username}:{self.config.password or ''}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        return {}

    def _request(
        self,
        method: str,
        path: str,
        query: Mapping[str, str] | None = None,
        data: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[int, Message, bytes]:
        """Perform one request and return ``(status, headers, body)``.

        A 3xx raises: redirects are not followed (see `_http.no_redirect_opener`), and a
        redirect must never be mistaken for an answer. Every transport failure
        — including a timeout or reset while reading the response, which
        urllib does not wrap in `URLError` — becomes a `TrackiwiError`.
        """
        url = self.config.url + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            url,
            data=data,
            headers={"User-Agent": _http.USER_AGENT, **(headers or {})},
            method=method,
        )
        try:
            result = _http.send(
                self.opener,
                request,
                WRITE_TIMEOUT,
                f"cannot reach InfluxDB at {self.config.url}",
            )
        except TrackiwiError as error:
            raise TrackiwiError(self._redact(str(error))) from error
        status, response_headers, _ = result
        if 300 <= status < 400:
            location = self._redact(response_headers.get("Location") or "(no Location)")
            raise TrackiwiError(
                f"InfluxDB at {self.config.url} answered HTTP {status}, a redirect to "
                f"{location}; redirects are not followed — set url to the final address"
            )
        return result

    # -- version -------------------------------------------------------------

    def detect_version(self) -> int:
        """Read the major version from the ``X-Influxdb-Version`` header of ``/ping``."""
        _, headers, _ = self._request("GET", "/ping")
        raw = (headers.get("X-Influxdb-Version") or "").strip()
        text = raw.lstrip("vV")
        if text[:1] in ("1", "2"):
            return int(text[0])
        if raw.lower().startswith("cloud"):
            return 2
        raise TrackiwiError(
            f"could not detect the InfluxDB version at {self.config.url} "
            f"(X-Influxdb-Version: {raw or 'missing'}); set version = 1 or 2 in influx.toml"
        )

    @property
    def version(self) -> int:
        """The target's major version, detected on first use unless configured."""
        if self._version is None:
            self._version = self.detect_version()
        return self._version

    def target_key(self) -> str:
        """Stable identity of this target for mirror bookkeeping; no secrets."""
        if self.version == 2:
            return f"{self.config.url}|v2|{self.config.org}/{self.config.bucket}"
        return f"{self.config.url}|v1|{self.config.database}"

    # -- write ---------------------------------------------------------------

    def _write_endpoint(self) -> tuple[str, dict[str, str]]:
        """Path and query for a write, validating the settings it needs."""
        cfg = self.config
        if self.version == 2:
            if not cfg.token:
                raise TrackiwiError(
                    f"InfluxDB 2.x needs a token: set ${cfg.token_env} or token_file"
                )
            if not cfg.org or not cfg.bucket:
                raise TrackiwiError("InfluxDB 2.x needs 'org' and 'bucket' configured")
            return "/api/v2/write", {"org": cfg.org, "bucket": cfg.bucket, "precision": "s"}
        if not cfg.database:
            raise TrackiwiError("InfluxDB 1.x needs 'database' configured")
        return "/write", {"db": cfg.database, "precision": "s"}

    def _target_name(self) -> str:
        """Human name of the write destination, for error messages."""
        if self.version == 2:
            return f"bucket '{self.config.bucket}' in org '{self.config.org}'"
        return f"database '{self.config.database}'"

    def write(self, lines: Sequence[str]) -> None:
        """POST one gzipped batch of line protocol; raise unless acknowledged.

        Acknowledged means 2xx, or a 4xx whose body says the points were
        "beyond retention policy" (dropped for good, reported on stderr).
        """
        if not lines:
            return
        path, query = self._write_endpoint()
        body = gzip.compress("\n".join(lines).encode("utf-8"))
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "Content-Encoding": "gzip",
            **self._auth_headers(),
        }
        status, _, response = self._request("POST", path, query=query, data=body, headers=headers)
        if 200 <= status < 300:
            return
        detail = self._redact(response[:200].decode("utf-8", "replace"))
        if 400 <= status < 500 and _RETENTION_DROP in response:
            # A partial write: InfluxDB stored every point it could and dropped
            # the ones older than the bucket's retention, which it will never
            # accept. Refusing the batch would re-send it forever and stall the
            # mirror behind it, so it counts as acknowledged (REQ_MIRROR_RESUME).
            print(
                f"warning: InfluxDB dropped points outside the retention of "
                f"{self._target_name()} (HTTP {status}): {detail}",
                file=sys.stderr,
            )
            return
        if status in (401, 403):
            raise TrackiwiError(
                f"InfluxDB rejected the token or credentials (HTTP {status}) "
                f"for {self._target_name()}"
            )
        if status == 404:
            raise TrackiwiError(f"InfluxDB has no {self._target_name()} (HTTP 404)")
        raise TrackiwiError(f"InfluxDB write failed (HTTP {status}): {detail}")

    # -- check ---------------------------------------------------------------

    def check(self) -> tuple[bool, list[str]]:
        """Probe connectivity, version and auth. Read-only: writes nothing."""
        lines = [f"InfluxDB {self.version}.x at {self.config.url}"]
        if self.version == 2:
            status, _, body = self._request(
                "GET",
                "/api/v2/buckets",
                query={"org": self.config.org or "", "name": self.config.bucket or ""},
                headers=self._auth_headers(),
            )
            if status == 401:
                return False, [*lines, "token rejected (HTTP 401)"]
            if status == 403:
                return True, [*lines, "auth OK, bucket not verifiable (write-only token)"]
            if status != 200:
                detail = self._redact(body[:200].decode("utf-8", "replace"))
                return False, [*lines, f"bucket lookup failed (HTTP {status}): {detail}"]
            found = json.loads(body or b"{}").get("buckets") or []
            if any(b.get("name") == self.config.bucket for b in found):
                return True, [*lines, f"bucket '{self.config.bucket}' found"]
            return False, [*lines, f"bucket '{self.config.bucket}' not found"]
        status, _, body = self._request(
            "GET", "/query", query={"q": "SHOW DATABASES"}, headers=self._auth_headers()
        )
        if status in (401, 403):
            return False, [*lines, f"credentials rejected (HTTP {status})"]
        if status != 200:
            detail = self._redact(body[:200].decode("utf-8", "replace"))
            return False, [*lines, f"database lookup failed (HTTP {status}): {detail}"]
        series = (json.loads(body).get("results") or [{}])[0].get("series") or [{}]
        names = {row[0] for row in series[0].get("values") or []}
        if self.config.database in names:
            return True, [*lines, f"database '{self.config.database}' found"]
        return False, [*lines, f"database '{self.config.database}' not found"]


BATCH_SIZE = 5000


class WriterProtocol(Protocol):
    """Interface for a writer that mirror uses. Lets tests inject fake writers."""

    def target_key(self) -> str:
        """Stable identity of this target for mirror bookkeeping."""
        ...

    def write(self, lines: Sequence[str]) -> None:
        """POST one gzipped batch of line protocol; raise unless acknowledged."""
        ...


def mirror(store: Store, writer: WriterProtocol, batch_size: int = BATCH_SIZE) -> int:
    """Send every cached row not yet mirrored to the writer's target.

    Implements :need:`REQ_MIRROR_RESUME`: the stored position advances only
    after a batch is acknowledged, so it never moves past a row InfluxDB has
    not accepted, and a failure leaves earlier batches recorded. Implements
    :need:`REQ_MIRROR_IDEMPOTENT`: a row always becomes the same point (same
    tags, same timestamp), which InfluxDB overwrites rather than duplicates,
    so re-sending is always safe. That is why the ``tracker_name`` tag is only
    ever the stored name: a batch holding a row whose tracker has no stored
    name is not sent at all (the mirror stops before it with a
    `TrackiwiError`), because a tag made up here would later split that
    tracker into two series. `ingest` stores a name for every cached tracker
    (:need:`REQ_MIRROR_FALLBACK_NAME`), so this only happens before the first
    name refresh that succeeds.
    Returns the number of points sent; the first failed batch re-raises its
    `TrackiwiError`.
    """
    target = writer.target_key()
    last = store.mirror_position(target)
    names = store.tracker_names()
    sent = 0
    while True:
        rows = store.rows_after(last, batch_size)
        if not rows:
            return sent
        for row in rows:
            tracker_id = int(row["tracker_id"])
            if not names.get(tracker_id):
                raise TrackiwiError(
                    f"no name known for tracker {tracker_id} — run `trackiwi ingest` "
                    "while trackiwi is reachable"
                )
        lines = [format_point(row, names[int(row["tracker_id"])]) for row in rows]
        writer.write(lines)
        last = int(rows[-1]["id"])
        store.set_mirror_position(target, last)
        sent += len(rows)
