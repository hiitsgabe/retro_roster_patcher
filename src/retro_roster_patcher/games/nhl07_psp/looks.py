"""Each player's skin tone in NHL 07, the one look the bio table holds.

SPBT `SKIN` is 3 bits. The retail disc uses 1 and 2 for pale players (1427
and 1734 of 3260 bios), 3 for tan (72), 5 and 6 for dark (10 and 12: Laraque,
Weekes and Brashear 5; Iginla, Grier and Emery 6), 7 for the darkest (4), and
4 once; 0 never. The shades were ordered by eye on those players; the cut
points below are the ITA angles (`core.skin`) between them, to be checked in
PPSSPP. Players wear helmets, so there is no hair to set.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.looks import pick
from ...core.skin import tone
from .models import NHL07PlayerRecord

#: The game's shades, lightest first, and the ITA cuts between them.
SKIN_VALUES = (1, 2, 3, 5, 6, 7)
SKIN_CUTS = (40.0, 20.0, 0.0, -20.0, -45.0)
#: A player without a head shot: the disc's shares of `SKIN_VALUES`.
SKIN_WITHOUT_PHOTO = (1427, 1734, 72, 10, 12, 4)


def skin(player_id: int, ita: float | None) -> int:
    """The shade for a measured face, or a stable pick weighted like the
    disc for a player without one."""
    if ita is not None:
        return SKIN_VALUES[tone(ita, SKIN_CUTS)]
    return SKIN_VALUES[pick(SKIN_WITHOUT_PHOTO, f"nhl07:{player_id}:skin")]


def dress(records: Sequence[NHL07PlayerRecord], faces: Mapping[int, Face] | None = None) -> None:
    """Set every record's `skin`: from `faces` (what `measure_faces` read, by
    player id) where it has a tone, else the no-photo pick."""
    for record in records:
        face = (faces or {}).get(record.player_id) if record.player_id else None
        record.skin = skin(record.player_id, face.ita if face else None)
