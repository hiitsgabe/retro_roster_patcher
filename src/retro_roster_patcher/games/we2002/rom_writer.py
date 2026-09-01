"""WE2002 ROM writer — patches player and team data into a copy of the ROM.

Offset constants and data structures sourced from:
  https://github.com/thyddralisk/WE2002-editor-2.0 (edDlg.cpp / giocatore.cpp)

The PS1 BIN file is Mode2/2352 bytes per sector.  All offsets are absolute raw
byte positions in that file (including sector sync/header/ECC overhead).  Data
regions span multiple sectors; the C++ editor re-seeks at known boundaries to
skip ECC bytes — we replicate that exact logic here.

Player attributes are encoded in a 12-byte str_carat field using the bit layout
defined in giocatore::decodifica().  Skills use a 3-bit offset-12 scheme
(storage value 0-7 = in-game values 12-19).

Team names are variable-length strings.  The per-entry byte budget is defined by
hardcoded lun_nomiN[] arrays (one per name variant).  Names are null-terminated
within their budget.  The ROM stores 95 entries: indices 0-62 are national/allstar
teams, indices 63-94 are the 32 ML teams.  Both ML and national names are patched.

Flags are parametric: 1 byte style (geometric pattern) + 16×2-byte colors.
Style is written identically at 5 FORMA offsets.  Colors are written in a
complex non-sequential order at COLORE offsets.
"""

import os
import shutil
import struct
import unicodedata

from .models import WEPlayerRecord, WETeamRecord


def _sync(f):
    """Flush and fsync to ensure writes hit disk (critical on exFAT/FUSE)."""
    f.flush()
    os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Absolute byte offsets in the Mode2/2352 BIN (from WE2002-editor-2.0 source)
# ---------------------------------------------------------------------------

# Master League player NAME sections (10 bytes per player)
_OFS_NOMI_GML = 2_006_288  # ML player names, section 1: players 0–203
_OFS_NOMI_GML2 = 2_008_632  # ML player names, section 2: players 204–407 + first 8 of 408
_OFS_NOMI_GML3 = 2_010_984  # ML player names, section 3: last 2 of 408, then 409–461

# Master League player CHARACTERISTICS sections (12 bytes per player)
_OFS_CARAT_GML = 2_204_112  # Characteristics section 1: players 0–147
# Characteristics section 2: last 4 of 148, then 149–318, + first 4 of 319
_OFS_CARAT_GML1 = 2_206_200
_OFS_CARAT_GML2 = 2_208_552  # Characteristics section 3: last 8 of 319, then 320–461

# Team NAME offsets — 6 name variants + lowercase + abbreviations + additional ML
_OFS_NOMI_SQ1 = 1_012_640  # Name variant 1
_OFS_NOMI_SQ1A = 1_013_736  # SQ1 continuation after sector boundary (national team 40)
_OFS_NOMI_SQ2 = 1_881_968  # Name variant 2
_OFS_NOMI_SQ3 = 2_003_996  # Name variant 3
_OFS_NOMI_SQ4 = 2_830_160  # Name variant 4
_OFS_NOMI_SQ5 = 4_822_908  # Name variant 5
_OFS_NOMI_SQ5A = 4_823_976  # SQ5 continuation after sector boundary
_OFS_NOMI_SQ6 = 5_651_448  # Name variant 6
_OFS_NOMI_SQ6A = 5_651_880  # SQ6 sector boundary (ML team i==15)
_OFS_NOMI_SQ6B = 5_652_364  # SQ6 national/allstar start
_OFS_NOMI_SQ_M = 4_598_596  # Lowercase name
_OFS_NOMI_SQ_AB1 = 2_004_996  # Abbreviation variant 1 (fixed 4 bytes)
_OFS_NOMI_SQ_AB2 = 5_651_068  # Abbreviation variant 2 (fixed 4 bytes)
_OFS_NOMI_SQ_AB3 = 4_234_484  # Abbreviation variant 3 (fixed 4 bytes)

# Flag (bandiera) offsets
_OFS_BANDIERE_FORMA1 = 1_929_004  # Flag style byte × 5 copies
_OFS_BANDIERE_FORMA2 = 2_005_412
_OFS_BANDIERE_FORMA3 = 2_328_060
_OFS_BANDIERE_FORMA4 = 4_904_664
_OFS_BANDIERE_FORMA5 = 5_711_640
_OFS_BANDIERE_COLORE = 12_549_518  # Flag colors (complex layout)
_OFS_BANDIERE_COLORE1 = 12_550_296
_OFS_BANDIERE_COLORE2 = 12_552_648
_OFS_BANDIERE_COLORE_SEN = 12_545_758

# Force bars
_OFS_BAR = 2_328_184
_OFS_BAR1 = 2_328_504  # Continuation after sector boundary (national team 3)

# Jersey preview colors
_OFS_ANT_MAGLIE2 = 2_671_896  # ML jersey previews (32 teams × 64 bytes)

# National player NAME sections (10 bytes per player, 8 sections)
_OFS_NOMI_G = 387_792
_OFS_NOMI_G2 = 390_456
_OFS_NOMI_G3 = 392_808
_OFS_NOMI_G4 = 395_160
_OFS_NOMI_G5 = 397_512
_OFS_NOMI_G6 = 399_864
_OFS_NOMI_G7 = 402_216
_OFS_NOMI_G8 = 404_568

# National player CHARACTERISTICS sections (12 bytes per player, 10 sections)
_OFS_CARAT_G = 2_179_492
_OFS_CARAT_G1 = 2_180_328
_OFS_CARAT_G2 = 2_182_680
_OFS_CARAT_G3 = 2_185_032
_OFS_CARAT_G4 = 2_187_384
_OFS_CARAT_G5 = 2_189_736
_OFS_CARAT_G6 = 2_192_088
_OFS_CARAT_G7 = 2_194_440
_OFS_CARAT_G8 = 2_196_792
_OFS_CARAT_G9 = 2_199_144

# National jersey preview colors (64 bytes per team: maglia1 + maglia2)
_OFS_ANT_MAGLIE = 2_667_256  # National + allstar jersey start
_OFS_ANT_MAGLIE1 = 2_669_544  # Continuation after sector boundary (team 30)

# Kanji (2-byte encoded) team name section
_OFS_NOMI_SQK = 2_002_316  # Kanji names start (ML reverse, then nationals reverse)
_OFS_NOMI_SQK1 = 2_003_928  # Sector boundary continuation (national i=58 split)

# ---------------------------------------------------------------------------
# Sector boundary crossing points (from C++ switch-case indices)
# ---------------------------------------------------------------------------

_NOME_LAST_GML = 203
_NOME_LAST_GML2 = 407
_NOME_STRADDLE = 408
_CARAT_STRADDLE1 = 148
_CARAT_STRADDLE2 = 319

# National player name straddle points (national index, bytes_before, bytes_after)
# Index 820 is a clean break (0 before, 10 after = full seek to next section)
_NAT_NOME_STRADDLES = {
    0: (8, 2, _OFS_NOMI_G + 312),  # special: seek +312 within same section
    205: (6, 4, _OFS_NOMI_G2),
    410: (4, 6, _OFS_NOMI_G3),
    615: (2, 8, _OFS_NOMI_G4),
    820: (0, 10, _OFS_NOMI_G5),  # clean break
    1024: (8, 2, _OFS_NOMI_G6),
    1229: (6, 4, _OFS_NOMI_G7),
    1434: (4, 6, _OFS_NOMI_G8),
}

# National characteristics straddle points (national index, bytes_before, bytes_after)
# Indices 215, 727, 1239 are clean breaks (0 before, 12 after)
_NAT_CARAT_STRADDLES = {
    44: (4, 8, _OFS_CARAT_G1),
    215: (0, 12, _OFS_CARAT_G2),  # clean break
    385: (8, 4, _OFS_CARAT_G3),
    556: (4, 8, _OFS_CARAT_G4),
    727: (0, 12, _OFS_CARAT_G5),  # clean break
    897: (8, 4, _OFS_CARAT_G6),
    1068: (4, 8, _OFS_CARAT_G7),
    1239: (0, 12, _OFS_CARAT_G8),  # clean break
    1409: (8, 4, _OFS_CARAT_G9),
}

# ---------------------------------------------------------------------------
# ROM constants
# ---------------------------------------------------------------------------

_SQUADRE_ML = 32
_SQUADRE_NAZ = 63  # National + allstar teams
_GIOCATORI_NC = 462
_PLAYERS_PER_NAT = 23
_NOME_SIZE = 10
_CARAT_SIZE = 12

_POS_MAP = {0: 0, 1: 1, 2: 3, 3: 6}

# ---------------------------------------------------------------------------
# 3D jersey TEX file constants (CD filesystem)
# ---------------------------------------------------------------------------

_TEX_BASE_LBA = 8400  # LBA of TEX_00.BIN on CD
_TEX_LBA_STRIDE = 20  # Each TEX allocated 20 sectors
_TEX_COUNT = 95  # 63 national + 32 ML teams
_TEX_MAX_BYTES = 20 * 2048  # 40960 bytes max per TEX slot

# ISO9660 BIN directory location (for updating TEX file sizes)
_BIN_DIR_LBA = 2925
_BIN_DIR_SIZE = 12288

