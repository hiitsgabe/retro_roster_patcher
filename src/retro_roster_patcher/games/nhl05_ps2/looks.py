"""Each player's skin tone in NHL 2005: the bio's `SKIN` and his face texture.

SPBT `SKIN` is 3 bits, the same ladder NHL 07 kept: on the retail disc 1 and
2 are pale (1283 and 1094 of 2465 bios), 3 olive (65: Belfour, Guerin,
Handzus), 6 brown (11: Iginla, Grier, Emery, Malhotra) and 5 the darkest
(11: Laraque, Brashear, Weekes, Worrell); 4 is Richard Park alone, an East
Asian face. The order of 5 and 6 is the cheek colour of those players' own
face textures (`faces.FACE_TONES`). The game draws the face from the texture
the bio's `ART_` names, not from `SKIN`, so the patcher also writes each
player a generic face in his shade (`faces`). Players wear helmets, so there
is no hair to set. Without a head shot a player gets the first shade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.looks import EAST_ASIAN
from ...core.skin import tone
from .models import NHL05PlayerRecord

#: The game's shades, lightest first, and the ITA cuts between them.
SKIN_VALUES = (1, 2, 3, 6, 5)
SKIN_CUTS = (45.0, 20.0, 5.0, -20.0)
#: Richard Park's shade: an East Asian face. ESPN's hockey rosters give a
#: citizenship as an IOC code, the soccer ones as a name (`core.looks`).
EAST_ASIAN_SKIN = 4
EAST_ASIAN_CODES = frozenset({"JPN", "KOR", "PRK", "CHN", "TPE", "HKG", "MAC", "MNG"})
#: A player without a head shot: the lightest shade, the plain default the
#: user asked for.
DEFAULT_SKIN = 1


def skin(ita: float | None, nationality: str = "") -> int:
    """The shade for a measured face (an East Asian nationality's its own),
    or `DEFAULT_SKIN` without one."""
    if nationality in EAST_ASIAN or nationality in EAST_ASIAN_CODES:
        return EAST_ASIAN_SKIN
    if ita is not None:
        return SKIN_VALUES[tone(ita, SKIN_CUTS)]
    return DEFAULT_SKIN


def dress(records: Sequence[NHL05PlayerRecord], faces: Mapping[int, Face] | None = None) -> None:
    """Set every record's `skin`: from `faces` (what `measure_faces` read, by
    player id) where it has a tone, else the no-photo pick."""
    for record in records:
        face = (faces or {}).get(record.player_id) if record.player_id else None
        record.skin = skin(face.ita if face else None, record.nationality)
