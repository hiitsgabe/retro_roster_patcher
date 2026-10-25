"""Each player's skin tone in NHL 07: the bio's `SKIN` and his face texture.

SPBT `SKIN` is 3 bits. On the retail disc 1 and 2 are pale (1427 and 1734
of 3260 bios), 3 olive (72: Luongo), 6 brown (12: Iginla, Grier, Emery,
Malhotra) and 5 the darkest (10: Laraque, Brashear, Weekes, Carter); 4 is
Richard Park alone, and 7 marks four pale players. The order of 5 and 6 is
the cheek colour of those players' own face textures (`faces.FACE_TONES`).
The game draws the face from the texture the bio's `ART_` names, not from
`SKIN`, so the patcher also writes each player a generic face in his shade
(`faces`). Players wear helmets, so there is no hair to set. Without a head
shot a player gets the first shade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.skin import tone
from .models import NHL07PlayerRecord

#: The game's shades, lightest first, and the ITA cuts between them.
SKIN_VALUES = (1, 2, 3, 6, 5)
SKIN_CUTS = (45.0, 20.0, 5.0, -20.0)
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
