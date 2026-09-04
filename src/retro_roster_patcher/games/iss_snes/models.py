"""Data models and team orderings for the ISS SNES patcher.

International Superstar Soccer (Konami, 1994, SNES) has 27 team slots -- 26
national sides and "Super Star" -- of exactly 15 players each.

**The 27 slots appear in six different orders.** Every table in this file and in
`rom_writer.py` is a permutation of the same 27 names, and which one applies
depends on what is being written:

  * `TEAM_ENUM_ORDER` -- player *data*, kit colours, the predominant-colour
    byte, the flag-tile pointer table, the description pointer table and the
    team-name-text pointer table. This is the canonical order and the one a
    `SlotMapping.slot_index` means.
  * `TEAM_NAME_ORDER` -- player *names*, and nothing else. Identical to
    `TEAM_ENUM_ORDER` except that Scotland moves from index 5 to index 24.
  * four further orders in `rom_writer.py` for the two outfield kit ranges, the
    goalkeeper kit range and the two flag-colour ranges.

`name_storage_index` below is the single place the enum-order-to-name-order
translation happens. Upstream did it inline in `ISSRomWriter.write_player_names`
and nowhere else, which was correct but left the fact untestable and unshared;
this port needs it in the reader too, so it lives here once. Getting it wrong
puts every player on the wrong team, and only for one of the two blocks, which
is the kind of defect that shows up as "the names are fine but shifted by one
from Wales onwards".
"""

from __future__ import annotations

from dataclasses import dataclass, field

# -- the 27 slots, in the two orders the ROM stores them in -------------------

#: Canonical order. A `SlotMapping.slot_index` indexes this list.
TEAM_ENUM_ORDER = [
    "Germany",
    "Italy",
    "Holland",
    "Spain",
    "England",
    "Scotland",
    "Wales",
    "France",
    "Denmark",
    "Sweden",
    "Norway",
    "Ireland",
    "Belgium",
    "Austria",
    "Switz",
    "Romania",
    "Bulgaria",
    "Russia",
    "Argentina",
    "Brazil",
    "Colombia",
    "Mexico",
    "U.S.A.",
    "Nigeria",
    "Cameroon",
    "S.Korea",
    "Super Star",
]

#: The order the 8-byte player-name records are stored in, which is not the one
#: above. Scotland is at 24 here and at 5 there; everything between shifts down
#: by one. `name_storage_index` is the translation.
TEAM_NAME_ORDER = [
    "Germany",
    "Italy",
    "Holland",
    "Spain",
    "England",
    "Wales",
    "France",
    "Denmark",
    "Sweden",
    "Norway",
    "Ireland",
    "Belgium",
    "Austria",
    "Switz",
    "Romania",
    "Bulgaria",
    "Russia",
    "Argentina",
    "Brazil",
    "Colombia",
    "Mexico",
    "U.S.A.",
    "Nigeria",
    "Cameroon",
    "Scotland",
    "S.Korea",
    "Super Star",
]

#: Hair-style ordinals, in the order the ROM's low nibble numbers them. Eleven
#: of them, which is where `rom_writer.write_player_data`'s clamp comes from --
#: it is `len(HAIR_STYLES) - 1` and not a transcribed 10.
HAIR_STYLES = [
    "Short",
    "Curly",
    "Long Curly",
    "Long Beard",
    "Long Straight",
    "Dreadlocks",
    "Afro",
    "Ponytail",
    "Bald",
    "Mid Length",
    "Long Ribbon",
]

PLAYERS_PER_TEAM = 15
TOTAL_TEAMS = 27

#: `TEAM_ENUM_ORDER` index -> `TEAM_NAME_ORDER` index, precomputed.
#:
#: Built by lookup rather than written out, so the two lists above stay the only
#: statement of the fact and a third transcription cannot drift from them. The
#: two lists hold the same 27 names -- `tests/games/iss_snes/test_models.py`
#: asserts that as a set equality, which is what makes every lookup here total.
_NAME_ORDER_INDEX = tuple(TEAM_NAME_ORDER.index(name) for name in TEAM_ENUM_ORDER)


