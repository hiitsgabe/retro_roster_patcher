"""Each player's look in NBA Live 95: the skin byte (0x06) and the head byte (0x07).

The skin byte colours the court sprite's skin. On the retail cartridge 1 is the
darkest (171 of 324 players: Mutombo, Parish), 0 brown (86: Pippen, Reggie
Miller, Cassell), 2 and 3 pale (34: Kukoc, Divac, Majerle; 33: Stockton,
Mullin, Kerr). The head byte picks the head sprite, hair and beard drawn in
that skin (Team-95/FaceRacePicker shows all 39): 0x00 shaved, 0x03 shaved with
a beard, 0x05 short dark hair (the commonest), 0x0D dark hair and a beard, and
on pale players 0x0C dark hair, 0x18 brown hair and 0x14 fair hair.

The intro's portrait is a photo of its own, tied to the roster slot, and does
not follow these bytes. Without a head shot a player gets the plain default:
pale skin, dark hair, no beard.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.skin import tone
from .models import NBALive95PlayerRecord

#: The game's skins, lightest first, and the ITA cuts between them. ESPN's
#: studio light puts many brown faces at ITA 20-40, so the brown band is wide.
SKIN_VALUES = (3, 2, 0, 1)
SKIN_CUTS = (45.0, 30.0, 0.0)
DEFAULT_SKIN = 3
DEFAULT_HEAD = 0x0C

_DARK_HAIR = {"black", "dark_brown"}
_FAIR_HAIR = {"blond", "red", "grey"}


def look(face: Face | None) -> tuple[int, int]:
    """(skin byte, head byte) for a measured face, or the plain default."""
    if face is None or face.ita is None:
        return DEFAULT_SKIN, DEFAULT_HEAD
    skin = SKIN_VALUES[tone(face.ita, SKIN_CUTS)]
    beard = bool(face.beard)
    if face.hair == "bald":
        return skin, 0x03 if beard else 0x00
    if skin in (0, 1):
        return skin, 0x0D if beard else 0x05
    if face.hair in _FAIR_HAIR:
        return skin, 0x14
    if face.hair in _DARK_HAIR or face.hair is None:
        return skin, 0x0D if beard else 0x0C
    return skin, 0x18


def dress(
    records: Sequence[NBALive95PlayerRecord],
    ids: Sequence[int],
    faces: Mapping[int, Face] | None = None,
) -> None:
    """Set each record's skin and head from `faces` (by player id, `ids`
    parallel to `records`), else the plain default."""
    for record, pid in zip(records, ids, strict=True):
        record.skin_color, record.hair_style = look((faces or {}).get(pid))
