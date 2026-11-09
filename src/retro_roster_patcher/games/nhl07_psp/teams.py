"""Clubs the disc knows under an old identity (`core.nhl_clubs`): the
Thrashers become the Winnipeg Jets, the Coyotes the Utah Mammoth, and the East
and West All-Star sides Seattle and Vegas.

What is NHL 07's own: its All-Star sides' string ids, and that `FE/LOCAL.VIV`
is rebuilt rather than rewritten in place (`strings.replace_strings` does the
same), so the caller writes it with `write_in_place` and updates `local.bh`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ...core.nhl_clubs import Rename, rename_entries
from ...formats.ea_strings import StringTableError, build_table, parse_table
from ...formats.ea_tdb import bigf_parse, bigf_replace, refpack_compress, refpack_decompress

#: The All-Star sides' names in `front<language>.bin`, by string id and the part
#: of the name each holds: the full name, the abbreviation, the city in two
#: lists and the nickname in one (frontenglish.bin entries 1767/1310, 1611/3962,
#: 1580/4585, 4346/1288 and 2238/4266). The ids are the same in English, French
#: and German; French splits "Étoiles de l'Est" into "Étoiles" (the second
#: city) and "de l'Est" (the nickname). "All-Stars" is every side's nickname,
#: and the North America and World sides keep theirs.
ALL_STAR_STRINGS: Mapping[int, Mapping[int, str]] = {
    30: {
        0x9930F4B1: "full",
        0xC49CFB94: "abbr",
        0xFA83B68D: "city",
        0x2EAF0E28: "city",
        0x2EC126A9: "nick",
    },
    31: {
        0x1D577047: "full",
        0xC49D48AA: "abbr",
        0xFA8403A3: "city",
        0x2EAF5B3E: "city",
        0x2EC173BF: "nick",
    },
}


def rename_strings(local_viv: bytes, renames: Iterable[Rename]) -> tuple[bytes, int]:
    """`local_viv` with every string table's old club names changed to the
    new, and how many entries changed. A changed table is recompressed and the
    archive rebuilt around it, so its directory may change."""
    renames = list(renames)
    out = local_viv
    changed = 0
    names = [e.name for e in bigf_parse(local_viv) if e.name.lower().endswith(".bin")]
    for name in names:
        # Looked up again each time: a rebuild moves the members after it.
        entry = next(e for e in bigf_parse(out) if e.name == name)
        try:
            entries = parse_table(refpack_decompress(out[entry.offset : entry.offset + entry.size]))
        except (StringTableError, ValueError):
            continue
        edited = rename_entries(entries, renames, ALL_STAR_STRINGS)
        if edited:
            out = bigf_replace(out, name, refpack_compress(build_table(entries), tight=True))
            changed += edited
    return out, changed
