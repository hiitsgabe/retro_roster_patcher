"""The slice of HTTP the sports clients need, on the stdlib.

The library declares zero runtime dependencies because neither target platform
has a reliable pip: `console_utilities` ships as a `.pygame` zip on Batocera, and
`retro_toolbox` embeds CPython through `serious_python`. So no `requests`.

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

# urllib announces itself as `Python-urllib/3.x`, where `requests` sent
# `python-requests/2.x`. api-web.nhle.com blocks on the `Python-urllib` token
# specifically — 403 for it, 200 for anything else — and because the clients
# swallow ApiError in favour of an empty result, that would surface as every NHL
# call quietly returning nothing. Deliberately carries no version number: the
# package version already lives in two places, and importing `__version__` here
# would risk a partially-initialised package once the root re-exports games.
DEFAULT_USER_AGENT = "retro-roster-patcher (+https://github.com/hiitsgabe/retro_roster_patcher)"

# How much of an unusable response body to quote back in an error message.
_BODY_SNIPPET = 200

# (url, headers, timeout) -> raw response body
Transport = Callable[[str, Mapping[str, str], float], bytes]


def _describe(body: object) -> str:
    """Size and leading content of an unusable body, for an error message.

    Tolerates a non-bytes body: `json.loads` raises `TypeError` for one, and an
    error path must not raise an error of its own. That branch is bounded too —
    it is where a transport returning already-parsed JSON lands, so its input is
    typically a whole decoded fixture rather than a short scrap.
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
        # An HTTPError *is* the response, so it carries the body — which is where
        # these providers put their quota, plan, and auth explanations. Read it
        # before it propagates or the only artifact left is the status line.
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

    Raises `ApiError` on any transport failure or unparseable body. Callers that
    prefer an empty result to an exception catch it themselves — the ported
    clients all do, preserving their previous behaviour exactly.

    Error messages quote the full URL, query string included. That is safe while
    every provider authenticates by header (API-Football sends `x-apisports-key`
    that way); a provider that takes its key as a `?api_key=` param would leak it
    into any log that catches the `ApiError`, so redact here if one ever does.
    """
    if params:
        query = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        if query:
            # Assumes a fragment-free URL, which is all any client builds.
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
        # An empty 200, an HTML interstitial, and a captive portal all produce the
        # same parser message, and the clients swallow the exception, so quote the
        # body: it is the only artifact anyone will have.
        raise ApiError(f"Malformed JSON from {url}: {exc} (body was {_describe(body)})") from exc
