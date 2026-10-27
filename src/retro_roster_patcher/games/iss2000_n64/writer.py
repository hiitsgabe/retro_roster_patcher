"""Writes mapped teams into a cartridge image (`rom` has the layout)."""

from __future__ import annotations

import struct

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


#: Team select starts each side on the first team of the page it saved
#: (`lbu a0, 0x0B4F(a0)` at $802A5264, ROM below: page 5, CONCACAF, so the
#: U.S.A.). The patch turns the load into `addiu a0, zero, page` so it opens
#: on the clubs' page. Pages (first team id each) as the game lists them.
TEAM_SELECT_PAGE_LOAD = 0x4598A4
_PAGE_LOAD = 0x90840B4F
PAGE_FIRST = (0, 14, 24, 37, 51, 61, 69, 79, 86, 96)


def start_page(slots: list[int]) -> int:
    """The page whose first team is a club when one is, else the page of the
    lowest club slot."""
    pages = [k for k, first in enumerate(PAGE_FIRST) if first in slots]
    if pages:
        return pages[0]
    low = min(slots)
    return max(k for k, first in enumerate(PAGE_FIRST) if first <= low)


def write_start_page(image: bytearray, slots: list[int]) -> bool:
    """Open team select on the clubs' page; False when the code is not the
    cartridge's (or an earlier patch's) and is left alone."""
    word = struct.unpack_from(">I", image, TEAM_SELECT_PAGE_LOAD)[0]
    if not slots or (word != _PAGE_LOAD and word >> 16 != 0x2404):
        return False
    struct.pack_into(">I", image, TEAM_SELECT_PAGE_LOAD, 0x24040000 | start_page(slots))
    return True