# Known home shirt colors from the 2D maglia palette (entry 2, BGR555→RGB888).
# TEX 0-62 = national/allstar, TEX 63-94 = Master League.
# Dominant shirt color = most frequent RGB among maglia entries 2-7.
# Known home jersey colors for each TEX slot (real-world 2001-02 kits).
# National teams use real kit colors; ML teams use the real club counterpart.
_TEX_JERSEY_COLORS = {
    # --- National teams (0-53) ---
    0: (0, 128, 48),  # Ireland — green
    1: (0, 0, 102),  # Scotland — dark navy
    2: (200, 0, 0),  # Wales — red
    3: (255, 255, 255),  # England — white
    4: (128, 0, 24),  # Portugal — dark red/maroon
    5: (200, 0, 0),  # Spain — red
    6: (0, 32, 160),  # France — blue
    7: (200, 0, 0),  # Belgium — red
    8: (255, 128, 0),  # Netherlands — orange
    9: (200, 0, 0),  # Switzerland — red
    10: (0, 82, 165),  # Italy — azzurri blue
    11: (180, 0, 48),  # Czech Republic — dark red
    12: (255, 255, 255),  # Germany — white
    13: (200, 0, 0),  # Denmark — red
    14: (200, 0, 0),  # Norway — red
    15: (255, 204, 0),  # Sweden — yellow
    16: (0, 0, 160),  # Iceland — blue
    17: (255, 255, 255),  # Poland — white
    18: (0, 0, 160),  # Slovakia — blue
    19: (255, 255, 255),  # Austria — white
    20: (200, 0, 0),  # Hungary — red
    21: (200, 0, 0),  # Albania — red
    22: (200, 50, 50),  # Croatia — red/white checkered
    23: (0, 0, 160),  # Serbia — blue
    24: (255, 204, 0),  # Romania — yellow
    25: (0, 0, 160),  # Bosnia — blue
    26: (255, 255, 255),  # Greece — white
    27: (200, 0, 0),  # Turkey — red
    28: (255, 204, 0),  # Ukraine — yellow
    29: (255, 255, 255),  # Russia — white
    30: (200, 0, 0),  # Morocco — red
    31: (255, 128, 0),  # Ivory Coast — orange
    32: (200, 0, 0),  # Egypt — red
    33: (0, 128, 0),  # Nigeria — green
    34: (0, 128, 0),  # Cameroon — green
    35: (0, 128, 0),  # Algeria — green
    36: (255, 255, 255),  # Ghana — white
    37: (255, 255, 255),  # U.S.A. — white
    38: (0, 128, 0),  # Mexico — green
    39: (128, 0, 0),  # Venezuela — vinotinto (dark maroon)
    40: (255, 204, 0),  # Colombia — yellow
    41: (255, 204, 0),  # Brazil — yellow (canarinha)
    42: (255, 255, 255),  # Peru — white (with red sash)
    43: (200, 0, 0),  # Chile — red
    44: (200, 0, 50),  # Paraguay — red/white stripes
    45: (128, 180, 230),  # Uruguay — celeste (light blue)
    46: (128, 180, 230),  # Argentina — albiceleste
    47: (255, 204, 0),  # Ecuador — yellow
    48: (0, 0, 180),  # Japan — blue
    49: (200, 0, 0),  # South Korea — red
    50: (200, 0, 0),  # China — red
    51: (0, 0, 160),  # India — blue
    52: (255, 255, 255),  # New Zealand — white (All Whites)
    53: (255, 204, 0),  # Australia — gold/yellow
    # --- All-Star / Classic teams (54-62) ---
    54: (255, 255, 255),  # Euro All Stars — white
    55: (255, 255, 255),  # World All Stars — white
    56: (255, 255, 255),  # Clas. England — white
    57: (0, 32, 160),  # Clas. France — blue
    58: (255, 128, 0),  # Clas. Netherlands — orange
    59: (0, 82, 165),  # Clas. Italy — azzurri blue
    60: (255, 255, 255),  # Clas. Germany — white
    61: (255, 204, 0),  # Clas. Brazil — yellow
    62: (128, 180, 230),  # Clas. Argentina — light blue
    # --- Master League teams (63-94) ---
    # These are FICTIONAL teams with custom kits — colors from maglia palette,
    # not real-world clubs.  Only override when maglia clearly wrong.
    63: (156, 33, 33),  # Aragon (≈Man Utd) — red
    64: (165, 33, 57),  # London (≈Arsenal) — red
    65: (0, 57, 173),  # Europort (≈Chelsea) — blue
    66: (173, 16, 49),  # Liguria (≈Liverpool) — red
    67: (255, 255, 255),  # Yorkshire (≈Man City) — white kit in-game
    68: (33, 41, 49),  # Highlands (≈Tottenham) — dark kit in-game
    69: (165, 33, 57),  # Vascongadas (≈Atl. Madrid) — red
    70: (148, 8, 57),  # Cataluna (≈Barcelona) — dark red/blue
    71: (255, 255, 255),  # Navarra (≈Real Madrid) — white
    72: (255, 255, 255),  # Andalucia (≈Valencia) — white
    73: (57, 74, 148),  # Cantabria (≈Sevilla) — blue kit in-game
    74: (132, 41, 33),  # Provence (≈Monaco) — red
    75: (255, 255, 255),  # Languedoc (≈Porto) — white kit in-game
    76: (57, 74, 148),  # Normandie (≈PSG) — blue
    77: (255, 255, 255),  # Medoc (≈Benfica) — white kit in-game
    78: (255, 255, 255),  # Rijnkanaal (≈Ajax) — white
    79: (99, 0, 8),  # Noordzeekanaal (≈CSKA) — dark red
    80: (255, 255, 255),  # Flandre (≈Zenit) — white/red kit in-game
    81: (16, 57, 140),  # Marche (≈Inter) — dark blue
    82: (40, 40, 40),  # Piemonte (≈Juventus) — black/white
    83: (156, 8, 33),  # Lombardia (≈Milan) — red
    84: (90, 132, 189),  # Umbria (≈Lazio) — sky blue
    85: (41, 41, 123),  # Emilia (≈Napoli) — dark blue-purple
    86: (66, 57, 123),  # Toscana (≈Fiorentina) — purple
    87: (115, 41, 41),  # Abruzzi (≈Roma) — dark red
    88: (41, 41, 41),  # Westfalen (≈Dortmund) — dark kit in-game
    89: (41, 41, 41),  # Anhalt (≈Bayern) — dark kit in-game
    90: (140, 33, 33),  # Ruhr (≈Leverkusen) — red
    91: (255, 255, 255),  # Peloponnisos (≈Wolfsburg) — white kit in-game
    92: (165, 165, 74),  # Byzantinobul (≈Galatasaray) — olive/yellow
    93: (255, 255, 255),  # Marmara (≈Shakhtar) — white kit in-game
    94: (16, 57, 140),  # Patagonia (≈Basel) — dark blue
}

# ---------------------------------------------------------------------------
# Hardcoded name length tables from WE2002-editor-2.0 (edDlg.cpp lines 639-709)
#
# 95 entries each: indices 0-62 = national/allstar, indices 63-94 = 32 ML teams.
# The C++ writes ML teams in reverse: squad_ml[31-i] uses lun_nomiN[94-i].
# For our slot_index (0-31), the array index is: 63 + slot_index.
# But ROM write order is reversed: slot_index maps to lun_nomi index 94-rom_i
# where rom_i = 31-slot_index.  So: lun_index = 94 - (31 - slot_index) = 63 + slot_index.
# ---------------------------------------------------------------------------

_LUN_NOMI1 = [
    8,
    12,
    8,
    8,
    12,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    12,
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    16,
    8,
    12,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    16,
    12,
    16,
    16,
    16,
    16,
    20,
    16,
    16,
    16,
    20,
    8,
    8,
    8,
    12,
    12,
    12,
    12,
    12,
    8,
    12,
    12,
    12,
    12,
    12,
    8,
    12,
    16,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    16,
    16,
    8,
    12,
]

_LUN_NOMI2 = [
    8,
    12,
    8,
    12,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    12,
    12,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    12,
    12,
    16,
    16,
    16,
    12,
    16,
    12,
    12,
    16,
    16,
    8,
    8,
    8,
    12,
    8,
    8,
    16,
    8,
    8,
    12,
    12,
    12,
    12,
    12,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    12,
    16,
    8,
    12,
]

_LUN_NOMI3 = [
    8,
    12,
    8,
    8,
    12,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    12,
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    16,
    8,
    12,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    16,
    12,
    16,
    16,
    16,
    16,
    20,
    16,
    16,
    16,
    20,
    8,
    8,
    8,
    12,
    12,
    12,
    12,
    12,
    8,
    12,
    12,
    12,
    12,
    12,
    8,
    12,
    16,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    16,
    16,
    8,
    12,
]

_LUN_NOMI4 = [
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    8,
    8,
    8,
    4,
    4,
    4,
    8,
    8,
    8,
    8,
    8,
    16,
    12,
    12,
    12,
    12,
    12,
    16,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    4,
    8,
    12,
    8,
    8,
]

_LUN_NOMI5 = [
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    12,
    8,
    12,
    16,
    12,
    12,
    12,
    12,
    12,
    12,
    12,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    4,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    8,
    4,
    12,
    12,
    8,
    8,
]

_LUN_NOMI6 = [
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    12,
    8,
    12,
    16,
    16,
    12,
    12,
    12,
    12,
    16,
    16,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    4,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    8,
    4,
    12,
    12,
    8,
    8,
]

_LUN_NOMI_MIN = [
    8,
    12,
    8,
    12,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    4,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    8,
    8,
    8,
    8,
    8,
    8,
    8,
    4,
    12,
    8,
    12,
    16,
    16,
    12,
    12,
    12,
    12,
    16,
    16,
    8,
    8,
    8,
    8,
    8,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    12,
    8,
    8,
    8,
    12,
    8,
    4,
    8,
    12,
    8,
    8,
    8,
    8,
    12,
    8,
    4,
    12,
    12,
    8,
    8,
]


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


def _ml_name_budget(slot_index: int, lun_table: list) -> int:
    """Return the byte budget for ML slot_index (0-31) from a lun_nomi table."""
    return lun_table[63 + slot_index]


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def _we_val(attr: int) -> int:
    """Map 1-9 WE attribute to 0-7 ROM storage value (offset-12 scheme)."""
    return min(7, max(0, attr - 1))


def _encode_player_carat(player: WEPlayerRecord) -> bytes:
    """Encode WEPlayerRecord attributes into the 12-byte str_carat format.

    Bit layout (from giocatore::decodifica() in WE2002-editor-2.0):
      Byte  0: posizione[2:0], stile_capelli_low4[7:4]
      Byte  1: stile_capelli_bit4[0], col_capelli[3:1], stile_barba[7:5]
      Byte  2: col_barba[3:1], altezza_low4[7:4]
      Byte  3: altezza_high2[1:0], (numero-1)[6:2], fuori_ruolo[7]
      Byte  4: col_pelle[1:0], corporatura[4:2], eta_low3[7:5]
      Byte  5: eta_high2[1:0], riflessi[4:2], forza_low2[7:6]
      Byte  6: forza_high1[0], resistenza[3:1], dribbling[6:4], velocita_low1[7]
      Byte  7: velocita_high2[1:0], accel[4:2], attacco[7:5]
      Byte  8: difesa[2:0], pot_tiro[5:3], prec_tiro_low2[7:6]
      Byte  9: prec_tiro_high1[0], passaggio[3:1], tecnica[6:4], testa_low1[7]
      Byte 10: testa_high2[1:0], salto[4:2], effetto[7:5]
      Byte 11: aggress[2:0], scarpe[5:3], piede[7:6]
    """
    a = player.attributes

    posizione = _POS_MAP.get(player.position, 1)
    stile_capelli = 0
    col_capelli = 0
    stile_barba = 0
    col_barba = 0
    col_pelle = 0
    corporatura = 2
    altezza = 178
    eta = 25
    scarpe = 0
    piede = 0
    fuori_ruolo = 0
    numero = max(1, min(32, player.shirt_number or 1))

    attacco = _we_val(a.offensive)
    difesa = _we_val(a.defensive)
    forza = _we_val(a.body_balance)
    resistenza = _we_val(a.stamina)
    velocita = _we_val(a.speed)
    accel = _we_val(a.acceleration)
    passaggio = _we_val(a.pass_accuracy)
    pot_tiro = _we_val(a.shoot_power)
    prec_tiro = _we_val(a.shoot_accuracy)
    salto = _we_val(a.jump_power)
    testa = _we_val(a.heading)
    tecnica = _we_val(a.technique)
    dribbling = _we_val(a.dribble)
    effetto = _we_val(a.curve)
    aggress = _we_val(a.aggression)
    riflessi = _we_val(a.defensive)

    h = altezza - 148
    e = eta - 15

    c = bytearray(12)
    c[0] = (posizione & 0x07) | ((stile_capelli & 0x0F) << 4)
    c[1] = ((stile_capelli >> 4) & 0x01) | ((col_capelli & 0x07) << 1) | ((stile_barba & 0x07) << 5)
    c[2] = ((col_barba & 0x07) << 1) | ((h & 0x0F) << 4)
    c[3] = ((h >> 4) & 0x03) | (((numero - 1) & 0x1F) << 2) | ((fuori_ruolo & 0x01) << 7)
    c[4] = (col_pelle & 0x03) | ((corporatura & 0x07) << 2) | ((e & 0x07) << 5)
    c[5] = ((e >> 3) & 0x03) | ((riflessi & 0x07) << 2) | ((forza & 0x03) << 6)
    c[6] = (
        ((forza >> 2) & 0x01)
        | ((resistenza & 0x07) << 1)
        | ((dribbling & 0x07) << 4)
        | ((velocita & 0x01) << 7)
    )
    c[7] = ((velocita >> 1) & 0x03) | ((accel & 0x07) << 2) | ((attacco & 0x07) << 5)
    c[8] = (difesa & 0x07) | ((pot_tiro & 0x07) << 3) | ((prec_tiro & 0x03) << 6)
    c[9] = (
        ((prec_tiro >> 2) & 0x01)
        | ((passaggio & 0x07) << 1)
        | ((tecnica & 0x07) << 4)
        | ((testa & 0x01) << 7)
    )
    c[10] = ((testa >> 1) & 0x03) | ((salto & 0x07) << 2) | ((effetto & 0x07) << 5)
    c[11] = (aggress & 0x07) | ((scarpe & 0x07) << 3) | ((piede & 0x03) << 6)
    return bytes(c)


def _to_ascii(text: str) -> str:
    """Convert Unicode text to plain ASCII by stripping diacritics.

    Handles accented Latin characters (é→e, ñ→n, ê→e, ü→u, etc.)
    and special ligatures/letters that don't decompose via NFKD.
    """
    _SPECIAL = {
        "ø": "o",
        "Ø": "O",
        "ß": "ss",
        "đ": "d",
        "Đ": "D",
        "ł": "l",
        "Ł": "L",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "þ": "th",
        "Þ": "TH",
    }
    for src, dst in _SPECIAL.items():
        text = text.replace(src, dst)
    normalized = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in normalized if not unicodedata.combining(c))
    return stripped.encode("ascii", errors="ignore").decode("ascii")


def _encode_player_name(name: str) -> bytes:
    """Encode a name string to 10-byte ROM format: ASCII uppercase, null-padded.

    The game only displays the first 8 characters; bytes 9-10 must be null.
    """
    ascii_bytes = _to_ascii(name.upper()).encode("ascii")[:8]
    return ascii_bytes.ljust(_NOME_SIZE, b"\x00")


