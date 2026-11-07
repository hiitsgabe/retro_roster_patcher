"""Player cards: the black-and-white 48x48 photos on Game Setup, Matchups and
the Player Cards screen.

Measured in genesis_plus_gx on the retail cartridge (Nov 2026):

* `TABLE` holds 28 pointers, one per team (the 26 clubs, then the two All-Star
  teams), to a list of `(u32 card, u16 roster index)` entries ending in a u32
  0. The cartridge lists 144 photos, mostly each team's Best line (its line 0:
  goalie, defencemen, forwards), and a few players with a drawing.
* A card is an EA art record: u32 palette offset, u32 map offset, u16 tile
  count, then 3-bit pixels, MSB first, 24 bytes a tile, the 36 tiles of a 6x6
  grid row by row. A pixel of value v draws CRAM colour v + 5 of the photo
  palette: 0 white, 1 black, 2-7 the greys from light to dark. The palette
  always comes from the record at $C63F8; a 0 map offset takes its 6x6 map.
  Of the cartridge's 148 cards, 139 are plain (`00000000 00000000 0024`).
* The lookup at $FAE26 searches a team's list for a roster index. On a miss it
  walks the roster to the player's stat byte 4 and draws one of four generic
  drawings (`GENERIC`) by his role and hand. Game Setup cycles entries 0-5 of
  the list (past the end it would draw from address 0); Matchups looks up the
  starters; the Player Cards screen browses the list.

`write` gives every written player an entry: a card from his head shot, else
the drawing the game itself would pick, in a new list per team in `REGION` of
the image grown to 2 MiB. Line 0's six come first, as on the cartridge.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...formats.genesis import LEVELS
from .rom_reader import POINTER_TABLE_OFFSET
from .rom_writer import grow

TABLE = 0xF92F4
TEAMS = 28
#: The 26 clubs, whose rosters a patch writes.
CLUBS = 26
REGION = range(0x110000, 0x200000)
SIDE = 48
#: The game's own drawing for a player with no card, by (is goalie, hand bit).
GENERIC = {
    (False, 0): 0xC682E,
    (False, 1): 0xC6B98,
    (True, 1): 0xC6F02,
    (True, 0): 0xC726C,
}
#: A plain card: no palette or map of its own, 36 tiles.
HEADER = bytes(8) + b"\x00\x24"
CARD_SIZE = len(HEADER) + 36 * 24
#: Pixel value of each grey level (0 black .. 7 white): the cartridge's ramp
#: is white, black, then greys 6 down to 1.
VALUE = {7: 0, 0: 1, 6: 2, 5: 3, 4: 4, 3: 5, 2: 6, 1: 7}
#: What shows behind a cut-out head shot: the dark grey of the cartridge's photos.
BACKGROUND = (52, 52, 52)
#: Most entries a list is read for; the cartridge's longest has 25.
MAX_ENTRIES = 64

#: (is goalie, hand bit, head shot URL or "") per roster record.
Player = tuple[bool, int, str]


def table_ok(rom: bytes | bytearray) -> bool:
    """Is the card table there: 28 lists of at least the six entries Game Setup
    reads, ending within `MAX_ENTRIES`, each pointing at a card record of 1 to
    36 tiles inside the image?"""
    if len(rom) < TABLE + 4 * TEAMS:
        return False
    for team in range(TEAMS):
        at = struct.unpack_from(">I", rom, TABLE + 4 * team)[0]
        for k in range(MAX_ENTRIES + 1):
            if k == MAX_ENTRIES or at % 2 or at + 6 * k + 4 > len(rom):
                return False
            card = struct.unpack_from(">I", rom, at + 6 * k)[0]
            if card == 0:
                if k < 6:
                    return False
                break
            if card % 2 or card + 10 > len(rom) or not 1 <= rom[card + 9] <= 36 or rom[card + 8]:
                return False
    return True


def card(picture: Any) -> bytes:
    """A plain card of `picture` (48x48 RGBA): on a dark grey, in 8 greys
    stretched to the full range and dithered."""
    from PIL import Image, ImageOps

    flat = Image.new("RGBA", (SIDE, SIDE), (*BACKGROUND, 255))
    flat.alpha_composite(picture.convert("RGBA").resize((SIDE, SIDE)))
    grey = ImageOps.autocontrast(flat.convert("L"), cutoff=1)
    ramp = Image.new("P", (1, 1))
    ramp.putpalette([level for level in LEVELS for _ in range(3)] + [0] * 3 * 248)
    levels = grey.convert("RGB").quantize(palette=ramp, dither=Image.Dither.FLOYDSTEINBERG)
    palette = levels.getpalette() or []
    level = {i: LEVELS.index(palette[3 * i]) for i in range(len(palette) // 3)}
    pixels = levels.tobytes()
    bits = 0
    for tile in range(36):
        x0, y0 = tile % 6 * 8, tile // 6 * 8
        for y in range(y0, y0 + 8):
            for x in range(x0, x0 + 8):
                bits = bits << 3 | VALUE[level[pixels[y * SIDE + x]]]
    return HEADER + bits.to_bytes(36 * 24, "big")


def order(line0: bytes, count: int) -> list[int]:
    """The roster indices of a team's list: line 0's goalie, defencemen and
    forwards (counted from 1 there) as the cartridge lists its stars, then
    everyone else; repeated to six for Game Setup."""
    out = list(dict.fromkeys(n - 1 for n in line0[:6] if 0 < n <= count))
    out += [i for i in range(count) if i not in out]
    while out and len(out) < 6:
        out += out[: 6 - len(out)]
    return out


def _goalies(word: int) -> int:
    """The goalie count the game reads off a team's goalie word ($FAE7A)."""
    count, word = 1, word << 4 & 0xFFFF
    while word:
        count, word = count + 1, word << 4 & 0xFFFF
    return count


