"""Fabricate a small PES 2013 (PS2) disc image in memory.

    ISO 9660 -> /SLUS_219.55, /0_TEXT.AFS, /OVER.AFS
    0_TEXT.AFS entries 46 and 47 -> block -> part table -> players, squads
    OVER.AFS entries 5..19       -> "MWo3" overlays with a team-name table

Nothing here comes from a real disc. Counts are tiny: `PLAYER_COUNT` players,
one row per club, two language overlays with a real table and the rest empty.
The layouts (record sizes, table shape, overlay header) are the ones
`games/pes2013_ps2/models.py` documents, restated here independently so a test
that writes through the module and reads back through this file means
something.
"""

from __future__ import annotations

import struct
import zlib

from retro_roster_patcher.games.pes2013_ps2.models import (
    CLUB_COUNT,
    CLUB_SQUAD_SIZE,
    DATABASE_OVERLAY_ENGLISH,
    FIRST_CLUB_ENTRY,
    NATIONAL_COUNT,
    NATIONAL_SQUAD_SIZE,
)
from tests.fixtures import synthetic_iso as iso

SECTOR = iso.SECTOR_SIZE
PLAYER_COUNT = 40
OVERLAY_BASE = 0x65E000
#: Clubs the fixture names; the rest of the 139 rows are empty.
CLUB_NAMES = ["NORTH LONDON", "WEST MIDLANDS VILLAGE", "FC BARCELONA"]
CLUB_CODES = ["NLN", "WMV", "FCB"]
#: Position nibble per squad slot: a keeper, three defenders, four midfielders,
#: three attackers, then nothing.
SLOT_POSITIONS = [0, 3, 3, 4, 5, 7, 7, 8, 11, 12, 12]
NATIONAL_ROW_0 = [1, 2, 3, 34, 35, 36]


def player_record(display: str, shirt: str, position: int) -> bytes:
    name = display.encode("utf-16-le")[:30].ljust(32, b"\0")
    stats = bytearray(b"\x11" * 76)
    stats[53 - 48] = (position << 4) | 1
    return name + shirt.encode("ascii")[:15].ljust(16, b"\0") + bytes(stats)


def block(payload: bytes, *, compress: bool) -> bytes:
    body = zlib.compress(payload) if compress else payload
    return (
        struct.pack("<BBBBII", 0, 6, 1 if compress else 0, 0, len(body), len(payload))
        + b"\0" * 20
        + body
    )