def name_storage_index(enum_index: int) -> int:
    """Where slot `enum_index`'s player names are stored.

    The single translation between the two orders. `rom_writer` uses it to
    write names and `rom_reader` to read them back, so a change to either list
    moves both in step.

    Raises `IndexError` for a slot outside 0..26 rather than answering a wrong
    offset. Upstream caught `ValueError` from `TEAM_NAME_ORDER.index` and
    returned without writing -- a case its own comment called "shouldn't
    happen", and which cannot happen while the two lists hold the same names,
    so that arm was unreachable rather than defensive. An out-of-range slot is
    a caller error and both call sites bound their indices before they get here.
    """
    return _NAME_ORDER_INDEX[enum_index]


# -- record types ------------------------------------------------------------


@dataclass
class ISSPlayerAttributes:
    """One player's four ISS ratings, on the two scales the ROM stores.

    `speed` and `stamina` are 1-16 and occupy a whole byte and a nibble
    respectively. `shooting` and `technique` are 1-15 odd-only: the ROM stores a
    3-bit index into `rom_writer._SHOOTING_VALUES`, so the eight representable
    values are 1, 3, 5, ..., 15 and an even number handed in here is rounded to
    the nearest of them by `_shooting_to_rom`.
    """

    speed: int = 8  # 1-16
    shooting: int = 7  # 1-15, odd
    stamina: int = 8  # 1-16
    technique: int = 7  # 1-15, odd


@dataclass
class ISSPlayerRecord:
    """Complete player record ready to write to ROM.

    `position` never reaches the image. It exists because
    `ISSStatMapper._select_best_15` needs it to build a 4-4-2, and the ROM
    derives a player's role from his slot in the fifteen rather than from a
    stored field.
    """

    name: str  # 8 characters max, ISS custom encoding
    shirt_number: int = 1  # 1-16
    position: int = 2  # 0=GK, 1=DF, 2=MF, 3=FW; not stored in the ROM
    hair_style: int = 0  # index into HAIR_STYLES
    is_special: bool = False  # star player: unique in-game appearance
    attributes: ISSPlayerAttributes = field(default_factory=ISSPlayerAttributes)


@dataclass
class ISSTeamRecord:
    """Complete team record ready to write to ROM.

    `flag_colors` is this port's home for the two colours the flag tiles and the
    predominant-colour byte are built from. Upstream declared the field and left
    it empty, carrying the same two colours in a `patched_flag_colors` dict that
    `ISSPatcher.patch_rom` built beside the records; that dict could not cross
    the `MappedRosters` boundary this library maps through, and the field was
    already here. Empty means the provider gave no primary colour, in which case
    upstream wrote neither the flag nor the predominant byte; otherwise it holds
    exactly two RGB triples, primary then alternate -- and primary twice when the
    provider supplied no alternate, which is what upstream did.
    """

    name: str  # full team name, for the selection screen and the description
    short_name: str  # 3-letter abbreviation, for the in-game name tile
    kit_home: tuple[tuple[int, int, int], ...] = ()  # shirt, shorts, socks
    kit_away: tuple[tuple[int, int, int], ...] = ()
    kit_gk: tuple[tuple[int, int, int], ...] = ()  # shirt, shorts
    flag_colors: list[tuple[int, int, int]] = field(default_factory=list)
    players: list[ISSPlayerRecord] = field(default_factory=list)  # up to 15


@dataclass
class ISSTeamSlot:
    """One of the 27 team-shaped holes in the ROM.

    `name` is the constant from `TEAM_ENUM_ORDER`; `first_player` is read out of
    the image. Upstream's third field was `enum_name`, which held the same string
    as `current_name` -- two names for one constant and nothing from the ROM at
    all. The NBA Live 95 and Ken Griffey Jr. ports answer the same shape, and for
    the same reason: this reader parses no team-name string, so the only
    ROM-derived text it can put in `RomInfo`'s `current_name` is a player's.
    """

    index: int
    name: str
    first_player: str = ""


@dataclass
class ISSRomInfo:
    """Information about a loaded ISS SNES ROM."""

    path: str
    size: int
    team_slots: list[ISSTeamSlot] = field(default_factory=list)
    is_valid: bool = False
    has_header: bool = False  # SNES ROMs may carry a 512-byte copier header
