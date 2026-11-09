"""Clubs an EA NHL disc knows under an old identity: the Thrashers are the
Winnipeg Jets now and the Coyotes the Utah Mammoth, and the East and West
All-Star sides become Seattle and Vegas.

A club's name lives in two places every EA NHL disc of the PS2/PSP era shares.
The master TDB's `STEA` row (full name and city; its abbreviation stays, since
the disc names the club's arena, logo and sound files by it, and a club with an
unknown one drops out of every list) and `SARE` row (arena, its city); and the
string tables of the front end's `LOCAL.VIV` (`front<language>.bin` and
`game<language>.bin`, the menus' and the game's text, where the lists of clubs
hold the full name, the city and the nickname as entries of their own, found
here by their text). The slot keeps its number, its audio and its art ids.

An All-Star side keeps the All-Star game's arena, and its strings are found by
id, not by text: every side's nickname is "All-Stars". The ids differ by game,
so each game passes its own table to `rename_strings`.

`rename_strings` rewrites the string tables in place, keeping the archive's
directory; one that compresses too large for its member is left as it was and
named in the result. A game that can rebuild the archive renames each parsed
table with `rename_entries` instead.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..formats.ea_strings import StringTableError, build_table, parse_table
from ..formats.ea_tdb import TDBFile, bigf_parse, refpack_compress, refpack_decompress

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


#: By slot: the clubs whose franchise moved since the disc.
RELOCATED: Mapping[int, Identity] = {
    1: Identity("Winnipeg Jets", "Winnipeg", "Jets", "WPG", "Canada Life Centre", "Winnipeg, MB"),
    22: Identity("Utah Mammoth", "Utah", "Mammoth", "UTA", "Delta Center", "Salt Lake City, UT"),
}


#: By slot: the All-Star sides Seattle and Vegas take. No arena: the sides play
#: at the All-Star game's.
ALL_STARS: Mapping[int, Identity] = {
    30: Identity("Seattle Kraken", "Seattle", "Kraken", "SEA", "", ""),
    31: Identity("Vegas Golden Knights", "Vegas", "Golden Knights", "VGK", "", ""),
}

#: A game's All-Star strings: `{slot: {string id: part}}`, the part naming an
#: `Identity` field (`full`, `abbr`, `city`, `nick`).
AllStarStrings = Mapping[int, Mapping[int, str]]


def plain(text: object) -> str:
    """A disc string without its trademark marks or padding."""
    return "".join(c for c in str(text) if c not in MARKS).strip()


def rename_tables(tdb: TDBFile, slots: Iterable[int]) -> list[Rename]:
    """Give each relocated club and All-Star side among `slots` its new
    identity in STEA (and a club's in SARE), returning what changed, with the
    old names the strings still use."""
    stea = tdb.get_table("STEA")
    if stea is None:
        return []
    renames = []
    identities = {**RELOCATED, **ALL_STARS}
    wanted = {slot: identities[slot] for slot in slots if slot in identities}
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
        by_slot = {r.slot: r.new for r in renames if r.new.arena}
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


def rename_entries(
    entries: list[tuple[int, str]], renames: Iterable[Rename], all_star_strings: AllStarStrings
) -> int:
    """Change, in one parsed string table, every entry that spells an old name
    (or holds an All-Star side's, by `all_star_strings`) to the new; how many."""
    renames = list(renames)
    wanted = _replacements(r for r in renames if r.slot in RELOCATED)
    by_id = {
        string: getattr(r.new, part)
        for r in renames
        for string, part in all_star_strings.get(r.slot, {}).items()
    }
    edited = 0
    for i, (h, text) in enumerate(entries):
        new = by_id.get(h) or wanted.get(plain(text))
        if new is not None and new != text:
            entries[i] = (h, new)
            edited += 1
    return edited


def rename_strings(
    archive: bytes, renames: Iterable[Rename], all_star_strings: AllStarStrings
) -> tuple[bytes, int, list[str]]:
    """`LOCAL.VIV` with every string table renamed (`rename_entries`) in
    place, its directory unchanged: (the archive, entries changed, tables that
    could not take their rewrite)."""
    renames = list(renames)
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
        edited = rename_entries(entries, renames, all_star_strings)
        if not edited:
            continue
        data = refpack_compress(build_table(entries), tight=True)
        if len(data) > entry.size:
            skipped.append(entry.name)
            continue
        out[entry.offset : entry.offset + entry.size] = data.ljust(entry.size, b"\0")
        changed += edited
    return bytes(out), changed, skipped
