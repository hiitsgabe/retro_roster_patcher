"""Built-in French translation PPF for WE2002 (SLPM-87056).

Generates a PPF1 patch that writes French team names to the Kanji
(2-byte encoded) name section of the ROM.  The original game stores
Japanese katakana names at OFS_NOMI_SQK; this patch replaces them
with the French equivalents using the game's own 2-byte encoding.

The PPF covers all 95 teams (63 national/allstar + 32 Master League).
When the patcher runs, ML kanji names are then overwritten again by
the ROM writer with actual team names from the API.

Team names and encoding sourced from WE2002-editor-2.0 (edDlg.cpp).
Note: accented characters (é, è, ê, ç, etc.) are not supported by the
game's 2-byte encoding and are replaced with unaccented equivalents.
"""

import os
import struct

# ---------------------------------------------------------------------------
# ROM offsets for the Kanji team name section
# ---------------------------------------------------------------------------

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

# French team names — indices 0-62 = national/allstar, 63-94 = ML teams
_TEAM_NAMES = [
    # National teams (0-53)
    "Irlande",
    "Ecosse",
    "Galles",
    "Angleterre",
    "Portugal",
    "Espagne",
    "France",
    "Belgique",
    "Pays-Bas",
    "Suisse",
    "Italie",
    "Rep. Tcheque",
    "Allemagne",
    "Danemark",
    "Norvege",
    "Suede",
    "Islande",
    "Pologne",
    "Slovaquie",
    "Autriche",
    "Hongrie",
    "Albanie",
    "Croatie",
    "Serbie",
    "Roumanie",
    "Bosnie",
    "Grece",
    "Turquie",
    "Ukraine",
    "Russie",
    "Maroc",
    "Cote d Ivoire",
    "Egypte",
    "Nigeria",
    "Cameroun",
    "Algerie",
    "Ghana",
    "Etats-Unis",
    "Mexique",
    "Venezuela",
    "Colombie",
    "Bresil",
    "Perou",
    "Chili",
    "Paraguay",
    "Uruguay",
    "Argentine",
    "Equateur",
    "Japon",
    "Coree du Sud",
    "Chine",
    "Inde",
    "Nlle-Zelande",
    "Australie",
    # Allstar/Classic teams (54-62)
    "Etoiles Europe",
    "Etoiles Monde",
    "Clas. Anglet.",
    "Clas. France",
    "Clas. Pays-Bas",
    "Clas. Italie",
    "Clas. Allem.",
    "Clas. Bresil",
    "Clas. Argentine",
    # Master League teams (63-94)
    "Manchester U.",
    "Arsenal",
    "Chelsea",
    "Liverpool",
    "Manchester City",
    "Tottenham",
    "Atletico Madrid",
    "Barcelone",
    "Real Madrid",
    "Valence",
    "Seville",
    "Monaco",
    "Porto",
    "P.S.G.",
    "Benfica",
    "Ajax",
    "CSKA Moscou",
    "Zenit",
    "Inter",
    "Juventus",
    "Milan",
    "Lazio",
    "Naples",
    "Fiorentina",
    "Rome",
    "B. Dortmund",
    "B. Munich",
    "B. Leverkusen",
    "Wolfsbourg",
    "Galatasaray",
    "Chakhtar Donetsk",
    "Bale",
]


def _ascii_to_kanji(text: str, char_budget: int) -> bytes:
    """Convert ASCII text to WE2002 2-byte encoding.

    Matches the asciitokanji() function from edDlg.cpp.
    char_budget is lun_nomik[idx]; output is char_budget * 2 bytes.
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

    # Null terminator at end
    term = min(len(text), max_chars)
    buf[term * 2] = 0x00
    buf[term * 2 + 1] = 0x00

    return bytes(buf)


def _titlecase_name(name: str, max_chars: int) -> str:
    """Convert name to titlecase and truncate."""
    if not name:
        return ""
    result = name[0].upper() + name[1:].lower() if len(name) > 1 else name.upper()
    return result[:max_chars]


def _build_kanji_records() -> list:
    """Build list of (offset, data) tuples for the kanji name section."""
    records = []
    pos = _OFS_NOMI_SQK

    # --- ML teams: squad_ml[31] first, squad_ml[0] last ---
    for i in range(32):
        team_idx = 94 - i
        budget = _LUN_NOMIK[team_idx]
        name = _TEAM_NAMES[team_idx] if team_idx < len(_TEAM_NAMES) else ""
        kanji_name = _titlecase_name(name, budget - 1)
        data = _ascii_to_kanji(kanji_name, budget)
        records.append((pos, data))
        pos += budget * 2

    # --- National/allstar teams: squad_nazall[62] first, squad_nazall[0] last ---
    for i in range(63):
        team_idx = 62 - i
        budget = _LUN_NOMIK[team_idx]
        name = _TEAM_NAMES[team_idx] if team_idx < len(_TEAM_NAMES) else ""
        kanji_name = _titlecase_name(name, budget - 1)
        data = _ascii_to_kanji(kanji_name, budget)

        if i == 58:
            records.append((pos, data[:4]))
            records.append((_OFS_NOMI_SQK1, data[4:]))
            pos = _OFS_NOMI_SQK1 + len(data[4:])
        else:
            records.append((pos, data))
            pos += budget * 2

    return records


def _make_ppf1(description: str, records: list) -> bytes:
    """Generate a PPF1 format patch from (offset, data) records."""
    buf = bytearray()
    buf.extend(b"PPF10")
    buf.append(0x00)
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


def generate_french_ppf(assets_dir: str = "") -> bytes:
    """Generate the built-in French translation PPF for WE2002.

    If assets_dir is given and contains the community English PPF,
    translated menu records are merged in so the menus appear in
    French instead of Japanese.
    """
    records = _build_kanji_records()
    if assets_dir:
        from .menu_records import get_menu_records

        menu = get_menu_records(assets_dir, "fr")
        if menu:
            records = menu + records  # team names written AFTER menus (override)
    return _make_ppf1("WE2002 French - Console Utilities", records)


def ensure_ppf(assets_dir: str) -> str:
    """Ensure the French PPF exists in the assets directory."""
    ppf_path = os.path.join(assets_dir, "we2002_french.ppf")
    has_community = os.path.exists(os.path.join(assets_dir, "w202-english.ppf"))
    if os.path.exists(ppf_path):
        if has_community and os.path.getsize(ppf_path) < 10000:
            os.remove(ppf_path)
    if not os.path.exists(ppf_path):
        os.makedirs(assets_dir, exist_ok=True)
        ppf_data = generate_french_ppf(assets_dir)
        with open(ppf_path, "wb") as f:
            f.write(ppf_data)
    return ppf_path
