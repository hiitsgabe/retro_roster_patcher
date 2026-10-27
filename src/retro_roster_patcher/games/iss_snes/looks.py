"""Each ISS team's two looks, chosen from its players' head shots.

What the cartridge can draw (traced in its code at $80:ADDA-$80:AE7A): a team's
kit record holds its hair colour and five skin shades, loaded into OBJ palette
0 (the second team: 1) and again into palette 2 (3). Palette 2 then takes the
team's alternate skin and hair from two pointer tables in bank $81, `$8D3C`
and `$8D74` (one bank $85 address per team, or 0 for the team's own), and a
player whose b5 bit 6 is set is drawn in it. So a team has two looks, shared
by its fifteen, and the keeper a third of his own (hair and three shades in
the keeper's kit record).

A look is one of the cartridge's own skin ramps (`SKIN_RAMPS`, cut from the
face's ITA like every game here) and a hair colour (`core.hair`'s names). The
team's own palette takes any of them; the alternate only what a shared record
in bank $85 holds (`ALT_SKINS`, `ALT_HAIRS`). `dress` picks the pair of looks
that fits the fifteen best, skin before hair, and puts each outfield player in
the nearer. A player without a photo counts as the plain default the other
games use: the lightest skin, black hair.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...core.headshots import Face
from ...core.looks import EAST_ASIAN as EAST_ASIAN_NATIONS
from ...core.looks import Person, look_cost, two_looks
from ...core.skin import tone as cut
from .models import ISSLook, ISSTeamRecord

RGB = tuple[int, int, int]

LIGHT, TAN, BROWN, DARK, DARKEST, EAST_ASIAN = range(6)
#: The cartridge's own five-shade ramps, darkest shade first, in the ladder's
#: order: the USA's (and the shared record at $85:E9E1), Brazil's ($85:E9F9),
#: Colombia's, the shared dark record's ($85:EA05), Nigeria's, then South
#: Korea's for an East Asian face.
SKIN_RAMPS: tuple[tuple[RGB, ...], ...] = (
    ((128, 64, 24), (184, 88, 64), (200, 136, 112), (208, 184, 168), (224, 224, 216)),
    ((88, 40, 16), (112, 56, 24), (168, 88, 64), (192, 136, 88), (224, 208, 176)),
    ((64, 24, 8), (88, 40, 16), (144, 80, 40), (176, 112, 72), (208, 184, 160)),
    ((56, 32, 0), (72, 48, 0), (104, 56, 0), (168, 96, 0), (224, 168, 128)),
    ((40, 16, 0), (56, 24, 0), (88, 48, 0), (144, 80, 0), (232, 120, 104)),
    ((40, 32, 0), (96, 56, 0), (176, 152, 64), (200, 192, 144), (232, 216, 184)),
)
#: The same soccer photos as PES 2013's five-step ladder, so the same cuts.
SKIN_CUTS = (28.0, 18.0, 5.0, -20.0)
#: The cartridge's hair colours (Spain's black is darker; this is the shared
#: record's), and a grey of its own.
HAIR: dict[str, RGB] = {
    "black": (32, 16, 0),
    "dark_brown": (64, 48, 0),
    "brown": (128, 96, 0),
    "blond": (192, 176, 0),
    "red": (104, 24, 0),
    "grey": (144, 144, 144),
}
#: What the alternate look can take: the shared records in bank $85
#: (`rom_writer.ALT_SKIN_RECORDS`, `ALT_HAIR_RECORDS`).
ALT_SKINS = (LIGHT, TAN, BROWN, DARK)
ALT_HAIRS = ("black", "dark_brown", "brown", "blond", "red")
#: A shaved head reads as short dark hair at this size.
_HAIR_AS = {"bald": "black"}
#: Where an East Asian face sits on the ladder, for the cost.
_POSITION = {EAST_ASIAN: 0.5}


def tone(nationality: str, ita: float | None) -> int:
    """The ramp for a nationality and a measured face (None without one)."""
    if nationality in EAST_ASIAN_NATIONS:
        return EAST_ASIAN
    if ita is None:
        return LIGHT
    return cut(ita, SKIN_CUTS)


def _person(nationality: str, face: Face | None) -> Person:
    if face is None:
        return LIGHT, "black"
    hair = face.hair or "black"
    return tone(nationality, face.ita), _HAIR_AS.get(hair, hair)


def dress(
    team: ISSTeamRecord | Any,
    faces: Mapping[int, Face],
    alt_skins: Sequence[int] = ALT_SKINS,
    alt_hairs: Sequence[str] = ALT_HAIRS,
) -> None:
    """Set `team.look` and each player's `alternate` from `faces` (player id
    -> `Face`). Player 0 is the keeper, drawn in his own record. `team` is an
    `ISSTeamRecord` or anything with its `players` (`player_id`,
    `nationality`, `alternate`) and `look`; ISS Deluxe passes its own, with
    the alternates its records allow."""
    if not team.players:
        team.look = ISSLook()
        return
    keeper, *outfield = team.players
    people = [_person(p.nationality, faces.get(p.player_id)) for p in outfield]
    own, alt = (
        two_looks(people, range(len(SKIN_RAMPS)), HAIR, alt_skins, alt_hairs, _POSITION)
        if people
        else ((LIGHT, "black"), None)
    )
    keeper_skin, keeper_hair = _person(keeper.nationality, faces.get(keeper.player_id))
    team.look = ISSLook(
        skin=own[0],
        hair=own[1],
        alt_skin=None if alt is None or alt[0] == own[0] else alt[0],
        alt_hair=None if alt is None or alt[1] == own[1] else alt[1],
        keeper_skin=keeper_skin,
        keeper_hair=keeper_hair,
    )
    keeper.alternate = False
    for player, person in zip(outfield, people, strict=True):
        player.alternate = alt is not None and look_cost(person, alt, _POSITION) < look_cost(
            person, own, _POSITION
        )
