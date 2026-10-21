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
    nationals = bytes(NATIONAL_COUNT * NATIONAL_SQUAD_SIZE * 2)
    order = bytes(NATIONAL_COUNT * NATIONAL_SQUAD_SIZE)
    parts = [
        block(players, compress=True),
        block(nationals, compress=True),
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
    text_afs = build_afs({46: player_entry, 47: player_entry}, 48)
    over_afs = build_afs(overlays, 20)
    boot = b"\x7fELF" + b"\0" * 2044

    root_sector = 17
    boot_sector = 18
    text_sector = boot_sector + len(boot) // SECTOR
    over_sector = text_sector + -(-len(text_afs) // SECTOR)
    total = over_sector + -(-len(over_afs) // SECTOR) + 1
    records = [
        iso.dir_record(f"{boot_name};1".encode(), boot_sector, len(boot), is_dir=False),
        iso.dir_record(b"0_TEXT.AFS;1", text_sector, len(text_afs), is_dir=False),
        iso.dir_record(b"OVER.AFS;1", over_sector, len(over_afs), is_dir=False),
    ]
    image = iso.build_image(
        {
            iso.PVD_SECTOR: iso.pvd(total, root_sector, SECTOR, volume_id=b"PES2013_FIXTURE"),
            root_sector: iso.directory_extent(root_sector, root_sector, records),
            boot_sector: boot,
            text_sector: text_afs,
            over_sector: over_afs,
        },
        total,
    )
    return image


def write_iso(path, **kwargs) -> int:  # type: ignore[no-untyped-def]
    image = build_iso(**kwargs)
    with open(path, "wb") as f:
        f.write(image)
        # Over the reader's size floor without real bytes.
        f.truncate(max(len(image), 5 * 1024 * 1024))
    return len(image)
