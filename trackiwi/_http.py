"""HTTP plumbing shared by `client.py` and `influx.py`.

Both transports import this module and neither imports the other. It holds
what they would otherwise each carry a copy of: the opener that refuses
redirects, the ``User-Agent``, and the conversion of urllib's exceptions.
Redaction lives here too, so both transports scrub the same way.
"""

from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

from . import TrackiwiError, __version__

USER_AGENT = f"trackiwi-client/{__version__} (+python-urllib)"

#: Anything that looks like an auth credential in server-controlled text.
_AUTH_RE = re.compile(r"\b(Token|Bearer|Basic)\s+\S+", re.IGNORECASE)


def redact(text: str, *secrets: str | None) -> str:
    """Strip auth credentials from text that is about to be shown.

    Implements :need:`REQ_TOKEN_NEVER_LOGGED`.

    Proxies and API gateways echo request headers into 4xx/5xx bodies, and the
    CLI prints those to stderr. Any ``Token``/``Bearer``/``Basic`` value is
    scrubbed, then each given secret wherever it appears on its own.
    """
    text = _AUTH_RE.sub(lambda m: f"{m.group(1)} <redacted>", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect, so a 3xx comes back as an `HTTPError`.

    urllib copies the ``Authorization`` header onto the redirected request —
    to any host and any scheme — and a followed redirect to a login page reads
    as a successful, empty answer.
    """

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Decline to build the redirected request."""
        return None


def no_redirect_opener() -> urllib.request.OpenerDirector:
    """A fresh opener that never follows redirects (one per client or writer).

    Implements :need:`REQ_NO_REDIRECTS`.
    """
    return urllib.request.build_opener(_NoRedirect)


def read_error_body(error: urllib.error.HTTPError) -> bytes:
    """Read an `HTTPError`'s body, tolerating a transport failure mid-read.

    The status and headers are already known once urllib raises `HTTPError`;
    letting a raw `TimeoutError`/`ConnectionResetError`/
    `http.client.IncompleteRead` from `error.read()` escape would turn a
    status callers already map into an unmapped, unrecognisable error. An
    empty body still lets callers map the status; it is only the detail text
    that is lost.
    """
    try:
        return error.read()
    except (OSError, http.client.HTTPException):
        return b""


def send(
    opener: Callable[..., Any], request: urllib.request.Request, timeout: float, failure: str
) -> tuple[int, Message, bytes]:
    """Perform `request` and return ``(status, headers, body)``.

    An `HTTPError` is an answer, not a failure: it comes back as a tuple, a
    3xx included, for the caller to judge. `headers` keeps the
    case-insensitive lookup real HTTP headers have. Every transport failure —
    including a timeout or reset while reading the response, which urllib
    does not wrap in `URLError` — becomes a `TrackiwiError` whose message
    starts with `failure`.
    """
    try:
        with opener(request, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers or Message(), read_error_body(error)
    except urllib.error.URLError as error:
        raise TrackiwiError(f"{failure}: {error.reason}") from error
    except (OSError, http.client.HTTPException) as error:
        # Raised by getresponse()/read(): TimeoutError, ConnectionResetError,
        # RemoteDisconnected, IncompleteRead.
        raise TrackiwiError(f"{failure}: {str(error) or type(error).__name__}") from error
