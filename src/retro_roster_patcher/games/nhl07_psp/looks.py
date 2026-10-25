"""Each player's skin tone in NHL 07, the one look the bio table holds.

SPBT `SKIN` is 3 bits. The retail disc uses 1 and 2 for pale players (1427
and 1734 of 3260 bios), 3 for tan (72), 5 and 6 for dark (10 and 12: Laraque,
Weekes and Brashear 5; Iginla, Grier and Emery 6), 7 for the darkest (4), and
4 once; 0 never. The shades were ordered by eye on those players; the cut
points below are the ITA angles (`core.skin`) between them.
PPSSPP (2026-10-24) showed the value landing in the table but not on screen:
the game draws each bio its own face texture, and the Player Editor's head
did not change across `SKIN` 0-7, so until the face-texture mapping is found
a new player keeps the 2006 occupant's face. Players wear helmets, so there
is no hair to set. Without a head shot a player gets the first shade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.skin import tone
from .models import NHL07PlayerRecord

#: The game's shades, lightest first, and the ITA cuts between them.
SKIN_VALUES = (1, 2, 3, 5, 6, 7)
SKIN_CUTS = (45.0, 20.0, 5.0, -15.0, -40.0)
#: A player without a head shot: the lightest shade, the plain default the
#: user asked for.
DEFAULT_SKIN = 1


def skin(ita: float | None) -> int:
    """The shade for a measured face, or `DEFAULT_SKIN` without one."""
    if ita is not None:
        return SKIN_VALUES[tone(ita, SKIN_CUTS)]
    return DEFAULT_SKIN


def dress(records: Sequence[NHL07PlayerRecord], faces: Mapping[int, Face] | None = None) -> None:
    """Set every record's `skin`: from `faces` (what `measure_faces` read, by
    player id) where it has a tone, else the no-photo pick."""
    for record in records:
        face = (faces or {}).get(record.player_id) if record.player_id else None
        record.skin = skin(face.ita if face else None)
