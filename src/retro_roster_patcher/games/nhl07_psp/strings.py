"""The front end's text: the string tables in `FE/LOCAL.VIV`.

The menus name every string as `$ID`, and the game finds it by `ea_hash(ID)`
in `front<language>.bin` (English, French, German), each a RefPack-compressed
member of `FE/LOCAL.VIV`. A table, all little-endian:

    +0       0xFEFEFEFE, n, 0, 0
    +16      n u32 id hashes, in no particular order (it differs by language)
    +16+4n   n u32 absolute string offsets, ascending
    strings  UTF-8, each followed by four NULs, back to back

Lines inside a string break with CRLF. Checked on the retail disc: all three
tables rebuild byte for byte, and `$EA_COPYRIGHT` (the title screen's
"(c) 2006 Electronic Arts Inc." lines) is found the way the movies name it.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping

from ...core.errors import RomError
from ...formats.ea_tdb import bigf_parse, bigf_replace, refpack_compress, refpack_decompress

LOCAL_VIV_PATH = "PSP_GAME/USRDIR/FE/LOCAL.VIV"
FMISC_VIV_PATH = "PSP_GAME/USRDIR/PRELOAD/FMISC.VIV"
LOCAL_BH_NAME = "local.bh"
FRONT_TABLES = {"en": "frontenglish.bin", "fr": "frontfrench.bin", "de": "frontgerman.bin"}

_MAGIC = 0xFEFEFEFE


class StringTableError(RomError):
    """The bytes are not a string table this module can read."""


def ea_hash(name: str) -> int:
    """The id hash the game looks strings up by: h = h * 33 + byte, from 0xFFFFFFFF."""
    h = 0xFFFFFFFF
    for byte in name.encode("ascii"):
        h = (h * 33 + byte) & 0xFFFFFFFF
    return h


def parse_table(data: bytes) -> list[tuple[int, str]]:
    """(hash, text) per entry, in file order.

    Raises:
        StringTableError: not a table, or laid out other than `build_table` writes.
    """
    if len(data) < 16:
        raise StringTableError("String table too short")
    magic, n, zero1, zero2 = struct.unpack_from("<4I", data, 0)
    if magic != _MAGIC or zero1 or zero2 or 16 + 8 * n > len(data):
        raise StringTableError("Not a string table")
    hashes = struct.unpack_from(f"<{n}I", data, 16)
    offsets = struct.unpack_from(f"<{n}I", data, 16 + 4 * n)
    entries, pos = [], 16 + 8 * n
    for h, offset in zip(hashes, offsets, strict=True):
        end = data.find(b"\0", offset)
        if offset != pos or end < 0 or data[end : end + 4] != b"\0\0\0\0":
            raise StringTableError(f"Unexpected string layout at {offset:#x}")
        try:
            entries.append((h, data[offset:end].decode("utf-8")))
        except UnicodeDecodeError as e:
            raise StringTableError(f"String at {offset:#x} is not UTF-8") from e
        pos = end + 4
    if pos != len(data):
        raise StringTableError("Trailing bytes after the last string")
    return entries


def build_table(entries: list[tuple[int, str]]) -> bytes:
    """The table bytes; `build_table(parse_table(x)) == x`."""
    n = len(entries)
    blobs = [text.encode("utf-8") + b"\0\0\0\0" for _, text in entries]
    offsets, pos = [], 16 + 8 * n
    for blob in blobs:
        offsets.append(pos)
        pos += len(blob)
    head = struct.pack(f"<4I{n}I{n}I", _MAGIC, n, 0, 0, *(h for h, _ in entries), *offsets)
    return head + b"".join(blobs)


def lookup(local_viv: bytes, string_id: str, language: str = "en") -> str | None:
    """One string's text, or None when the table or the id is not there."""
    member = next(
        (e for e in bigf_parse(local_viv) if e.name.lower() == FRONT_TABLES[language]), None
    )
    if member is None:
        return None
    table = refpack_decompress(local_viv[member.offset : member.offset + member.size])
    wanted = ea_hash(string_id.lstrip("$"))
    return next((text for h, text in parse_table(table) if h == wanted), None)


def replace_strings(
    local_viv: bytes, texts: Mapping[str, str | Mapping[str, str]]
) -> tuple[bytes, int]:
    """`local_viv` with the given strings replaced, and how many entries changed.

    `texts` maps an id (with or without its `$`) to one text for every
    language or to `{language: text}`. A changed table never fits its old
    slot (this RefPack is about 1% larger than EA's), so the archive is
    rebuilt and its directory changes: the caller must update `local.bh`.
    Ids a table does not hold are ignored.
    """
    wanted = {ea_hash(sid.lstrip("$")): value for sid, value in texts.items()}
    out = local_viv
    changed = 0
    for language, name in FRONT_TABLES.items():
        member = next((e for e in bigf_parse(out) if e.name.lower() == name), None)
        if member is None:
            continue
        entries = parse_table(refpack_decompress(out[member.offset : member.offset + member.size]))
        edited = False
        for i, (h, text) in enumerate(entries):
            value = wanted.get(h)
            if value is None:
                continue
            new = value if isinstance(value, str) else value.get(language)
            if new is not None and new != text:
                entries[i] = (h, new)
                edited = True
                changed += 1
        if edited:
            out = bigf_replace(out, member.name, refpack_compress(build_table(entries)))
    return out, changed
