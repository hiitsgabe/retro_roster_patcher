"""The one call site in this package that reaches the network.

`download_and_convert` used to be a bare `requests.get`, which the library cannot
ship: `pyproject.toml` declares no runtime dependencies. It now goes through
`_http`, so the transport seam exists and is honoured, the suite-wide network
guard reaches this call site the same way it reaches the sports clients, and a
transport failure leaves as an `ApiError` rather than a raw `urllib` exception.

No test here touches Pillow, which is an optional extra and is not installed in
the dev venv. The tests that would need it either finish inside the transport or
substitute `png_to_tim`.

The rest reach the two pieces of `png_to_tim` that decide the output but take no
image: `_tim_row_words`, which refuses a width the TIM pixel block cannot
describe, and `_build_clut`, which fits however many palette entries the
quantiser supplied into the fixed number the header declares. Both were pulled
out of `png_to_tim` so they could be reached without the optional extra.
"""

import os
import pathlib
import tempfile

import pytest

from retro_roster_patcher.core.errors import ApiError
from retro_roster_patcher.games.we2002.tim_generator import (
    _TIM_PIXELS_PER_WORD,
    TimGenerator,
    _tim_row_words,
)

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


def test_the_row_width_is_the_pixel_count_divided_by_the_pixels_per_word():
    # A TIM pixel block declares its width in 16-bit units, so a 4bpp row packs
    # four pixels per unit and an 8bpp row two. 64 is chosen because the two
    # answers differ — 16 and 32 — so a helper that ignored `bpp` would fail one.
    assert _tim_row_words(64, 4) == 16
    assert _tim_row_words(64, 8) == 32


def test_a_width_that_is_not_a_whole_number_of_units_is_refused():
    # At width 66 and 4bpp the declared stride is 16 units, 32 bytes, while the
    # real packed row is 33 bytes, so every row after the first is shifted by
    # one byte and the image shears. The block-length field stays
    # self-consistent, so a size assertion on the output does not catch it.
    with pytest.raises(ValueError, match="multiple of 4"):
        _tim_row_words(66, 4)
    with pytest.raises(ValueError, match="multiple of 2"):
        _tim_row_words(65, 8)


def test_the_width_rule_depends_on_the_depth_and_not_only_on_the_width():
    # 66 is refused at 4bpp and accepted at 8bpp, which is what proves the check
    # reads `bpp` rather than applying one divisor to everything.
    assert _tim_row_words(66, 8) == 33
    with pytest.raises(ValueError):
        _tim_row_words(66, 4)


def test_the_supported_depths_are_exactly_four_and_eight_bits():
    # `png_to_tim` refuses anything else before it reaches `_tim_row_words`, and
    # this is the table both of them agree on.
    assert sorted(_TIM_PIXELS_PER_WORD) == [4, 8]
    assert _TIM_PIXELS_PER_WORD == {4: 4, 8: 2}


def test_a_palette_shorter_than_the_clut_is_padded_rather_than_overrun():
    # `Image.getpalette()` returns only the entries the quantiser used, not a
    # fixed 768-byte table, so an image quantised to 16 colours that holds five
    # distinct ones comes back with 15 ints. Indexing 16 entries out of that
    # raised `IndexError` and lost the conversion.
    gen = TimGenerator()
    five_colours = [255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255, 8, 8, 8]

    clut = gen._build_clut(five_colours, 16)

    assert len(clut) == 16
    assert clut[:5] == [
        gen._rgb_to_bgr555(255, 0, 0),
        gen._rgb_to_bgr555(0, 255, 0),
        gen._rgb_to_bgr555(0, 0, 255),
        gen._rgb_to_bgr555(255, 255, 255),
        gen._rgb_to_bgr555(8, 8, 8),
    ]
    # The unused slots are black, which is what index 0 of an unused entry would
    # have shown anyway. Zero is not the value of any colour above.
    assert clut[5:] == [0] * 11


def test_a_palette_longer_than_the_clut_is_truncated_to_it(tmp_path):
    # The other direction: `num_colors` is what the header declares and what
    # `struct.pack` is given, so the list has to be exactly that long.
    gen = TimGenerator()
    long_palette = [1, 2, 3] * 256

    assert len(gen._build_clut(long_palette, 16)) == 16
    assert len(gen._build_clut(long_palette, 256)) == 256


def test_a_palette_of_exactly_the_right_length_is_neither_padded_nor_cut():
    # Every channel is at least 8 so no entry collapses to the black that
    # padding uses; a clut that had been cut short and refilled would show a
    # zero in the tail.
    gen = TimGenerator()
    exact = [8 + (i * 5) % 240 for i in range(16 * 3)]

    clut = gen._build_clut(exact, 16)

    assert clut == [gen._rgb_to_bgr555(*exact[i * 3 : i * 3 + 3]) for i in range(16)]
    assert (0 in clut) is False