def _encode_team_name(name: str, budget: int, uppercase: bool = True) -> bytes:
    """Encode a team name to fit within the ROM byte budget, null-terminated."""
    text = name.upper() if uppercase else name
    ascii_bytes = _to_ascii(text).encode("ascii")
    max_len = max(0, budget - 1)
    return ascii_bytes[:max_len].ljust(budget, b"\x00")


def _encode_abbreviation(code: str) -> bytes:
    """Encode a 3-letter abbreviation to fixed 4-byte ROM format."""
    ascii_bytes = _to_ascii(code.upper()).encode("ascii")[:3]
    return ascii_bytes.ljust(4, b"\x00")


def _encode_kanji_name(name: str, budget: int) -> bytes:
    """Encode a team name to the 2-byte Kanji format for the SQK section.

    budget is lun_nomik[idx] (character count); output is budget * 2 bytes.
    Name is titlecased (first char upper, rest lower) per C++ editor convention.
    """
    # Titlecase: first char uppercase, rest lowercase
    if len(name) > 1:
        text = name[0].upper() + name[1:].lower()
    elif name:
        text = name.upper()
    else:
        text = ""

    max_chars = budget - 1  # last position is null terminator
    text = text[:max_chars]

    buf = bytearray(budget * 2)
    for i, ch in enumerate(text):
        c = ord(ch)
        if 65 <= c <= 90:  # A-Z
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = c + 31
        elif 97 <= c <= 122:  # a-z
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = c + 32
        elif 48 <= c <= 57:  # 0-9
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = c + 31
        elif c == 46:  # period '.'
            buf[i * 2] = 0x81
            buf[i * 2 + 1] = 0x42
        else:  # space / default
            buf[i * 2] = 0x82
            buf[i * 2 + 1] = 0x80

    # Null terminator
    term = len(text)
    buf[term * 2] = 0x00
    buf[term * 2 + 1] = 0x00
    return bytes(buf)


def _ml_kanji_offset(slot_index: int) -> int:
    """Calculate the absolute file offset for an ML team's kanji name.

    ML kanji names are written at OFS_NOMI_SQK in reverse order:
    rom_i=0 is squad_ml[31], rom_i=31 is squad_ml[0].
    """
    rom_i = 31 - slot_index
    off = _OFS_NOMI_SQK
    for j in range(rom_i):
        off += _LUN_NOMIK[94 - j] * 2
    return off


def _nat_kanji_base() -> int:
    """Starting offset for national kanji names (after all 32 ML teams)."""
    base = _OFS_NOMI_SQK
    for j in range(32):
        base += _LUN_NOMIK[94 - j] * 2
    return base


_NAT_KANJI_BASE = _nat_kanji_base()

# National kanji sector boundary: writing in reverse (62, 61, ..., 0),
# nat_index=4 straddles the sector 851/852 boundary.
# Sector 851 data area ends at byte 2_003_623; sector 852 data starts at SQK1.
_NAT_KANJI_STRADDLE_IDX = 4
_NAT_KANJI_SECTOR_END = 2_003_623  # last byte of sector 851 data area


def _nat_kanji_chunks(nat_index: int) -> list[tuple[int, int]]:
    """Return [(offset, nbytes)] chunks for a national team's kanji name.

    National kanji names are written in reverse order (62, 61, ..., 0) after
    the 32 ML kanji names.  A sector boundary splits nat_index=4: 4 bytes
    before the boundary, then 8 bytes at _OFS_NOMI_SQK1.
    """
    budget = _LUN_NOMIK[nat_index]
    total_bytes = budget * 2

    if nat_index > _NAT_KANJI_STRADDLE_IDX:
        # Before the boundary in write order — simple linear offset
        off = _NAT_KANJI_BASE
        for rev_idx in range(62, nat_index, -1):
            off += _LUN_NOMIK[rev_idx] * 2
        return [(off, total_bytes)]

    elif nat_index == _NAT_KANJI_STRADDLE_IDX:
        # Straddle: compute linear offset, split across sector boundary
        off = _NAT_KANJI_BASE
        for rev_idx in range(62, nat_index, -1):
            off += _LUN_NOMIK[rev_idx] * 2
        before = _NAT_KANJI_SECTOR_END - off + 1  # bytes that fit in sector 851
        after = total_bytes - before
        return [(off, before), (_OFS_NOMI_SQK1, after)]

    else:
        # After the boundary — recalculate from _OFS_NOMI_SQK1 + straddle tail
        straddle_off = _NAT_KANJI_BASE
        for rev_idx in range(62, _NAT_KANJI_STRADDLE_IDX, -1):
            straddle_off += _LUN_NOMIK[rev_idx] * 2
        straddle_before = _NAT_KANJI_SECTOR_END - straddle_off + 1
        straddle_tail = _LUN_NOMIK[_NAT_KANJI_STRADDLE_IDX] * 2 - straddle_before

        off = _OFS_NOMI_SQK1 + straddle_tail
        for rev_idx in range(_NAT_KANJI_STRADDLE_IDX - 1, nat_index, -1):
            off += _LUN_NOMIK[rev_idx] * 2
        return [(off, total_bytes)]


def _rgb_to_ps1_color(r: int, g: int, b: int) -> int:
    """Convert RGB888 to PS1 15-bit BGR555 (unsigned short).

    PS1 format: 0bbbbbgggggrrrrr (bit 15 = STP/semi-transparency, set to 0).
    """
    r5 = (r >> 3) & 0x1F
    g5 = (g >> 3) & 0x1F
    b5 = (b >> 3) & 0x1F
    return r5 | (g5 << 5) | (b5 << 10)


def _build_flag_data(team: "WETeamRecord") -> tuple[int, bytes]:
    """Build (style_byte, color_data_32bytes) for a team's flag.

    Flag style byte selects the pattern rendered in-game:
        0 = solid colour
        1 = 3 vertical stripes
        2 = solid colour
        3 = animal print pattern
        4 = star pattern
        5 = ball pattern
    Each style reads from the 16-colour palette differently.

    If team.flag_style / team.flag_palette are set, uses those directly.
    Otherwise falls back to style 1 (3 vertical stripes) with 8×primary +
    8×secondary from ESPN colors.
    """
    if team.flag_style is not None and team.flag_palette and len(team.flag_palette) == 16:
        style = team.flag_style
        palette = [_rgb_to_ps1_color(*c) for c in team.flag_palette]
    else:
        style = 1  # 3 vertical stripes (see flag style table above)
        primary = _rgb_to_ps1_color(*team.kit_home)
        secondary = _rgb_to_ps1_color(*team.kit_away)
        palette = [primary] * 8 + [secondary] * 8
    color_data = struct.pack("<" + "H" * 16, *palette)
    return style, color_data


# ---------------------------------------------------------------------------
# Sector-boundary-aware offset calculation
# ---------------------------------------------------------------------------


def _nome_chunks(player_idx: int) -> list[tuple[int, int]]:
    """Return [(file_offset, byte_count)] to write the 10-byte player name."""
    if player_idx <= _NOME_LAST_GML:
        return [(_OFS_NOMI_GML + player_idx * _NOME_SIZE, _NOME_SIZE)]

    if player_idx <= _NOME_LAST_GML2:
        offset = _OFS_NOMI_GML2 + (player_idx - (_NOME_LAST_GML + 1)) * _NOME_SIZE
        return [(offset, _NOME_SIZE)]

    if player_idx == _NOME_STRADDLE:
        off1 = _OFS_NOMI_GML2 + (player_idx - (_NOME_LAST_GML + 1)) * _NOME_SIZE
        return [(off1, 8), (_OFS_NOMI_GML3, 2)]

    off = _OFS_NOMI_GML3 + 2 + (player_idx - (_NOME_STRADDLE + 1)) * _NOME_SIZE
    return [(off, _NOME_SIZE)]


def _carat_chunks(player_idx: int) -> list[tuple[int, int]]:
    """Return [(file_offset, byte_count)] to write the 12-byte characteristics."""
    if player_idx < _CARAT_STRADDLE1:
        return [(_OFS_CARAT_GML + player_idx * _CARAT_SIZE, _CARAT_SIZE)]

    if player_idx == _CARAT_STRADDLE1:
        off1 = _OFS_CARAT_GML + _CARAT_STRADDLE1 * _CARAT_SIZE
        return [(off1, 8), (_OFS_CARAT_GML1, 4)]

    if player_idx < _CARAT_STRADDLE2:
        off = _OFS_CARAT_GML1 + 4 + (player_idx - (_CARAT_STRADDLE1 + 1)) * _CARAT_SIZE
        return [(off, _CARAT_SIZE)]

    if player_idx == _CARAT_STRADDLE2:
        off1 = _OFS_CARAT_GML1 + 4 + (player_idx - (_CARAT_STRADDLE1 + 1)) * _CARAT_SIZE
        return [(off1, 4), (_OFS_CARAT_GML2, 8)]

    off = _OFS_CARAT_GML2 + 8 + (player_idx - (_CARAT_STRADDLE2 + 1)) * _CARAT_SIZE
    return [(off, _CARAT_SIZE)]


# ---------------------------------------------------------------------------
# National player sector-boundary-aware offset calculation
# ---------------------------------------------------------------------------

# Contiguous sections for national player names.  Each tuple is
# (first_nat_idx_inclusive, first_nat_idx_exclusive, base_offset).
# Straddle indices are the LAST element of their section; their linear
# offset is computed normally, then _nat_nome_chunks splits the write.
_NAT_NOME_SECTIONS = [
    (0, 206, _OFS_NOMI_G),  # index 0 straddles (8+2 at G+312)
    (206, 411, _OFS_NOMI_G2 + 4),  # after straddle 205 tail
    (411, 616, _OFS_NOMI_G3 + 6),  # after straddle 410 tail
    (616, 821, _OFS_NOMI_G4 + 8),  # after straddle 615 tail
    (821, 1025, _OFS_NOMI_G5 + 10),  # after straddle 820 (full 10)
    (1025, 1230, _OFS_NOMI_G6 + 2),  # after straddle 1024 tail
    (1230, 1435, _OFS_NOMI_G7 + 4),  # after straddle 1229 tail
    (1435, 1449, _OFS_NOMI_G8 + 6),  # after straddle 1434 tail
]

# Special: index 0 has an internal 312-byte gap (8 bytes at G, then skip to G+312
# for the remaining 2 bytes).  Indices 1+ within section 0 start after that tail.
_NAT_NOME_SEC0_GAP = 312 + 2  # bytes from _OFS_NOMI_G to first byte of index 1


def _nat_nome_chunks(nat_idx: int) -> list[tuple[int, int]]:
    """Return [(file_offset, byte_count)] to write a 10-byte national player name.

    nat_idx is the national player index (0-based, relative to GIOCATORI_NC).
    """
    if nat_idx in _NAT_NOME_STRADDLES:
        before, after, next_off = _NAT_NOME_STRADDLES[nat_idx]
        if before == 0:
            return [(next_off, _NOME_SIZE)]
        before_off = _nat_nome_linear_offset(nat_idx)
        return [(before_off, before), (next_off, after)]
    return [(_nat_nome_linear_offset(nat_idx), _NOME_SIZE)]


def _nat_nome_linear_offset(nat_idx: int) -> int:
    """Compute the file offset for a national player name."""
    if nat_idx == 0:
        return _OFS_NOMI_G
    # Section 0 indices 1-205: after the index-0 straddle gap
    if 1 <= nat_idx < 206:
        return _OFS_NOMI_G + _NAT_NOME_SEC0_GAP + (nat_idx - 1) * _NOME_SIZE
    for start, end, base in _NAT_NOME_SECTIONS[1:]:
        if start <= nat_idx < end:
            return base + (nat_idx - start) * _NOME_SIZE
    return _NAT_NOME_SECTIONS[-1][2] + (nat_idx - _NAT_NOME_SECTIONS[-1][0]) * _NOME_SIZE


# Contiguous sections for national player characteristics.
_NAT_CARAT_SECTIONS = [
    (0, 45, _OFS_CARAT_G),  # indices 0..44 (straddle 44 at end)
    (45, 216, _OFS_CARAT_G1 + 8),  # after straddle 44 tail
    (216, 386, _OFS_CARAT_G2 + 12),  # after clean break 215
    (386, 557, _OFS_CARAT_G3 + 4),  # after straddle 385 tail
    (557, 728, _OFS_CARAT_G4 + 8),  # after straddle 556 tail
    (728, 898, _OFS_CARAT_G5 + 12),  # after clean break 727
    (898, 1069, _OFS_CARAT_G6 + 4),  # after straddle 897 tail
    (1069, 1240, _OFS_CARAT_G7 + 8),  # after straddle 1068 tail
    (1240, 1410, _OFS_CARAT_G8 + 12),  # after clean break 1239
    (1410, 1449, _OFS_CARAT_G9 + 4),  # after straddle 1409 tail
]


