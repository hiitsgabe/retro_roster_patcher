"""Built-in English translation PPF for WE2002 (SLPM-87056).

Generates a PPF1 patch that rewrites the game's own 2-byte encoded team name
section at OFS_NOMI_SQK for all 95 teams (63 national/allstar + 32 Master
League). The ROM writer later overwrites the 32 ML names with API team names.
"""

import os
import struct

_OFS_NOMI_SQK = 2_002_316  # Kanji names start (ML reverse, then nationals reverse)
_OFS_NOMI_SQK1 = 2_003_928  # Sector boundary continuation (national i=58 split)

# Kanji byte budget per team (number of 2-byte chars, *2 = raw byte count)
_LUN_NOMIK = [
    8,
    8,
    6,
    8,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    8,
    8,
    6,
    6,
    8,
    6,
    6,
    6,
    8,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    8,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    6,
    8,
    6,
    6,
    6,
    6,
    6,
    8,
    8,
    12,
    12,
    14,
    12,
    12,
    12,
    10,
    12,
    14,
    6,
    6,
    6,
    8,
    8,
    6,
    10,
    8,
    6,
    8,
    8,
    8,
    8,
    8,
    6,
    8,
    10,
    6,
    6,
    6,
    8,
    6,
    6,
    6,
    8,
    10,
    6,
    6,
    8,
    10,
    6,
    6,
]

# English team names — indices 0-62 = national/allstar, 63-94 = ML teams
_TEAM_NAMES = [
    # National teams (0-53)
    "Ireland",
    "Scotland",
    "Wales",
    "England",
    "Portugal",
    "Spain",
    "France",
    "Belgium",
    "Netherlands",
    "Switzerland",
    "Italy",
    "Czech Rep.",
    "Germany",
    "Denmark",
    "Norway",
    "Sweden",
    "Iceland",
    "Poland",
    "Slovakia",
    "Austria",
    "Hungary",
    "Albania",
    "Croatia",
    "Serbia",
    "Romania",
    "Bosnia",
    "Greece",
    "Turkey",
    "Ukraine",
    "Russia",
    "Morocco",
    "Ivory Coast",
    "Egypt",
    "Nigeria",
    "Cameroon",
    "Algeria",
    "Ghana",
    "U.S.A.",
    "Mexico",
    "Venezuela",
    "Colombia",
    "Brazil",
    "Peru",
    "Chile",
    "Paraguay",
    "Uruguay",
    "Argentina",
    "Ecuador",
    "Japan",
    "South Korea",
    "China",
    "India",
    "New Zealand",
    "Australia",
    # Allstar/Classic teams (54-62)
    "Euro All Stars",
    "World All Stars",
    "Clas. England",
    "Clas. France",
    "Clas. Netherlands",
    "Clas. Italy",
    "Clas. Germany",
    "Clas. Brazil",
    "Clas. Argentina",
    # Master League teams (63-94)
    "Manchester U.",
    "Arsenal",
    "Chelsea",
    "Liverpool",
    "Manchester City",
    "Tottenham",
    "Atletico Madrid",
    "Barcelona",
    "Real Madrid",
    "Valencia",
    "Sevilla",
    "Monaco",
    "Porto",
    "P.S.G.",
    "Benfica",
    "Ajax",
    "CSKA Moskva",
    "Zenit",
    "Inter",
    "Juventus",
    "Milan",
    "Lazio",
    "Napoli",
    "Fiorentina",
    "Roma",
    "B. Dortmund",
    "B. Munchen",
    "B. Leverkusen",
    "Wolfsburg",
    "Galatasaray",
    "Shakhtar Donetsk",
    "Basilea",
]


def _ascii_to_kanji(text: str, char_budget: int) -> bytes:
    """Convert ASCII to the WE2002 2-byte encoding.

    `char_budget` is lun_nomik[idx]; the result is always char_budget * 2 bytes
    and the last character position is reserved for the null terminator.
    """
    buf = bytearray(char_budget * 2)
    max_chars = char_budget - 1  # last position is null terminator

    for i in range(min(len(text), max_chars)):
        ch = ord(text[i])
        if 65 <= ch <= 90:  # A-Z
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = ch + 31
        elif 97 <= ch <= 122:  # a-z
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = ch + 32
        elif 48 <= ch <= 57:  # 0-9
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = ch + 31
        elif ch == 46:  # period '.'
            buf[i * 2] = 0x81
            buf[i * 2 + 1] = 0x42
        elif ch == 0:  # null
            buf[i * 2] = 0x00
            buf[i * 2 + 1] = 0x00
        else:  # space / default
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = 0x80

    term = min(len(text), max_chars)
    buf[term * 2] = 0x00
    buf[term * 2 + 1] = 0x00

    return bytes(buf)


