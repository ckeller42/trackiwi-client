"""Write cached positions to InfluxDB.

The only module that talks to InfluxDB. It never imports `client.py`, and
`client.py` never imports it. Configuration lives in
``~/.config/trackiwi/influx.toml`` and every key can be overridden by an
environment variable named ``TRACKIWI_INFLUX_<KEY>``. Secrets are never
stored in the TOML file: the token comes from the variable named by
``token_env`` or from ``token_file``.
"""

from __future__ import annotations

import contextlib
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import TrackiwiError

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