def _nat_carat_chunks(nat_idx: int) -> list[tuple[int, int]]:
    """Return [(file_offset, byte_count)] to write 12-byte national player characteristics."""
    if nat_idx in _NAT_CARAT_STRADDLES:
        before, after, next_off = _NAT_CARAT_STRADDLES[nat_idx]
        if before == 0:
            return [(next_off, _CARAT_SIZE)]
        before_off = _nat_carat_linear_offset(nat_idx)
        return [(before_off, before), (next_off, after)]
    return [(_nat_carat_linear_offset(nat_idx), _CARAT_SIZE)]


def _nat_carat_linear_offset(nat_idx: int) -> int:
    """Compute the file offset for national player characteristics."""
    for start, end, base in _NAT_CARAT_SECTIONS:
        if start <= nat_idx < end:
            return base + (nat_idx - start) * _CARAT_SIZE
    return _NAT_CARAT_SECTIONS[-1][2] + (nat_idx - _NAT_CARAT_SECTIONS[-1][0]) * _CARAT_SIZE


def _nat_slot_player_range(nat_index: int) -> tuple[int, int]:
    """Return (first_nat_player_index, player_count) for a national team slot.

    nat_index: 0-62 (national + allstar team index).
    Returns indices relative to GIOCATORI_NC (national player base).
    """
    return nat_index * _PLAYERS_PER_NAT, _PLAYERS_PER_NAT


# ---------------------------------------------------------------------------
# Team-to-player index mapping
# ---------------------------------------------------------------------------


def _slot_player_range(slot_index: int) -> tuple[int, int]:
    """Return (first_global_player_index, player_count) for a ML slot.

    WE2002 stores squad_ml[31] first in the ROM, squad_ml[0] last.  Player
    indices 0-461 correspond to teams in that order.  Distribution assumption:
      - Slots 18-31 (ROM positions 0-13):  15 players each  → indices 0-209
      - Slots  0-17 (ROM positions 14-31): 14 players each  → indices 210-461
    """
    if slot_index >= 18:
        rom_pos = 31 - slot_index
        return rom_pos * 15, 15
    else:
        rom_pos = 31 - slot_index
        first = 14 * 15 + (rom_pos - 14) * 14
        return first, 14


def _write_chunks(f, data: bytes, chunks: list[tuple[int, int]]) -> None:
    """Write data bytes in pieces according to (offset, size) chunk list."""
    pos = 0
    for offset, size in chunks:
        f.seek(offset)
        f.write(data[pos : pos + size])
        pos += size


# ---------------------------------------------------------------------------
# Team name offset calculation
#
# The C++ writes names sequentially: 32 ML teams (reversed) then 63 national.
# We only patch ML teams.  For a given name variant offset base, the ML team
# at slot_index is written at:
#   base + sum(lun_nomiN[94 - j] for j in range(rom_position))
# where rom_position = the team's write index (0 = squad_ml[31], 31 = squad_ml[0]).
# ---------------------------------------------------------------------------


def _ml_name_offset(base_offset: int, slot_index: int, lun_table: list) -> int:
    """Calculate the absolute file offset for an ML team name.

    ML teams are written in reverse ROM order: rom_i=0 is squad_ml[31],
    rom_i=31 is squad_ml[0].  slot_index maps to rom_i = 31 - slot_index.
    """
    rom_i = 31 - slot_index
    off = base_offset
    for j in range(rom_i):
        # j-th ML team written uses lun_table index: 94 - j
        off += lun_table[94 - j]
    return off


# ---------------------------------------------------------------------------
# National team name offset calculation
#
# The C++ writes team names sequentially: 32 ML teams (reversed) then 63 national.
# For nationals, the array index is simply nat_index (0-62).
# The offset is: base + sum of all 32 ML budgets + sum of nat budgets 0..nat_index-1.
# Sector boundaries within national ranges must be handled per variant.
# ---------------------------------------------------------------------------


def _nat_name_offset(base_offset: int, nat_index: int, lun_table: list) -> int:
    """Calculate the absolute file offset for a national team name.

    The write order is: 32 ML teams (reversed) then 63 national teams sequential.
    National team at nat_index uses lun_table[nat_index].
    """
    off = base_offset
    # Skip all 32 ML teams (written first in reverse)
    for j in range(32):
        off += lun_table[94 - j]
    # Skip preceding national teams
    for j in range(nat_index):
        off += lun_table[j]
    return off


def _nat_name_offset_sq1(nat_index: int) -> int:
    """SQ1 has a sector boundary at national team 40 → OFS_NOMI_SQ1A."""
    off = _nat_name_offset(_OFS_NOMI_SQ1, nat_index, _LUN_NOMI1)
    if nat_index >= 40:
        # Recalculate from SQ1A base for teams at and after the boundary
        off = _OFS_NOMI_SQ1A
        for j in range(40, nat_index):
            off += _LUN_NOMI1[j]
    return off


def _nat_name_offset_sq5(nat_index: int) -> int:
    """SQ5 has a sector boundary → OFS_NOMI_SQ5A in the national range."""
    # Compute the sequential offset; the C++ seeks to SQ5A at a certain point.
    # SQ5A is at 4_823_976.  We compute the transition point by summing budgets.
    off = _nat_name_offset(_OFS_NOMI_SQ5, nat_index, _LUN_NOMI5)
    # Find where SQ5A kicks in: sum ML + nationals until we cross SQ5A
    running = _OFS_NOMI_SQ5
    for j in range(32):
        running += _LUN_NOMI5[94 - j]
    for j in range(63):
        if running >= _OFS_NOMI_SQ5A and j <= nat_index:
            # Boundary crossed at national team j
            off = _OFS_NOMI_SQ5A
            for k in range(j, nat_index):
                off += _LUN_NOMI5[k]
            return off
        running += _LUN_NOMI5[j]
    return off


def _nat_name_offset_sq6(nat_index: int) -> int:
    """SQ6 has a sector boundary at OFS_NOMI_SQ6B for nationals."""
    # Nationals start writing at SQ6B (after ML's SQ6/SQ6A region)
    off = _OFS_NOMI_SQ6B
    for j in range(nat_index):
        off += _LUN_NOMI6[j]
    return off


# National abbreviation offset: after 32 ML abbreviations (written in reverse)
def _nat_ab_offset(base_offset: int, nat_index: int) -> int:
    """National abbreviation offset: 32 ML (reversed) then 63 national sequential."""
    return base_offset + 32 * 4 + nat_index * 4


# ---------------------------------------------------------------------------
# National force bar offset calculation
# ---------------------------------------------------------------------------


def _nat_bar_offset(nat_index: int) -> list[tuple[int, int]]:
    """Return [(offset, nbytes)] chunks for a national team's 5 force bar bytes.

    National teams 0-62 are written first at OFS_BAR, with a sector boundary
    at team 3 (1 byte before boundary, then seek to OFS_BAR1 for 4 bytes).
    """
    if nat_index < 3:
        off = _OFS_BAR + nat_index * 5
        return [(off, 5)]
    elif nat_index == 3:
        # Straddle: 1 byte (bar_attacco) then seek to OFS_BAR1 for 4 bytes
        return [(_OFS_BAR + 15, 1), (_OFS_BAR1, 4)]
    else:
        # Teams 4-62: contiguous after OFS_BAR1 + 4
        off = _OFS_BAR1 + 4 + (nat_index - 4) * 5
        return [(off, 5)]


# National jersey preview offset calculation
def _nat_jersey_offset(nat_index: int) -> int:
    """Return the file offset for a national team's jersey preview (64 bytes).

    Sector boundary at team 30: seeks to OFS_ANT_MAGLIE1.
    """
    if nat_index < 30:
        return _OFS_ANT_MAGLIE + nat_index * 64
    else:
        # Teams 30-62 continue from OFS_ANT_MAGLIE1
        return _OFS_ANT_MAGLIE1 + (nat_index - 30) * 64


# ---------------------------------------------------------------------------
# National flag color offset computation
#
# The C++ OnWriteCD() writes national colors (i=0..55) then ML colors.
# We trace the file pointer to compute each national team's color offset.
# ---------------------------------------------------------------------------


def _compute_nat_color_offsets() -> dict:
    """Compute {nat_index: [(offset, nbytes)]} for national team flag colors.

    Traces the C++ OnWriteCD national color write loop (lines 5989-6015):
      - i=13: straddle (26 bytes + seek to COLORE1 + 6 bytes)
      - i=36, 39, 47: skipped
      - i=1, 40, 52: seek +32 (skip old slot) then write 32
      - default: write 32
    """
    result = {}
    pos = _OFS_BANDIERE_COLORE

    for i in range(56):
        if i == 13:
            result[i] = [(pos, 26), (_OFS_BANDIERE_COLORE1, 6)]
            pos += 26
            pos = _OFS_BANDIERE_COLORE1 + 6
        elif i in (36, 39, 47):
            pass  # skipped entirely — no write, no advance
        elif i in (1, 40, 52):
            pos += 32  # skip old slot
            result[i] = [(pos, 32)]
            pos += 32
        else:
            result[i] = [(pos, 32)]
            pos += 32

    # Teams 56-62 are allstar teams — they are in the ML color region
    # (handled by _compute_ml_color_offsets).  We don't write them here.

    return result


_NAT_COLOR_OFFSETS = _compute_nat_color_offsets()


# ---------------------------------------------------------------------------
# Flag color write order
#
# The C++ OnWriteCD() writes ML flag colors in this exact non-sequential order,
# with relative seeks interspersed.  We replicate the absolute offsets by
# tracking file position as the C++ code does.
# ---------------------------------------------------------------------------


def _build_ml_color_write_plan() -> list[tuple[int, int]]:
    """Build (ml_squad_index, relative_position) pairs for color writes.

    Returns a list of (squad_ml_index, absolute_offset_from_COLORE) entries.
    We simulate the C++ file pointer to compute absolute positions.
    """
    # The C++ first writes 56 national team colors, then does relative seeks
    # for ML teams.  We need to figure out the file position after all the
    # national writes, then trace the ML writes.
    #
    # Rather than simulate the full national write (which has its own skips),
    # we compute the ML color offsets by tracking the file pointer from known
    # absolute positions.  The ML color section starts after national teams.
    #
    # From the C++ (OnWriteCD lines 5987-6055):
    #   After writing 56 national colors with skips, the file pointer is at
    #   some position.  Then:
    #     Seek(64, current)      → skip 2 empty slots
    #     Write ml[0..4]         → 5 × 32 bytes
    #     Write ml[10]           → 32 bytes
    #     Write ml[7..9]         → 3 × 32 bytes
    #     Write ml[11..12]       → 2 × 32 bytes
    #     Write ml[15]           → 32 bytes
    #     Write ml[18..21]       → 4 × 32 bytes
    #     Seek(32, current)      → skip 1 slot
    #     Write ml[14]           → 32 bytes
    #     Write ml[24]           → 32 bytes
    #     Write ml[25]           → 32 bytes
    #     Write ml[26] partial   → 26 bytes (straddles to COLORE2)
    #     --- Seek to COLORE2 ---
    #     Write ml[26] tail      → 6 bytes
    #     Write ml[27]           → 32 bytes
    #     Write ml[16..17]       → 2 × 32 bytes
    #     Seek(64, current)      → skip 2 slots
    #     Write ml[13]           → 32 bytes
    #     Seek(288, current)     → skip 9 slots
    #     Write nazall[39]       → 32 bytes (national team, skip)
    #     Seek(64, current)      → skip 2 slots
    #     Write nazall[47]       → 32 bytes (national team, skip)
    #     Write ml[6]            → 32 bytes
    #     Write ml[23]           → 32 bytes
    #     Write ml[28..31]       → 4 × 32 bytes
    #
    # We don't need absolute offsets for every position — instead we compute
    # them relative to OFS_BANDIERE_COLORE2 for the second segment,
    # and use a tracking approach for the first segment.
    #
    # Actually, the simplest correct approach: we know the file position after
    # writing nationals.  Let's compute it.
    #
    # National color write (lines 5989-6015):
    #   56 teams with special cases:
    #   - i=13: write 26, seek to COLORE1, write 6 (straddle)
    #   - i=36,39,47: skip (no write)
    #   - i=1,40,52: seek +32 before write (skip old team slot)
    #   - default: write 32
    #
    # After COLORE1 seek at i=13, file position is OFS_BANDIERE_COLORE1 + 6.
    # Then normal 32-byte writes continue for i=14..55 (minus skips).
    # Actually this is getting complex — let me just hardcode the ML offsets
    # by doing the math once.

    # I'll trace the file pointer through the entire national+ML write sequence.
    # Start at OFS_BANDIERE_COLORE.
    return []  # placeholder — actual computation below