def _titlecase_name(name: str, max_chars: int) -> str:
    """First character uppercase, rest lowercase, truncated to
    lun_nomik[idx] - 1 characters."""
    if not name:
        return ""
    result = name[0].upper() + name[1:].lower() if len(name) > 1 else name.upper()
    return result[:max_chars]


def _build_kanji_records() -> list:
    records = []
    pos = _OFS_NOMI_SQK

    # ML names are stored in reverse: squad_ml[31] first, squad_ml[0] last.
    for i in range(32):
        team_idx = 94 - i  # index into _TEAM_NAMES and _LUN_NOMIK
        budget = _LUN_NOMIK[team_idx]
        name = _TEAM_NAMES[team_idx] if team_idx < len(_TEAM_NAMES) else ""
        kanji_name = _titlecase_name(name, budget - 1)
        data = _ascii_to_kanji(kanji_name, budget)
        records.append((pos, data))
        pos += budget * 2

    # Nationals are also reversed: squad_nazall[62] first, squad_nazall[0] last.
    for i in range(63):
        team_idx = 62 - i  # index into _TEAM_NAMES and _LUN_NOMIK
        budget = _LUN_NOMIK[team_idx]
        name = _TEAM_NAMES[team_idx] if team_idx < len(_TEAM_NAMES) else ""
        kanji_name = _titlecase_name(name, budget - 1)
        data = _ascii_to_kanji(kanji_name, budget)

        if i == 58:
            # i == 58 straddles a sector boundary: 4 bytes here, rest at
            # _OFS_NOMI_SQK1.
            records.append((pos, data[:4]))
            records.append((_OFS_NOMI_SQK1, data[4:]))
            pos = _OFS_NOMI_SQK1 + len(data[4:])
        else:
            records.append((pos, data))
            pos += budget * 2

    return records


def _make_ppf1(description: str, records: list) -> bytes:
    """Generate a PPF1 format patch from (offset, data) records.

    Header: b"PPF10" + u8 encoding + 50-byte description.
    Record: u32 LE offset + u8 count + `count` bytes, so 255 is the format's
    hard per-record limit and longer data must be split across consecutive
    offsets.
    """
    buf = bytearray()
    buf.extend(b"PPF10")
    buf.append(0x00)  # encoding method
    desc_bytes = description.encode("ascii", errors="replace")[:50]
    buf.extend(desc_bytes.ljust(50, b"\x00"))

    for offset, data in records:
        remaining = data
        cur_offset = offset
        while remaining:
            chunk = remaining[:255]
            remaining = remaining[255:]
            buf.extend(struct.pack("<I", cur_offset))
            buf.append(len(chunk))
            buf.extend(chunk)
            cur_offset += len(chunk)

    return bytes(buf)


def generate_english_ppf(assets_dir: str = "") -> bytes:
    """Generate the built-in English translation PPF for WE2002.

    A community English PPF in `assets_dir` also supplies the menu strings,
    which are otherwise left in Japanese.
    """
    records = _build_kanji_records()
    if assets_dir:
        from .menu_records import get_menu_records

        menu = get_menu_records(assets_dir, "en")
        if menu:
            records = menu + records
    return _make_ppf1("WE2002 English - Console Utilities", records)


def ensure_ppf(cache_dir: str, assets_dir: str = "") -> str:
    """Generate the English PPF into `cache_dir` and return its path.

    `cache_dir` is written to; `assets_dir` is only read, and only for the
    optional community translation. A cache written before that translation was
    available holds exactly the unmerged output, so it is discarded and rebuilt
    with the translated menu records merged in.
    """
    ppf_path = os.path.join(cache_dir, "we2002_english.ppf")
    has_community = bool(assets_dir) and os.path.exists(
        os.path.join(assets_dir, "w202-english.ppf")
    )
    if has_community and os.path.exists(ppf_path):
        with open(ppf_path, "rb") as f:
            if f.read() == generate_english_ppf():
                os.remove(ppf_path)
    if not os.path.exists(ppf_path):
        os.makedirs(cache_dir, exist_ok=True)
        ppf_data = generate_english_ppf(assets_dir)
        with open(ppf_path, "wb") as f:
            f.write(ppf_data)
    return ppf_path
