"""Writes mapped teams into a cartridge image (`rom` has the layout)."""

from __future__ import annotations

from . import rom as R
from .mapper import FORMATION_DONORS, N64Player, N64Team

_ABILITY_BYTES = {name: R.ABILITIES + k for k, name in enumerate(R.ABILITY_NAMES)}
_ABILITY_BYTES.update(goalkeeping=R.GOALKEEPING, keeper2=R.KEEPER2, attack=0x2F, cover=0x30)


def donor_rows(image: bytes | bytearray) -> dict[tuple[int, int, int], tuple[bytes, int]]:
    """Each formation's row and id as the donor team has it, read before any
    team is written: a donor slot may itself be patched with another shape."""
    return {
        shape: (
            bytes(image[R.FORMATIONS + d * R.FORMATION_ROW :][: R.FORMATION_ROW]),
            image[R.METADATA + d * 6 + 5],
        )
        for shape, d in FORMATION_DONORS.items()
    }


def write_formation(
    image: bytearray,
    slot: int,
    shape: tuple[int, int, int],
    donors: dict[tuple[int, int, int], tuple[bytes, int]] | None = None,
) -> None:
    """Copy the donor team's formation row and id over the slot's."""
    row, ident = (donors or donor_rows(image))[shape]
    dst = R.FORMATIONS + slot * R.FORMATION_ROW
    image[dst : dst + R.FORMATION_ROW] = row
    image[R.METADATA + slot * 6 + 5] = ident


def starting_order(image: bytes | bytearray, slot: int) -> list[int]:
    """Record index for each of the 11 starters as `mapper` orders them:
    keeper, then each line from one touchline to the other."""
    lines = R.formation_lines(image, slot)
    widths = R.formation_widths(image, slot)
    return sorted(range(11), key=lambda k: (lines[k], widths[k], k))


def write_player(image: bytearray, slot: int, index: int, p: N64Player) -> None:
    o = R.record_offset(slot, index)
    image[o : o + 4] = bytes(4)  # special abilities: none known to be earned
    image[o + R.NUMBER] = (image[o + R.NUMBER] & 0xC0) | (p.number & 0x3F)
    if p.look is not None:
        hair, skin, face = p.look
        image[o + R.LOOK] = (hair & 15) << 4 | (skin & 15)
        image[o + R.FACE] = face
    image[o + R.NAME : o + R.NAME + R.NAME_LEN] = R.encode_name(p.name)
    for key, value in p.ratings.items():
        at = _ABILITY_BYTES.get(key)
        if at is not None:
            image[o + at] = max(0, min(99, value))
    image[o + R.FOOT] = p.foot
    image[o + R.HEIGHT] = max(58, min(99, p.height_cm - 100))
    image[o + R.BUILD] = p.build
    image[o + R.POSITION] = p.position_bits


def write_team(
    image: bytearray,
    slot: int,
    team: N64Team,
    donors: dict[tuple[int, int, int], tuple[bytes, int]] | None = None,
) -> int:
    """Write `team` over `slot`; returns how many players were written.
    `donors` (`donor_rows` of the unpatched image) when writing several."""
    if team.formation is not None:
        write_formation(image, slot, team.formation, donors)
    order = starting_order(image, slot) + list(range(11, R.PLAYERS_PER_TEAM))
    for index, player in zip(order, team.players, strict=False):
        write_player(image, slot, index, player)
    write_team_ratings(image, slot)
    return min(len(team.players), R.PLAYERS_PER_TEAM)


#: Team ratings the team select shows (AL is their mean, worked out by the
#: game). Each is a least-squares fit, to within about 2 points on the 100
#: nations, of the XI's mean of some record bytes.
ATTACK_TABLE = 0x3753C8
DEFENCE_TABLE = 0x375438
_FITS = {
    ATTACK_TABLE: ((0x20, 0x24, 0x29, 0x2B), 1.634, -16.57),
    DEFENCE_TABLE: ((0x22, 0x24, 0x28, 0x2B), 1.698, -20.29),
}


def write_team_ratings(image: bytearray, slot: int) -> None:
    for table, (offsets, a, b) in _FITS.items():
        xi = [R.record_offset(slot, i) for i in range(11)]
        mean = sum(image[o + k] for o in xi for k in offsets) / (11 * len(offsets))
        image[table + slot] = max(30, min(99, round(a * mean + b)))
