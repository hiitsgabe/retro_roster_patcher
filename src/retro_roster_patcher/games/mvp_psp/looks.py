"""Each player's looks in MVP Baseball: the `attrib` face, skin, hair and beard columns.

Column 12 (`face`) under 900 is a real player's own 3D head (and portrait), so
a new player gets one of the generic heads, chosen by his complexion: on the
retail disc 901-905 are white players' heads, 906 East Asian, 907-910 Latin
American and 911-915 Black players'. On a generic head the game draws column 13
(skin tone), 14 (hair style), 15 (hair colour) and 16 (facial hair).

Skin tone, lightest first: 0, 1, 2 (white players), 4, 5, 6 (Latin American),
7, 8 (mixed), 9, 10 (Black players); 3 is the East Asian players' alone, given
by nationality. The cuts put ESPN's 1079 MLB head shots of 2026 into those
levels in the retail disc's proportions.

ESPN's MLB head shots wear caps, so the hair under them cannot be measured:
hair is the disc's commonest for the complexion (short, dark brown on white
players and black on the rest, style 7 on Black players' heads); a beard is
measured (column 16, 5 when there is one). Without a head shot: the plain
default, a pale white player's head, short dark hair, no beard.
"""

from __future__ import annotations

from ...core.headshots import Face
from ...core.looks import EAST_ASIAN, pick
from ...core.skin import tone

SKIN_LADDER = (0, 1, 2, 4, 5, 6, 7, 8, 9, 10)
SKIN_CUTS = (48.2, 34.1, 25.4, 22.4, 15.2, 11.8, 6.6, -0.5, -25.8)
EAST_ASIAN_SKIN = 3
#: Generic heads by complexion, with the retail disc's counts as weights.
HEADS = {
    "white": ((901, 902, 903, 904, 905), (55, 63, 49, 57, 48)),
    "asian": ((906,), (3,)),
    "latin": ((907, 908, 909, 910), (32, 22, 17, 11)),
    "black": ((911, 912, 913, 914, 915), (18, 10, 8, 11, 10)),
}
#: (hair style, hair colour) by complexion.
HAIR = {"white": (3, 5), "asian": (3, 0), "latin": (3, 0), "black": (7, 0)}
BEARD = 5
DEFAULT = {12: 901, 13: 1, 14: 3, 15: 5, 16: 0}


def _group(skin: int) -> str:
    if skin == EAST_ASIAN_SKIN:
        return "asian"
    return "white" if skin <= 2 else ("latin" if skin <= 6 else "black")


def columns(face: Face | None, nationality: str, key: str) -> dict[int, int]:
    """The `attrib` columns 12-16 for a measured face (or None), the player's
    nationality and a stable `key` for the head pick."""
    if nationality in EAST_ASIAN:
        skin = EAST_ASIAN_SKIN
    elif face is None or face.ita is None:
        return dict(DEFAULT)
    else:
        skin = SKIN_LADDER[tone(face.ita, SKIN_CUTS)]
    group = _group(skin)
    heads, weights = HEADS[group]
    style, colour = HAIR[group]
    beard = BEARD if face is not None and face.beard else 0
    return {12: heads[pick(weights, key)], 13: skin, 14: style, 15: colour, 16: beard}
