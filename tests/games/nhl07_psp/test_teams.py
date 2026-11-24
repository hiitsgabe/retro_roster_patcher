"""`nhl07_psp.teams` and `logos`: the relocated clubs and the All-Star sides
get their new names and crests.

On the retail disc (Nov 9 2026) the clubs' names sit in frontenglish.bin as
"Atlanta Thrashers®", "Thrashers®", "Atlanta" and "ATL", and the All-Star
sides' under the ids in `teams.ALL_STAR_STRINGS`.
"""

from __future__ import annotations

import io
import random

import pytest

from retro_roster_patcher.core.nhl_clubs import ALL_STARS, RELOCATED, Identity, Rename
from retro_roster_patcher.formats.ea_strings import build_table, parse_table
from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    bigf_build,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl07_psp import logos, screens, teams
from tests.fixtures import synthetic_shpm as synthetic

PIL = pytest.importorskip("PIL.Image")

RENAMES = [
    Rename(1, Identity("Atlanta Thrashers", "Atlanta", "Thrashers", "ATL", "", ""), RELOCATED[1]),
    Rename(30, Identity("Eastern All-Stars", "Eastern", "All-Stars", "EAS", "", ""), ALL_STARS[30]),
]


def _archive(members: dict[str, bytes]) -> bytes:
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


def _table(archive: bytes, name: str) -> dict[int, str]:
    entry = next(e for e in bigf_parse(archive) if e.name == name)
    return dict(parse_table(refpack_decompress(archive[entry.offset : entry.offset + entry.size])))


def test_club_and_all_star_strings_take_the_new_names():
    entries = [
        (1, "Atlanta Thrashers®"),
        (2, "Thrashers®"),
        (3, "Atlanta"),
        (4, "ATL"),
        (0x9930F4B1, "Eastern All-Stars"),
        (0x2EC126A9, "All-Stars"),
        (0x2EC173BF, "All-Stars"),  # the West's: Vegas is not renamed here
        (5, "Eastern Conference"),
    ]
    table = refpack_compress(build_table(entries))
    old = _archive({"frontenglish.bin": table, "LOCALE_ENGLISH.loc": b"loc"})
    new, changed = teams.rename_strings(old, RENAMES)
    assert changed == 6
    assert _table(new, "frontenglish.bin") == {
        1: "Winnipeg Jets",
        2: "Jets",
        3: "Winnipeg",
        4: "WPG",
        0x9930F4B1: "Seattle Kraken",
        0x2EC126A9: "Kraken",
        0x2EC173BF: "All-Stars",
        5: "Eastern Conference",
    }
    loc = next(e for e in bigf_parse(new) if e.name == "LOCALE_ENGLISH.loc")
    assert new[loc.offset : loc.offset + loc.size] == b"loc"


def test_an_archive_with_no_old_name_is_returned_unchanged():
    old = _archive({"frontenglish.bin": refpack_compress(build_table([(1, "Boston")]))})
    assert teams.rename_strings(old, RENAMES) == (old, 0)


def _logo_big(name: str, size: int) -> bytes:
    """A compressed `LOGO_<size>_<abbr>.big`: a movie and the `.msh`, noise in
    its one shape so the recompressed crest has room."""
    rng = random.Random(name)
    msh = synthetic.shpm_file(
        synthetic.eight_bit_shape(size, size, bytes(rng.randrange(256) for _ in range(size**2))),
        names=("1",),
    )
    inner = {f"{name}.apt": b"Apt Data:1.0" + bytes(20), f"{name}.msh": msh}
    return refpack_compress(_archive(inner))


def _crest() -> bytes:
    out = io.BytesIO()
    PIL.new("RGBA", (200, 100), (200, 20, 20, 255)).save(out, "PNG")
    return out.getvalue()


