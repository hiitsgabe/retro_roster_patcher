"""Team identity: what a slot is called, its colours and its logos.

Every place was found on the retail cartridge in Mesen 2 (offsets below are
headerless file offsets):

* Text. Each team block ends in four length-prefixed strings -- city,
  abbreviation (the in-game HUD, Stars of the Game), nickname, arena (Ron
  Barr) -- found through the 16-bit offset in block bytes 4-5, wrapped inside
  bank $9C. `write` rewrites them where they are when they fit, else 16
  bytes into the longest 0xFF run in bank $9C (on the cartridge, the 1 723
  bytes of padding at $9C:F945); the roster writer then packs
  every block, strings and all, so it runs after this. Game Setup's
  Home/Visitor and Ron Barr's %H/%V print "NY Islanders"/"NY Rangers" for
  slots 13 and 14 ($9B:C5AB), so those two strings change only when another
  club takes either slot.
* Banners. The coloured name boxes (Game Setup, Player Select, Matchups, Game
  Stats, the playoff bracket, the jumbotron) draw a second list of
  null-terminated cities, 16-bit pointers at $9C:9649, in a font of A-Z, 0-9,
  '.' and space whose widths sit at $9C:9623; a name may be 66 px wide. The
  list is rebuilt where it is. Their colours are 16-colour palettes behind
  the long pointers at $9C:850F: black, white, six shades of the team colour
  (top band, dark to light), seven of the alternate (bottom band, light to
  dark), the yellow border.
* Big logos. One compressed archive at $81:ABDE (`30 FB`, 0x7C00 bytes
  decompressed into $7F:7800) holds 30 entries -- the 28 slots, the NHL
  shield, the NHLPA logo -- and three loaders ($9D:87DB, $9D:C69B,
  $9D:D9B7) name it with `LDA #bank / STA $0E / LDA #address`. A mapped
  team's entry becomes its logo, 48x32 in six 16x16 sprites and 15 colours,
  the archive is RefPack-compressed into one bank of `REGION` and the three
  loaders repointed; the colours are a 16-colour palette behind the long
  pointers at $9C:8497.
* Portraits. Each slot's 16 portrait colours sit behind the long pointers at
  $9D:D8B3 (`portraits.PALETTES`; slots 26 and 27 share one): 1-12 a skin
  ramp, 14 the jersey of every photograph and drawing, 13 and 15 its stripes.
  A mapped slot gets its own copy, the ramp kept, in white, its colour and its
  alternate, before `portraits` maps the head shots onto it.
* Center ice and the title-screen pucks. Long pointers at $9C:83B7, 8 bytes a
  slot: tiles (packed 4 bits a pixel, left pixel high; tile 0 blank) and a map
  (u16 width, height, 0x0308, then one word per tile: its index | 0x0400, 0
  for none). They are drawn in BG palette 1, the rink's, which no team owns,
  so a logo is drawn in its nearest rink colours and its white is left to the
  ice. At most 30 tiles, as on the cartridge.
* Uniforms. 32-byte palettes, away at $96:C9CE and home at $96:CD4E, 32 bytes
  a slot. By entry: 3 sleeves, 4 pants, 5 socks, 6 shoulders, 8-10 stripes,
  11 the jersey's outline and helmet shade, 12 the jersey and helmet, 13 its
  trim; 1, 2, 7, 14 and 15 are skates, skin, stick and the ice shadow and stay.
  Home is white with stripes in the team's colours, away in the team's colour
  with white stripes, the cartridge's convention, so a mapped team never
  clashes with one the patch leaves alone.
* Playoffs. 32 seed tables of 16 slots at $9C:ED59: eight Eastern teams then
  eight Western, the bracket's first-round pairs in order. New Playoffs picks
  one at random and walks on from it to the first holding the user's team,
  forever if none does, so every slot 0-25 is in some table. They are rebuilt
  with today's conferences. Slots 26 and 27 are exhibition-only: the team
  selector stops at 25.

Everything this module adds lives in `REGION`, 0x100000-0x15FFFF of the image
grown to 2 MiB; no stream crosses a 32 KiB bank, since the game's decoder
indexes one bank with a 16-bit register. The region is rebuilt on every
patch: what a slot left alone points at there is carried over first, so a
patched ROM can be patched again.

`write` takes an image `recognised` as the cartridge's; within it, a part
that does not look as on the cartridge is skipped with a status line rather
than written.
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from ...core.errors import RomError
from ...formats import ea_huff, snes
from ...formats.ea_tdb import EaTdbError, refpack_compress, refpack_decompress
from . import portraits
from .models import MODERN_NHL_TO_NHL94, NHL94TeamRecord

RGB = tuple[int, int, int]
StatusFn = Callable[[str], None]


@dataclass(frozen=True)
class Club:
    """What the cartridge needs to know about a club and the provider may not
    say (the NHL API sends no colours), or says wrong (ESPN's arenas for New
    Jersey and Winnipeg are long gone)."""

    city: str
    nickname: str
    arena: str
    #: ESPN's `color` and `alternateColor`, used when the provider sends none.
    color: str
    alternate: str
    east: bool


#: The 32 clubs by ESPN abbreviation, which the HUD shows. October 2026.
CLUBS: dict[str, Club] = {
    "ANA": Club("Anaheim", "Ducks", "Honda Center", "fc4c02", "000000", False),
    "BOS": Club("Boston", "Bruins", "TD Garden", "231f20", "fdb71a", True),
    "BUF": Club("Buffalo", "Sabres", "KeyBank Center", "00468b", "fdb71a", True),
    "CGY": Club("Calgary", "Flames", "Scotiabank Saddledome", "dd1a32", "000000", False),
    "CAR": Club("Carolina", "Hurricanes", "Lenovo Center", "e30426", "000000", True),
    "CHI": Club("Chicago", "Blackhawks", "United Center", "e31937", "000000", False),
    "COL": Club("Colorado", "Avalanche", "Ball Arena", "860038", "005ea3", False),
    "CBJ": Club("Columbus", "Blue Jackets", "Nationwide Arena", "002d62", "e31937", True),
    "DAL": Club("Dallas", "Stars", "American Airlines Center", "20864c", "000000", False),
    "DET": Club("Detroit", "Red Wings", "Little Caesars Arena", "e30526", "ffffff", True),
    "EDM": Club("Edmonton", "Oilers", "Rogers Place", "00205b", "ff4c00", False),
    "FLA": Club("Florida", "Panthers", "Amerant Bank Arena", "e51937", "002d62", True),
    "LA": Club("Los Angeles", "Kings", "Crypto.com Arena", "121212", "a2aaad", False),
    "MIN": Club("Minnesota", "Wild", "Grand Casino Arena", "124734", "ae122a", False),
    "MTL": Club("Montreal", "Canadiens", "Bell Centre", "c41230", "013a81", True),
    "NSH": Club("Nashville", "Predators", "Bridgestone Arena", "fdba31", "002d62", False),
    "NJ": Club("New Jersey", "Devils", "Prudential Center", "e30b2b", "000000", True),
    "NYI": Club("New York", "Islanders", "UBS Arena", "00529b", "f47d31", True),
    "NYR": Club("New York", "Rangers", "Madison Square Garden", "0056ae", "e51937", True),
    "OTT": Club("Ottawa", "Senators", "Canadian Tire Centre", "dd1a32", "b79257", True),
    "PHI": Club("Philadelphia", "Flyers", "Xfinity Mobile Arena", "fe5823", "000000", True),
    "PIT": Club("Pittsburgh", "Penguins", "PPG Paints Arena", "000000", "fdb71a", True),
    "SJ": Club("San Jose", "Sharks", "SAP Center", "00788a", "070707", False),
    "SEA": Club("Seattle", "Kraken", "Climate Pledge Arena", "000d33", "a3dce4", False),
    "STL": Club("St. Louis", "Blues", "Enterprise Center", "0070b9", "fdb71a", False),
    "TB": Club("Tampa Bay", "Lightning", "Benchmark International Arena", "003e7e", "ffffff", True),
    "TOR": Club("Toronto", "Maple Leafs", "Scotiabank Arena", "003e7e", "ffffff", True),
    "UTA": Club("Utah", "Mammoth", "Delta Center", "000000", "7ab2e1", False),
    "VAN": Club("Vancouver", "Canucks", "Rogers Arena", "003e7e", "008752", False),
    "VGK": Club("Vegas", "Golden Knights", "T-Mobile Arena", "344043", "b4975a", False),
    "WSH": Club("Washington", "Capitals", "Capital One Arena", "d71830", "0b1f41", True),
    "WPG": Club("Winnipeg", "Jets", "Canada Life Centre", "002d62", "c41230", False),
}

#: The NHL API's codes for the four clubs ESPN abbreviates in two letters.
ALIASES = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}


def club_code(code: str) -> str | None:
    """The `CLUBS` key for a provider's team code, or None."""
    code = ALIASES.get(code.upper(), code.upper())
    return code if code in CLUBS else None


