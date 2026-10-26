"""Each player's look from his head shot: skin tone, hair colour, a face.

Record byte 0x11 holds the hair colour (high nibble) and the skin tone (low,
0 lightest to 8 darkest); byte 0x14 the face, 0-85, which is the portrait
and the head the match draws. Checked in the Create Player screens on every
value. The faces are sorted by eye into bald, bearded (moustaches included)
and the rest; face 50 is unused on the cartridge and 86 and up draw noise.
"""

from __future__ import annotations

from ...core.headshots import Face
from ...core.looks import pick
from .mapper import N64Player

HAIR = {"black": 3, "dark_brown": 4, "brown": 6, "blond": 8, "red": 5, "grey": 0, "bald": 3}
#: ITA cuts between the nine skin tones, lightest first.
SKIN_CUTS = (52.0, 45.0, 38.0, 30.0, 22.0, 12.0, 0.0, -20.0)

BALD = (20, 27, 45, 74, 77, 85)
BEARD = (3, 14, 16, 17, 37, 42, 54, 56, 58, 59, 60, 62, 64, 68, 69, 79, 82, 83, 84)
PLAIN = tuple(f for f in range(86) if f != 50 and f not in BALD and f not in BEARD)

#: Without a photo: the look the cartridge's players most often have.
DEFAULT = (3, 2)


def skin_tone(ita: float) -> int:
    return sum(ita < cut for cut in SKIN_CUTS)


def look_of(player: N64Player, face: Face | None) -> tuple[int, int, int]:
    """(hair, skin, face) for `player`; the face is a stable pick by his id
    among those matching his photo."""
    hair, skin = DEFAULT
    pool = PLAIN
    if face is not None:
        if face.ita is not None:
            skin = skin_tone(face.ita)
        if face.hair is not None:
            hair = HAIR.get(face.hair, hair)
            if face.hair == "bald":
                pool = BALD
        if face.beard and pool is PLAIN:
            pool = BEARD
    choice = pool[pick([1] * len(pool), f"iss2000-face-{player.player_id or player.name}")]
    return hair, skin, choice


def dress(players: list[N64Player], faces: dict[int, Face]) -> None:
    for p in players:
        p.look = look_of(p, faces.get(p.player_id))
