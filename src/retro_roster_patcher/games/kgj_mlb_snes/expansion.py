"""Arizona and Tampa Bay, the 1998 expansion clubs, in the All-Star teams' places.

The game has 30 teams: the 28 clubs of 1994, then the National and American
League All-Stars, game teams 28 and 29 (team words 0x38 and 0x3A), who play the
ALL-STAR GAME of the main menu and a season's All-Star break. Arizona takes the
National League team (library slot 28) and Tampa Bay the American (slot 29):
their names, abbreviations, logos and uniforms through `identity`, and here a
25-player block each, which the All-Stars never had, and the code that reads it.

Measured on the retail cartridge and checked in Mesen 2:

* Every player read goes through one accessor ($85:D738). For the All-Stars it
  looks the player up in three tables of 25 words (club, slot, position:
  $83:D660 NL, $83:D6F6 AL) and reads that club's record. The lookups ($85:D7B4
  NL, $85:D802 AL) become `LDA #block / STA $CE / LDA #bank / STA $D0 / JMP
  $D889`, the accessor's own add-player-and-field path, so the teams read 800
  bytes of their own in bank $C1 of the image grown to 4 MiB (`BLOCKS`). The
  Home Run Derby uses the same team words and branches off before ($12B6) to
  its own records, untouched.
* $85:D6C2: the lineup builder gave team words from 0x38 an NL lineup (eight
  fielders from the records, the pitcher ninth); its branch now takes the AL
  path, nine batters with their own positions, a DH among them. The All-Star
  Game is always in Baltimore's park with the DH ($84:E0E2), and the
  cartridge's All-Stars come out as before: the game made their ninth, a bench
  player, the DH batting sixth either way.
* $85:C86A: in a season's All-Star game the cartridge copied the season lines
  of the user's club's All-Stars into the team, finding them through the
  tables ($85:C871); a club of its own has nothing to copy, so that is skipped.
* The All-Star menu's first option, ALL-STAR GAME, reads ARIZONA-TAMPA: its
  text in the menu script and its highlight, a string of its own (the main
  menu's ALL-STAR GAME, which shares the cartridge's, leads to the derby too
  and stays).

A slot without a mapped club keeps what the image has: the cartridge's
All-Stars, or the club an earlier patch put there.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from ...formats import snes
from . import identity
from .models import PLAYER_LENGTH, PLAYERS_PER_TEAM, TEAM_LENGTH, KGJTeamRecord
from .rom_reader import header_size

if TYPE_CHECKING:
    from .rom_writer import KGJRomWriter

#: Library slots, which are also the game's team numbers.
ARIZONA, TAMPA_BAY = 28, 29
TEAMS = 30
NAMES = {ARIZONA: "National League All-Stars", TAMPA_BAY: "American League All-Stars"}

_SIZE = 0x400000
#: The two blocks (headerless file offset): bank $C1, after `identity`'s part.
BLOCKS = 0x208000
#: Per slot: the (club, position, slot) tables, 25 words each, one after the
#: other, and the lookup the block replaces with its first 13 bytes.
_TABLES = {ARIZONA: 0x1D660, TAMPA_BAY: 0x1D6F6}
_LOOKUP = {
    ARIZONA: (0x2D7B4, bytes.fromhex("ae071bac091bc00900d006bf92")),
    TAMPA_BAY: (0x2D802, bytes.fromhex("ae071bac091bc00900d006bf28")),
}
_JUMP = 0xD889


def _lookup(slot: int) -> bytes:
    at = BLOCKS + (slot - ARIZONA) * TEAM_LENGTH
    address, bank = 0x8000 | at & 0x7FFF, 0x80 | at >> 15
    return struct.pack("<BH8BH", 0xA9, address, 0x85, 0xCE, 0xA9, bank, 0, 0x85, 0xD0, 0x4C, _JUMP)


def _text(text: str) -> bytes:
    return bytes(0 if c == " " else 0x25 if c == "-" else 0x0B + ord(c) - 65 for c in text)


#: (headerless file offset, cartridge bytes, patched bytes).
_LINEUP = (0x2D6BF, bytes.fromhex("c93800b005"), bytes.fromhex("c93800b00c"))
_CARRY_OVER = (0x2C864, bytes.fromhex("ad2618c91a00d0"), bytes.fromhex("ad2618c91a0080"))
#: The menu option's text, its cell's string (an offset from the string table
#: at $86:F36D) and, in the padding at the end of bank $86, the new string's
#: pointer ($86:FF61) and the string, drawn from a column before the text to
#: one after it ($86:FFE0, after `identity`'s $86:FF70-FFDF).
_MENU = (
    (0x361A6, _text("ALL-STAR GAME"), _text("ARIZONA-TAMPA")),
    (0x273A2, b"\x02\x00", struct.pack("<H", 0xFF61 - 0xF36D)),
    (0x37F61, b"\xff\xff", b"\xe0\xff"),
    (0x37FE0, b"\xff" * 16, _text(" ARIZONA-TAMPA ") + b"\xd0"),
)


def _ready(rom: bytes | bytearray, pokes: tuple[tuple[int, bytes, bytes], ...]) -> bool:
    """Does each place hold the cartridge's bytes or the patch's?"""
    return all(rom[at : at + len(old)] in (old, new) for at, old, new in pokes)


def _poke(rom: bytearray, pokes: tuple[tuple[int, bytes, bytes], ...]) -> None:
    for at, _, new in pokes:
        rom[at : at + len(new)] = new


def known(rom: bytes | bytearray) -> bool:
    """Is `rom` (headerless) the cartridge, or a patch of it, this module edits?"""
    lookups = tuple((at, old, _lookup(slot)) for slot, (at, old) in _LOOKUP.items())
    return len(rom) >= 0x200000 and _ready(rom, (*lookups, _LINEUP, _CARRY_OVER))


def all_stars(rom: bytes | bytearray, slot: int, team_offset: Callable[[int], int]) -> bytes:
    """The cartridge's All-Star team at `slot` as a block: each player's club
    record, his All-Star position in it. `team_offset(library slot)` is where
    a club's block is."""
    tables = _TABLES[slot]
    out = bytearray()
    for i in range(PLAYERS_PER_TEAM):
        club, position, player = (
            struct.unpack_from("<H", rom, tables + 50 * k + 2 * i)[0] for k in range(3)
        )
        at = team_offset(identity.team_of(club // 2)) + player // 2 * PLAYER_LENGTH
        record = bytearray(rom[at : at + PLAYER_LENGTH])
        record[9] = position
        out += record
    return bytes(out)


def write(
    writer: KGJRomWriter, teams: Mapping[int, KGJTeamRecord], status: Callable[[str], None]
) -> dict[int, int]:
    """Arizona's and Tampa Bay's players, from `teams` by slot, into blocks of
    their own and the game pointed at them; how many records each got. Grows
    the image to 4 MiB. A block starts as its All-Star team the first time, so
    a short squad keeps the All-Stars' records past it, as a club's keeps its
    own."""
    slots = [s for s in (ARIZONA, TAMPA_BAY) if s in teams and teams[s].players]
    assert writer.data is not None
    data, base = writer.data, header_size(len(writer.data))
    if not slots:
        return {}
    if not known(data[base:]):
        status("Arizona and Tampa Bay left out: not the cartridge this patcher knows")
        return {}
    body = data[base:]
    snes.grow(body, _SIZE)
    data[base:] = body

    written = {}
    for slot in slots:
        at, old = _LOOKUP[slot]
        block = base + BLOCKS + (slot - ARIZONA) * TEAM_LENGTH
        if data[base + at : base + at + len(old)] == old:
            data[block : block + TEAM_LENGTH] = all_stars(
                data[base:], slot, lambda s: writer.reader.get_team_offset(s) - base
            )
            data[base + at : base + at + len(old)] = _lookup(slot)
        records = teams[slot].players[:PLAYERS_PER_TEAM]
        written[slot] = sum(
            writer.write_record(block + i * PLAYER_LENGTH, r)
            for i, r in enumerate(records)
            if r is not None
        )
    body = data[base:]
    _poke(body, (_LINEUP, _CARRY_OVER))
    if len(slots) == 2 and _ready(body, _MENU):
        _poke(body, _MENU)
    data[base:] = body
    return written
