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
  (1 keeper, 2 centre back, 3 full back, 4 midfielder, 5 attacking
  midfielder, 6 forward). Byte 5 is the player's place in the formation
  (0-10 the eleven, 11-19 the bench; a permutation in every team), byte 6
  flags this patch keeps.
* Team ids are the order of `TEAMS`; the game stores them doubled.
* The main menu's default teams (Brazil v England) are immediates at
  `DEFAULT_P1` and `DEFAULT_P2` (`LDA #team` before `STA $0DA0/$1F8C`).
"""

from __future__ import annotations

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
GK, CB, FB, MF, AM, FW = 1, 2, 3, 4, 5, 6
GROUP_OF_CODE = {GK: "GK", CB: "DF", FB: "DF", MF: "MF", AM: "MF", FW: "FW"}

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


def formation_codes(image: bytes | bytearray, team: int) -> list[int]:
    """Position code of each of the eleven places, in place order."""
    codes = [0] * 11
    for i in range(PLAYERS):
        r = image[record_at(team, i) :][:RECORD]
        if r[5] < 11:
            codes[r[5]] = nibbles(r)[9]
    return codes
