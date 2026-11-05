"""International Superstar Soccer Deluxe (SNES, USA): the cartridge image.

Checked on the USA cartridge (MD5 345ddedcd63412b9373dabb67c11fc05, 2 MiB
LoROM) in Mesen 2, with the labels of Yoshifanatic1's disassembly of that
version (github.com/Yoshifanatic1/International-Superstar-Soccer-Deluxe-SNES-
Disassembly) and the name layout of rofldark's ISS Deluxe Editor.

* Player names $87:818E (ROM 0x3818E): 36 teams x 20 players x 8 bytes in
  the tall menu font (`encode_name`), padded with 0x00; the game finds each
  team's block through the pointer table $87:8138.
* Player skills $8A:8000 (ROM 0x50000): 36 x 20 records of 7 bytes, the same
  player order. Bytes 0-4 hold ten nibbles, low nibble first: speed, dash,
  curl, shot power, intelligence, balance, jump, dribble, stamina (each the
  bar count the Edit Player Skills screen shows, less one) and the position
  code (1 keeper, 2 defender, 3 defender or midfielder, 4 midfielder, 5
  midfielder or forward, 6 forward: the squad screens colour the name by it,
  and `ALLOWED_LINES` says which lines a code may play). Byte 5 is the shirt
  number less one, a permutation of 0-19 that in nine nations is not the
  record order. The record's index is the player's place (0-10 the eleven,
  11-19 the bench), so the place and its line come from the formation record
  below. Byte 6 flags this patch keeps.
* Team formations $8B:EF48 (ROM 0x5EF48, `FORMATION_PTRS`): one word per team
  (the 36 nations in `TEAMS` order, the six star teams, one extra), the
  address in bank $8B of the team's 31-byte record: the formation id
  (`FORMATIONS`), the ten outfield places' (depth, lateral) offsets as signed
  bytes (depth is further forward within the place's line, a negative lateral
  the team's left) and the ten places' flags (bits 0-1 the line, 1 defence, 2
  midfield, 3 attack; bit 2 the attack arrow). Place 0 is the keeper. Each id
  has a default record, its positions at $82:8134 and its flags at $82:8074
  (`default_record`). Team select prints the id, and the match plays the
  record. The routine that labels a place's line on the squad screens reads
  the id's default flags, so a record keeps the default lines of its id.
* Team ids are the order of `TEAMS`; the game stores them doubled.
* The main menu's default teams (Brazil v England) are immediates at
  `DEFAULT_P1` and `DEFAULT_P2` (`LDA #team` before `STA $0DA0/$1F8C`).
"""

from __future__ import annotations

import struct

from ...core.text import to_ascii

ROM_SIZE = 0x200000
TITLE = b"SUPERSTAR SOCCER 2"
TITLE_AT = 0x7FC0

TEAMS = (
    "Italy", "Holland", "England", "Norway", "Spain", "Ireland", "Portugal", "Denmark",
    "Germany", "France", "Belgium", "Sweden", "Romania", "Bulgaria", "Russia", "Switzerland",
    "Greece", "Croatia", "Austria", "Wales", "Scotland", "N.Ireland", "Czech Rep.", "Poland",
    "Japan", "S.Korea", "Turkey", "Nigeria", "Cameroon", "Morocco", "Brazil", "Argentina",
    "Colombia", "Mexico", "U.S.A.", "Uruguay",
)  # fmt: skip
#: The nations whose big names the stadium select borrows ($82:FAFD): Japan,
#: U.S.A., Spain, Italy, England, Germany, Brazil, Nigeria.
STADIUM_TEAMS = (24, 34, 4, 0, 2, 8, 30, 27)
#: Team select's page order ($81:DA3F on the cartridge).
PAGE_ORDER = (2, 8, 0, 19, 20, 21, 9, 1, 3, 4, 5, 6, 11, 22, 7, 18, 10, 23, 12, 14, 13, 15, 17, 16,
              24, 26, 25, 27, 28, 29, 30, 31, 32, 33, 34, 35)  # fmt: skip
#: Slots to fill first, in order: the stadium nations last, so their names
#: (which the stadium select shows) stay theirs while clubs fit elsewhere.
SLOT_ORDER = tuple(t for t in PAGE_ORDER if t not in STADIUM_TEAMS) + tuple(
    t for t in PAGE_ORDER if t in STADIUM_TEAMS
)

