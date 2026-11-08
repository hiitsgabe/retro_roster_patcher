"""The cartridge's own looks of its 100 nations, packaged, so a patch can find
or give back a nation's flag, kits and name over an image an earlier patch
wrote: `assets/iss2000_retail.zlib` holds, per team id 0-99, its flag's
texture (768 bytes, as `flags` stores it) and palette (32), both kit records
(512 each, `kits`) and its name cell (8 rows of 64 bytes: `names` draws a
name twice, and the cartridge's two copies are not always alike). A real-ROM
test pins every byte to the cartridge's.
"""

from __future__ import annotations

import hashlib
import zlib
from functools import cache

from ...core.assets import package_bytes

NATIONS = 100
TEX, PAL, KIT, NAME = 768, 32, 512, 512


@cache
def _data() -> bytes:
    return zlib.decompress(package_bytes("retro_roster_patcher.assets", "iss2000_retail.zlib"))


def flag(n: int) -> tuple[bytes, bytes]:
    """Nation `n`'s flag on the cartridge: (texture, palette)."""
    d = _data()
    pal = NATIONS * TEX + n * PAL
    return d[n * TEX : (n + 1) * TEX], d[pal : pal + PAL]


def kit(n: int, side: int) -> bytes:
    """Nation `n`'s first (side 0) or second kit record on the cartridge."""
    at = NATIONS * (TEX + PAL) + (2 * n + side) * KIT
    return _data()[at : at + KIT]


def name(n: int) -> bytes:
    """Nation `n`'s name cell on the cartridge."""
    at = NATIONS * (TEX + PAL + 2 * KIT) + n * NAME
    return _data()[at : at + NAME]


def fingerprint(*parts: bytes) -> str:
    return hashlib.sha1(b"".join(parts)).hexdigest()


@cache
def flag_prints() -> dict[str, int]:
    """{fingerprint of a flag's texture and palette: nation}."""
    return {fingerprint(*flag(n)): n for n in range(NATIONS)}


@cache
def kit_prints() -> dict[str, int]:
    """{fingerprint of both kit records: nation}."""
    return {fingerprint(kit(n, 0), kit(n, 1)): n for n in range(NATIONS)}
