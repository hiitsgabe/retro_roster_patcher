"""PSX TIM format image generator for WE2002 team flags/emblems."""

import os
import struct
import tempfile

from ...core.errors import ApiError
from ...sports import _http

try:
    from PIL import Image

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# A TIM pixel block declares its width in 16-bit units, so the number of pixels
# a unit holds is the depth's divisor: four at 4bpp, two at 8bpp. `png_to_tim`
# refuses any other depth before it gets this far.
_TIM_PIXELS_PER_WORD = {4: 4, 8: 2}


def _tim_row_words(width: int, bpp: int) -> int:
    """Pixel-block width for `width` screen pixels, in 16-bit units.

    Refuses a width that is not a whole number of units. At width 66 and 4bpp
    the declared stride would be 16 units — 32 bytes — while the packed row is
    really 33, so every row after the first is shifted by one byte and the image
    shears. The block-length field is computed from the packed bytes rather than
    from this number, so it stays self-consistent and a size check on the
    finished TIM does not catch it. This is the only check that does.
    """
    per_word = _TIM_PIXELS_PER_WORD[bpp]
    if width % per_word:
        raise ValueError(
            f"width {width} is not a multiple of {per_word}, which a {bpp}bpp TIM row requires"
        )
    return width // per_word


class TimGenerator:
    """Converts images to PSX TIM format.

    Nothing in `src/` outside this module refers to `TimGenerator`, so no code
    path in the library reaches `png_to_tim` today; `download_and_convert` is
    its only caller and has no caller of its own. It is kept for a consumer that
    wants team crests, and its network seam is covered because that seam is
    shared with the sports clients.
    """

    TIM_MAGIC = b"\x10\x00\x00\x00"

    def png_to_tim(self, png_path: str, width: int, height: int, bpp: int = 4) -> bytes:
        """Convert a PNG/image file to PSX TIM format."""
        if not PIL_AVAILABLE:
            raise ImportError(
                "Pillow is required for TIM generation. Install with: pip install Pillow"
            )

        if bpp == 4:
            num_colors = 16
        elif bpp == 8:
            num_colors = 256
        else:
            raise ValueError(f"Unsupported bpp: {bpp}. Use 4 or 8.")

        # Refused before the download is decoded and resized, not after: a width
        # the pixel block cannot describe produces a sheared image that no later
        # check would reject.
        tim_pixel_width = _tim_row_words(width, bpp)

        img = Image.open(png_path).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)

        # Quantize to palette
        img_quantized = img.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
        # A flat RGB list holding only the entries the quantiser actually used —
        # three ints per colour, and NOT a fixed 768-byte table. An image
        # quantised to 16 colours that holds five distinct ones comes back with
        # 15 ints, so this can be shorter than `num_colors * 3`.
        palette = img_quantized.getpalette()
        pixel_data_raw = list(img_quantized.getdata())

        # Build CLUT (Color Look-Up Table) in BGR555 format
        clut_colors = self._build_clut(palette, num_colors)
        clut_data = struct.pack(f"<{num_colors}H", *clut_colors)

        # CLUT block: size(4) + x(2) + y(2) + w(2) + h(2) + data
        clut_block_len = 12 + len(clut_data)  # header fields + data
        clut_block = struct.pack("<IHHHH", clut_block_len, 0, 0, num_colors, 1) + clut_data

        # Pack pixel data
        if bpp == 4:
            # 2 pixels per byte, low nibble first
            packed = []
            for i in range(0, len(pixel_data_raw), 2):
                lo = pixel_data_raw[i] & 0xF
                hi = (pixel_data_raw[i + 1] & 0xF) if i + 1 < len(pixel_data_raw) else 0
                packed.append(lo | (hi << 4))
            pixel_bytes = bytes(packed)
        else:  # bpp == 8
            pixel_bytes = bytes(pixel_data_raw)

        # Pixel block: size(4) + x(2) + y(2) + w(2) + h(2) + data
        pixel_block_len = 12 + len(pixel_bytes)
        pixel_block = (
            struct.pack("<IHHHH", pixel_block_len, 0, 0, tim_pixel_width, height) + pixel_bytes
        )

        # TIM header: magic(4) + flags(4)
        # flags: bpp_mode bits 0-1: 0=4bpp, 1=8bpp; bit 3: has CLUT
        bpp_flag = 0 if bpp == 4 else 1
        flags = bpp_flag | (1 << 3)  # bit 3 = has CLUT
        header = self.TIM_MAGIC + struct.pack("<I", flags)

        return header + clut_block + pixel_block

    def download_and_convert(
        self,
        logo_url: str,
        output_size: tuple,
        bpp: int = 4,
        *,
        transport: _http.Transport | None = None,
    ) -> bytes:
        """Download a team logo image from URL and convert to TIM format.

        Goes to the transport rather than `_http.get_json`, because a logo is
        binary image bytes and `get_json` would try to parse them as JSON and
        raise. The transport is the same seam the sports clients take, so the
        suite-wide `forbid_default_transport` guard covers this call site too.

        It is the only call site outside `_http` itself that invokes a transport
        directly, so it repeats the normalisation `get_json` does: every
        network failure leaves here as an `ApiError`. A failing HTTP status
        already arrives as one from `_urllib_transport`, which is why nothing
        re-checks the status, but a connection that never completes does not —
        a refused port raises a bare `urllib.error.URLError` — and callers should
        not have to catch two unrelated exception types for the same event.
        """
        tx = transport or _http.default_transport
        try:
            content = tx(logo_url, {}, 15.0)
        except ApiError:
            raise
        except Exception as exc:
            raise ApiError(f"GET {logo_url} failed: {exc}") from exc

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            return self.png_to_tim(tmp_path, output_size[0], output_size[1], bpp)
        finally:
            os.unlink(tmp_path)

    def _build_clut(self, palette: list[int], num_colors: int) -> list[int]:
        """`num_colors` BGR555 entries, from however many the quantiser supplied.

        `Image.getpalette()` returns only the entries in use, so `palette` may
        hold fewer than `num_colors * 3` ints; reading a fixed `num_colors`
        entries out of it raised `IndexError` for any image with fewer distinct
        colours than the depth allows. Short palettes are padded with black,
        which is what an unused CLUT slot would have shown anyway, and a longer
        one is cut — `struct.pack` is given exactly `num_colors` entries either
        way.
        """
        supplied = min(len(palette) // 3, num_colors)
        clut = [
            self._rgb_to_bgr555(palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2])
            for i in range(supplied)
        ]
        clut.extend([0] * (num_colors - supplied))
        return clut

    def _rgb_to_bgr555(self, r: int, g: int, b: int) -> int:
        """Convert 8-bit RGB to 15-bit BGR555 (PSX color format)."""
        # Bit layout: STP(1) Blue(5) Green(5) Red(5)
        return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