#: Every team with a flag: the nations, then the six special teams.
TEAMS_ALL = (
    *TEAMS,
    "All Star",
    "Euro Star A",
    "Euro Star B",
    "Asian Star",
    "African Star",
    "All American Star",
)
PLAYERS = 20
NAMES = 0x3818E
NAME_LEN = 8
SKILLS = 0x50000
RECORD = 7
DEFAULT_P1 = 0x2A3E6
DEFAULT_P2 = 0x2A3EF

SKILL_NAMES = (
    "speed", "dash", "curl", "shot", "intelligence", "balance", "jump", "dribble", "stamina",
)  # fmt: skip
#: The position codes. FB plays defence or midfield, AM midfield or attack.
GK, CB, FB, MF, AM, FW = 1, 2, 3, 4, 5, 6
GROUP_OF_CODE = {GK: "GK", CB: "DF", FB: "DF", MF: "MF", AM: "MF", FW: "FW"}
#: The lines (0 keeper, 1 defence, 2 midfield, 3 attack) a code may play: the game's
#: DATA_81AE81 ($81:AE81). A player in a place of another line is out of position.
ALLOWED_LINES = {
    GK: frozenset({0}),
    CB: frozenset({1}),
    FB: frozenset({1, 2}),
    MF: frozenset({2}),
    AM: frozenset({2, 3}),
    FW: frozenset({3}),
}

BANK_8B = 0x58000
FORMATION_PTRS = 0x5EF48
FORMATION_RECORD = 31
DEFAULT_POSITIONS = 0x10134
DEFAULT_FLAGS = 0x10074
#: The 16 formations by id, as team select prints them.
FORMATIONS = tuple(
    (
        "4-5-1 4-4-2 4-3-3 4-2-4 3-5-2 3-4-3 3-3-4 3-2-5 "
        "2-5-3 2-4-4 2-3-5 5-4-1 5-3-2 5-2-3 1-5-4 1-4-5"
    ).split()
)

_FONT = {" ": 0x00, "!": 0x52, ".": 0x54, "-": 0x57, "'": 0x5A, ":": 0x5C}
_FONT.update({str(d): 0x5E + d for d in range(10)})
_FONT.update({chr(65 + k): 0x68 + k for k in range(26)})
_FONT.update({chr(97 + k): 0x82 + k for k in range(26)})
_CHARS = {v: k for k, v in _FONT.items()}


def is_deluxe(image: bytes) -> bool:
    return len(image) == ROM_SIZE and image[TITLE_AT : TITLE_AT + len(TITLE)] == TITLE


def encode_name(text: str) -> bytes:
    codes = [_FONT[c] for c in to_ascii(text) if c in _FONT][:NAME_LEN]
    return bytes(codes).ljust(NAME_LEN, b"\0")


def decode_name(raw: bytes) -> str:
    return "".join(_CHARS.get(b, "?") for b in raw).strip()


def name_at(team: int, index: int) -> int:
    return NAMES + (team * PLAYERS + index) * NAME_LEN


def record_at(team: int, index: int) -> int:
    return SKILLS + (team * PLAYERS + index) * RECORD


def nibbles(record: bytes | bytearray) -> list[int]:
    return [v for b in record[:5] for v in (b & 15, b >> 4)]


def pack(values: list[int]) -> bytes:
    return bytes((values[i] & 15) | (values[i + 1] & 15) << 4 for i in range(0, 10, 2))


def formation_at(image: bytes | bytearray, team: int) -> int:
    """ROM offset of `team`'s 31-byte formation record."""
    return BANK_8B + (struct.unpack_from("<H", image, FORMATION_PTRS + 2 * team)[0] & 0x7FFF)


def slot_lines(image: bytes | bytearray, team: int) -> list[int]:
    """The line of each of the eleven places, in place order: the keeper 0, then the line (1
    defence, 2 midfield, 3 attack) in each outfield place's flags."""
    at = formation_at(image, team)
    return [0] + [flags & 3 for flags in image[at + 21 : at + 31]]


def slot_offsets(image: bytes | bytearray, team: int) -> list[tuple[int, int]]:
    """The (depth, lateral) offset of each of the eleven places, in place order; the keeper's is
    (0, 0). Both are signed: further forward and the team's right are positive."""
    raw = struct.unpack_from("20b", image, formation_at(image, team) + 1)
    return [(0, 0), *zip(raw[0::2], raw[1::2], strict=True)]


def default_record(image: bytes | bytearray, fid: int) -> bytes:
    """The formation record of id `fid` as the game starts it: the id, the id's 20 default
    position bytes and its 10 default flags."""
    positions = image[DEFAULT_POSITIONS + 20 * fid :][:20]
    flags = image[DEFAULT_FLAGS + 10 * fid :][:10]
    return bytes([fid]) + bytes(positions) + bytes(flags)
