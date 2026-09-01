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


class TimGenerator:
    """Converts images to PSX TIM format."""

    TIM_MAGIC = b"\x10\x00\x00\x00"

    def png_to_tim(self, png_path: str, width: int, height: int, bpp: int = 4) -> bytes:
        """Convert a PNG/image file to PSX TIM format."""
        if not PIL_AVAILABLE:
            raise ImportError(
                "Pillow is required for TIM generation. Install with: pip install Pillow"
            )

        img = Image.open(png_path).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)

        if bpp == 4:
            num_colors = 16
        elif bpp == 8:
            num_colors = 256
        else:
            raise ValueError(f"Unsupported bpp: {bpp}. Use 4 or 8.")

        # Quantize to palette
        img_quantized = img.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
        palette = img_quantized.getpalette()  # flat RGB list, 768 bytes (256*3)
        pixel_data_raw = list(img_quantized.getdata())

        # Build CLUT (Color Look-Up Table) in BGR555 format
        clut_colors = []
        for i in range(num_colors):
            r = palette[i * 3]
            g = palette[i * 3 + 1]
            b = palette[i * 3 + 2]
            clut_colors.append(self._rgb_to_bgr555(r, g, b))
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
            # width in TIM pixel block = width/4 for 4bpp (width stored in 16-bit units)
            tim_pixel_width = width // 4
        else:  # bpp == 8
            pixel_bytes = bytes(pixel_data_raw)
            tim_pixel_width = width // 2

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

    def _rgb_to_bgr555(self, r: int, g: int, b: int) -> int:
        """Convert 8-bit RGB to 15-bit BGR555 (PSX color format)."""
        # Bit layout: STP(1) Blue(5) Green(5) Red(5)
        return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)

    def _build_tim_header(self, bpp: int, has_clut: bool) -> bytes:
        """Build the 8-byte TIM file header."""
        bpp_flag = {4: 0, 8: 1, 16: 2, 24: 3}.get(bpp, 0)
        flags = bpp_flag | ((1 << 3) if has_clut else 0)
        return self.TIM_MAGIC + struct.pack("<I", flags)
