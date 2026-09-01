"""The one call site in this package that reaches the network.

`download_and_convert` used to be a bare `requests.get`, which the library
cannot ship: `pyproject.toml` declares no runtime dependencies. It now goes
through `_http`, and these tests pin the two consequences of that — the transport
seam exists and is honoured, and the suite-wide network guard therefore reaches
this call site the same way it reaches the sports clients.

Neither test touches Pillow: the transport is consulted before `png_to_tim`, so
both finish before the optional dependency is ever needed.
"""

import pytest

from retro_roster_patcher.games.we2002.tim_generator import TimGenerator

from ...conftest import TransportLeak


class _Fetched(Exception):
    """Sentinel proving the supplied transport, not the default one, was called."""


def test_the_network_guard_reaches_this_call_site(tmp_path):
    # No `transport=`, so the method falls back to `_http.default_transport` —
    # which the autouse guard in `tests/conftest.py` has replaced.
    with pytest.raises(TransportLeak):
        TimGenerator().download_and_convert("http://example.invalid/logo.png", (64, 64))


def test_a_supplied_transport_receives_the_url_and_the_fifteen_second_timeout():
    calls = []

    def transport(url, headers, timeout):
        calls.append((url, dict(headers), timeout))
        raise _Fetched

    with pytest.raises(_Fetched):
        TimGenerator().download_and_convert(
            "https://example.invalid/crest.png", (32, 32), transport=transport
        )

    assert calls == [("https://example.invalid/crest.png", {}, 15.0)]
