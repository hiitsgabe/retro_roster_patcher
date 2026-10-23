"""Provider head shots, downloaded once into `cache_dir/headshots`, for every
game that draws or measures players' faces."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..sports import _http

#: What a head shot body must start with to be kept: PNG or JPEG.
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")


def load_headshot(
    cache_dir: Path, url: str, transport: _http.Transport | None = None
) -> bytes | None:
    """The head shot at `url`, from `cache_dir/headshots` or downloaded once;
    None when it cannot be had.

    Only a PNG or JPEG body is kept, so an empty 200 or an HTML error page is
    refetched next time rather than cached as the player's photo. No failure
    here, cache I/O included, raises: a game falls back to its own drawing.
    """
    cache = Path(cache_dir) / "headshots" / f"{hashlib.sha1(url.encode()).hexdigest()}.png"
    try:
        cached = cache.read_bytes() if cache.is_file() else b""
    except OSError:
        cached = b""
    if cached.startswith(IMAGE_MAGIC):
        return cached
    try:
        content = (transport or _http.default_transport)(url, {}, 15.0)
    except Exception:  # noqa: BLE001 - a missing photo must not fail the patch
        return None
    if not content.startswith(IMAGE_MAGIC):
        return None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        partial = cache.with_suffix(".part")
        partial.write_bytes(content)
        partial.replace(cache)
    except OSError:
        pass
    return content
