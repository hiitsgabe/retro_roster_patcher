"""Hand-built SHPM files and NHL 07 portrait archives for tests."""

from __future__ import annotations

import struct

from retro_roster_patcher.formats import shpm
from retro_roster_patcher.formats.ea_tdb import BigfEntry, bigf_build, refpack_compress


def block(code: int, w: int, h: int, payload: bytes, *, last: bool, py: int = 0) -> bytes:
    size = 16 + len(payload)
    header = bytes([code]) + (0 if last else size).to_bytes(3, "little")
    return header + struct.pack("<6H", w, h, 0, 0, 0x2000, py) + payload


def shpm_file(*shapes: bytes, names: tuple[str, ...] = ()) -> bytes:
    """An SHPM file of the given shapes, with the directory and `Buy ERTS` pad.

    Shapes are named by `names`, else by their index.
    """
    pad = b"Buy ERTS" if len(shapes) % 2 else b""
    head = 16 + 8 * len(shapes) + len(pad)
    offsets, pos = [], head
    for shape in shapes:
        offsets.append(pos)
        pos += len(shape)
    out = b"SHPM" + struct.pack("<II", pos, len(shapes)) + b"G359"
    for i, offset in enumerate(offsets):
        name = names[i] if i < len(names) else str(i)
        out += f"{name:<4}".encode() + struct.pack("<I", offset)
    return out + pad + b"".join(shapes)


PALETTE = bytes([10, 20, 30, 255, 200, 100, 50, 255, 0, 0, 0, 0]) + bytes(4 * 253)


def eight_bit_shape(w: int = 16, h: int = 8, indices: bytes | None = None) -> bytes:
    """An 8-bit shape; by default index (x + y) % 3, three colours, one clear."""
    linear = (
        indices if indices is not None else bytes((x + y) % 3 for y in range(h) for x in range(w))
    )
    return block(shpm.PIX8, w, h, shpm.swizzle(linear, w, h, 8), last=False) + block(
        shpm.PAL32, 256, 1, PALETTE, last=True
    )


def thirty_two_bit_shape(w: int = 4, h: int = 4) -> bytes:
    linear = bytes(c for i in range(w * h) for c in (i * 10 % 256, (255 - i) % 256, 7, 255))
    return block(shpm.PIX32, w, h, shpm.swizzle(linear, w, h, 32), last=True)


def portrait_entry(art: int, msh: bytes) -> bytes:
    """One compressed `NNNN.big`: a stand-in movie and the portrait."""
    name = f"{art:04d}"
    inner = {f"{name}.apt": b"Apt Data:1.0" + bytes(20), f"{name}.msh": msh}
    return refpack_compress(bigf_build([BigfEntry(n, 0, 0) for n in inner], inner))


def portapt(entries: dict[int, bytes]) -> bytes:
    """A `PORTAPT.VIV` of the given `ART_` -> SHPM portraits."""
    contents = {f"{art:04d}.big": portrait_entry(art, msh) for art, msh in entries.items()}
    return bigf_build([BigfEntry(n, 0, 0) for n in contents], contents)
