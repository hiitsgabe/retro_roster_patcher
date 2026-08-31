"""The slice of HTTP the sports clients need, on the stdlib.

The library declares zero runtime dependencies because neither target platform
has a reliable pip: `console_utilities` ships as a `.pygame` zip on Batocera, and
`retro_toolbox` embeds CPython through `serious_python`. So no `requests`.

Every client accepts a `transport` and passes it down, which is what lets the
test suite replay recorded JSON fixtures offline instead of hitting the network.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from ..core.errors import ApiError

DEFAULT_TIMEOUT = 30.0

# (url, headers, timeout) -> raw response body
Transport = Callable[[str, Mapping[str, str], float], bytes]


def _urllib_transport(url: str, headers: Mapping[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.read()


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
    """
    if params:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if query:
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
        raise ApiError(f"Malformed JSON from {url}: {exc}") from exc
