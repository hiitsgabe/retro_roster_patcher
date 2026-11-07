"""Provider head shots, downloaded once into `cache_dir/headshots`, for every
game that draws or measures players' faces; and `measure_faces`, the pass that
measures a whole league's skin tones and hair colours from them."""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from ..sports import _http
from .concurrency import parallel_map

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


#: How far above the hair a portrait starts, as a share of its side: a row or
#: two of 48, as EA's NHL 94 photographs have it.
PORTRAIT_HEADROOM = 0.03
#: A portrait's side as a share of the subject's height: the head about as wide
#: as EA's NHL 94 photographs have it.
PORTRAIT_SCALE = 0.68
#: A head shot whose most saturated opaque pixel is below this is grey all
#: over: the NHL API's silhouette for a player without a photo (0; real ones 208+).
SILHOUETTE_SATURATION = 16


def portrait(png: bytes, side: int) -> Any:
    """A provider head shot cropped and scaled to a `side`x`side` RGBA portrait,
    or None for a grey one (the NHL API's silhouette for a player without a
    photo), which would draw as a blank head.

    A square centred on the head (the opaque columns in the top third of the
    subject, or the whole picture when it has no transparency), the hair a row
    or two from the top. Raises whatever Pillow raises for a picture it cannot
    read.
    """
    import io

    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = picture.getchannel("A")
    saturation = picture.convert("RGB").convert("HSV").getchannel("S")
    if not any(saturation.histogram(mask=alpha)[SILHOUETTE_SATURATION:]):
        return None
    left, top, right, bottom = alpha.getbbox() or (0, 0, *picture.size)
    head = alpha.crop((left, top, right, top + max(1, (bottom - top) // 3))).getbbox()
    centre = left + ((head[0] + head[2]) // 2 if head else (right - left) // 2)
    span = max(1, round((bottom - top) * PORTRAIT_SCALE))
    x0, y0 = centre - span // 2, top - round(span * PORTRAIT_HEADROOM)
    square = picture.crop((x0, y0, x0 + span, y0 + span))
    return square.resize((side, side), Image.Resampling.LANCZOS)


class Face(NamedTuple):
    """What a head shot says about a player: the face's ITA (`core.skin`), the
    hair colour (`core.hair`) and whether he has a beard (`core.beard`), each
    None when it could not be measured."""

    ita: float | None
    hair: str | None
    beard: bool | None = None


#: Head shots measured per status line: a whole league can run past the app's
#: stall watchdog (it kills a job that stays silent for 15 minutes) on a slow
#: connection.
MEASURE_CHUNK = 25


def measure_faces(
    cache_dir: Path | str,
    urls: Mapping[int, str],
    transport: _http.Transport | None = None,
    status: Callable[[str], None] | None = None,
) -> dict[int, Face]:
    """The `Face` of every player (by id) whose head shot at `urls[id]` shows
    a face or hair to measure. Each URL is loaded once (`load_headshot`),
    the loads overlapped in chunks of `MEASURE_CHUNK` with a status line per
    chunk. A photo that will not load, or that Pillow cannot read, gives no
    entry. Without Pillow, nothing is measured and `status` says so."""
    say = status or (lambda line: None)
    if not urls:
        return {}
    if importlib.util.find_spec("PIL") is None:
        say("Pillow is not installed; player looks not taken from head shots")
        return {}
    from .beard import beard_of
    from .hair import hair_colour_of
    from .skin import _frame, face_lab, ita_of

    def measure(url: str) -> Face:
        png = load_headshot(Path(cache_dir), url, transport)
        if png is None:
            return Face(None, None)
        try:
            frame = _frame(png)
            colour = face_lab(frame) if frame is not None else None
            if frame is None or colour is None:
                return Face(None, None)
            return Face(ita_of(colour), hair_colour_of(frame, colour), beard_of(frame, colour))
        except Exception:  # noqa: BLE001 - Pillow's errors (a decompression bomb among them) share no base
            return Face(None, None)

    say("Loading head shots...")
    unique = sorted(set(urls.values()))
    measured: dict[str, Face] = {}
    for at in range(0, len(unique), MEASURE_CHUNK):
        batch = unique[at : at + MEASURE_CHUNK]
        measured.update(zip(batch, parallel_map(measure, batch), strict=True))
        say(f"Head shots {min(at + MEASURE_CHUNK, len(unique))}/{len(unique)}...")
    faces = {pid: face for pid, url in urls.items() if (face := measured[url]) != Face(None, None)}
    say(f"Faces measured from head shots: {len(faces)} of {len(urls)} players")
    return faces