def _roster(rom: bytes | bytearray, team: int) -> tuple[list[Player], bytes]:
    """A club's roster as the image holds it, with no head shots, and its line 0."""
    base = struct.unpack_from(">I", rom, POINTER_TABLE_OFFSET + 4 * team)[0]
    at = base + struct.unpack_from(">H", rom, base)[0]
    line0 = bytes(rom[base + struct.unpack_from(">H", rom, base + 6)[0] :][:8])
    goalie_word = base + struct.unpack_from(">H", rom, base + 0xA)[0]
    goalies = _goalies(struct.unpack_from(">H", rom, goalie_word)[0])
    players: list[Player] = []
    while (length := struct.unpack_from(">H", rom, at)[0]) >= 3:
        players.append((len(players) < goalies, rom[at + length + 4] & 1, ""))
        at += length + 8
    return players, line0


def write(
    rom: bytearray,
    teams: Mapping[int, Sequence[Player]],
    lines: Mapping[int, bytes],
    load: Callable[[list[str]], Sequence[Any]] | None,
) -> tuple[int, int]:
    """Give each team in `teams` (club index to its written roster) a new card
    list in `REGION`, grown into the image: a card per player whose head shot
    `load` turns into a picture (None for a failure; without `load` none is
    fetched), the game's drawing for the others. A club whose list an earlier
    patch put in `REGION` and that is not in `teams` gets drawings for its
    roster as it stands. Returns (photos, drawings) over `teams`' players."""
    grow(rom)
    stale = {
        team: _roster(rom, team)
        for team in range(CLUBS)
        if team not in teams and struct.unpack_from(">I", rom, TABLE + 4 * team)[0] in REGION
    }
    urls = list(dict.fromkeys(url for players in teams.values() for *_, url in players if url))
    pictures = dict(zip(urls, load(urls), strict=True)) if load and urls else {}
    rom[REGION.start : REGION.stop] = b"\xff" * len(REGION)
    # ponytail: no room check: 26 clubs of 23 players (the patcher's cap) take
    # 523 KiB of cards and lists in the 960 KiB region; check if the cap grows.
    at = REGION.start
    cards: dict[str, int] = {}
    photos = drawings = 0
    plans = {**{t: (p, lines.get(t, b"")) for t, p in teams.items()}, **stale}
    for team, (players, line0) in sorted(plans.items()):
        entries = []
        for index in order(line0, len(players)):
            goalie, hand, url = players[index]
            if url and pictures.get(url) is not None and url not in cards:
                rom[at : at + CARD_SIZE] = card(pictures[url])
                cards[url], at = at, at + CARD_SIZE
            entries.append((cards.get(url, GENERIC[(goalie, hand & 1)]), index))
        if team in teams:
            drawn = sum(1 for _, _, url in players if url in cards)
            photos, drawings = photos + drawn, drawings + len(players) - drawn
        struct.pack_into(">I", rom, TABLE + 4 * team, at)
        for card_at, index in entries:
            rom[at : at + 6] = struct.pack(">IH", card_at, index)
            at += 6
        rom[at : at + 4] = bytes(4)
        at += 4
    return photos, drawings
