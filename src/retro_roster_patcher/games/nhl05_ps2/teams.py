"""Clubs the disc knows under an old identity: the Thrashers are the Winnipeg
Jets now and the Coyotes the Utah Mammoth.

A club's name lives in three places. The master TDB's `STEA` row (full name
and city; its abbreviation stays, since the disc names the club's arena, logo
and sound files by it, and a club with an unknown one drops out of every
list) and `SARE` row (arena, its city); the string tables of
`FE/LOCAL.VIV` (`front<language>.bin` and `game<language>.bin`, the menus'
and the game's text in five languages, where the lists of clubs hold the full
name, the city and the nickname as entries of their own, found here by their
text); and the wordmark of `FE/TLOGOAPT.VIV` (`wordmarks`). The slot keeps
its number, its audio and its art ids.

The tables are rewritten in place; a string table that compresses too large
for its member is left as it was, with a status line.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ...formats.ea_tdb import TDBFile, bigf_parse, refpack_compress, refpack_decompress
from ..nhl07_psp.strings import StringTableError, build_table, parse_table

LOCAL_ARCHIVE = "FE/LOCAL.VIV"
#: Trademark marks the disc appends to names, dropped before comparing.
MARKS = "™®�"


@dataclass(frozen=True)
class Identity:
    full: str
    city: str
    nick: str
    abbr: str
    arena: str
    arena_city: str


@dataclass(frozen=True)
class Rename:
    """A slot's old names, as the disc spells them, and its new identity."""

    slot: int
    old: Identity
    new: Identity


#: By slot: the clubs whose franchise moved since 2004.
RELOCATED: Mapping[int, Identity] = {
    1: Identity("Winnipeg Jets", "Winnipeg", "Jets", "WPG", "Canada Life Centre", "Winnipeg, MB"),
    22: Identity("Utah Mammoth", "Utah", "Mammoth", "UTA", "Delta Center", "Salt Lake City, UT"),
}


def plain(text: object) -> str:
    """A disc string without its trademark marks or padding."""
    return "".join(c for c in str(text) if c not in MARKS).strip()


def rename_tables(tdb: TDBFile, slots: Iterable[int]) -> list[Rename]:
    """Give each relocated club among `slots` its new identity in STEA and
    SARE, returning what changed, with the old names the strings still use."""
    stea = tdb.get_table("STEA")
    if stea is None:
        return []
    renames = []
    wanted = {slot: RELOCATED[slot] for slot in slots if slot in RELOCATED}
    for i in range(min(stea.num_records, stea.capacity)):
        row = stea.read_record(i)
        slot = row.get("INDX")
        new = wanted.get(slot) if isinstance(slot, int) else None
        if new is None or not isinstance(slot, int):
            continue
        city = plain(row.get("SNME", ""))
        full = plain(row.get("FNME", ""))
        nick = full[len(city) :].strip() if full.startswith(city) else full
        old = Identity(full, city, nick, plain(row.get("ABBR", "")), "", "")
        # Not `ABBR`: the disc names its arena, logo and sound files by it.
        stea.write_record(i, {"FNME": new.full, "SNME": new.city})
        renames.append(Rename(slot, old, new))
    sare = tdb.get_table("SARE")
    if sare is not None:
        by_slot = {r.slot: r.new for r in renames}
        for i in range(min(sare.num_records, sare.capacity)):
            index = sare.read_record(i).get("INDX")
            new = by_slot.get(index) if isinstance(index, int) else None
            if new is not None:
                sare.write_record(i, {"NAME": new.arena, "CITY": new.arena_city})
    return renames


def _replacements(renames: Iterable[Rename]) -> dict[str, str]:
    out = {}
    for r in renames:
        for old, new in (
            (r.old.full, r.new.full),
            (r.old.nick, r.new.nick),
            (r.old.city, r.new.city),
        ):
            if old:
                out[old] = new
        if len(r.old.abbr) >= 2:
            out[r.old.abbr] = r.new.abbr
    return out


def rename_strings(archive: bytes, renames: Iterable[Rename]) -> tuple[bytes, int, list[str]]:
    """`FE/LOCAL.VIV` with every string table's entries that spell an old name
    changed to the new, in place: (the archive, entries changed, tables that
    could not take their rewrite)."""
    wanted = _replacements(renames)
    out = bytearray(archive)
    changed = 0
    skipped = []
    for entry in bigf_parse(archive):
        if not entry.name.lower().endswith(".bin"):
            continue
        member = archive[entry.offset : entry.offset + entry.size]
        try:
            entries = parse_table(refpack_decompress(member))
        except (StringTableError, ValueError):
            continue
        edited = 0
        for i, (h, text) in enumerate(entries):
            new = wanted.get(plain(text))
            if new is not None and new != text:
                entries[i] = (h, new)
                edited += 1
        if not edited:
            continue
        data = refpack_compress(build_table(entries), tight=True)
        if len(data) > entry.size:
            skipped.append(entry.name)
            continue
        out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        changed += edited
    return bytes(out), changed, skipped
