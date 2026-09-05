"""The slice of HTTP the sports clients need, on the stdlib.

Zero runtime dependencies is a hard requirement — neither target platform has a
reliable pip — so stdlib `urllib` only, never `requests`.

Every client accepts a `transport` and passes it down, which is what lets the
test suite replay recorded JSON fixtures offline instead of hitting the network.
"""

from __future__ import annotations

import json
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


default_transport: Transport = _urllib_transport


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