def table(parts: list[bytes]) -> bytes:
    n = len(parts) + 1
    head_len = 4 + 4 * n
    pos = (head_len // 16 + 1) * 16
    offsets = [8]
    body = b""
    for part in parts:
        offsets.append(pos + len(body))
        body += part + b"\0" * (-len(part) % 16)
    head = struct.pack(f"<I{n}I", n, *offsets)
    return head + b"\0" * (pos - len(head)) + body


def build_player_entry(names: list[tuple[str, str]] | None = None) -> bytes:
    """Entry 46/47: players, national squads, club squads, order, numbers, extra."""
    names = names or [(f"PLAYER {i}", f"P{i}") for i in range(PLAYER_COUNT)]
    players = b""
    for i, (display, shirt) in enumerate(names):
        pos = SLOT_POSITIONS[(i - 1) % len(SLOT_POSITIONS)] if i else 0
        players += player_record(display, shirt, pos)
    # Clubs 0..2 get players 1..11, 12..22, 23..33 in their first eleven slots.
    squads = bytearray(CLUB_COUNT * CLUB_SQUAD_SIZE * 2)
    numbers = bytearray(CLUB_COUNT * CLUB_SQUAD_SIZE)
    for club in range(len(CLUB_NAMES)):
        for slot in range(len(SLOT_POSITIONS)):
            idx = 1 + club * len(SLOT_POSITIONS) + slot
            struct.pack_into("<H", squads, (club * CLUB_SQUAD_SIZE + slot) * 2, idx)
            numbers[club * CLUB_SQUAD_SIZE + slot] = slot
    # National row 0 ("NATION 0") shares players 1..3 with club 0 and owns
    # players 34..36 (keeper, defender, attacker) that no club points at;
    # players 37..39 belong to nobody.
    nationals = bytearray(NATIONAL_COUNT * NATIONAL_SQUAD_SIZE * 2)
    for slot, idx in enumerate(NATIONAL_ROW_0):
        struct.pack_into("<H", nationals, slot * 2, idx)
    order = bytes(NATIONAL_COUNT * NATIONAL_SQUAD_SIZE)
    parts = [
        block(players, compress=True),
        block(bytes(nationals), compress=True),
        block(bytes(squads), compress=True),
        block(order, compress=True),
        block(bytes(numbers), compress=True),
        block(b"\0" * 64, compress=True),
    ]
    # The disc's entries end in a few KB of zeros, which is the room a rebuilt
    # entry has to fit in: its size is never changed.
    return block(table(parts), compress=False) + b"\0" * 4096


def build_overlay(
    lang: str, names: list[str] | None = None, codes: list[str] | None = None
) -> bytes:
    """A `database_<lang>.ovl`: header, strings, then the team table."""
    names = names or CLUB_NAMES
    codes = codes or CLUB_CODES
    strings = bytearray()
    pointers: list[tuple[int, int]] = []
    rows: list[tuple[str, str]] = [(f"NATION {i}", f"N{i:02d}") for i in range(FIRST_CLUB_ENTRY)]
    rows += list(zip(names, codes, strict=True))
    for name, code in rows:
        name_off = 0x100 + len(strings)
        strings += name.encode("utf-8").ljust(16, b"\0")
        code_off = 0x100 + len(strings)
        strings += code.encode("ascii").ljust(8, b"\0")
        pointers.append((name_off, code_off))
    table_off = 0x100 + len(strings)
    table_off += -table_off % 16
    entries = b""
    for k, (name_off, code_off) in enumerate(pointers):
        ident = 0x16020000 | (k - FIRST_CLUB_ENTRY) if k >= FIRST_CLUB_ENTRY else 0x100000CD
        entries += struct.pack(
            "<IIII", OVERLAY_BASE + name_off, OVERLAY_BASE + code_off, 0x900006, ident
        )
    # Pad the table to the 40 entries the reader wants to see in a run.
    while len(entries) < 40 * 16:
        name_off, code_off = pointers[-1]
        entries += struct.pack(
            "<IIII", OVERLAY_BASE + name_off, OVERLAY_BASE + code_off, 0, 0x16020000 | 0xFF
        )
    total = table_off + len(entries) + 16
    data = bytearray(total)
    struct.pack_into("<4sIII", data, 0, b"MWo3", 1, OVERLAY_BASE, total)
    data[32:48] = f"database_{lang}.ovl".encode().ljust(16, b"\0")
    data[0x100 : 0x100 + len(strings)] = strings
    data[table_off : table_off + len(entries)] = entries
    return bytes(data)


def build_afs(entries: dict[int, bytes], count: int) -> bytes:
    """An AFS with `count` entries; unlisted ones are two bytes of nothing."""
    head_len = 8 + 8 * count + 8
    pos = head_len + (-head_len % 2048)
    body = b""
    table_bytes = b""
    for i in range(count):
        data = entries.get(i, b"\0\0")
        table_bytes += struct.pack("<II", pos + len(body), len(data))
        body += data + b"\0" * (-len(data) % 2048)
    return (
        b"AFS\0"
        + struct.pack("<I", count)
        + table_bytes
        + b"\0" * 8
        + b"\0" * (pos - head_len)
        + body
    )


def build_iso(
    *,
    player_entry: bytes | None = None,
    overlays: dict[int, bytes] | None = None,
    boot_name: str = "SLUS_219.55",
) -> bytes:
    player_entry = player_entry or build_player_entry()
    if overlays is None:
        overlays = {DATABASE_OVERLAY_ENGLISH: build_overlay("u"), 5: build_overlay("b")}
    entries = {46: player_entry, 47: player_entry}
    for index, side in ((600, 32), (602, 32), (604, 64), (606, 64)):
        entries[index] = block(build_atlas(side), compress=False) + b"\0" * 1024
    text_afs = build_afs(entries, 610)
    over_afs = build_afs(overlays, 20)
    boot = b"\x7fELF" + b"\0" * 2044
    languages = {"U_TEXT.AFS": build_text_afs("u"), "B_TEXT.AFS": build_text_afs("b")}

    root_sector = 17
    boot_sector = 18
    sectors: dict[int, bytes] = {boot_sector: boot}
    records = [iso.dir_record(f"{boot_name};1".encode(), boot_sector, len(boot), is_dir=False)]
    nxt = boot_sector + len(boot) // SECTOR
    for name, data in [("0_TEXT.AFS", text_afs), ("OVER.AFS", over_afs), *languages.items()]:
        sectors[nxt] = data
        records.append(iso.dir_record(f"{name};1".encode(), nxt, len(data), is_dir=False))
        nxt += -(-len(data) // SECTOR)
    total = nxt + 1
    sectors[iso.PVD_SECTOR] = iso.pvd(total, root_sector, SECTOR, volume_id=b"PES2013_FIXTURE")
    sectors[root_sector] = iso.directory_extent(root_sector, root_sector, records)
    return iso.build_image(sectors, total)


def write_iso(path, **kwargs) -> int:  # type: ignore[no-untyped-def]
    image = build_iso(**kwargs)
    with open(path, "wb") as f:
        f.write(image)
        # Over the reader's size floor without real bytes.
        f.truncate(max(len(image), 5 * 1024 * 1024))
    return len(image)


# -- emblem atlases and texture bundles ------------------------------------


def build_atlas(side: int, clubs: int = 4) -> bytes:
    """An emblem atlas: an empty flag table, `clubs` crests, no extras, one
    palette for the club table. Crest k is filled with index k + 1."""
    import zlib

    header = (
        struct.pack("<I", side * side)
        + bytes([0, 0, side, side])
        + struct.pack("<II", 0x00010001, 0)
    )
    tables = [[], [(k, zlib.compress(bytes([k + 1]) * (side * side), 9)) for k in range(clubs)], []]
    table_off = 0x28
    offsets = []
    for recs in tables:
        offsets.append((len(recs), table_off if recs else 0))
        table_off += 16 * len(recs)
    data = bytearray(header + struct.pack("<6I", *(v for pair in offsets for v in pair)))
    assert len(data) == 0x28
    body = bytearray()
    stream_offs = []
    for recs in tables:
        offs = []
        for _ident, stream in recs:
            offs.append(table_off + len(body))
            body += stream + b"\0" * (-len(stream) % 4)
        stream_offs.append(offs)
    palette = bytearray(1024)
    for i in range(1, 256):
        palette[4 * i : 4 * i + 4] = bytes((i, 255 - i, 128, 0x80))
    pal_off = table_off + len(body)
    body += palette
    for t, recs in enumerate(tables):
        for k, (ident, stream) in enumerate(recs):
            flags = 0x04000000 if k == 0 else 0
            data += struct.pack(
                "<IIII",
                ident,
                flags | len(stream),
                stream_offs[t][k],
                pal_off if (k == 0 and t == 1) else 0,
            )
    return bytes(data) + bytes(body)


def build_texture(w: int, h: int, fill: int = 3) -> bytes:
    """A PSMT8 texture: 128-byte header, palette, pixels all `fill` (swizzle-free
    because every pixel is the same)."""
    header = bytearray(128)
    struct.pack_into("<I", header, 0, 0x29857294)
    struct.pack_into("<HH", header, 0x14, w, h)
    header[0x19] = 0x13
    palette = bytearray(1024)
    for i in range(256):
        palette[4 * i : 4 * i + 4] = bytes((i, i, i, 0x80 if i else 0))
    return bytes(header) + bytes(palette) + bytes([fill]) * (w * h)


def build_bundle(textures: dict[str, bytes]) -> bytes:
    """A named texture bundle, each texture one zlib block."""
    import zlib

    names = list(textures)
    count = len(names)
    table = 16 + 4 * count
    names_off = table + 4 * count + 4
    head = bytearray(struct.pack("<IIII", count, 0x10, table, 0))
    head += struct.pack(f"<{count}I", *(names_off + 16 * k for k in range(count)))
    head += b"\0" * (4 * count + 4)  # the block offset table, filled below
    for n in names:
        head += n.encode("ascii").ljust(16, b"\0")
    head += b"\0" * (-len(head) % 16)
    body = bytearray()
    for k, n in enumerate(names):
        struct.pack_into("<I", head, table + 4 * k, len(head) + len(body))
        comp = zlib.compress(textures[n], 9)
        body += struct.pack("<BBBBII", 0, 3, 1, 0, len(comp), len(textures[n])) + b"\0" * 20 + comp
        body += b"\0" * (-len(body) % 16)
    return bytes(head) + bytes(body)


def build_league_table(names: list[str]) -> bytes:
    """A NUL-separated string table holding the league names in game order."""
    return b"\0".join(n.encode("utf-8") for n in names) + b"\0"


def build_text_afs(lang: str) -> bytes:
    """A `*_TEXT.AFS`: entry 15 and 17 with the league names, entry 337 with the
    team-select icon and the screen art textures."""
    leagues = [
        "League A",
        "English League",
        "Ligue 1",
        "German League",
        "Italian League",
        "Eredivisie",
        "LIGA BBVA",
    ]
    if lang == "b":
        leagues = [
            "Liga A",
            "Liga inglesa",
            "Ligue 1",
            "Liga alemã",
            "Liga italiana",
            "Eredivisie",
            "LIGA BBVA",
        ]
    names_part = block(build_league_table(leagues), compress=True)
    entry15 = block(table([names_part, block(b"\0" * 64, compress=True)]), compress=True)
    bundle = build_bundle(
        {
            "mn_ts_eng_l": build_texture(32, 32),
            "title_bgNS": build_texture(64, 64, fill=5),
            "mo_sub_bgUS": build_texture(64, 64, fill=6),
            "loading_bg00": build_texture(32, 32),
            "ts_match_bg": build_texture(32, 32),
        }
    )
    entry337 = block(bundle, compress=False)
    return build_afs(
        {15: entry15 + b"\0" * 512, 17: entry15 + b"\0" * 512, 337: entry337 + b"\0" * 2048}, 340
    )