def test_a_crest_is_drawn_into_both_sizes_in_place():
    members = {
        f"LOGO_{s}_ATL.big": _logo_big(f"LOGO_{s}_ATL", n) for s, n in (("LRG", 64), ("MED", 32))
    }
    members["LOGO_LRG_BOS.big"] = _logo_big("LOGO_LRG_BOS", 64)
    old = _archive(members)
    new, drawn = logos.edits(old, {1: _crest()})
    assert drawn == {1: 2}
    assert [(e.name, e.offset, e.size) for e in bigf_parse(new)] == [
        (e.name, e.offset, e.size) for e in bigf_parse(old)
    ]
    for size, side in (("LRG", 64), ("MED", 32)):
        name = f"LOGO_{size}_ATL"
        entry = next(e for e in bigf_parse(bytes(new)) if e.name == f"{name}.big")
        copy = screens.Copy(logos.ARCHIVE, entry.name, f"{name}.msh", "1")
        picture = screens.read_picture(bytes(new[entry.offset : entry.offset + entry.size]), copy)
        assert picture is not None and picture.size == (side, side)
        assert picture.getpixel((side // 2, side // 2))[:3] == pytest.approx((200, 20, 20), abs=12)
        assert picture.getpixel((0, 0))[3] == 0
    bos = next(e for e in bigf_parse(old) if e.name == "LOGO_LRG_BOS.big")
    assert new[bos.offset : bos.offset + bos.size] == old[bos.offset : bos.offset + bos.size]


def test_the_slot_files_follow_steas_order():
    # STEA 24 is St. Louis and 25 San Jose on this disc, the other way round
    # from NHL 2005's.
    assert (logos.SLOT_FILES[1], logos.SLOT_FILES[22]) == ("ATL", "PHO")
    assert logos.SLOT_FILES[24:26] == ("STL", "SJ")
    assert logos.SLOT_FILES[30:] == ("EAS", "WES")


def test_seattle_and_vegas_play_in_copies_of_two_national_sides_arenas():
    # The game draws an arena by its art id (AAID) and crashes on one no arena
    # uses, so each side's arena copies a national side's, art id included.
    from retro_roster_patcher.formats.ea_tdb import TDBFile
    from tests.fixtures.synthetic_tdb import (
        TYPE_STRING,
        TYPE_UINT,
        FieldSpec,
        TableSpec,
        build_tdb,
        pack_bits,
    )

    def table(name, fields, size, rows):
        data = b"".join(pack_bits(fields, row, size) for row in rows)
        return TableSpec(name, fields, size, len(rows), len(rows), data)

    stea = [
        FieldSpec("INDX", TYPE_UINT, 0, 8),
        FieldSpec("FNME", TYPE_STRING, 8, 248),
        FieldSpec("SNME", TYPE_STRING, 256, 256),
        FieldSpec("ABBR", TYPE_STRING, 512, 40),
    ]
    sare = [
        FieldSpec("INDX", TYPE_UINT, 0, 8),
        FieldSpec("NAME", TYPE_STRING, 8, 280),
        FieldSpec("CITY", TYPE_STRING, 288, 256),
        FieldSpec("LOGO", TYPE_UINT, 544, 8),
        FieldSpec("AAID", TYPE_UINT, 552, 8),
    ]
    aren = [FieldSpec("INDX", TYPE_UINT, 0, 8), FieldSpec("ID__", TYPE_UINT, 8, 8)]
    burnaby = {"NAME": "EA SPORTS Arena", "CITY": "Burnaby, BC", "AAID": 0}
    arenas = [
        {
            "INDX": 30,
            "NAME": "American Airlines Center",
            "CITY": "Dallas, TX",
            "LOGO": 9,
            "AAID": 9,
        },
        {"INDX": 41, "NAME": "EA SPORTS Arena", "CITY": "Japan", "LOGO": 43, "AAID": 43},
        {"INDX": 50, "NAME": "EA SPORTS Arena", "CITY": "Ukraine", "LOGO": 52, "AAID": 52},
        {"INDX": 107, **burnaby, "LOGO": 54},
        {"INDX": 108, **burnaby, "LOGO": 55},
    ]
    sides = [
        {"INDX": 30, "FNME": "Eastern All-Stars", "SNME": "Eastern", "ABBR": "EAS"},
        {"INDX": 31, "FNME": "Western All-Stars", "SNME": "Western", "ABBR": "WES"},
    ]
    tdb = TDBFile.parse(
        build_tdb(
            [
                table("STEA", stea, 69, sides),
                table("SARE", sare, 71, arenas),
                table("AREN", aren, 2, [{"INDX": s, "ID__": 30} for s in (30, 31, 32)]),
            ]
        )
    )
    teams.rename_tables(tdb, [30, 31])
    rows = [tdb.get_table("SARE").read_record(i) for i in range(5)]
    assert rows[:3] == arenas[:3]
    assert rows[3] == {"INDX": 107, "NAME": "T-Mobile Arena", "CITY": "Las Vegas, NV"} | {
        "LOGO": 52,
        "AAID": 52,
    }
    assert rows[4] == {"INDX": 108, "NAME": "Climate Pledge Arena", "CITY": "Seattle, WA"} | {
        "LOGO": 43,
        "AAID": 43,
    }
    assert [tdb.get_table("AREN").read_record(i)["ID__"] for i in range(3)] == [108, 107, 30]


def test_utahs_abbreviation_replaces_the_front_ends_and_the_scoreboard_finds_it():
    from retro_roster_patcher.formats.ea_strings import ea_hash

    front = ea_hash("TEAM_ABBR_PHO")
    table = build_table([(front, "PHO"), (0x1234, "Phoenix Coyotes")])
    local = _archive({"frontenglish.bin": refpack_compress(table)})
    rename = Rename(
        22, Identity("Phoenix Coyotes", "Phoenix", "Coyotes", "PHX", "", ""), RELOCATED[22]
    )
    new, _ = teams.rename_strings(local, [rename])
    texts = _table(new, "frontenglish.bin")
    assert texts[front] == "UTA" and texts[ea_hash("TEAM_ABBR_PHX")] == "UTA"
    assert texts[0x1234] == "Utah Mammoth"
