"""Each player's skin colour in PES 2013, the look the record holds in a
field this patcher knows (calibrated in PCSX2 on 2026-10-24).

Byte 115 bits 0-2 is the editor's "Skin Color" less one. Only 0-5 draw (6
and 7 draw garbage). By the lightness of the neck and ears the game draws
them: 0 pale, 1 pale yellow (the East Asian face: Japan, Korea and China are
100% of it on the disc, nobody else), 4 light brown, 2 brown, 5 dark, 3
darkest. So a measured face walks the ladder (0, 4, 2, 5, 3), an East Asian
national gets 1, and a player without a photo the palest.

A licensed ("Preset") face keeps its own face texture whatever the colour,
so `rom_writer.RomWriter.set_look` also does what the game does when a
player is edited: it turns the licensed face and hair off, and the generic
face takes the colour. Hair colour and beard are in the record too but
their bits were not found; the game's own on the record stay.

A player who is on the 2012 disc already (Neymar at Santos, Hulk at Zenit)
keeps the disc's own look of him wherever he plays now: `disc_players` maps
ESPN ids to disc records (`assets/pes2013_disc_players.json`, built by
`assets/pes2013_players/match.py`), and `RomWriter.copy_look` moves that
record's look region onto his new record, licensed face and all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from functools import cache

from ...core.assets import package_bytes
from ...core.headshots import Face
from ...core.looks import EAST_ASIAN
from ...core.skin import tone
from .models import PESLook, PESPlayer

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


DISC_PLAYERS_FILE = "pes2013_disc_players.json"


@cache
def disc_players() -> dict[int, int]:
    """ESPN player id -> the disc record (index into entry 46/47 part 0)
    that is that player, for everyone the matcher could pair by name."""
    raw = json.loads(package_bytes("retro_roster_patcher.assets", DISC_PLAYERS_FILE))
    return {int(k): int(v) for k, v in raw["players"].items()}


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
        player.look = PESLook(
            skin=skin(player.nationality, face.ita if face else None),
            disc_record=on_disc.get(player.player_id) if player.player_id else None,
        )