# Instead of the complex tracing, compute the ML color offsets directly.
# The ML color region starts at a known position that we can derive.


def _compute_ml_color_offsets() -> dict:
    """Compute {ml_index: (offset, size)} for 30 of the 32 ML teams' color data.

    ml[5] and ml[22] have no known offset and are absent from the result — the
    upstream C++ write sequence traced below never writes them. See the comment
    at the end of this function and `_write_flag_impl` for what that costs.

    For teams that straddle a sector boundary (ml[26]), returns two entries
    that must be written separately.

    Returns dict mapping ml_index to list of (offset, nbytes) chunks.
    """
    # Trace the C++ file pointer through OnWriteCD lines 5989-6055.
    pos = _OFS_BANDIERE_COLORE

    # --- National teams (i=0..55) ---
    for i in range(56):
        if i == 13:
            # Write 26 bytes, then seek to COLORE1, write 6 bytes
            pos += 26
            pos = _OFS_BANDIERE_COLORE1 + 6  # after writing the 6-byte tail
        elif i in (36, 39, 47):
            pass  # skipped entirely
        elif i in (1, 40, 52):
            pos += 32  # skip an old slot
            pos += 32  # then write 32
        else:
            pos += 32

    # Now pos is after all national color writes.

    result = {}

    # Seek(64, current) — skip 2 empty slots
    pos += 64

    # ml[0..4]: 5 × 32
    for idx in range(5):
        result[idx] = [(pos, 32)]
        pos += 32

    # ml[10]: 32
    result[10] = [(pos, 32)]
    pos += 32

    # ml[7..9]: 3 × 32
    for idx in range(7, 10):
        result[idx] = [(pos, 32)]
        pos += 32

    # ml[11..12]: 2 × 32
    for idx in range(11, 13):
        result[idx] = [(pos, 32)]
        pos += 32

    # ml[15]: 32
    result[15] = [(pos, 32)]
    pos += 32

    # ml[18..21]: 4 × 32
    for idx in range(18, 22):
        result[idx] = [(pos, 32)]
        pos += 32

    # Seek(32, current) — skip 1 slot
    pos += 32

    # ml[14]: 32
    result[14] = [(pos, 32)]
    pos += 32

    # ml[24]: 32
    result[24] = [(pos, 32)]
    pos += 32

    # ml[25]: 32
    result[25] = [(pos, 32)]
    pos += 32

    # ml[26]: straddle — 26 bytes here, then 6 bytes at COLORE2
    result[26] = [(pos, 26), (_OFS_BANDIERE_COLORE2, 6)]
    pos += 26
    pos = _OFS_BANDIERE_COLORE2 + 6  # after writing tail at COLORE2

    # ml[27]: 32
    result[27] = [(pos, 32)]
    pos += 32

    # ml[16..17]: 2 × 32
    for idx in range(16, 18):
        result[idx] = [(pos, 32)]
        pos += 32

    # Seek(64, current) — skip 2 slots
    pos += 64

    # ml[13]: 32
    result[13] = [(pos, 32)]
    pos += 32

    # Seek(288, current) — skip 9 slots
    pos += 288

    # nazall[39]: 32 (national team — we skip it)
    pos += 32

    # Seek(64, current) — skip 2 slots
    pos += 64

    # nazall[47]: 32 (national team — we skip it)
    pos += 32

    # ml[6]: 32
    result[6] = [(pos, 32)]
    pos += 32

    # ml[23]: 32
    result[23] = [(pos, 32)]
    pos += 32

    # ml[28..31]: 4 × 32
    for idx in range(28, 32):
        result[idx] = [(pos, 32)]
        pos += 32

    # TWO slots are missing from the C++ write sequence, not one: ml[5] and
    # ml[22]. Neither is reached by any of the writes traced above, so `result`
    # holds 30 of the 32. They may share a slot with one of the national teams
    # or sit at a position that has not been found.
    #
    # Leave both unpatched. Guessing an offset here writes 32 colour bytes into
    # an unidentified ISO structure, which is a worse outcome than an unpatched
    # flag palette. `_write_flag_impl` still writes the FORMA style byte for
    # them, so those two clubs get a new geometric pattern drawn over the
    # original Japanese palette, and `patcher.py` counts them in
    # `teams_patched`. That is the accepted cost.
    #
    # This is inherited from the upstream write sequence, not a porting error:
    # `_compute_ml_color_offsets` is byte-identical to the version it was
    # ported from, and so was the one-slot claim this comment replaces.

    return result


# Pre-compute once at import time
_ML_COLOR_OFFSETS = _compute_ml_color_offsets()

# ---------------------------------------------------------------------------
# Force bar offset calculation
#
# Force bars: 5 bytes per team.  National teams (0-62) first at OFS_BAR,
# with a sector boundary at national team index 3 (seeks to OFS_BAR1 after
# writing bar_attacco, before bar_difesa).  Then ML teams (0-31) sequentially.
# ML bars follow directly after the last national team bar.
# ---------------------------------------------------------------------------


def _compute_ml_bar_offset() -> int:
    """Compute the file offset where ML force bars begin.

    National teams: 63 teams × 5 bytes each, with sector boundary at team 3.
    The boundary is after writing team 3's bar_attacco (1 byte into team 3's
    5-byte block).  So:
      - Teams 0-2: 3 × 5 = 15 bytes at OFS_BAR
      - Team 3: 1 byte (bar_attacco) at OFS_BAR + 15
      - Then seek to OFS_BAR1, write remaining 4 bytes of team 3
      - Teams 4-62: 59 × 5 = 295 bytes continuing from OFS_BAR1 + 4
    Total national bytes after OFS_BAR1: 4 + 59×5 = 299
    ML starts at: OFS_BAR1 + 299
    """
    return _OFS_BAR1 + 4 + 59 * 5


_ML_BAR_OFFSET = _compute_ml_bar_offset()

# ---------------------------------------------------------------------------
# 3D jersey TEX file helpers (CD sector I/O with EDC)
# ---------------------------------------------------------------------------


def _edc_compute(data: bytes) -> int:
    """Compute EDC (CRC-32) for CD-ROM Mode 2 Form 1 sector data.

    Covers bytes 16..2071 of a sector (subheader + user data = 2056 bytes).
    Uses the CD-ROM polynomial 0xD8018001.
    """
    table = []
    for i in range(256):
        edc = i
        for _ in range(8):
            edc = (edc >> 1) ^ 0xD8018001 if edc & 1 else edc >> 1
        table.append(edc)
    crc = 0
    for b in data:
        crc = table[(crc ^ b) & 0xFF] ^ (crc >> 8)
    return crc


