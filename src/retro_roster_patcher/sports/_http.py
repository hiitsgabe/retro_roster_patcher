"""The slice of HTTP the sports clients need, on the stdlib.

Zero runtime dependencies is a hard requirement — neither target platform has a
reliable pip — so stdlib `urllib` only, never `requests`.

Every client accepts a `transport` and passes it down, which is what lets the
test suite replay recorded JSON fixtures offline instead of hitting the network.
"""

from __future__ import annotations

import http.client
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from ..core.errors import ApiError

DEFAULT_TIMEOUT = 30.0

# api-web.nhle.com 403s on urllib's default `Python-urllib/3.x` token, so this
# must not be left unset. No version number in it, deliberately: importing
# `__version__` here risks a partially-initialised package.
DEFAULT_USER_AGENT = "retro-roster-patcher (+https://github.com/hiitsgabe/retro_roster_patcher)"

# How much of an unusable response body to quote back in an error message.
_BODY_SNIPPET = 200

# (url, headers, timeout) -> raw response body
Transport = Callable[[str, Mapping[str, str], float], bytes]


def _describe(body: object) -> str:
    """Size and leading content of an unusable body, for an error message.

    Must tolerate a non-bytes body: an error path may not raise its own error.
    """
    if isinstance(body, bytes | bytearray | str):
        unit = "characters" if isinstance(body, str) else "bytes"
        return f"{len(body)} {unit} starting {body[:_BODY_SNIPPET]!r}"
    return f"a {type(body).__name__}: {_truncate(repr(body))}"


def _truncate(text: str) -> str:
    return text if len(text) <= _BODY_SNIPPET else f"{text[:_BODY_SNIPPET]}..."


def _with_default_user_agent(headers: Mapping[str, str]) -> dict[str, str]:
    """Caller-supplied headers win, so a client can override the default UA."""
    merged = {"User-Agent": DEFAULT_USER_AGENT}
    for key, value in headers.items():
        if key.lower() == "user-agent":
            merged.pop("User-Agent", None)
        merged[key] = value
    return merged


def _urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=_with_default_user_agent(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as exc:
        # An HTTPError *is* the response; read its body before it propagates or
        # the only artifact left is the status line.
        raise ApiError(f"HTTP {exc.code} {exc.reason} from {url}: {_describe(exc.read())}") from exc


# Idle keep-alive connections by (scheme, host, port). A league fetch makes
# hundreds of requests to one host; a fresh TLS handshake for each is most of
# its wall time.
_MAX_IDLE_PER_HOST = 16
_idle: dict[tuple[str, str, int | None], list[http.client.HTTPConnection]] = {}
_idle_lock = threading.Lock()


def _checkout(key: tuple[str, str, int | None], timeout: float) -> http.client.HTTPConnection:
    with _idle_lock:
        idle = _idle.get(key)
        conn = idle.pop() if idle else None
    scheme, host, port = key
    cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    if conn is not None:
        conn.timeout = timeout
        try:
            if conn.sock is not None:  # a live socket keeps the timeout it opened with
                conn.sock.settimeout(timeout)
            return conn
        except OSError:  # the socket died while idle
            conn.close()
    return cls(host, port, timeout=timeout)


def _checkin(key: tuple[str, str, int | None], conn: http.client.HTTPConnection) -> None:
    with _idle_lock:
        idle = _idle.setdefault(key, [])
        if len(idle) < _MAX_IDLE_PER_HOST:
            idle.append(conn)
            return
    conn.close()


def _pooled_transport(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    """GET over a reused connection; `_urllib_transport` for anything it can't do.

    The fallback is deliberately wide: a proxy configured in the environment, a
    redirect, or any connection-level failure (a keep-alive socket the server
    closed while idle looks exactly like one) is retried by urllib, which does
    all of that. Only an HTTP status of 400 or above is final here, because it
    is an answer, not a transport fault.
    """
    parts = urllib.parse.urlsplit(url)
    scheme, host = parts.scheme, parts.hostname
    if scheme not in ("http", "https") or not host:
        return _urllib_transport(url, headers, timeout)
    if urllib.request.getproxies().get(scheme) and not urllib.request.proxy_bypass(host):
        return _urllib_transport(url, headers, timeout)

    key = (scheme, host, parts.port)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    conn = _checkout(key, timeout)
    try:
        conn.request("GET", target, headers=_with_default_user_agent(headers))
        response = conn.getresponse()
        body = response.read()
    except TimeoutError:
        conn.close()
        raise  # a hung server, not a stale socket: retrying would double the wait
    except (OSError, http.client.HTTPException):
        conn.close()
        return _urllib_transport(url, headers, timeout)

    if response.will_close:
        conn.close()
    else:
        _checkin(key, conn)
    if 300 <= response.status < 400:
        return _urllib_transport(url, headers, timeout)
    if response.status >= 400:
        raise ApiError(f"HTTP {response.status} {response.reason} from {url}: {_describe(body)}")
    return body


default_transport: Transport = _pooled_transport


def get_json(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    transport: Transport | None = None,
) -> Any:
    """GET a URL and parse the response as JSON.

    Raises `ApiError` on any transport failure or unparseable body.

    Error messages quote the full URL, query string included. Redact here before
    adding any provider that passes a credential as a query parameter.
    """
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        if query:
            # Assumes a fragment-free URL.
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{query}"

    tx = transport or default_transport
    try:
        body = tx(url, headers or {}, timeout)
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(f"GET {url} failed: {exc}") from exc

    try:
        return json.loads(body)
    except (ValueError, TypeError) as exc:
        # Quote the body: an empty 200, an HTML interstitial and a captive
        # portal all produce the same parser message.
        raise ApiError(f"Malformed JSON from {url}: {exc} (body was {_describe(body)})") from exc