def logo_url(code: str) -> str:
    """ESPN's logo for a club, for a provider that sends none."""
    return f"https://a.espncdn.com/i/teamlogos/nhl/500/{code.lower()}.png"


# -- where things are (headerless file offsets) --------------------------------

TWO_MIB = 0x200000
#: This module's part of the grown image.
REGION = range(0x100000, 0x160000)
BANK = 0x8000

POINTER_TABLE = 0xE25E7  # team blocks, 16-bit pointers into bank $9C, 4 bytes a slot
BANK_9C = 0xE0000
STRINGS_WORD = 4
#: (CPU address of the length-prefixed string, its room) for slots 13 and 14.
NY_STRINGS = {13: (0x9BC5E7, 14), 14: (0x9BC5F5, 12)}
NY_CODES = {13: "NYI", 14: "NYR"}

BANNER_POINTERS = 0xE1649
BANNER_LIST = range(0xE1681, 0xE1786)
#: Banner font widths in pixels ($9C:9623); a lower-case letter draws as its capital.
_WIDTHS = [7, 7, 6, 7, 6, 6, 7, 6, 2, 5, 7, 5, 8, 7, 8, 6, 8, 6, 5, 6, 6, 7, 8, 6, 6, 5, 3]
BANNER_WIDTHS = dict(zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ.", _WIDTHS, strict=True))
BANNER_WIDTHS |= dict.fromkeys(" 0123456789", 5)
BANNER_MAX_WIDTH = 66

BANNER_PALETTES = 0xE050F
LOGO_PALETTES = 0xE0497
CENTER_ICE = 0xE03B7
AWAY_UNIFORMS = 0xB49CE
HOME_UNIFORMS = 0xB4D4E
#: Each loads the logo archive: A9 bb 00 85 0E A9 aa aa.
LOGO_LOADERS = (0xE87DB, 0xEC69B, 0xED9B7)
ARCHIVE_SIZE = 0x7C00
ARCHIVE_ENTRIES = 30
SEEDS = 0xE6D59
SEED_TABLES = 32
SEED_SIZE = 16
TEAM_SLOTS = 26  # slots that play the playoffs
SLOTS = 28

#: BG palette 1, the rink's, in the game (CGRAM); the center-ice logo draws in it.
RINK_PALETTE = (
    0x7F98, 0x6F7B, 0x4A52, 0x2529, 0x1084, 0x025B, 0x01B6, 0x0000,
    0x4884, 0x3400, 0x0124, 0x0080, 0x0136, 0x000D, 0x35B6, 0x112D,
)  # fmt: skip

WHITE: RGB = (232, 232, 232)
GREY: RGB = (160, 160, 160)


def banner_width(text: str) -> int:
    """How wide the banner font draws `text`, in pixels."""
    return sum(BANNER_WIDTHS.get(c, 5) for c in text.upper())


# -- small helpers ---------------------------------------------------------------


def _file(cpu: int) -> int:
    return (cpu & 0x7F0000) >> 1 | (cpu & 0x7FFF)


def _cpu(offset: int) -> int:
    return 0x800000 | (offset << 1 & 0x7F0000) | 0x8000 | (offset & 0x7FFF)


def _u16(rom: bytes | bytearray, at: int) -> int:
    return rom[at] | rom[at + 1] << 8


def _long(rom: bytes | bytearray, at: int) -> int:
    return int.from_bytes(rom[at : at + 3], "little")


def _set_long(rom: bytearray, at: int, cpu: int) -> None:
    rom[at : at + 3] = cpu.to_bytes(3, "little")


def _hex(value: str) -> RGB | None:
    text = (value or "").strip().lstrip("#")
    try:
        rgb = bytes.fromhex(text)
    except ValueError:
        return None
    return (rgb[0], rgb[1], rgb[2]) if len(rgb) == 3 else None


def bgr555(rgb: RGB) -> int:
    r, g, b = rgb
    return r >> 3 | (g >> 3) << 5 | (b >> 3) << 10


def _scale(rgb: RGB, k: float) -> RGB:
    r, g, b = (min(255, int(c * k)) for c in rgb)
    return r, g, b


def _luma(rgb: RGB) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _visible(rgb: RGB) -> RGB:
    """Black as the cartridge draws a black team: a dark grey the shading shows on."""
    return (80, 80, 80) if max(rgb) < 48 else rgb


def _words(values: list[int]) -> bytes:
    return struct.pack(f"<{len(values)}H", *values)


def _string(text: str) -> bytes:
    data = text.encode("ascii", "replace")
    return struct.pack("<H", len(data) + 2) + data


# -- one slot's identity -----------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    """Everything `write` puts in one slot."""

    code: str  # the `CLUBS` key, also the HUD abbreviation
    club: Club
    color: RGB
    alternate: RGB
    #: The logo picture (PNG or anything Pillow reads); None keeps the cartridge's.
    logo: bytes | None = None


def identity_of(record: NHL94TeamRecord, logo: bytes | None = None) -> Identity | None:
    """`record`'s club, in its provider's colours where it sent them, or None
    for a record with no team or one this module does not know."""
    if record.team is None:
        return None
    code = club_code(record.team.code)
    if code is None:
        return None
    club = CLUBS[code]
    color = _hex(record.team.color) or _hex(club.color)
    alternate = _hex(record.team.alternate_color) or _hex(club.alternate)
    assert color is not None and alternate is not None
    return Identity(code, club, color, alternate, logo)


# -- the writer ---------------------------------------------------------------------


class _Region:
    """A bump allocator over `REGION` that never lets a blob cross a bank."""

    def __init__(self, rom: bytearray) -> None:
        self.rom = rom
        self.at = REGION.start

    def put(self, blob: bytes) -> int:
        """Write `blob`; its CPU address."""
        if len(blob) > BANK:
            raise RomError(f"A {len(blob)}-byte stream does not fit one bank")
        if self.at % BANK + len(blob) > BANK:
            self.at += BANK - self.at % BANK
        if self.at + len(blob) > REGION.stop:
            raise RomError("The team identity does not fit its part of the ROM")
        self.rom[self.at : self.at + len(blob)] = blob
        self.at += len(blob)
        return _cpu(self.at - len(blob))


def recognised(rom: bytes | bytearray) -> bool:
    """Does `rom` (headerless) have the cartridge's logo loaders and banner list,
    the layout `write` was measured on?"""
    sites = [bytes(rom[s : s + 8]) for s in LOGO_LOADERS]
    if not all(s[0] == 0xA9 and s[2:6] == b"\x00\x85\x0e\xa9" and s == sites[0] for s in sites):
        return False
    for slot in range(SLOTS):
        at = BANK_9C + _u16(rom, BANNER_POINTERS + 2 * slot) - 0x8000
        if at not in BANNER_LIST or rom.find(b"\0", at, BANNER_LIST.stop) < 0:
            return False
    return True


def write(rom: bytearray, teams: Mapping[int, Identity], status: StatusFn) -> None:
    """Write each slot's identity into a `recognised` `rom` (headerless, in
    place), growing it to 2 MiB. Call it before the roster writer packs the
    team blocks."""
    snes.grow(rom, TWO_MIB)
    old = bytes(rom[REGION.start : REGION.stop])
    rom[REGION.start : REGION.stop] = b"\xff" * len(REGION)
    region = _Region(rom)

    def read(cpu: int) -> bytes:
        """The bytes from `cpu` on, as they were before the region was cleared."""
        at = _file(cpu)
        return old[at - REGION.start :] if at in REGION else bytes(rom[at:])

    # First what the cartridge's pointers lead to in the region, from a patch
    # before this one; then this patch's, over the pointers of the slots it maps.
    for at in (
        [BANNER_PALETTES + 4 * s for s in range(SLOTS)]
        + [LOGO_PALETTES + 4 * s for s in range(ARCHIVE_ENTRIES)]
        + [portraits.PALETTES + 4 * s for s in range(SLOTS)]
    ):
        if _file(_long(rom, at)) in REGION:
            _set_long(rom, at, region.put(read(_long(rom, at))[:32]))
    for at in (CENTER_ICE + 8 * s for s in range(SLOTS)):
        tiles, layout = _long(rom, at), _long(rom, at + 4)
        if _file(tiles) in REGION:
            _set_long(rom, at, region.put(refpack_compress(refpack_decompress(read(tiles)))))
        if _file(layout) in REGION:
            width, height = struct.unpack_from("<HH", read(layout))
            _set_long(rom, at + 4, region.put(read(layout)[: 6 + 2 * width * height]))

    _write_logos(rom, teams, region, read, status)
    faces = portraits.table_ok(rom)
    for slot, team in teams.items():
        _set_long(rom, BANNER_PALETTES + 4 * slot, region.put(_banner_palette(team)))
        if faces:
            at = _file(_long(rom, portraits.PALETTES + 4 * slot))
            palette = _portrait_palette(bytes(rom[at : at + 32]), team)
            _set_long(rom, portraits.PALETTES + 4 * slot, region.put(palette))
        _write_uniforms(rom, slot, team)
    _write_texts(rom, teams, status)
    _write_banners(rom, teams, status)
    _write_seeds(rom, status)


def _write_texts(rom: bytearray, teams: Mapping[int, Identity], status: StatusFn) -> None:
    """Each slot's four block strings, and the NY strings when 13 or 14 change hands."""
    for slot, team in sorted(teams.items()):
        club = team.club
        texts = [club.city, team.code, club.nickname, club.arena]
        if not _put_strings(rom, slot, b"".join(_string(t) for t in texts)):
            status(f"Slot {slot}: team block not as on the cartridge; its name was kept")
        if slot in NY_STRINGS and team.code != NY_CODES[slot]:
            cpu, room = NY_STRINGS[slot]
            at = _file(cpu)
            if 2 <= _u16(rom, at) <= room:  # a string in its room, as on the cartridge
                text = _string(club.city[: room - 2])
                rom[at : at + len(text)] = text


def _block(rom: bytes | bytearray, slot: int) -> int:
    """A team block's file offset: the game reads its pointer in bank $9C."""
    return BANK_9C + (_u16(rom, POINTER_TABLE + 4 * slot) & 0x7FFF)


def _put_strings(rom: bytearray, slot: int, new: bytes) -> bool:
    """`new` as the block's strings: in place when it fits, else in the longest
    run of 0xFF in bank $9C (the padding that ends it, on the cartridge), past a
    16-byte margin. The bytes it leaves become 0xFF, the roster writer's free
    space. False, writing nothing, when the block's strings are not four sane
    ones."""
    base = _block(rom, slot)
    address = 0x8000 + base - BANK_9C + _u16(rom, base + STRINGS_WORD) & 0xFFFF
    if address < 0x8000:
        return False
    start = end = BANK_9C + address - 0x8000
    for _ in range(4):
        if end + 2 > BANK_9C + BANK or not 2 <= _u16(rom, end) <= 40:
            return False
        end += _u16(rom, end)
    if end > BANK_9C + BANK:
        return False
    if len(new) <= end - start:
        rom[start:end] = new.ljust(end - start, b"\xff")
        return True
    kept = bytes(rom[start:end])
    rom[start:end] = b"\xff" * (end - start)
    runs = [m for m in _ff_runs(rom, BANK_9C, BANK_9C + BANK) if m[1] - m[0] >= len(new) + 16]
    if not runs:
        rom[start:end] = kept
        raise RomError("Bank $9C has no room left for the team names")
    at = max(runs, key=lambda m: m[1] - m[0])[0] + 16
    rom[at : at + len(new)] = new
    struct.pack_into("<H", rom, base + STRINGS_WORD, (at - base) & 0xFFFF)
    return True


def _ff_runs(rom: bytes | bytearray, lo: int, hi: int) -> list[tuple[int, int]]:
    """The runs of 0xFF in `rom[lo:hi]`, as (start, end)."""
    runs: list[tuple[int, int]] = []
    at = lo
    while (at := rom.find(b"\xff", at, hi)) >= 0:
        end = at
        while end < hi and rom[end] == 0xFF:
            end += 1
        runs.append((at, end))
        at = end
    return runs


def _write_banners(rom: bytearray, teams: Mapping[int, Identity], status: StatusFn) -> None:
    """The banner list rebuilt where it is, the slots left alone keeping theirs."""
    names = []
    for slot in range(SLOTS):
        at = BANK_9C + _u16(rom, BANNER_POINTERS + 2 * slot) - 0x8000
        names.append(bytes(rom[at : rom.find(b"\0", at, BANNER_LIST.stop)]))
    for slot, team in teams.items():
        names[slot] = team.club.city.encode("ascii", "replace")
    blob, where = b"", {}
    for name in dict.fromkeys(names):
        where[name] = BANNER_LIST.start + len(blob)
        blob += name + b"\0"
    if len(blob) > len(BANNER_LIST):
        status("Banner names do not fit; kept")
        return
    rom[BANNER_LIST.start : BANNER_LIST.start + len(blob)] = blob
    for slot, name in enumerate(names):
        struct.pack_into("<H", rom, BANNER_POINTERS + 2 * slot, 0x8000 + where[name] - BANK_9C)


def _ramp(rgb: RGB, steps: int) -> list[int]:
    """`steps` shades of `rgb`, dark to light."""
    return [bgr555(_scale(rgb, 0.45 + 0.55 * k / (steps - 1))) for k in range(steps)]


def _banner_palette(team: Identity) -> bytes:
    """Black, white, the top band, the bottom band, the yellow border."""
    top = _visible(team.color)
    bottom = _visible(team.alternate)
    if bottom == top or _luma(bottom) > 200:  # a white band would swallow the white letters
        bottom = _scale(top, 0.6)
    return _words([0, 0x6F7B, *_ramp(top, 6), *_ramp(bottom, 7)[::-1], 0x031A])


def _portrait_palette(current: bytes, team: Identity) -> bytes:
    """`current` with the jersey in the team's colours: white stripes (13), its
    colour (14), its alternate (15), as the cartridge dresses most slots."""
    words = list(struct.unpack("<16H", current))
    words[13:] = [bgr555(WHITE), bgr555(team.color), bgr555(team.alternate)]
    return _words(words)


def _write_uniforms(rom: bytearray, slot: int, team: Identity) -> None:
    """Home white and away in the team colour; skates, skin, stick and the ice
    shadow stay the cartridge's."""
    c1, c2 = team.color, team.alternate
    pants = _scale(min(c1, c2, key=_luma), 0.8)
    for table, home in ((HOME_UNIFORMS, True), (AWAY_UNIFORMS, False)):
        at = table + 32 * slot
        words = list(struct.unpack_from("<16H", rom, at))
        jersey = WHITE if home else _visible(c1)
        shade = GREY if home else _scale(jersey, 0.55)
        stripe, middle = (c1, c2) if home else (WHITE, c2)
        roles = {3: jersey, 5: jersey, 6: jersey, 12: jersey, 13: jersey, 11: shade}
        roles |= {4: pants, 8: stripe, 9: middle, 10: stripe}
        for entry, rgb in roles.items():
            words[entry] = bgr555(rgb)
        rom[at : at + 32] = _words(words)


def _write_seeds(rom: bytearray, status: StatusFn) -> None:
    """32 brackets, eight of today's East then eight of its West, each conference
    rotated through the tables so every slot plays in some."""
    current = rom[SEEDS : SEEDS + SEED_TABLES * SEED_SIZE]
    tables = [current[i : i + SEED_SIZE] for i in range(0, len(current), SEED_SIZE)]
    if any(max(t) >= TEAM_SLOTS or len(set(t)) != SEED_SIZE for t in tables):
        status("Playoff brackets not as on the cartridge; kept")
        return
    east = sorted({s for c, s in MODERN_NHL_TO_NHL94.items() if s < TEAM_SLOTS and _east(c)})
    west = sorted({s for c, s in MODERN_NHL_TO_NHL94.items() if s < TEAM_SLOTS} - set(east))
    out = bytearray()
    for k in range(SEED_TABLES):
        for side in (east, west):
            out += bytes(side[(8 * k + i) % len(side)] for i in range(8))
    rom[SEEDS : SEEDS + len(out)] = out


def _east(code: str) -> bool:
    key = club_code(code)
    return key is not None and CLUBS[key].east


# -- logos -----------------------------------------------------------------------


def _archive_entries(archive: bytes) -> list[bytes]:
    offsets = list(struct.unpack_from(f"<{ARCHIVE_ENTRIES}I", archive)) + [len(archive)]
    return [archive[offsets[i] : offsets[i + 1]] for i in range(ARCHIVE_ENTRIES)]


def _build_archive(entries: list[bytes]) -> bytes:
    out = bytearray(4 * len(entries))
    for i, entry in enumerate(entries):
        struct.pack_into("<I", out, 4 * i, len(out))
        out += entry
    return bytes(out)


def _decode(stream: bytes) -> bytes:
    if stream[:2] == ea_huff.MAGIC:
        return ea_huff.decompress(stream)
    return refpack_decompress(stream)


def _write_logos(
    rom: bytearray,
    teams: Mapping[int, Identity],
    region: _Region,
    read: Callable[[int], bytes],
    status: StatusFn,
) -> None:
    """The big-logo archive, its palettes and center ice for each team with a logo.

    The archive is rebuilt whenever it already lives in `REGION`, so it moves
    with the rest; the loaders all name one stream, the first is read.
    """
    site = LOGO_LOADERS[0]
    current = rom[site + 1] << 16 | _u16(rom, site + 6)
    pictures = {slot: t.logo for slot, t in teams.items() if t.logo}
    if not pictures and _file(current) not in REGION:
        return
    try:
        entries = _archive_entries(_decode(read(current)))
    except (EaTdbError, IndexError, struct.error):
        status("Logo archive not as on the cartridge; logos kept")
        return

    from .logo import big_logo, center_ice  # Pillow, the optional images extra

    drawn = 0
    for slot, picture in sorted(pictures.items()):
        try:
            entry, palette = big_logo(picture)
            tiles, layout = center_ice(picture, RINK_PALETTE)
        except Exception as exc:  # noqa: BLE001 - Pillow's errors share no base; a logo never fails the patch
            status(f"Slot {slot}: logo skipped: {exc}")
            continue
        candidate = entries[:slot] + [entry] + entries[slot + 1 :]
        if len(_build_archive(candidate)) > ARCHIVE_SIZE:
            status(f"Slot {slot}: logo skipped: the logo archive is full")
            continue
        entries = candidate
        _set_long(rom, LOGO_PALETTES + 4 * slot, region.put(palette))
        _set_long(rom, CENTER_ICE + 8 * slot, region.put(refpack_compress(tiles)))
        _set_long(rom, CENTER_ICE + 8 * slot + 4, region.put(layout))
        drawn += 1
    archive = _build_archive(entries)
    stream = refpack_compress(archive.ljust(ARCHIVE_SIZE, b"\0"))
    address = region.put(stream)
    for site in LOGO_LOADERS:
        rom[site + 1] = address >> 16
        rom[site + 6 : site + 8] = (address & 0xFFFF).to_bytes(2, "little")
    if drawn:
        status(f"Team logos drawn: {drawn}")
