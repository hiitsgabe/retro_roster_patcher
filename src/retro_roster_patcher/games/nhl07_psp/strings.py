"""The front end's text: the string tables in `FE/LOCAL.VIV`.

The menus name every string as `$ID`, and the game finds it by `ea_hash(ID)`
in `front<language>.bin` (English, French, German), each a RefPack-compressed
member of `FE/LOCAL.VIV` in the format `formats.ea_strings` reads. Checked on
the retail disc: all three tables rebuild byte for byte, and `$EA_COPYRIGHT`
(the title screen's "(c) 2006 Electronic Arts Inc." lines) is found the way
the movies name it.
"""

from __future__ import annotations

from collections.abc import Mapping

from ...formats.ea_strings import StringTableError, build_table, ea_hash, parse_table
from ...formats.ea_tdb import bigf_parse, bigf_replace, refpack_compress, refpack_decompress

__all__ = [
    "FMISC_VIV_PATH",
    "FRONT_TABLES",
    "LOCAL_BH_NAME",
    "LOCAL_VIV_PATH",
    "StringTableError",
    "build_table",
    "ea_hash",
    "lookup",
    "parse_table",
    "replace_strings",
]

LOCAL_VIV_PATH = "PSP_GAME/USRDIR/FE/LOCAL.VIV"
FMISC_VIV_PATH = "PSP_GAME/USRDIR/PRELOAD/FMISC.VIV"
LOCAL_BH_NAME = "local.bh"
FRONT_TABLES = {"en": "frontenglish.bin", "fr": "frontfrench.bin", "de": "frontgerman.bin"}


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
