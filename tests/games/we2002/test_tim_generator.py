"""The one call site in this package that reaches the network.

`download_and_convert` used to be a bare `requests.get`, which the library
cannot ship: `pyproject.toml` declares no runtime dependencies. It now goes
through `_http`, and these tests pin the consequences of that — the transport
seam exists and is honoured, the suite-wide network guard therefore reaches this
call site the same way it reaches the sports clients, and a transport failure
leaves as an `ApiError` rather than a raw `urllib` exception.

No test here touches Pillow, which is an optional extra and is not installed in
the dev venv. Four of them finish inside the transport, before `png_to_tim` is
ever reached; the fifth substitutes `png_to_tim` so it can exercise the tempfile
handling around it without needing the real converter.
"""

import os
import pathlib
import tempfile

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.games.we2002.tim_generator import TimGenerator

from ...conftest import TransportLeak


class _Fetched(BaseException):
    """Sentinel proving the supplied transport, not the default one, was called.

    Deliberately not an `Exception`, for the same reason `TransportLeak` is not:
    `download_and_convert` now normalises any `Exception` out of the transport
    into an `ApiError`, so an `Exception`-derived sentinel would arrive at the
    assertion below wrapped, and this test would be reading the wrapper rather
    than pinning the arguments the transport was handed.
    """


def test_the_network_guard_reaches_this_call_site():
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


def test_a_transport_failure_leaves_as_an_api_error():
    # `_urllib_transport` raises `ApiError` for a failing HTTP status, but a
    # connection that never completes raises a bare `urllib.error.URLError`.
    # `download_and_convert` normalises that the way `_http.get_json` does, so a
    # caller has one exception type to catch for every network failure.
    boom = OSError("connection refused")

    def transport(url, headers, timeout):
        raise boom

    with pytest.raises(ApiError) as excinfo:
        TimGenerator().download_and_convert(
            "https://example.invalid/crest.png", (32, 32), transport=transport
        )

    assert "https://example.invalid/crest.png" in str(excinfo.value)
    assert excinfo.value.__cause__ == boom


def test_an_api_error_from_the_transport_is_not_wrapped_a_second_time():
    original = ApiError("HTTP 404 Not Found")

    def transport(url, headers, timeout):
        raise original

    with pytest.raises(ApiError) as excinfo:
        TimGenerator().download_and_convert(
            "https://example.invalid/crest.png", (32, 32), transport=transport
        )

    assert excinfo.value == original
    assert excinfo.value.__cause__ is None


def test_the_downloaded_bytes_reach_png_to_tim_and_the_temp_file_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    seen = {}

    def fake_png_to_tim(path, width, height, bpp):
        seen["path"] = path
        seen["content"] = pathlib.Path(path).read_bytes()
        seen["args"] = (width, height, bpp)
        return b"TIM"

    gen = TimGenerator()
    monkeypatch.setattr(gen, "png_to_tim", fake_png_to_tim)
    result = gen.download_and_convert(
        "https://example.invalid/crest.png", (64, 32), transport=lambda u, h, t: b"\x89PNGbody"
    )
    assert result == b"TIM"
    assert seen["content"] == b"\x89PNGbody"
    assert seen["args"] == (64, 32, 4)
    assert seen["path"].endswith(".png") is True
    assert os.path.exists(seen["path"]) is False
    assert list(tmp_path.iterdir()) == []
