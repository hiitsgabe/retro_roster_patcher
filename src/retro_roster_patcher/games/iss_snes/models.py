"""Data models and team orderings for the ISS SNES patcher.

27 team slots -- 26 national sides and "Super Star" -- of exactly 15 players
each. The 27 slots appear in six different orders; every table here and in
`rom_writer.py` is a permutation of the same 27 names:

  * `TEAM_ENUM_ORDER` -- player *data*, kit colours, the predominant-colour
    byte, the flag-tile pointer table, the description pointer table and the
    team-name-text pointer table. This is the canonical order and the one a
    `SlotMapping.slot_index` means.
  * `TEAM_NAME_ORDER` -- player *names*, and nothing else. Identical to
    `TEAM_ENUM_ORDER` except that Scotland moves from index 5 to index 24.
  * four further orders in `rom_writer.py` for the two outfield kit ranges, the
    goalkeeper kit range and the two flag-colour ranges.

`name_storage_index` is the single enum-order-to-name-order translation; keep it
the only one. Getting it wrong shifts every name from Wales onwards by one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
#: above: Scotland is at 24 here and at 5 there, and everything between shifts
#: down by one.
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

#: Hair-style ordinals, in the order the ROM's low nibble numbers them.
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

#: `TEAM_ENUM_ORDER` index -> `TEAM_NAME_ORDER` index. Derived by lookup, never
#: transcribed: the two lists above must stay the only statement of the orders.
_NAME_ORDER_INDEX = tuple(TEAM_NAME_ORDER.index(name) for name in TEAM_ENUM_ORDER)


def name_storage_index(enum_index: int) -> int:
    """Where slot `enum_index`'s player names are stored.

    Let `IndexError` escape for a slot outside 0..26 rather than swallowing it
    and answering a wrong offset.
    """
    return _NAME_ORDER_INDEX[enum_index]


@dataclass
class ISSPlayerAttributes:
    """One player's four ISS ratings, on the two scales the ROM stores.

    `speed` and `stamina` are 1-16 and occupy a whole byte and a nibble
    respectively. `shooting` and `technique` are 1-15 odd-only: the ROM stores a
    3-bit index into `rom_writer._SHOOTING_VALUES`, so the eight representable
    values are 1, 3, 5, ..., 15 and an even number is rounded to the nearest.
    """

    speed: int = 8  # 1-16
    shooting: int = 7  # 1-15, odd
    stamina: int = 8  # 1-16
    technique: int = 7  # 1-15, odd


@dataclass
class ISSPlayerRecord:
    """Complete player record ready to write to ROM.

    The ROM derives a player's role from his slot in the fifteen, so `position`
    never reaches the image; it only feeds `_select_best_15`'s 4-4-2.
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

    `flag_colors` holds the two colours the flag tiles and the predominant-colour
    byte are built from: empty when the provider gave no primary colour, and then
    neither is written; otherwise exactly two RGB triples, primary then
    alternate, with primary repeated when there is no alternate.
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

    `name` is the constant from `TEAM_ENUM_ORDER`; `first_player` is the only
    text read out of the image, because this reader parses no team name.
    """

    index: int
    name: str
    first_player: str = ""


@dataclass
class ISSRomInfo:
    path: str
    size: int
    team_slots: list[ISSTeamSlot] = field(default_factory=list)
    is_valid: bool = False
    has_header: bool = False  # SNES ROMs may carry a 512-byte copier header
