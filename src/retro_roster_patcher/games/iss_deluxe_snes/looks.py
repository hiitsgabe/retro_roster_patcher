"""ISS Deluxe looks: each team's two looks and its keeper's, from head shots.

The cartridge draws a squad as ISS does (`iss_snes.looks`), with the same
skin ramps and hair colours. Traced in Yoshifanatic1's disassembly
(CODE_8BCFBB, CODE_98F869) and the USA cartridge:

* Each kit palette ($82:827A, $82:82D0; `kits.palette_at`) holds the team's
  own look: entry 1 the hair, 2-6 the five skin shades.
* The alternate look: `ALT_SKIN` ($82:837C) and `ALT_HAIR` ($82:83D2), a
  bank $89 record address per team (0 for the team's own), loaded over the
  second OBJ palette. A player whose skill record byte 6 has bit 4 set
  (`ALTERNATE`) is drawn in it; byte 6's low nibble is his hair style (0
  plain, 1-13 the cartridge's stars' own: Valderrama's curls and the like),
  which a patched player never keeps.
* The cartridge's shared records hold only some looks, so the patch writes
  one record per skin ramp and hair colour into bank $89's free space
  (`RECORDS`, $89:FA42) and the alternate can be any look.
* The keeper's record ($82:8326): entry 1 hair, 2-4 three skin shades.
* The close-ups (an own goal): `CLOSE_HAIR` ($88:FB00) and `CLOSE_SKIN`
  ($88:FBA8), the own and alternate look's records per team, picked from the
  cartridge's by the look.
* Team select's team photo: OBJ palettes 6 and 7 from per-team records
  (`PHOTO`, $82:FA5F; `PHOTO_KEEPER`, $82:F89D), entries 9-12 the skin and
  13 the hair, in photo shades of their own (`PHOTO_SKIN`, `PHOTO_HAIR`);
  some heads take the alternate look from one of twelve shared records
  (`PHOTO_ALT`, $82:F945), a byte pair per team at $82:F95F picks them. The
  kit colours of the photo are `kits.write`'s.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from typing import Any

from ...core.headshots import Face
from ..iss_snes import looks as iss
from ..iss_snes.looks import BROWN, DARK, DARKEST, EAST_ASIAN, HAIR, LIGHT, SKIN_RAMPS, TAN
from ..iss_snes.models import ISSLook
from ..iss_snes.rom_writer import _exact_bgr555
from . import kits

RGB = tuple[int, int, int]

BANK_89 = 0x48000
ALT_SKIN = 0x1037C  # $82:837C
ALT_HAIR = 0x103D2  # $82:83D2
CLOSE_HAIR = 0x47B00  # $88:FB00
CLOSE_SKIN = 0x47BA8  # $88:FBA8
PHOTO = kits.PHOTO
PHOTO_KEEPER = 0x1789D  # $82:F89D
PHOTO_ALT = 0x17945  # $82:F945
PHOTO_ALT_PICK = 0x1795F  # $82:F95F
PHOTO_ALTS = 12
ALTERNATE = 0x10
#: Bank $89's free run ($89:FA42, 1470 bytes): a record per skin ramp
#: (header 9 and five shades) then per hair colour (header 1 and one).
RECORDS = 0xFA42
SKIN_RECORD = {s: RECORDS + 12 * s for s in range(len(SKIN_RAMPS))}
HAIR_RECORD = {h: RECORDS + 12 * len(SKIN_RAMPS) + 4 * k for k, h in enumerate(HAIR)}
#: The keeper's three shades, of the five a ramp has (as ISS).
KEEPER_SHADES = (1, 3, 4)

#: The cartridge's close-up records nearest each look (by the teams that use them).
CLOSE_SKINS = {
    LIGHT: 0xAF26, TAN: 0xAF76, BROWN: 0xAF8A, DARK: 0xAF8A, DARKEST: 0xAF9E, EAST_ASIAN: 0xAF62,
}  # fmt: skip
CLOSE_HAIRS = {
    "black": 0xAE76, "dark_brown": 0xAE96, "brown": 0xAEA6, "red": 0xAEA6, "blond": 0xAEC6,
    "grey": 0xAF16,
}  # fmt: skip

_PALE = ((224, 216, 200), (208, 176, 152), (184, 136, 128), (144, 96, 96))
_OLIVE = ((224, 192, 144), (208, 152, 120), (184, 120, 96), (168, 88, 64))
_TAN = ((224, 152, 104), (208, 112, 88), (168, 88, 56), (144, 64, 40))
_DARK = ((200, 120, 88), (168, 88, 72), (136, 72, 64), (88, 56, 56))
#: The team photo's four skin shades (light to dark) per ramp, as the
#: cartridge's teams with that ramp have them.
PHOTO_SKIN = {LIGHT: _PALE, TAN: _TAN, BROWN: _TAN, DARK: _DARK, DARKEST: _DARK, EAST_ASIAN: _OLIVE}
PHOTO_HAIR: dict[str, RGB] = {
    "black": (56, 40, 32),
    "dark_brown": (96, 72, 24),
    "brown": (96, 72, 24),
    "red": (96, 72, 24),
    "blond": (176, 112, 64),
    "grey": (208, 208, 216),
}


def dress(team: Any, faces: Mapping[int, Face]) -> None:
    """`iss_snes.looks.dress` with any look as the alternate (`RECORDS`)."""
    iss.dress(team, faces, alt_skins=tuple(range(len(SKIN_RAMPS))), alt_hairs=tuple(HAIR))


def write_records(image: bytearray) -> None:
    """Every look's record in bank $89's free run, for the alternate pointers."""
    for skin, at in SKIN_RECORD.items():
        o = BANK_89 + at - 0x8000
        image[o : o + 12] = struct.pack("<H", 9) + _exact_bgr555(*SKIN_RAMPS[skin])
    for hair, at in HAIR_RECORD.items():
        o = BANK_89 + at - 0x8000
        image[o : o + 4] = struct.pack("<H", 1) + _exact_bgr555(HAIR[hair])


def _record(image: bytes | bytearray, table: int, team: int) -> int:
    return BANK_89 + (struct.unpack_from("<H", image, table + 2 * team)[0] & 0x7FFF)


def _photo_alt(image: bytes | bytearray, skin: int, hair: str) -> int:
    """The byte offset ($82:F95F's value) of the shared photo head record
    nearest the look: same skin and hair, else same skin, else the first."""
    want = _exact_bgr555(*PHOTO_SKIN[skin])
    hair_word = _exact_bgr555(PHOTO_HAIR[hair])
    same_skin = None
    for k in range(PHOTO_ALTS):
        at = _record(image, PHOTO_ALT, k) + 2
        if bytes(image[at : at + 8]) == want:
            if bytes(image[at + 8 : at + 10]) == hair_word:
                return 2 * k
            same_skin = 2 * k if same_skin is None else same_skin
    return same_skin if same_skin is not None else 0


def write(image: bytearray, team: int, look: ISSLook) -> None:
    """Put `team`'s looks in: its own in both kit palettes, the close-ups and
    the team photo; its alternate's pointers (0 for the team's own); its
    keeper's hair and three shades. `write_records` fills what the alternate
    pointers point at."""
    own = _exact_bgr555(HAIR[look.hair], *SKIN_RAMPS[look.skin])
    for kit in (0, 1):
        at = kits.palette_at(image, team, kit) + 4  # entry 1
        image[at : at + 12] = own
    alt_skin = look.skin if look.alt_skin is None else look.alt_skin
    alt_hair = look.hair if look.alt_hair is None else look.alt_hair
    struct.pack_into(
        "<H", image, ALT_SKIN + 2 * team, 0 if alt_skin == look.skin else SKIN_RECORD[alt_skin]
    )
    struct.pack_into(
        "<H", image, ALT_HAIR + 2 * team, 0 if alt_hair == look.hair else HAIR_RECORD[alt_hair]
    )
    ramp = SKIN_RAMPS[look.keeper_skin]
    at = _record(image, kits.KEEPER, team) + 2  # entry 1: the record starts there
    image[at : at + 8] = _exact_bgr555(HAIR[look.keeper_hair], *(ramp[i] for i in KEEPER_SHADES))
    struct.pack_into(
        "<2H", image, CLOSE_HAIR + 4 * team, CLOSE_HAIRS[look.hair], CLOSE_HAIRS[alt_hair]
    )
    struct.pack_into(
        "<2H", image, CLOSE_SKIN + 4 * team, CLOSE_SKINS[look.skin], CLOSE_SKINS[alt_skin]
    )
    head = _exact_bgr555(*PHOTO_SKIN[look.skin], PHOTO_HAIR[look.hair])
    for table in (PHOTO, PHOTO_KEEPER):
        at = _record(image, table, team) + 2 + 2 * 8  # entry 9
        image[at : at + 10] = head
    pick = _photo_alt(image, alt_skin, alt_hair)
    image[PHOTO_ALT_PICK + 2 * team : PHOTO_ALT_PICK + 2 * team + 2] = bytes((pick, pick))
