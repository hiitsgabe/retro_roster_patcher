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

from ...core.kits import KitSpec

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
    """One player's nine ability fields, as the game stores them.

    The 6-byte record at 0x387EC (15 per team, `TEAM_ENUM_ORDER`), traced in the
    game code and checked in Mesen 2 against the DATA bars of the pre-match
    "Substituting" screen:

      * b0: low nibble `dribble` (base speed and a dash's floor), high nibble
        `dash` (dash top speed without the ball).
      * b1: bits 0-2 `kick` (power cap of passes and charged kicks), bits 4-6
        `shooting`. Bits 3 and 7 are 0.
      * b2: bits 0-2 `technique`, high nibble `stamina` (energy pool
        `(16 + 4 * stamina) << 4`). Bit 3 is 0.
      * b3: low nibble shirt number - 1, high nibble `ball_dash` (dash top
        speed with the ball).
      * b4: low nibble `defense`, high nibble `offense`, an opposed pair the game
        compares in duels.
      * b5: low nibble hair style; bit 6 the team's alternate skin and hair
        palette. Bits 4, 5 and 7 are 0.

    The bars read technique `2 * technique`, shooting `2 * shooting`, speed
    `(2 * dribble + dash + ball_dash) >> 2` and stamina `stamina`. The kick-off
    condition adds -5..+2 to every field and speeds floor at 0x1C, so a low
    value plays worse than it reads. Super Star records hold every field at its
    maximum.

    Four-bit fields are 0-15 and three-bit fields 0-7. The defaults are the
    retail outfield medians.
    """

    dribble: int = 7  # 0-15
    dash: int = 10  # 0-15
    ball_dash: int = 9  # 0-15
    kick: int = 4  # 0-7
    shooting: int = 4  # 0-7
    technique: int = 3  # 0-7
    stamina: int = 10  # 0-15
    defense: int = 8  # 0-15
    offense: int = 7  # 0-15


@dataclass
class ISSPlayerRecord:
    """Complete player record ready to write to ROM.

    The ROM derives a player's role from his slot in the fifteen, so `position`
    never reaches the image; it only feeds `_select_best_15`'s 4-4-2.
    """

    name: str  # 8 characters max, ISS custom encoding
    shirt_number: int = 1  # 1-15, each once per team
    position: int = 2  # 0=GK, 1=DF, 2=MF, 3=FW; not stored in the ROM
    attributes: ISSPlayerAttributes = field(default_factory=ISSPlayerAttributes)


@dataclass
class ISSTeamRecord:
    """Complete team record ready to write to ROM.

    `flag_colors` holds the two colours the two-band flag is built from: empty
    when the provider gave no primary colour, and then none is drawn; otherwise
    exactly two RGB triples, primary then alternate, with primary repeated when
    there is no alternate.

    `kit_home_spec` / `kit_away_spec` are the kits `patch` draws by default
    (`core.kits.default_kits`), `kit_home` / `kit_away` their colours; a record
    with colours and no spec gets plain kits in them.
    """

    name: str  # full team name, for the selection screen and the description
    short_name: str  # 3-letter abbreviation, for the in-game name tile
    kit_home: tuple[tuple[int, int, int], ...] = ()  # shirt, shorts, socks
    kit_away: tuple[tuple[int, int, int], ...] = ()
    kit_gk: tuple[tuple[int, int, int], ...] = ()  # shirt, shorts
    kit_home_spec: KitSpec | None = None
    kit_away_spec: KitSpec | None = None
    flag_colors: list[tuple[int, int, int]] = field(default_factory=list)
    players: list[ISSPlayerRecord] = field(default_factory=list)  # up to 15
    # Where the crest comes from: `assets_dir/logos/<team_id>.png`, else the URL.
    team_id: int | None = None
    logo_url: str = ""


@dataclass
class Flag:
    """A 24x16 flag: 16 rows of 24 indices 0-3 into `colours`.

    The four RGB colours land in OBJ palette entries 12-15, in order. Entry 12
    is also the colour of the team's shirt numbers in a match, on both kits
    (seen in Mesen 2), so `rom_writer.number_first` picks it.
    """

    pixels: list[list[int]]
    colours: list[tuple[int, int, int]]


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
