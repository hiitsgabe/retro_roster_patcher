"""Each player's look in PES 2013: skin colour, hair colour and beard from his
head shot, on the generic built face (calibrated in PCSX2 on 2026-10-24 and
2026-10-25; the fields are in `models`).

Byte 115 bits 0-2 is the editor's "Skin Color" less one. Only 0-5 draw (6
and 7 draw garbage). By the lightness of the neck and ears the game draws
them: 0 pale, 1 pale yellow (the East Asian face: Japan, Korea and China are
100% of it on the disc, nobody else), 4 light brown, 2 brown, 5 dark, 3
darkest. So a measured face walks the ladder (0, 4, 2, 5, 3), an East Asian
national gets 1, and a player without a photo the palest.

A face model ("Preset" in the editor, licensed or not) ignores skin and hair,
and so does a licensed player's special hairstyle, which most disc records
have; so `rom_writer.RomWriter.set_look` makes the face the generic built one
and the hairstyle a plain short one (a stable pick from `HAIR_STYLES` by the
player's id, or `BALD_STYLE`), then sets the hair's colour in RGB
(`HAIR_RGB`) and the beard (`BEARD`, a short full beard, or none), the beard
in the hair's colour. A player without a photo gets the plain default: pale
skin, black hair, no beard.

A player who is on the 2012 disc already (Neymar, Hulk) keeps the disc's own
look of him wherever he plays now: `disc_players` maps ESPN ids to disc
records (`assets/pes2013_disc_players.json`), and `RomWriter.copy_look`
moves that record's look region onto his new record, licensed face and all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache

from ...core.assets import package_bytes
from ...core.headshots import Face
from ...core.looks import EAST_ASIAN, pick
from ...core.skin import tone
from .models import LIBERTADORES_BASE, PESLook, PESPlayer

#: The game's colours lightest first, and the ITA cuts between them.
SKIN_VALUES = (0, 4, 2, 5, 3)
#: Set on 110 labelled ESPN NHL and FotMob photos (2026-10-25).
SKIN_CUTS = (28.0, 18.0, 5.0, -20.0)
EAST_ASIAN_SKIN = 1
#: A player without a head shot: the palest, the plain default the user asked for.
DEFAULT_SKIN = 0


def skin(nationality: str, ita: float | None) -> int:
    """The colour for a nationality and a measured face (None without one)."""
    if nationality in EAST_ASIAN:
        return EAST_ASIAN_SKIN
    if ita is not None:
        return SKIN_VALUES[tone(ita, SKIN_CUTS)]
    return DEFAULT_SKIN


#: Hair colours as the editor's RGB (0-63 each): black and dark brown as the
#: disc has Cristiano Ronaldo's and Casillas', the blond checked on screen.
HAIR_RGB: dict[str, tuple[int, int, int]] = {
    "black": (4, 2, 0),
    "dark_brown": (16, 9, 5),
    "brown": (26, 17, 9),
    "blond": (50, 40, 10),
    "red": (42, 16, 4),
    "grey": (40, 40, 40),
}
#: The plain short hairstyles a player gets, equally likely, each seen on a
#: built face in PCSX2: Short Hair 1 from 85, Short Hair 2 from 108. (One
#: lower is not: 4-84 are buzz cuts so light they read bald.)
HAIR_STYLES = (85, 96, 109, 120)
BALD_STYLE = 0
#: A short full beard, the disc's most common.
BEARD = 92

DISC_PLAYERS_FILE = "pes2013_disc_players.json"


@cache
def disc_players() -> dict[int, int]:
    """ESPN player id -> the disc record that is that player: an index into
    the main table (entry 46/47 part 0), or `LIBERTADORES_BASE` plus an index
    into the South American clubs' table (entry 48/49 part 0)."""
    raw = json.loads(package_bytes("retro_roster_patcher.assets", DISC_PLAYERS_FILE))
    found = {int(k): int(v) for k, v in raw["players"].items()}
    found.update(
        (int(k), LIBERTADORES_BASE + int(v)) for k, v in raw.get("libertadores", {}).items()
    )
    return found


def dress(
    players: Sequence[PESPlayer],
    faces: Mapping[int, Face] | None = None,
    disc_players: Mapping[int, int] | None = None,
) -> None:
    """Give every player his `look`: the disc's own record for a player who
    is on the disc (`disc_players`, the packaged mapping by default), else
    the colour from `faces` (what `measure_faces` read, by player id) where
    it has a tone, else nationality and the default."""
    on_disc = globals()["disc_players"]() if disc_players is None else disc_players
    for player in players:
        face = (faces or {}).get(player.player_id) if player.player_id else None
        hair = (face.hair if face else None) or "black"
        style = HAIR_STYLES[pick([1] * len(HAIR_STYLES), f"pes-hair:{player.player_id}")]
        player.look = PESLook(
            skin=skin(player.nationality, face.ita if face else None),
            hair=hair,
            beard=bool(face and face.beard),
            style=BALD_STYLE if hair == "bald" else style,
            disc_record=on_disc.get(player.player_id) if player.player_id else None,
        )
