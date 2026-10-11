"""Write cached positions to InfluxDB 2.x.

The only module that talks to InfluxDB. It never imports `client.py`, and
`client.py` never imports it. Configuration lives in
``~/.config/trackiwi/influx.toml`` and every key can be overridden by an
environment variable named ``TRACKIWI_INFLUX_<KEY>``. The token is never
stored in the TOML file: it comes from the variable named by ``token_env``
(default ``TRACKIWI_INFLUX_TOKEN``). InfluxDB 1.x is not supported; git holds
the 1.x path if it is ever needed again.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
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
_KEYS = ("url", "org", "bucket", "token_env")


def default_influx_config_path() -> Path:
    """Return the InfluxDB config path, honouring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "trackiwi" / "influx.toml"


@dataclass(frozen=True)
class InfluxConfig:
    """A resolved InfluxDB 2.x target."""

    url: str
    org: str | None
    bucket: str | None
    token: str | None
    token_env: str


def _read_file(path: Path) -> dict[str, Any]:
    """Read the TOML file, narrowing a widened mode first (REQ_CONFIG_MODE_0600)."""
    if not path.exists():
        return {}
    # Narrow a widened mode, as for the session file; a chmod failure is ignored.
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
                "the token belongs in an environment variable, not this file"
            )
    return data


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
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        # Without this, a scheme-less value reaches Request() as a raw
        # ValueError outside the request's try (#19).
        raise TrackiwiError(f"InfluxDB 'url' must be an http(s) URL with a host, got {url!r}")
    if parsed.scheme == "http" and (parsed.hostname or "") not in _LOCAL_HOSTS:
        # http to a non-local host sends the token and history in the clear (#26).
        print(
            f"warning: InfluxDB url {url!r} is plain http to a non-local host; "
            "the token and position history travel unencrypted — use https",
            file=sys.stderr,
        )
    token_env = str(merged.get("token_env") or DEFAULT_TOKEN_ENV)
    token = env.get(token_env) or None

    def text(key: str) -> str | None:
        value = merged.get(key)
        return str(value) if value not in (None, "") else None

    return InfluxConfig(
        url=url,
        org=text("org"),
        bucket=text("bucket"),
        token=token.strip() if token else None,
        token_env=token_env,
    )


WRITE_TIMEOUT = 60
_RETENTION_DROP = b"beyond retention policy"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "influxdb"})


def _as_object(body: bytes) -> dict[str, Any] | None:
    """Parse a response body as a JSON object, or None if it is neither.

    `check()` uses this to report an unexpected 200 body (a captive portal's
    HTML, a JSON list) rather than crash on it (#19).
    """
    try:
        data = json.loads(body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


class InfluxWriter:
    """Talks to one InfluxDB 2.x target: write and check.

    Implements :need:`REQ_INFLUX_WRITE_SCOPE` and
    :need:`REQ_INFLUX_TOKEN_REDACT`. Redirects are never followed, so a 3xx
    is an error, not an acknowledgement (:need:`REQ_MIRROR_RESUME`).
    `opener` lets tests inject a fake transport.
    """

    def __init__(self, config: InfluxConfig, opener: Callable[..., Any] | None = None) -> None:
        self.config = config
        self.opener = opener or _http.no_redirect_opener(proxy=False).open
        self._secrets = (config.token,)

    # -- transport -----------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """The ``Token`` authorization header, when a token is configured."""
        if self.config.token:
            return {"Authorization": f"Token {self.config.token}"}
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

        A 3xx raises (see `_http.no_redirect_opener`). Every transport failure
        becomes a `TrackiwiError`.
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
            raise TrackiwiError(_http.redact(str(error), *self._secrets)) from error
        status, response_headers, _ = result
        if 300 <= status < 400:
            location = _http.redact(
                response_headers.get("Location") or "(no Location)", *self._secrets
            )
            raise TrackiwiError(
                f"InfluxDB at {self.config.url} answered HTTP {status}, a redirect to "
                f"{location}; redirects are not followed — set url to the final address"
            )
        return result

    def target_key(self) -> str:
        """Stable identity of this target for mirror bookkeeping; no secrets."""
        return f"{self.config.url}|v2|{self.config.org}/{self.config.bucket}"

    # -- write ---------------------------------------------------------------

    def _write_endpoint(self) -> tuple[str, dict[str, str]]:
        """Path and query for a write, validating the settings it needs."""
        cfg = self.config
        if not cfg.token:
            raise TrackiwiError(f"InfluxDB needs a token: set ${cfg.token_env}")
        if not cfg.org or not cfg.bucket:
            raise TrackiwiError("InfluxDB needs 'org' and 'bucket' configured")
        return "/api/v2/write", {"org": cfg.org, "bucket": cfg.bucket, "precision": "s"}

    def _target_name(self) -> str:
        """Human name of the write destination, for error messages."""
        return f"bucket '{self.config.bucket}' in org '{self.config.org}'"

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
        detail = _http.redact(response.decode("utf-8", "replace"), *self._secrets)[:200]
        if 400 <= status < 500 and _RETENTION_DROP in response:
            # Partial write: the dropped points will never be accepted, so
            # count the batch as acknowledged rather than stall the mirror.
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
        """Probe connectivity, auth and the bucket. Read-only: writes nothing."""
        lines = [f"InfluxDB 2.x at {self.config.url}"]
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
            detail = _http.redact(body.decode("utf-8", "replace"), *self._secrets)[:200]
            return False, [*lines, f"bucket lookup failed (HTTP {status}): {detail}"]
        obj = _as_object(body)
        if obj is None:
            return False, [*lines, "bucket lookup returned an unexpected response"]
        found = obj.get("buckets") or []
        if any(isinstance(b, dict) and b.get("name") == self.config.bucket for b in found):
            return True, [*lines, f"bucket '{self.config.bucket}' found"]
        return False, [*lines, f"bucket '{self.config.bucket}' not found"]


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
    after a batch is acknowledged. Implements :need:`REQ_MIRROR_IDEMPOTENT`:
    a row always becomes the same point, so re-sending is safe; hence
    ``tracker_name`` is only the stored name, and a batch with an unnamed
    tracker raises `TrackiwiError` before sending.
    Returns the number of points sent; the first failed batch re-raises.
    """
    target = writer.target_key()
    last = store.mirror_position(target)
    names = store.tracker_names()
    sent = 0
    while True:
        rows = store.rows_after(last, batch_size)
        if not rows:
            return sent
        lines = []
        for row in rows:
            tracker_id = int(row["tracker_id"])
            if not names.get(tracker_id):
                raise TrackiwiError(
                    f"no name known for tracker {tracker_id} — run `trackiwi ingest` "
                    "while trackiwi is reachable"
                )
            try:
                lines.append(format_point(row, names[tracker_id]))
            except ValueError as exc:
                # A legacy row with a non-finite value must not wedge the mirror
                # (#22, REQ_MALFORMED_SKIP): skip and count it, keep advancing.
                print(
                    f"warning: skipped malformed cached row {int(row['id'])}: {exc}",
                    file=sys.stderr,
                )
        if lines:
            writer.write(lines)
        last = int(rows[-1]["id"])
        store.set_mirror_position(target, last)
        sent += len(lines)
