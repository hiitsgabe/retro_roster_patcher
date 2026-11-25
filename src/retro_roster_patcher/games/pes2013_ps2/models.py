"""Where PES 2013 (PS2, SLUS_219.55) keeps its rosters, and the records this
patcher writes.

Players: `0_TEXT.AFS` entry 47 part 0 (Latin names) and entry 46 part 0
(Japanese names, same stats) hold 4941 records of 124 bytes:

    +0   UTF-16LE display name, 32 bytes
    +32  ASCII shirt name, 16 bytes
    +48  packed abilities, 44 bytes (`abilities.FIELDS`; byte 53 high nibble = registered
         position), then the look from +92

Bytes 48-123, bit by bit (counts over the 4790 records the disc's squads use):

    48-49    commentary name (`CALL_NAME_BYTE`): written, his own or none
    50       bits 0-2 the edited flags (name, shirt name, commentary name; PES 6's layout,
             bit 2 seen set by Edit Player on a commentary-name change), bits 3-7 unused: 0 on
             every record; left as they are
    51       bit 0 `NATIONAL_DOUBLE_FLAG`: cleared on a written player; bits 1-7 unused (0)
    52-89    `abilities.FIELDS`, the registered position (53 bits 4-7) and the playable
             flags (bit 7 of 54-65); the bits of 85, 87, 88 and 89 that no field names are
             listed in `abilities`
Kept below means as the record has it, or, for a player on the disc already, copied from his
own record with the look region (92-123).

    90-91    Unidentified (the abilities module's "shirt and body shape" is PES 6's reading,
             not checked here): 90 holds 54 values (120 on 1948 records, 88 on 837), 91 holds
             151 (119 on 1697); kept as the record has them
    92-104   the look `RomWriter.set_look` writes (below), and in the gaps: 93 bits 3-7
             (0-22, 2 on 2153), 97 (0 on 4740, else 4, 8 or 32), 98 (0 on 4608, else 16-160 in
             steps of 16), 99 bits 0-5 (0 on 4770) and bit 7 (set on 138), 100 (bits 0-3 15 on
             4688; bits 4-7 0 on 3830) and 101 (0 on 3770, else 1-111: with 100 bits 4-7 the
             12-bit face model number of FVitor7's PES-EDITOR-PS2, used by the 1027 records
             whose face type is 2), 103 bits 6-7 and 104 bits 6-7: Unidentified, kept, or
             copied with the rest of the look region from a disc player's own record
    105-108  Unidentified: 0x77 on most records (3033, 2250, 3684, 3611), two nibbles of 7 each,
             the middle of a 0-14 slider; plausibly PES's physique sliders, not checked; kept
    109-111  Unidentified: 109 holds 38 values (23 on 1931, 7 on 1799), 110 is 0 on 4785, 111
             is 0 on every record; kept
    112      nationality (`NATIONALITY_BYTE`): written
    113      bit 0 unused (0), bits 1-5 age (written), bits 6-7 0 on every record
    114      Unidentified: 0 on 4725, else 1-48; kept
    115      bits 0-2 skin (written); bits 3-7 Unidentified (all 32 values, 25 on 472); kept
    116-123  Unidentified: each 28-192 values over the whole range, no value on more than
             524 records (face-part parameters, by PES 6's look layout; not checked); kept

Squads, in both entries: part 2 = clubs, 139 rows x 32 u16 player index (row =
club index); part 4 = shirt number - 1 per slot; part 1 = nationals, 75 rows x
23 u16; part 3 = the national shirt numbers (number - 1, 23 per row). The game
shows the club table in RAM in the same order, so a club slot here is the
club's row.

Formations: `0_TEXT.AFS` entry 53 part 1 holds one 52-byte record per team (see
`formations.py`), national row r as record r and club c as record 67 + c.

Team names: `OVER.AFS` entries 5-19 are `database_<lang>.ovl` overlays, one per
language, each with a table of `{name ptr, code ptr, u32, u32 id}` entries
whose strings sit in place; a new name has to fit the room before the next
string.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.kits import KitSpec

BOOT_FILE = "SLUS_219.55"
TEXT_AFS = "0_TEXT.AFS"
OVER_AFS = "OVER.AFS"

#: `0_TEXT.AFS` entries carrying the player table and the squads.
PLAYER_ENTRY_LATIN = 47
PLAYER_ENTRY_JAPANESE = 46
PLAYER_ENTRIES = (PLAYER_ENTRY_LATIN, PLAYER_ENTRY_JAPANESE)
#: The South American clubs' players (Copa Libertadores, 2012): 943 records of
#: the same 124 bytes in part 0 of entry 49 (48 in Japanese), 38 squads of 25
#: in part 1 (each u16 0xA000 + record). A disc record from this table is
#: `LIBERTADORES_BASE` + its index wherever a record number is kept.
LIBERTADORES_ENTRY = 49
LIBERTADORES_BASE = 10000
#: Parts inside each of those entries.
PART_PLAYERS = 0
PART_NATIONAL_SQUADS = 1
PART_CLUB_SQUADS = 2
#: The national shirt numbers, number - 1, 23 per national row (not a lineup order).
PART_NATIONAL_ORDER = 3
PART_CLUB_NUMBERS = 4

PLAYER_RECORD_SIZE = 124
DISPLAY_NAME_BYTES = 32
DISPLAY_NAME_CHARS = DISPLAY_NAME_BYTES // 2 - 1
SHIRT_NAME_BYTES = 16
SHIRT_NAME_CHARS = SHIRT_NAME_BYTES - 1
POSITION_BYTE = 53
#: The playable-position flags are bit 7 of bytes 54-65 (bits 0-6 are abilities): the goalkeeper's
#: is byte 54, any other position's is byte 53 + its nibble (CB 3 is byte 56, WF 10 is byte 63).
#: The registered position's own flag is always set on the disc (4940 of 4940 records).
PLAYABLE_GK_BYTE = 54
PLAYABLE_FLAG = 0x80
#: The look fields `RomWriter.set_look` writes, the layout PES 6's (the
#: COFPES option-file editor's attribute files), checked one by one in
#: PCSX2's Edit Player on 2026-10-25:
#: face type, byte 102 bits 0-1 (0 Build, the generic face that takes the
#: skin and hair; 1 Special; 2 Preset, a face model that ignores them); skin
#: colour, byte 115 bits 0-2; hairstyle, byte 92 and byte 93 bits 0-2, with
#: the special-hairstyle flag (a licensed player's hair, which ignores the
#: colour) in byte 99 bit 6; hair colour, byte 94 bits 0-5 (a pattern 0-61,
#: or 62 for RGB) and R, G, B 0-63, each stored as 63 minus the value: R in
#: byte 102 bits 2-7, G and B in bytes 103 and 104 bits 0-5; beard, byte 95
#: bits 0-6 (0 none; bit 7 the cap), its colour byte 96 bits 0-5 (62: the
#: hair's).
SKIN_BYTE = 115
FACE_TYPE_BYTE = 102
FACE_TYPE_MASK = 0x03
HAIRSTYLE_BYTE = 92
SPECIAL_HAIR_BYTE = 99
SPECIAL_HAIR_BIT = 0x40
HAIR_COLOUR_BYTE = 94
COLOUR_RGB = 62
BEARD_BYTE = 95
BEARD_COLOUR_BYTE = 96
#: Nationality (the national-team row order, 0 Austria .. 59 New Zealand,
#: then nations without a squad) and age (bits 1-5 of byte 113, plus 15), as
#: the disc's national squads and known players showed them (2026-10-25).
NATIONALITY_BYTE = 112
#: A player whose nationality the disc has no flag for, or who has none, takes his team's most
#: common one; a team with none known at all, this (0, Austria, the first of the list).
DEFAULT_NATIONALITY = 0
#: The commentary name, bytes 48-49 (u16): an id of `0_TEXT.AFS` entry 9531's table, the name
#: Edit Player's "Commentary Name" shows and the commentator says (`CALL_NAMES_FILE`, the 2109
#: ids that list names); `NO_CALL_NAME` is its "---", which 1149 disc records have.
CALL_NAME_BYTE = 48
NO_CALL_NAME = 0xFFFF
CALL_NAMES_FILE = "pes2013_call_names.json"
#: Byte 51 bit 0: set on the 119 club records (Ramsey, Ivanović, Mikel, Dempsey...) that have a
#: fake-named copy in an unlicensed national row (Wales' "RAZROUGH"), on no other record, and on
#: none of the copies. A written player is a new person with no such copy: it is cleared.
NATIONAL_DOUBLE_BYTE = 51
NATIONAL_DOUBLE_FLAG = 0x01
AGE_BYTE = 113
AGE_SHIFT = 1
AGE_MASK = 0x1F
AGE_BASE = 15
#: The look region a patch copies from one record to another: hair, face
#: and skin fields, flags included, and with them the nationality and age
#: bytes, right for the same person; the names, position byte and abilities
#: before it stay the target's own.
LOOK_START = 92
LOOK_END = 124

CLUB_COUNT = 139
CLUB_SQUAD_SIZE = 32
NATIONAL_COUNT = 75
NATIONAL_SQUAD_SIZE = 23
#: National teams are slots too: row `r` of the national squad table is slot
#: `NATIONAL_SLOT_BASE + r`. Only the named rows (the first 67, which the
#: overlay team table names) are offered.
NATIONAL_SLOT_BASE = 1000
NATIONAL_NAMED = 67
#: The nations' rows, 0-59; the seven after them are the Classic sides (England, France,
#: Germany, Italy, Netherlands, Argentina, Brazil), which wear their nation's flag.
NATION_ROWS = 60
#: Nationalities the disc has a flag for (`0_TEXT.AFS` emblem table 0, image n is
#: nationality n), each named in every language overlay's nationality table: 0-59 are the
#: nations' rows, 101-103 placeholders without a name. A national row's flag is its
#: nationality, the low half of the third word of its team-table entry (both halves hold
#: it); its name and code are the entry's pointers. Changing the two was checked in PCSX2.
NATIONALITIES = 144
#: Position nibble written into a record that changes hands between groups.
GROUP_POSITIONS = {"Goalkeeper": 0, "Defender": 3, "Midfielder": 7, "Attacker": 12}

#: `0_TEXT.AFS` entry 53 holds the formations: part 1 is one 52-byte record per team, 197 of
#: them. Team `t` is national row `t` (0-66, the seven Classic teams last), or club `c` as record
#: `FIRST_CLUB_FORMATION + c` (clubs 0-129; the clubs past them have no record, as with kits).
FORMATION_ENTRY = 53
FORMATION_PART = 1
FORMATION_RECORD = 52
FORMATION_TEAMS = 197
FIRST_CLUB_FORMATION = 67

#: `0_TEXT.AFS` entries holding the emblem atlases: all teams at 64x64 and 32x32,
#: then the club-only copies the game also reads.
EMBLEM_ENTRIES = (604, 600, 606, 602)
#: (full atlas, club-only copy) of each size; a pair shares one palette.
EMBLEM_PAIRS = ((604, 606), (600, 602))

#: Screen art the patch can replace: slot name -> texture name in the UI
#: bundles (every occurrence, in every language archive, is rewritten).
IMAGE_SLOTS = {
    "title": "title_bgNS",  # the title screen (512x512)
    "menu": "mo_sub_bgUS",  # behind the main menus (512x512)
    "loading": "loading_bg00",  # the loading screen (256x256)
    "match": "ts_match_bg",  # behind the match screens (512x512)
}
IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(IMAGE_SLOTS)
#: The language archive the exported art is read from.
TEXT_ARCHIVE_PRIMARY = "U_TEXT.AFS"

#: The UI text archives, one per language of the US release.
TEXT_LANGUAGE_AFS = ("U_TEXT.AFS", "B_TEXT.AFS", "F_TEXT.AFS", "M_TEXT.AFS")
#: Entries of those archives whose string tables carry the league names.
LEAGUE_NAME_ENTRIES = (15, 17)
#: The league-name tables list the leagues around "Ligue 1", the one string
#: every language shares: a group's name sits this many strings from it.
LEAGUE_NAME_ANCHOR = "Ligue 1"


@dataclass(frozen=True)
class LeagueGroup:
    """A run of club slots the game shows as one league."""

    first: int
    count: int
    icon: str  # texture name of the team-select icon
    name_offset: int  # strings from "Ligue 1" in the league-name tables

    def holds(self, slot: int) -> bool:
        return self.first <= slot < self.first + self.count


LEAGUE_GROUPS = (
    LeagueGroup(0, 20, "mn_ts_eng_l", -1),
    LeagueGroup(20, 20, "mn_ts_fra_l", 0),
    LeagueGroup(40, 20, "mn_ts_ita_l", 2),
    LeagueGroup(60, 18, "mn_ts_net_l", 3),
    LeagueGroup(78, 20, "mn_ts_spa_l", 4),
)

#: `OVER.AFS` entries that are language database overlays, and the English one
#: the slot names are read from.
DATABASE_OVERLAYS = tuple(range(5, 20))
DATABASE_OVERLAY_ENGLISH = 18
#: Offset of the overlay's load address in its "MWo3" header.
OVERLAY_LOAD_ADDRESS_OFFSET = 8
#: Team-table entry: name pointer, code pointer, u32, u32 id (low byte = club index for clubs).
TEAM_ENTRY_SIZE = 16
#: Nationals occupy the first entries of the team table; clubs follow.
FIRST_CLUB_ENTRY = 67
TEAM_CODE_BYTES = 8
#: The team table's "UEFA Champions League" picker group: 18 rows carrying a club's id word
#: (Man Utd 7 ... Shakhtar 124), each wearing that club's UCL kits (`kits.PART_UCL`). The
#: Champions League mode offers every club of slots 0-125; these rows are only its first group.
UCL_ROWS = range(326, 344)

#: Registered-position nibble values (byte 53 >> 4) grouped the way ESPN
#: describes positions (2 SW, 3 CB, 4 SB, 6 WB defend; 5 DMF, 7 CMF, 8 SMF, 9 AMF
#: midfield; 10 WF, 11 SS, 12 CF attack). Unknown values count as midfielders.
POSITION_GROUPS = {
    0: "Goalkeeper",
    2: "Defender",
    3: "Defender",
    4: "Defender",
    5: "Midfielder",
    6: "Defender",
    7: "Midfielder",
    8: "Midfielder",
    9: "Midfielder",
    10: "Attacker",
    11: "Attacker",
    12: "Attacker",
}


@dataclass
class PESLook:
    """How the game draws a player, written by `rom_writer.RomWriter.set_look`
    on a built face: his skin colour (0-5, shown as 1-6 in the editor), hair
    colour (a `looks.HAIR_RGB` name, or "bald"), beard and hairstyle; or, for
    a player who is already on the disc, the record whose whole look region
    he keeps (`RomWriter.copy_look`), licensed face and all. The default is
    the plain look of a player without a photo: pale, black hair, no beard."""

    skin: int = 0
    hair: str = "black"
    beard: bool = False
    style: int = 85
    disc_record: int | None = None


@dataclass
class PESPlayer:
    """One player to write: names, his look and his abilities."""

    display_name: str
    shirt_name: str
    number: int | None = None
    position: str = ""  # ESPN group: Goalkeeper / Defender / Midfielder / Attacker
    #: A starter's (one of the first eleven) registered-position nibble: where
    #: the lineup had him, else his group's position; None for a bench player.
    #: Without a formation, starters take the squad places whose records hold
    #: that position, which is how the club's formation is laid out; with one
    #: (`PESTeamRecord.formation`) they stand on the preset's places instead.
    role: int | None = None
    #: His role (`sports.models.Player.role`: GK RB CB LB RWB LWB CDM CM CAM RM LM RW LW ST), ""
    #: when he has none: it sets his registered position and its playable flag
    #: (`patcher.ROLE_NIBBLE`) and seats him inside his line. `position` stays his squad group.
    role_name: str = ""
    #: The provider's player, for his look (`looks.dress`): id and photo for
    #: the head shot, nationality for the East Asian colour.
    player_id: int = 0
    photo_url: str = ""
    nationality: str = ""
    #: His nationality on the disc (`NATIONALITY_BYTE`), set by `Patcher.patch`: his own when
    #: the disc has a flag for it, else his team's default; None for a player who is on the
    #: disc already and keeps the nationality of his disc record.
    nation: int | None = None
    #: Set by `looks.dress`; None leaves the record's look alone.
    look: PESLook | None = None
    #: His age today, 0 when the provider gives neither age nor birth date: the record's
    #: age (`AGE_BYTE`) is left as it is then.
    age: int = 0
    #: The `abilities.FIELDS` his own data sets (`abilities.LeagueStats.abilities`), as stored;
    #: every other field gets his group's default (`abilities.defaults`).
    abilities: dict[str, int] = field(default_factory=dict)


@dataclass
class PESTeamRecord:
    """A club slot's new identity."""

    name: str
    code: str = ""
    players: list[PESPlayer] = field(default_factory=list)
    team_id: int | None = None
    logo_url: str = ""
    #: The league the team came from: written as the slot group's league.
    league_name: str = ""
    league_logo_url: str = ""
    #: A national-team slot (`NATIONAL_SLOT_BASE` and up).
    national: bool = False
    #: A national team's nationality on the disc (`patcher.nation_ident`: its flag, name and
    #: code in every language), set by the patch; it is at home in the row of that number.
    nation: int | None = None
    #: Home and away kits to draw from colours (`core.kits.KitSpec`); None keeps the disc's.
    kit_home: KitSpec | None = None
    kit_away: KitSpec | None = None
    #: The team's formation as the roster gives it (`sports.lineup.team_formation`:
    #: `extra["formation"]`, else its lineup's shape, when `parse_shape` reads it), "" otherwise:
    #: the patch then writes the preset nearest to it (`formations.pick_preset`) and seats the
    #: eleven on its places.
    formation: str = ""


@dataclass
class ClubSlot:
    index: int
    name: str
    code: str


@dataclass
class PESRomInfo:
    path: str
    size: int
    is_valid: bool
    clubs: list[ClubSlot] = field(default_factory=list)
    nationals: list[ClubSlot] = field(default_factory=list)
    player_count: int = 0
