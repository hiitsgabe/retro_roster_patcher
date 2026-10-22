"""The team kit records of PES 2013 (PS2): `0_TEXT.AFS` entry 54.

The entry is a raw block whose payload is a part table of zlib blocks: part 1
holds 130 club records of 648 bytes (row = club slot), part 0 holds 67
national records of 456 bytes (row = the overlay's national row). A record is
four kits of 88 bytes (goalkeeper home, player home, goalkeeper away, player
away) and a zero tail. A kit is 23 colours as little-endian RGBA5551 (bit 15
alpha, bits 0-4 red, 5-9 green, 10-14 blue) and 42 parameter bytes.

Colour slots, read off the uniform screen with a kit of twelve pure colours:
0 shirt base, 1 vertical stripes over the body (a shade of the base on plain
kits), 2 shoulder and side panels, 3 sleeves, 4 collar, 5 shorts, 6-9 socks,
10-11 further shirt details, 12-14 one colour three times (name and number
colour), 15-22 four black/colour pairs. The template knows vertical stripes
and contrasting sleeves and nothing else: hoops, halves and sashes are not
on the disc. Parameters: t[31] = 1 and t[34..35] =
ff ff mark a PARAMETRIC kit, drawn from these colours over a generic shirt;
t[31] = 0 and t[34..35] = 01 00 a LICENSED one, drawn from an encrypted texture
package the colours do not touch. t[36] is 4 for a parametric player kit and
2 for a goalkeeper's; t[1..4] = 49 3c 11 05 is the player model set of the
disc's parametric clubs. Clubs 130..138 have no record.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ...core.errors import RomError
from ...core.kits import KitSpec, contrast
from .afs import Table, unwrap, wrap

KIT_ENTRY = 54
PART_NATIONALS = 0
PART_CLUBS = 1
CLUB_RECORD = 648
NATIONAL_RECORD = 456
KIT_SIZE = 88
COLOURS = 23
PARAMS = 42
#: Kit order inside a record.
GK_HOME, HOME, GK_AWAY, AWAY = range(4)
#: Colour slots.
SHIRT, STRIPES, PANELS, SLEEVES, COLLAR, SHORTS = 0, 1, 2, 3, 4, 5
SOCKS = (6, 7, 8, 9)
SHIRT_DETAIL = (10, 11)
NAME_COLOUR = (12, 13, 14)
#: Parameter bytes that pick the parametric path.
PARAM_PARAMETRIC = 31
PARAM_LICENCE = (34, 35)
PARAM_MODEL = 36
PARAM_SET = slice(1, 5)
PLAYER_SET = bytes((0x49, 0x3C, 0x11, 0x05))

RGB = tuple[int, int, int]


def pack_colour(rgb: RGB) -> int:
    r, g, b = rgb
    return 0x8000 | (r >> 3) | ((g >> 3) << 5) | ((b >> 3) << 10)


def unpack_colour(word: int) -> RGB:
    return (
        (word & 31) * 255 // 31,
        ((word >> 5) & 31) * 255 // 31,
        ((word >> 10) & 31) * 255 // 31,
    )


@dataclass
class Kit:
    colours: list[int]  # 23 RGBA5551 words
    params: bytearray  # 42 bytes

    @classmethod
    def parse(cls, data: bytes) -> Kit:
        return cls(
            list(struct.unpack_from(f"<{COLOURS}H", data)), bytearray(data[2 * COLOURS : KIT_SIZE])
        )

    def build(self) -> bytes:
        return struct.pack(f"<{COLOURS}H", *self.colours) + bytes(self.params)

    @property
    def parametric(self) -> bool:
        return self.params[PARAM_PARAMETRIC] == 1

    def rgb(self, slot: int) -> RGB:
        return unpack_colour(self.colours[slot])

    def set_rgb(self, slot: int, rgb: RGB) -> None:
        self.colours[slot] = pack_colour(rgb)

    def make_parametric(self, model: int = 4) -> None:
        """Have the game draw this kit from its colours rather than a texture."""
        self.params[PARAM_PARAMETRIC] = 1
        self.params[PARAM_LICENCE[0]] = 0xFF
        self.params[PARAM_LICENCE[1]] = 0xFF
        self.params[PARAM_MODEL] = model
        self.params[PARAM_SET] = PLAYER_SET


#: What the template can draw of each pattern: the stripes slot takes the
#: secondary colour for striped kits (hoops and pinstripes become stripes,
#: the nearest the disc has), the sleeves do for sleeved ones; halves, a sash
#: and a chevron have no region and come out plain with contrasting sleeves.
STRIPED = {"stripes", "pinstripes", "hoops"}
SLEEVED = {"sleeves", "halves", "sash", "chevron"}


def apply_spec(kit: Kit, spec: KitSpec) -> None:
    """Write a `KitSpec` into a kit and have the game draw it from the colours."""
    for slot in (SHIRT, STRIPES, PANELS, *SHIRT_DETAIL):
        kit.set_rgb(slot, spec.shirt)
    if spec.pattern in STRIPED:
        kit.set_rgb(STRIPES, spec.secondary)
    sleeves = spec.sleeves or (spec.secondary if spec.pattern in SLEEVED else spec.shirt)
    kit.set_rgb(SLEEVES, sleeves)
    kit.set_rgb(COLLAR, spec.secondary)
    kit.set_rgb(SHORTS, spec.shorts)
    for slot in SOCKS:
        kit.set_rgb(slot, spec.socks)
    number = contrast(spec.shirt)
    for slot in NAME_COLOUR:
        kit.set_rgb(slot, number)
    kit.make_parametric()


@dataclass
class KitTable:
    """Entry 54 as parts; `clubs[s]` and `nationals[n]` are the four kits of a team."""

    header: bytes
    table: Table
    part_headers: dict[int, bytes]
    leaves: dict[int, bytes]
    clubs: list[list[Kit]]
    nationals: list[list[Kit]]

    @classmethod
    def parse(cls, block: bytes) -> KitTable:
        header, payload = unwrap(block)
        table = Table.parse(payload)
        if len(table.parts) <= PART_CLUBS:
            raise RomError("Kit entry has no club part")
        part_headers: dict[int, bytes] = {}
        leaves: dict[int, bytes] = {}
        teams: dict[int, list[list[Kit]]] = {}
        for part, record in ((PART_NATIONALS, NATIONAL_RECORD), (PART_CLUBS, CLUB_RECORD)):
            part_headers[part], leaves[part] = unwrap(table.parts[part])
            leaf = leaves[part]
            teams[part] = [
                [Kit.parse(leaf[at + k * KIT_SIZE : at + (k + 1) * KIT_SIZE]) for k in range(4)]
                for at in range(0, len(leaf) - record + 1, record)
            ]
        return cls(header, table, part_headers, leaves, teams[PART_CLUBS], teams[PART_NATIONALS])

    def build(self) -> bytes:
        for part, record, teams in (
            (PART_NATIONALS, NATIONAL_RECORD, self.nationals),
            (PART_CLUBS, CLUB_RECORD, self.clubs),
        ):
            leaf = bytearray(self.leaves[part])
            for row, kits in enumerate(teams):
                at = row * record
                for k, kit in enumerate(kits):
                    leaf[at + k * KIT_SIZE : at + (k + 1) * KIT_SIZE] = kit.build()
            self.table.parts[part] = wrap(self.part_headers[part], bytes(leaf))
        return wrap(self.header, self.table.build())
