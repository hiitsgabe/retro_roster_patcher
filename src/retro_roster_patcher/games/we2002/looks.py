"""Each player's own look in World Soccer Winning Eleven 2002.

The carat record (`rom_writer._encode_player_carat`) draws a player from a
skin tone, a hair style and colour, a beard and its colour, a build, a height
and an age. They are picked per player here:

* skin from his head shot (`core.skin`, cut at `SKIN_CUTS`), or the East
  Asian tone for an East Asian national (`core.looks.EAST_ASIAN`: on the disc
  that tone is Japan, Korea and China and nobody else);
* hair colour from his head shot (`core.hair`, through `HAIR_COLOUR`); the
  beard takes the colour;
* hair style and beard a stable pick from the player, weighted like the
  disc's players of his tone (`BALD_STYLE` for a shaved head);
* build from his height (`build`), height and age from the provider, else
  from the disc's spread by position and 25.

A player without a usable head shot gets `DEFAULT_LOOK`, the plainest
player the user asked for: pale skin, black hair in the disc's commonest
style, no beard. Never the disc's occupant, never a guess.

Only values the retail disc uses are picked (1911 records read on
2026-10-24): hair colours 0, 1, 4 and 7, builds 3-6, beard styles 0-6.
Skin as drawn: A pale, B yellow (the East Asian face), C brown, D dark.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...core.headshots import Face
from ...core.looks import EAST_ASIAN, pick
from ...core.skin import tone
from .models import WELook, WEPlayerRecord

#: The ITA (`core.skin`) between the game's tones 0 (pale), 2 (brown) and 3
#: (dark), lightest first; tone 1 is the East Asian face, by nationality.
SKIN_CUTS = (20.0, -15.0)
SKIN_LADDER = (0, 2, 3)
#: A player without a head shot: pale, black hair in the disc's commonest
#: style (value 20), no beard.
DEFAULT_LOOK = WELook(skin=0, hair_style=20, hair_colour=0, beard=0, beard_colour=0)
#: The game's colour for what `core.hair` read, as DuckStation draws the
#: eight (容姿設定, 2026-10-24): A black, B brown, C dark brown, D near
#: black, E grey, F yellow blond, G light brown, H golden blond (what the
#: disc's Scandinavians wear). No red: a redhead is drawn brown.
HAIR_COLOUR = {
    "black": 0,
    "dark_brown": 1,
    "brown": 1,
    "blond": 7,
    "red": 1,
    "grey": 4,
    "bald": 0,
}
#: No style is shaved; 19 (value 18) is the closest crop (DuckStation).
BALD_STYLE = 18
#: The disc's own shares, by tone group (tone 0, tone 1, tones 2-3), as
#: weights for each value from 0 up. Hair colours other than 0, 1 and 7 are
#: left out until they are seen drawn.
HAIR_STYLE_BY_GROUP = (
    (
        231,
        23,
        18,
        28,
        0,
        0,
        0,
        3,
        19,
        30,
        18,
        21,
        11,
        0,
        5,
        20,
        2,
        44,
        56,
        9,
        264,
        119,
        61,
        9,
        21,
        2,
        31,
        172,
        5,
        4,
        6,
        10,
    ),
    (
        25,
        1,
        0,
        4,
        0,
        0,
        0,
        1,
        1,
        12,
        4,
        1,
        2,
        0,
        0,
        2,
        0,
        0,
        6,
        0,
        21,
        4,
        2,
        0,
        2,
        2,
        10,
        9,
        0,
        1,
        0,
        0,
    ),
    (
        76,
        1,
        7,
        9,
        0,
        0,
        0,
        0,
        2,
        12,
        1,
        10,
        0,
        0,
        29,
        3,
        0,
        9,
        3,
        24,
        190,
        63,
        18,
        1,
        67,
        0,
        6,
        16,
        5,
        2,
        4,
        1,
    ),
)
BEARD_BY_GROUP = (
    (1175, 8, 28, 0, 5, 20, 6),
    (102, 1, 5, 0, 0, 2, 0),
    (467, 25, 44, 0, 5, 18, 0),
)
#: Builds on the disc run tall to short: 3 averages 188 cm, 4 185, 5 180,
#: 6 175. 0 is the untouched default of squads the designers never dressed,
#: 7 too rare to read; neither is picked.
BUILD_WITHOUT_HEIGHT = (0, 0, 0, 25, 494, 832, 257, 0)
BUILD_FROM = ((188, 3), (183, 4), (177, 5), (0, 6))
#: The disc's mean height by position (GK, DF, MF, FW), the centre of the
#: spread a player without a height is given.
HEIGHT_BY_POSITION = (186, 182, 177, 179)
HEIGHT_SPREAD = 6
HEIGHT_RANGE = (155, 202)
AGE_WITHOUT_DATA = 25


def _key(record: WEPlayerRecord) -> str:
    # The names too: CSV imports have no provider id, and two id-0 players
    # must not share every pick.
    return f"{record.player_id}:{record.last_name}:{record.first_name}:"


def skin_tone(nationality: str, ita: float | None) -> int:
    """The game's tone: East Asian nationals 1, a measured face by the
    ladder, anyone else the default's pale."""
    if nationality in EAST_ASIAN:
        return 1
    if ita is not None:
        return SKIN_LADDER[tone(ita, SKIN_CUTS)]
    return DEFAULT_LOOK.skin


def build(height_cm: int, key: str) -> int:
    """The build for a height, or a disc-weighted pick without one."""
    if height_cm <= 0:
        return pick(BUILD_WITHOUT_HEIGHT, key + "build")
    return next(value for floor, value in BUILD_FROM if height_cm >= floor)


def dress(records: Sequence[WEPlayerRecord], faces: Mapping[int, Face] | None = None) -> None:
    """Give each record its look, height and age.

    `faces` is what `core.headshots.measure_faces` read from the players'
    head shots, by player id; a player it lacks (or with no id) gets the
    picks for no head shot.
    """
    for record in records:
        key = _key(record)
        face = (faces or {}).get(record.player_id) if record.player_id else None
        look = record.look
        look.skin = skin_tone(record.nationality, face.ita if face else None)
        hair = face.hair if face else None
        if hair is None:
            # No face, or no hair read from it: the plain default, in the
            # measured tone when there is one.
            look.hair_colour, look.hair_style = DEFAULT_LOOK.hair_colour, DEFAULT_LOOK.hair_style
            look.beard, look.beard_colour = DEFAULT_LOOK.beard, DEFAULT_LOOK.beard_colour
        else:
            group = min(look.skin, 2)
            look.hair_colour = HAIR_COLOUR[hair]
            look.hair_style = (
                BALD_STYLE if hair == "bald" else pick(HAIR_STYLE_BY_GROUP[group], key + "hair")
            )
            look.beard = pick(BEARD_BY_GROUP[group], key + "beard")
            look.beard_colour = look.hair_colour
        if record.height_cm <= 0:
            centre = HEIGHT_BY_POSITION[min(max(record.position, 0), 3)]
            record.height_cm = (
                centre + pick((1,) * (2 * HEIGHT_SPREAD + 1), key + "height") - HEIGHT_SPREAD
            )
        record.height_cm = min(HEIGHT_RANGE[1], max(HEIGHT_RANGE[0], record.height_cm))
        look.build = build(record.height_cm, key)
        if record.age <= 0:
            record.age = AGE_WITHOUT_DATA