def _build_tex_dir_map(rom_data: bytes) -> tuple[dict, dict]:
    """Scan ISO9660 BIN directory to find TEX file entries.

    Returns:
        (sizes, dir_offsets) where:
        - sizes: dict of TEX index → file size in bytes
        - dir_offsets: dict of TEX index → absolute ROM byte offset of directory record
    """
    dir_data = bytearray()
    sectors = (_BIN_DIR_SIZE + 2047) // 2048
    for s in range(sectors):
        off = (_BIN_DIR_LBA + s) * 2352 + 24
        dir_data.extend(rom_data[off : off + 2048])

    sizes = {}
    dir_offsets = {}
    pos = 0
    while pos < len(dir_data):
        rec_len = dir_data[pos]
        if rec_len == 0:
            pos = ((pos // 2048) + 1) * 2048
            continue
        name_len = dir_data[pos + 32]
        name = dir_data[pos + 33 : pos + 33 + name_len].decode("ascii", errors="replace")
        if name.startswith("TEX_") and name.endswith(".BIN;1"):
            idx_str = name[4:-6]
            try:
                idx = int(idx_str)
                if idx < _TEX_COUNT:
                    ext_size = struct.unpack_from("<I", dir_data, pos + 10)[0]
                    sizes[idx] = ext_size
                    sector_in_dir = pos // 2048
                    offset_in_sector = pos % 2048
                    abs_pos = (_BIN_DIR_LBA + sector_in_dir) * 2352 + 24 + offset_in_sector
                    dir_offsets[idx] = abs_pos
            except ValueError:
                pass
        pos += rec_len
    return sizes, dir_offsets


def _update_iso_dir_size(rom: bytearray, dir_record_offset: int, new_size: int):
    """Update file size in an ISO9660 directory record (both LE and BE) + EDC."""
    struct.pack_into("<I", rom, dir_record_offset + 10, new_size)
    struct.pack_into(">I", rom, dir_record_offset + 14, new_size)
    # Recalculate EDC for the sector containing this directory record
    sector_off = (dir_record_offset // 2352) * 2352
    new_edc = _edc_compute(bytes(rom[sector_off + 16 : sector_off + 2072]))
    struct.pack_into("<I", rom, sector_off + 2072, new_edc)


def _read_cd_file(rom_data: bytes, lba: int, size: int) -> bytes:
    """Read a file from Mode2/2352 CD sectors, skipping sector overhead."""
    result = bytearray()
    current_lba = lba
    remaining = size
    while remaining > 0:
        sector_offset = current_lba * 2352 + 24
        chunk = min(remaining, 2048)
        result.extend(rom_data[sector_offset : sector_offset + chunk])
        remaining -= chunk
        current_lba += 1
    return bytes(result)


def _write_cd_file_with_edc(rom: bytearray, lba: int, file_data: bytes):
    """Write file data to CD sectors, recalculating EDC checksums."""
    current_lba = lba
    offset = 0
    while offset < len(file_data):
        sector_off = current_lba * 2352
        user_off = sector_off + 24
        chunk = min(len(file_data) - offset, 2048)
        rom[user_off : user_off + chunk] = file_data[offset : offset + chunk]
        new_edc = _edc_compute(bytes(rom[sector_off + 16 : sector_off + 2072]))
        struct.pack_into("<I", rom, sector_off + 2072, new_edc)
        offset += chunk
        current_lba += 1


def _read_cd_file_from_handle(f, lba: int, size: int) -> bytes:
    """Read a file from Mode2/2352 CD sectors using file seeks."""
    result = bytearray()
    current_lba = lba
    remaining = size
    while remaining > 0:
        f.seek(current_lba * 2352 + 24)
        chunk = min(remaining, 2048)
        result.extend(f.read(chunk))
        remaining -= chunk
        current_lba += 1
    return bytes(result)


def _write_cd_file_with_edc_to_handle(f, lba: int, file_data: bytes):
    """Write file data to CD sectors via file seeks, recalculating EDC."""
    current_lba = lba
    offset = 0
    while offset < len(file_data):
        sector_off = current_lba * 2352
        user_off = sector_off + 24
        chunk = min(len(file_data) - offset, 2048)
        # Write user data
        f.seek(user_off)
        f.write(file_data[offset : offset + chunk])
        # Read back sector bytes 16..2071 for EDC calculation
        f.seek(sector_off + 16)
        sector_data = f.read(2056)
        new_edc = _edc_compute(sector_data)
        f.seek(sector_off + 2072)
        f.write(struct.pack("<I", new_edc))
        offset += chunk
        current_lba += 1


def _update_iso_dir_size_in_handle(f, dir_record_offset: int, new_size: int):
    """Update file size in an ISO9660 directory record via file seeks."""
    f.seek(dir_record_offset + 10)
    f.write(struct.pack("<I", new_size))
    f.seek(dir_record_offset + 14)
    f.write(struct.pack(">I", new_size))
    # Recalculate EDC for the sector containing this directory record
    sector_off = (dir_record_offset // 2352) * 2352
    f.seek(sector_off + 16)
    sector_data = f.read(2056)
    new_edc = _edc_compute(sector_data)
    f.seek(sector_off + 2072)
    f.write(struct.pack("<I", new_edc))


def _build_tex_dir_map_from_dir(dir_data: bytearray) -> tuple[dict, dict]:
    """Parse pre-read directory data to find TEX file entries.

    Same logic as _build_tex_dir_map but works with already-read dir_data.
    """
    sizes = {}
    dir_offsets = {}
    pos = 0
    while pos < len(dir_data):
        rec_len = dir_data[pos]
        if rec_len == 0:
            pos = ((pos // 2048) + 1) * 2048
            continue
        name_len = dir_data[pos + 32]
        name = dir_data[pos + 33 : pos + 33 + name_len].decode("ascii", errors="replace")
        if name.startswith("TEX_") and name.endswith(".BIN;1"):
            idx_str = name[4:-6]
            try:
                idx = int(idx_str)
                if idx < _TEX_COUNT:
                    ext_size = struct.unpack_from("<I", dir_data, pos + 10)[0]
                    sizes[idx] = ext_size
                    sector_in_dir = pos // 2048
                    offset_in_sector = pos % 2048
                    abs_pos = (_BIN_DIR_LBA + sector_in_dir) * 2352 + 24 + offset_in_sector
                    dir_offsets[idx] = abs_pos
            except ValueError:
                pass
        pos += rec_len
    return sizes, dir_offsets


def _find_best_tex_match(target_rgb: tuple[int, int, int]) -> int | None:
    """Find the TEX index whose known jersey color is closest to target_rgb."""
    best_idx = None
    best_dist = float("inf")
    for idx, color in _TEX_JERSEY_COLORS.items():
        # strict=False keeps the ported behaviour: a caller passing a target of a
        # different length than the table's 3-channel colors still gets a distance
        # over the shared prefix rather than a ValueError.
        dist = sum((a - b) ** 2 for a, b in zip(target_rgb, color, strict=False))
        if dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx


# ---------------------------------------------------------------------------
# Public writer class
# ---------------------------------------------------------------------------

_DUMMY_NAME = b"PLAYER\x00\x00\x00\x00"
_DUMMY_CARAT = bytes(12)


class RomWriter:
    def __init__(self, rom_path: str, output_path: str):
        """Copy the ROM to output_path for patching. The original is never modified.

        If rom_path and output_path resolve to the same file (re-patch), the
        copy is skipped and the file is patched in-place.
        """
        self._in_place = os.path.abspath(rom_path) == os.path.abspath(output_path)
        if not self._in_place and os.path.exists(rom_path):
            shutil.copy2(rom_path, output_path)
            # Sync the new file to disk before patching begins
            fd = os.open(output_path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.output_path = output_path
        self._tex_cache = None  # Original TEX file data (avoids re-read corruption)
        self._tex_sizes = None  # TEX file sizes from ISO9660 directory
        self._tex_dir_offsets = None  # ISO9660 directory record offsets for size patching
        # Queued (tex_index, target_rgb) for batch apply
        self._pending_tex_patches: list[tuple[int, tuple[int, int, int]]] = []

    def write_team(
        self,
        slot_index: int,
        team: WETeamRecord,
        players: list[WEPlayerRecord] | None = None,
        include_flag: bool = True,
    ) -> int:
        """Write team names, abbreviations, force bars, players, and flag.

        Consolidates all writes into a single file open to minimize I/O
        on slow storage (SD cards on handhelds).

        Returns the number of supplied player records that reached the ROM: 0
        when there is no output file, when the slot is outside 0..31, or when no
        `players=` list was handed over, and otherwise the slot's capacity
        bounded by the list length. See `_write_players_impl` for why this
        return value diverges deliberately from upstream.
        """
        if not os.path.exists(self.output_path):
            return 0
        if slot_index < 0 or slot_index >= _SQUADRE_ML:
            return 0

        written = 0
        with open(self.output_path, "r+b") as f:
            self._write_team_names(f, slot_index, team)
            self._write_kanji_name(f, slot_index, team)
            self._write_abbreviations(f, slot_index, team)
            self._write_force_bars(f, slot_index, team)
            self._write_jersey_colors(f, slot_index, team)

            # Write players in same file handle
            if players is not None:
                written = self._write_players_impl(f, slot_index, players)

            # Write flag in same file handle
            if include_flag:
                self._write_flag_impl(f, slot_index, team)
            _sync(f)

        # Queue 3D jersey TEX patch (ML teams are TEX indices 63-94)
        tex_index = 63 + slot_index
        self._pending_tex_patches.append((tex_index, team.kit_home))
        return written

    def _write_team_names(self, f, slot_index: int, team: WETeamRecord):
        """Write all 6 name variants + lowercase for an ML team slot.

        The ROM has 6 name variants (nomi[0..5]) and a lowercase name (nome_m).
        Each has its own offset base and length table.  We write the team name
        to all of them — uppercase for variants 1-6, mixed case for lowercase.
        """
        name = team.name

        # Name variant 1 — uses lun_nomi1, offset base OFS_NOMI_SQ1
        # Special case: SQ1 has a sector boundary at national team i==40
        # (OFS_NOMI_SQ1A), but that's in the national section (after ML).
        # ML teams are written first (indices 0-31), so no boundary for ML.
        budget = _ml_name_budget(slot_index, _LUN_NOMI1)
        offset = _ml_name_offset(_OFS_NOMI_SQ1, slot_index, _LUN_NOMI1)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 2
        budget = _ml_name_budget(slot_index, _LUN_NOMI2)
        offset = _ml_name_offset(_OFS_NOMI_SQ2, slot_index, _LUN_NOMI2)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 3
        budget = _ml_name_budget(slot_index, _LUN_NOMI3)
        offset = _ml_name_offset(_OFS_NOMI_SQ3, slot_index, _LUN_NOMI3)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 4
        budget = _ml_name_budget(slot_index, _LUN_NOMI4)
        offset = _ml_name_offset(_OFS_NOMI_SQ4, slot_index, _LUN_NOMI4)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 5
        budget = _ml_name_budget(slot_index, _LUN_NOMI5)
        offset = _ml_name_offset(_OFS_NOMI_SQ5, slot_index, _LUN_NOMI5)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 6 — has sector boundary at ML i==15 (OFS_NOMI_SQ6A)
        budget = _ml_name_budget(slot_index, _LUN_NOMI6)
        offset = self._sq6_ml_offset(slot_index)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Lowercase name
        budget = _ml_name_budget(slot_index, _LUN_NOMI_MIN)
        offset = _ml_name_offset(_OFS_NOMI_SQ_M, slot_index, _LUN_NOMI_MIN)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=False))

    def _write_kanji_name(self, f, slot_index: int, team: WETeamRecord):
        """Write the 2-byte encoded (kanji) name for an ML team.

        This overwrites the kanji section (OFS_NOMI_SQK) which the built-in
        PPF sets to default English names.  After this, the kanji section
        shows the actual API team name instead of the original ML team name.
        """
        budget = _LUN_NOMIK[63 + slot_index]
        offset = _ml_kanji_offset(slot_index)
        f.seek(offset)
        f.write(_encode_kanji_name(team.name, budget))

    def _sq6_ml_offset(self, slot_index: int) -> int:
        """Compute offset for name variant 6 (SQ6), which has a sector boundary.

        The C++ seeks to OFS_NOMI_SQ6A when i==15 (rom write index 15,
        which is squad_ml[16] = slot_index 16).  ML teams before rom_i=15
        are at OFS_NOMI_SQ6, teams at and after rom_i=15 are at OFS_NOMI_SQ6A.
        """
        rom_i = 31 - slot_index
        if rom_i < 15:
            # Before the boundary
            off = _OFS_NOMI_SQ6
            for j in range(rom_i):
                off += _LUN_NOMI6[94 - j]
            return off
        else:
            # At or after the boundary — base is OFS_NOMI_SQ6A
            off = _OFS_NOMI_SQ6A
            for j in range(15, rom_i):
                off += _LUN_NOMI6[94 - j]
            return off

    def _write_abbreviations(self, f, slot_index: int, team: WETeamRecord):
        """Write 3 abbreviation variants (fixed 4 bytes each)."""
        code = team.short_name or team.name[:3]
        abbrev = _encode_abbreviation(code)

        # Abbreviations are written: 32 ML (reversed) then 63 national, sequential.
        rom_i = 31 - slot_index

        # AB1
        off = _OFS_NOMI_SQ_AB1 + rom_i * 4
        f.seek(off)
        f.write(abbrev)

        # AB2
        off = _OFS_NOMI_SQ_AB2 + rom_i * 4
        f.seek(off)
        f.write(abbrev)

        # AB3
        off = _OFS_NOMI_SQ_AB3 + rom_i * 4
        f.seek(off)
        f.write(abbrev)

    def _write_force_bars(self, f, slot_index: int, team: WETeamRecord):
        """Write 5 force bar bytes for an ML team.

        Force bars are written: 63 national first, then 32 ML sequential
        (squad_ml[0] first, not reversed).  Each team = 5 bytes:
        attack, defense, power, speed, technique.
        """
        # Compute overall team ratings from roster
        att, defe, power, speed, tech = self._compute_force_bars(team)

        off = _ML_BAR_OFFSET + slot_index * 5
        f.seek(off)
        f.write(bytes([att, defe, power, speed, tech]))

    def _compute_force_bars(self, team: WETeamRecord) -> tuple[int, int, int, int, int]:
        """Derive 5 force bar values (each 1-8) from team roster attributes."""
        if not team.players:
            return (4, 4, 4, 4, 4)

        # Average across all players for each bar category
        n = len(team.players)
        tot_att = sum(p.attributes.offensive for p in team.players)
        tot_def = sum(p.attributes.defensive for p in team.players)
        tot_pow = sum(p.attributes.body_balance + p.attributes.shoot_power for p in team.players)
        tot_spd = sum(p.attributes.speed + p.attributes.acceleration for p in team.players)
        tot_tec = sum(p.attributes.technique + p.attributes.pass_accuracy for p in team.players)

        def bar(total, divisor):
            avg = total / divisor
            return max(1, min(8, round(avg)))

        return (
            bar(tot_att, n),
            bar(tot_def, n),
            bar(tot_pow, n * 2),
            bar(tot_spd, n * 2),
            bar(tot_tec, n * 2),
        )

    def _write_jersey_colors(self, f, slot_index: int, team: WETeamRecord):
        """Write jersey preview palette (maglia1 + maglia2) for an ML team.

        The maglia controls the 2D menu preview and 3D shorts only.
        The 3D shirt body is controlled by TEX files (patched separately
        via patch_3d_jersey).  Indices 0-1 reserved, 2-9 = shirt preview,
        10-15 = shorts.
        """
        if team.jersey_data and len(team.jersey_data) == 64:
            off = _OFS_ANT_MAGLIE2 + slot_index * 64
            f.seek(off)
            f.write(team.jersey_data)
            return

        primary = _rgb_to_ps1_color(*team.kit_home)
        secondary = _rgb_to_ps1_color(*team.kit_away)

        home_palette = [0, 0] + [primary] * 8 + [secondary] * 6
        away_palette = [0, 0] + [secondary] * 8 + [primary] * 6

        maglia1 = struct.pack("<" + "H" * 16, *home_palette)
        maglia2 = struct.pack("<" + "H" * 16, *away_palette)

        off = _OFS_ANT_MAGLIE2 + slot_index * 64
        f.seek(off)
        f.write(maglia1)
        f.write(maglia2)

    def _write_players_impl(self, f, slot_index, players) -> int:
        """Write player names + characteristics (uses existing file handle).

        Returns the number of supplied records that reached the ROM. The loop is
        bounded by the slot's fixed capacity (14 or 15, see `_slot_player_range`)
        and `players[count:]` is dropped, so a caller that reports
        `len(players)` reports players it never wrote.

        The return value is a DELIBERATE DIVERGENCE from the upstream C++/Python
        writer, which returned nothing: `PatchResult.players_patched` is this
        project's own contract and needs a truthful number. Not a port bug — it
        writes exactly the same bytes upstream did. Do not "restore" it.
        """
        first_idx, count = _slot_player_range(slot_index)
        for i in range(count):
            global_idx = first_idx + i
            if i < len(players):
                p = players[i]
                name_bytes = _encode_player_name(p.last_name)
                carat_bytes = _encode_player_carat(p)
            else:
                name_bytes = _DUMMY_NAME
                carat_bytes = _DUMMY_CARAT
            _write_chunks(f, name_bytes, _nome_chunks(global_idx))
            _write_chunks(f, carat_bytes, _carat_chunks(global_idx))
        return min(len(players), count)

    def write_players(self, slot_index: int, players: list[WEPlayerRecord]) -> int:
        """Write player names + characteristics for a team slot.

        Returns the number of supplied records written, or 0 if there is no
        output file to write them to.
        """
        if not os.path.exists(self.output_path):
            return 0
        with open(self.output_path, "r+b") as f:
            written = self._write_players_impl(f, slot_index, players)
            _sync(f)
        return written

    def _write_flag_impl(self, f, slot_index, team):
        """Write team flag data (uses existing file handle).

        The style byte goes to all 32 ML slots; the 32-byte palette goes to 30
        of them. `_ML_COLOR_OFFSETS` has no entry for ml[5] or ml[22] — the
        upstream write sequence never wrote those two and no offset for them is
        known — so those slots take a new geometric pattern over the original
        Japanese palette. Inherited from upstream, and left that way on purpose:
        a guessed offset writes colour bytes into an unidentified structure.
        """
        if slot_index < 0 or slot_index >= _SQUADRE_ML:
            return
        style, color_data = _build_flag_data(team)
        for forma_base in [
            _OFS_BANDIERE_FORMA1,
            _OFS_BANDIERE_FORMA2,
            _OFS_BANDIERE_FORMA3,
            _OFS_BANDIERE_FORMA4,
            _OFS_BANDIERE_FORMA5,
        ]:
            off = forma_base + _SQUADRE_NAZ + slot_index
            f.seek(off)
            f.write(bytes([style]))
        if slot_index in _ML_COLOR_OFFSETS:
            chunks = _ML_COLOR_OFFSETS[slot_index]
            _write_chunks(f, color_data, chunks)

    def write_flag(self, slot_index: int, team: WETeamRecord):
        """Write team flag (style byte, and 16 colors for 30 of the 32 slots).

        ml[5] and ml[22] get the style byte and no palette; see
        `_write_flag_impl`.
        """
        if not os.path.exists(self.output_path):
            return
        with open(self.output_path, "r+b") as f:
            self._write_flag_impl(f, slot_index, team)
            _sync(f)

    # ------------------------------------------------------------------
    # National team writers
    # ------------------------------------------------------------------

    def write_nat_team(
        self,
        nat_index: int,
        team: WETeamRecord,
        players: list[WEPlayerRecord] | None = None,
        include_flag: bool = True,
    ):
        """Write national team names, abbreviations, force bars, players, and flag.

        Consolidates all writes into a single file open.
        """
        if not os.path.exists(self.output_path):
            return
        if nat_index < 0 or nat_index >= _SQUADRE_NAZ:
            return

        with open(self.output_path, "r+b") as f:
            self._write_nat_team_names(f, nat_index, team)
            self._write_nat_kanji_name(f, nat_index, team)
            self._write_nat_abbreviations(f, nat_index, team)
            self._write_nat_force_bars(f, nat_index, team)
            self._write_nat_jersey_colors(f, nat_index, team)

            if players is not None:
                self._write_nat_players_impl(f, nat_index, players)

            if include_flag:
                self._write_nat_flag_impl(f, nat_index, team)
            _sync(f)

        # Queue 3D jersey TEX patch (national teams are TEX indices 0-62)
        self._pending_tex_patches.append((nat_index, team.kit_home))

    def _write_nat_team_names(self, f, nat_index: int, team: WETeamRecord):
        """Write all name variants for a national team slot."""
        name = team.name

        # Name variant 1 (SQ1) — sector boundary at national team 40
        budget = _LUN_NOMI1[nat_index]
        offset = _nat_name_offset_sq1(nat_index)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 2 (SQ2) — no national boundary
        budget = _LUN_NOMI2[nat_index]
        offset = _nat_name_offset(_OFS_NOMI_SQ2, nat_index, _LUN_NOMI2)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 3 (SQ3) — no national boundary
        budget = _LUN_NOMI3[nat_index]
        offset = _nat_name_offset(_OFS_NOMI_SQ3, nat_index, _LUN_NOMI3)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 4 (SQ4) — no national boundary
        budget = _LUN_NOMI4[nat_index]
        offset = _nat_name_offset(_OFS_NOMI_SQ4, nat_index, _LUN_NOMI4)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 5 (SQ5) — has sector boundary in national range
        budget = _LUN_NOMI5[nat_index]
        offset = _nat_name_offset_sq5(nat_index)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Name variant 6 (SQ6) — nationals start at SQ6B
        budget = _LUN_NOMI6[nat_index]
        offset = _nat_name_offset_sq6(nat_index)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=True))

        # Lowercase name
        budget = _LUN_NOMI_MIN[nat_index]
        offset = _nat_name_offset(_OFS_NOMI_SQ_M, nat_index, _LUN_NOMI_MIN)
        f.seek(offset)
        f.write(_encode_team_name(name, budget, uppercase=False))

    def _write_nat_kanji_name(self, f, nat_index: int, team: WETeamRecord):
        """Write the 2-byte encoded (kanji) name for a national team.

        National kanji names are stored after ML kanji names in reverse order
        (62, 61, ..., 0) with a sector boundary straddling nat_index=4.
        """
        budget = _LUN_NOMIK[nat_index]
        encoded = _encode_kanji_name(team.name, budget)
        chunks = _nat_kanji_chunks(nat_index)
        _write_chunks(f, encoded, chunks)

    def _write_nat_abbreviations(self, f, nat_index: int, team: WETeamRecord):
        """Write 3 abbreviation variants for a national team slot."""
        code = team.short_name or team.name[:3]
        abbrev = _encode_abbreviation(code)

        f.seek(_nat_ab_offset(_OFS_NOMI_SQ_AB1, nat_index))
        f.write(abbrev)
        f.seek(_nat_ab_offset(_OFS_NOMI_SQ_AB2, nat_index))
        f.write(abbrev)
        f.seek(_nat_ab_offset(_OFS_NOMI_SQ_AB3, nat_index))
        f.write(abbrev)

    def _write_nat_force_bars(self, f, nat_index: int, team: WETeamRecord):
        """Write 5 force bar bytes for a national team slot."""
        att, defe, power, speed, tech = self._compute_force_bars(team)
        bar_data = bytes([att, defe, power, speed, tech])
        chunks = _nat_bar_offset(nat_index)
        _write_chunks(f, bar_data, chunks)

    def _write_nat_jersey_colors(self, f, nat_index: int, team: WETeamRecord):
        """Write jersey preview palette (maglia1 + maglia2) for a national team slot.

        The maglia controls the 2D menu preview and 3D shorts only.
        The 3D shirt body is controlled by TEX files (patched separately
        via patch_3d_jersey).  Indices 0-1 reserved, 2-9 = shirt preview,
        10-15 = shorts.
        """
        if team.jersey_data and len(team.jersey_data) == 64:
            off = _nat_jersey_offset(nat_index)
            f.seek(off)
            f.write(team.jersey_data)
            return

        primary = _rgb_to_ps1_color(*team.kit_home)
        secondary = _rgb_to_ps1_color(*team.kit_away)

        home_palette = [0, 0] + [primary] * 8 + [secondary] * 6
        away_palette = [0, 0] + [secondary] * 8 + [primary] * 6

        maglia1 = struct.pack("<" + "H" * 16, *home_palette)
        maglia2 = struct.pack("<" + "H" * 16, *away_palette)

        off = _nat_jersey_offset(nat_index)
        f.seek(off)
        f.write(maglia1)
        f.write(maglia2)

    def _write_nat_players_impl(self, f, nat_index, players):
        """Write national player names + characteristics (uses existing file handle)."""
        first_nat_idx, count = _nat_slot_player_range(nat_index)
        for i in range(count):
            nat_player_idx = first_nat_idx + i
            if i < len(players):
                p = players[i]
                name_bytes = _encode_player_name(p.last_name)
                carat_bytes = _encode_player_carat(p)
            else:
                name_bytes = _DUMMY_NAME
                carat_bytes = _DUMMY_CARAT
            _write_chunks(f, name_bytes, _nat_nome_chunks(nat_player_idx))
            _write_chunks(f, carat_bytes, _nat_carat_chunks(nat_player_idx))

    def write_nat_players(self, nat_index: int, players: list[WEPlayerRecord]):
        """Write player names + characteristics for a national team slot."""
        if not os.path.exists(self.output_path):
            return
        if nat_index < 0 or nat_index >= _SQUADRE_NAZ:
            return
        with open(self.output_path, "r+b") as f:
            self._write_nat_players_impl(f, nat_index, players)
            _sync(f)

    def _write_nat_flag_impl(self, f, nat_index, team):
        """Write national flag data (uses existing file handle)."""
        if nat_index < 0 or nat_index >= _SQUADRE_NAZ:
            return
        style, color_data = _build_flag_data(team)
        for forma_base in [
            _OFS_BANDIERE_FORMA1,
            _OFS_BANDIERE_FORMA2,
            _OFS_BANDIERE_FORMA3,
            _OFS_BANDIERE_FORMA4,
            _OFS_BANDIERE_FORMA5,
        ]:
            off = forma_base + nat_index
            f.seek(off)
            f.write(bytes([style]))
        if nat_index in _NAT_COLOR_OFFSETS:
            chunks = _NAT_COLOR_OFFSETS[nat_index]
            _write_chunks(f, color_data, chunks)

    def write_nat_flag(self, nat_index: int, team: WETeamRecord):
        """Write flag (style byte + 16 colors) for a national team slot."""
        if not os.path.exists(self.output_path):
            return
        with open(self.output_path, "r+b") as f:
            self._write_nat_flag_impl(f, nat_index, team)
            _sync(f)

    # ------------------------------------------------------------------
    # 3D jersey patching (TEX files on CD)
    # ------------------------------------------------------------------

    def _ensure_tex_cache(self):
        """Cache original TEX file data on first use for 3D jersey patching.

        Uses targeted file seeks instead of loading the entire ROM into memory,
        so it works on devices with limited RAM (e.g. RG35xxSP with ~256MB).
        """
        if self._tex_cache is not None:
            return
        if not os.path.exists(self.output_path):
            self._tex_cache = {}
            return

        with open(self.output_path, "rb") as f:
            # Read only the directory sectors needed for TEX file lookup
            dir_data = bytearray()
            sectors = (_BIN_DIR_SIZE + 2047) // 2048
            for s in range(sectors):
                f.seek((_BIN_DIR_LBA + s) * 2352 + 24)
                dir_data.extend(f.read(2048))

            tex_sizes, tex_dir_offsets = _build_tex_dir_map_from_dir(dir_data)
            self._tex_sizes = tex_sizes
            self._tex_dir_offsets = tex_dir_offsets

            # Cache each TEX file by reading only its sectors
            tex_cache = {}
            for idx in range(_TEX_COUNT):
                size = tex_sizes.get(idx)
                if size is None:
                    continue
                lba = _TEX_BASE_LBA + idx * _TEX_LBA_STRIDE
                tex_cache[idx] = _read_cd_file_from_handle(f, lba, size)
            self._tex_cache = tex_cache

    def patch_3d_jersey(self, tex_index: int, target_rgb: tuple[int, int, int]):
        """Patch a team's 3D jersey by copying the best color-matched TEX file.

        Uses the static _TEX_JERSEY_COLORS map (from 2D maglia data) to find
        the source TEX whose known jersey color is closest to target_rgb.
        Copies the entire TEX file and updates the ISO9660 directory size.

        Args:
            tex_index: TEX file index (0-62 national, 63-94 ML).
            target_rgb: Desired jersey color as (R, G, B).
        """
        if not os.path.exists(self.output_path):
            return

        self._ensure_tex_cache()
        if not self._tex_cache:
            return

        if tex_index not in self._tex_sizes:
            return

        best_idx = _find_best_tex_match(target_rgb)
        with open("/tmp/tex_debug.log", "a") as _dbg:
            if best_idx is not None and best_idx != tex_index:
                src_color = _TEX_JERSEY_COLORS[best_idx]
                # strict=False for the same reason as in `_find_best_tex_match`:
                # a ragged pair must not turn a debug log line into a ValueError.
                dist = sum((a - b) ** 2 for a, b in zip(target_rgb, src_color, strict=False))
                _dbg.write(
                    f"[TEX] Patch TEX_{tex_index:02d}: want RGB{target_rgb} -> "
                    f"best TEX_{best_idx:02d} RGB{src_color} dist={dist}\n"
                )
            else:
                reason = "no match" if best_idx is None else "same slot"
                _dbg.write(
                    f"[TEX] Patch TEX_{tex_index:02d}: want RGB{target_rgb} -> SKIP ({reason})\n"
                )
        if best_idx is None or best_idx == tex_index:
            return

        with open(self.output_path, "r+b") as f:
            rom_data = f.read()

        rom = bytearray(rom_data)
        dst_lba = _TEX_BASE_LBA + tex_index * _TEX_LBA_STRIDE

        # Use cached original TEX data to avoid reading from already-modified slots
        src_data = self._tex_cache[best_idx]
        src_size = len(src_data)

        # Write source TEX data into target slot sectors (pad to fill sectors)
        sectors_needed = (src_size + 2047) // 2048
        padded = bytearray(src_data) + bytearray(sectors_needed * 2048 - src_size)
        _write_cd_file_with_edc(rom, dst_lba, bytes(padded))

        # Update ISO9660 directory entry so game reads correct file size
        if tex_index in self._tex_dir_offsets:
            _update_iso_dir_size(rom, self._tex_dir_offsets[tex_index], src_size)

        with open(self.output_path, "wb") as f:
            f.write(rom)
            _sync(f)

    def flush_tex_patches(self):
        """Apply all queued 3D jersey TEX patches using targeted file seeks.

        Instead of loading the entire ROM into memory, each TEX patch is
        written directly to the file at the correct sector offsets. This
        works on devices with limited RAM (e.g. RG35xxSP).
        """
        if not self._pending_tex_patches or not os.path.exists(self.output_path):
            return

        self._ensure_tex_cache()
        if not self._tex_cache:
            self._pending_tex_patches = []
            return

        with open(self.output_path, "r+b") as f:
            for tex_index, target_rgb in self._pending_tex_patches:
                if tex_index not in self._tex_sizes:
                    continue

                best_idx = _find_best_tex_match(target_rgb)
                if best_idx is None or best_idx == tex_index:
                    continue

                src_data = self._tex_cache[best_idx]
                src_size = len(src_data)
                dst_lba = _TEX_BASE_LBA + tex_index * _TEX_LBA_STRIDE

                # Write TEX data to target sectors with EDC recalculation
                sectors_needed = (src_size + 2047) // 2048
                padded = bytearray(src_data) + bytearray(sectors_needed * 2048 - src_size)
                _write_cd_file_with_edc_to_handle(f, dst_lba, bytes(padded))

                # Update ISO9660 directory entry size
                if tex_index in self._tex_dir_offsets:
                    _update_iso_dir_size_in_handle(f, self._tex_dir_offsets[tex_index], src_size)
            _sync(f)

        self._pending_tex_patches = []

    def verify_patches(self, original_path: str, slot_mapping, we_teams) -> str:
        """Compare original vs patched ROM AND read-back the output to confirm.

        Three-phase check:
          1. ROM format validation (Mode2/2352 sync bytes)
          2. Binary diff: original vs patched at every written offset
          3. Read-back: re-read what we wrote and confirm it matches intent

        The report is written to an error.log file next to the output ROM.

        Args:
            original_path: Path to the unmodified source ROM.
            slot_mapping: List of SlotMapping used during patching.
            we_teams: Dict mapping slot_index → WETeamRecord that was written.

        Returns:
            Multi-line human-readable verification report string.
        """
        if not os.path.exists(original_path) or not os.path.exists(self.output_path):
            return "ERROR: Cannot verify — original or patched file missing."

        lines = []
        lines.append("=" * 60)
        lines.append("WE2002 PATCH VERIFICATION REPORT")
        lines.append("=" * 60)

        orig_size = os.path.getsize(original_path)
        patch_size = os.path.getsize(self.output_path)
        lines.append(f"Original ROM: {original_path}")
        lines.append(f"  Size: {orig_size:,} bytes")
        lines.append(f"Patched ROM:  {self.output_path}")
        lines.append(f"  Size: {patch_size:,} bytes")
        if orig_size != patch_size:
            lines.append("  *** SIZE MISMATCH — copy may have failed ***")
        lines.append("")

        # --- Phase 1: ROM format check ---
        lines.append("--- PHASE 1: ROM FORMAT CHECK ---")
        with open(original_path, "rb") as f:
            sync = f.read(12)
            is_mode2 = sync == b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"
            lines.append(f"Sector 0 sync: {sync.hex()}")
            lines.append(f"Mode2/2352: {'YES' if is_mode2 else 'NO  *** WRONG FORMAT ***'}")
            if not is_mode2:
                lines.append("!!! ROM is NOT a raw Mode2/2352 BIN dump.")
                lines.append("!!! All offsets are calibrated for Mode2/2352.")
                lines.append("!!! This is likely why the patch had no effect.")

            f.seek(2352)
            sync2 = f.read(12)
            sector1_ok = sync2 == b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"
            lines.append(f"Sector 1 sync: {sync2.hex()} ({'OK' if sector1_ok else 'MISMATCH'})")

            # Probe known data offsets for sanity
            f.seek(_OFS_NOMI_SQ_AB1)
            sample = f.read(32)
            has_ascii = any(0x20 <= b <= 0x7E for b in sample)
            lines.append(f"Probe @AB1 ({_OFS_NOMI_SQ_AB1}): {sample[:16].hex()}")
            lines.append(
                f"  Printable ASCII present: {'YES' if has_ascii else 'NO (wrong offset?)'}"
            )

            f.seek(_OFS_NOMI_GML)
            sample2 = f.read(40)
            has_names = any(0x41 <= b <= 0x5A for b in sample2)  # uppercase A-Z
            lines.append(f"Probe @GML ({_OFS_NOMI_GML}): {sample2[:20].hex()}")
            lines.append(
                f"  Uppercase letters present: {'YES' if has_names else 'NO (wrong offset?)'}"
            )
        lines.append("")

        # --- Phase 2: Binary diff original vs patched ---
        lines.append("--- PHASE 2: ORIGINAL vs PATCHED DIFF ---")
        total_changed = 0
        total_checked = 0
        skip_diff = self._in_place or (
            os.path.abspath(original_path) == os.path.abspath(self.output_path)
        )

        if skip_diff:
            lines.append("  (In-place re-patch — diff vs original skipped)")
            total_changed = -1  # sentinel: diff was skipped
        else:
            with (
                open(original_path, "rb") as orig,
                open(self.output_path, "rb") as patched,
            ):
                for mapping in slot_mapping:
                    si = mapping.slot_index
                    team_name = (
                        mapping.real_team.name if hasattr(mapping, "real_team") else f"slot {si}"
                    )
                    we_team = we_teams.get(si)
                    lines.append(f"\n  Slot {si}: {team_name}")

                    checks = self._get_check_points(si)
                    for label, offset, size in checks:
                        orig.seek(offset)
                        orig_data = orig.read(size)
                        patched.seek(offset)
                        patch_data = patched.read(size)
                        changed = orig_data != patch_data
                        total_checked += 1
                        if changed:
                            total_changed += 1
                        orig_repr = self._format_bytes(orig_data)
                        patch_repr = self._format_bytes(patch_data)
                        lines.append(
                            f"    {label} @{offset}: {orig_repr} -> {patch_repr} "
                            f"{'CHANGED' if changed else 'SAME'}"
                        )

        lines.append("")
        if total_changed == -1:
            lines.append("  Diff skipped (re-patch mode)")
        else:
            lines.append(f"  Diff totals: {total_changed}/{total_checked} regions changed")
        lines.append("")

        # --- Phase 3: Read-back verification on output file ---
        lines.append("--- PHASE 3: OUTPUT READ-BACK CHECK ---")
        readback_ok = 0
        readback_fail = 0

        with open(self.output_path, "rb") as f:
            for mapping in slot_mapping:
                si = mapping.slot_index
                we_team = we_teams.get(si)
                if not we_team:
                    continue

                team_name = (
                    mapping.real_team.name if hasattr(mapping, "real_team") else f"slot {si}"
                )

                # Verify abbreviation matches what we intended to write
                code = we_team.short_name or we_team.name[:3]
                expected_ab = _encode_abbreviation(code)
                rom_i = 31 - si
                ab_off = _OFS_NOMI_SQ_AB1 + rom_i * 4
                f.seek(ab_off)
                actual_ab = f.read(4)
                if actual_ab == expected_ab:
                    readback_ok += 1
                else:
                    readback_fail += 1
                    lines.append(
                        f"  FAIL slot {si} AB1: expected {expected_ab!r} got {actual_ab!r}"
                    )

                # Verify name variant 1
                budget = _ml_name_budget(si, _LUN_NOMI1)
                expected_name = _encode_team_name(we_team.name, budget, uppercase=True)
                n1_off = _ml_name_offset(_OFS_NOMI_SQ1, si, _LUN_NOMI1)
                f.seek(n1_off)
                actual_name = f.read(budget)
                if actual_name == expected_name:
                    readback_ok += 1
                else:
                    readback_fail += 1
                    lines.append(
                        f"  FAIL slot {si} Name1: expected {expected_name!r} got {actual_name!r}"
                    )

                # Verify first player name
                if we_team.players:
                    expected_pn = _encode_player_name(we_team.players[0].last_name)
                    first_idx, _ = _slot_player_range(si)
                    pn_chunks = _nome_chunks(first_idx)
                    f.seek(pn_chunks[0][0])
                    actual_pn = f.read(pn_chunks[0][1])
                    # For straddle cases, only check the first chunk
                    if actual_pn == expected_pn[: len(actual_pn)]:
                        readback_ok += 1
                    else:
                        readback_fail += 1
                        lines.append(
                            f"  FAIL slot {si} Player0: "
                            f"expected {expected_pn[: len(actual_pn)]!r} got {actual_pn!r}"
                        )

        if readback_fail == 0:
            lines.append(f"  All {readback_ok} read-back checks PASSED")
        else:
            lines.append(f"  {readback_fail} FAILED, {readback_ok} passed")
        lines.append("")

        # --- Summary ---
        lines.append("--- SUMMARY ---")
        lines.append(f"ROM format: {'Mode2/2352 OK' if is_mode2 else 'WRONG FORMAT'}")
        if total_changed == -1:
            lines.append("Diff: skipped (in-place re-patch)")
        else:
            lines.append(f"Diff: {total_changed}/{total_checked} regions changed")
        lines.append(f"Read-back: {readback_ok} OK, {readback_fail} FAIL")
        if total_changed == 0:
            lines.append("")
            lines.append("*** NO DATA CHANGED AT ALL ***")
            lines.append("Possible causes:")
            lines.append("  1. ROM is not Mode2/2352 BIN (check format above)")
            lines.append("  2. Emulator loading original file, not patched copy")
            lines.append("  3. shutil.copy2 failed (check disk space)")
        lines.append("=" * 60)

        report = "\n".join(lines)

        # Write to error.log next to the output ROM
        log_path = os.path.join(os.path.dirname(self.output_path), "error.log")
        try:
            with open(log_path, "w") as f:
                f.write(report)
        except Exception:
            pass

        return report

    @staticmethod
    def _get_check_points(slot_index):
        """Return list of (label, offset, size) to check for a given slot."""
        rom_i = 31 - slot_index
        checks = []

        # Abbreviation AB1
        checks.append(("AB1", _OFS_NOMI_SQ_AB1 + rom_i * 4, 4))

        # Name variant 1
        n1_off = _ml_name_offset(_OFS_NOMI_SQ1, slot_index, _LUN_NOMI1)
        n1_budget = _ml_name_budget(slot_index, _LUN_NOMI1)
        checks.append(("Name1", n1_off, n1_budget))

        # First player name
        first_idx, _ = _slot_player_range(slot_index)
        pn_chunks = _nome_chunks(first_idx)
        checks.append(("Player0", pn_chunks[0][0], pn_chunks[0][1]))

        # First player characteristics
        pc_chunks = _carat_chunks(first_idx)
        checks.append(("Carat0", pc_chunks[0][0], pc_chunks[0][1]))

        # Force bar
        checks.append(("ForceBar", _ML_BAR_OFFSET + slot_index * 5, 5))

        # Flag style
        checks.append(("FlagStyle", _OFS_BANDIERE_FORMA1 + _SQUADRE_NAZ + slot_index, 1))

        return checks

    @staticmethod
    def _format_bytes(data: bytes) -> str:
        """Format bytes for display — show as ASCII if printable, else hex."""
        try:
            text = data.rstrip(b"\x00").decode("ascii")
            if text and all(0x20 <= ord(c) <= 0x7E for c in text):
                return repr(data)
        except (UnicodeDecodeError, ValueError):
            pass
        if len(data) <= 5:
            return str(list(data))
        return data.hex()[:24] + "..."

    def finalize(self):
        """Post-processing after all patches are written.

        EDC checksums for TEX file sectors are recalculated inline during
        3D jersey patching.  Non-TEX patches (names, maglia, flags) modify
        sectors that emulators don't validate EDC for.
        """
        pass
