"""`nhl05_ps2.teams` and `wordmarks`: relocated clubs get their new identity."""

import random

import pytest

from retro_roster_patcher.formats.ea_tdb import (
    BigfEntry,
    TDBFile,
    bigf_build,
    bigf_parse,
    refpack_compress,
    refpack_decompress,
)
from retro_roster_patcher.games.nhl05_ps2 import teams, wordmarks
from retro_roster_patcher.games.nhl05_ps2.rom_reader import NHL05PS2RomReader
from retro_roster_patcher.games.nhl07_psp.strings import build_table, parse_table
from tests.fixtures.synthetic_tdb import (
    TYPE_STRING,
    TYPE_UINT,
    FieldSpec,
    TableSpec,
    build_tdb,
    pack_bits,
)
from tests.games.nhl05_ps2.test_patcher import iso, patched

PIL = pytest.importorskip("PIL.Image")


def jets_and_mammoth():
    return [
        teams.Rename(
            1,
            teams.Identity("Atlanta Thrashers", "Atlanta", "Thrashers", "ATL", "", ""),
            teams.RELOCATED[1],
        ),
        teams.Rename(
            22,
            teams.Identity("Phoenix Coyotes", "Phoenix", "Coyotes", "PHX", "", ""),
            teams.RELOCATED[22],
        ),
    ]


def local_viv(entries, padding=0, tight=False):
    table = refpack_compress(build_table(entries), tight=tight) + bytes(padding)
    members = {"frontEnglish.bin": table, "gameEnglish.bin": table, "notes.txt": b"x"}
    return bigf_build([BigfEntry(n, 0, 0) for n in members], members)


ENTRIES = [
    (1, "Atlanta Thrashers®"),
    (2, "Atlanta"),
    (3, "Thrashers®"),
    (4, "ATL"),
    (5, "Phoenix"),
    (6, "Boston Bruins®"),
    (7, "Atlanta is lovely in spring"),
] + [(100 + i, f"filler string number {i} with words") for i in range(60)]


def test_the_patched_disc_names_the_jets_in_its_team_table(tmp_path):
    _, out = patched(tmp_path)
    reader = NHL05PS2RomReader(str(out))
    assert reader.load()
    slots = {slot.index: slot for slot in reader.get_info(deep=True).team_slots}
    assert slots[1].name == "Winnipeg Jets"
    source = NHL05PS2RomReader(str(iso(tmp_path, name="source.iso")))
    assert source.load()
    before = {slot.index: slot for slot in source.get_info(deep=True).team_slots}
    assert slots[0] == before[0] and slots[1] != before[1]
    assert slots[1].abbreviation == before[1].abbreviation  # the file key stays


def test_strings_that_spell_an_old_name_change_and_the_rest_stay():
    archive = local_viv(ENTRIES, padding=64)
    edited, changed, skipped = teams.rename_strings(archive, jets_and_mammoth())
    assert (changed, skipped) == (10, []) and len(edited) == len(archive)
    for member in bigf_parse(edited):
        if not member.name.endswith(".bin"):
            continue
        texts = [
            t
            for _, t in parse_table(
                refpack_decompress(edited[member.offset : member.offset + member.size])
            )
        ]
        assert texts[:7] == [
            "Winnipeg Jets",
            "Winnipeg",
            "Jets",
            "WPG",
            "Utah",
            "Boston Bruins®",
            "Atlanta is lovely in spring",
        ]
        assert texts[7:] == [t for _, t in ENTRIES[7:]]


def test_a_table_that_no_longer_fits_its_member_is_left_alone():
    # Nothing in this table repeats, so a longer name makes it longer. The
    # member is packed by the parse the rewrite uses: packed by the plainer
    # one, it often left the rewrite the byte it needed to win back.
    rng = random.Random(0)
    entries = [(1, "Atlanta")] + [(i, rng.randbytes(6).hex()) for i in range(2, 40)]
    archive = local_viv(entries, tight=True)
    edited, changed, skipped = teams.rename_strings(archive, jets_and_mammoth())
    assert edited == archive and changed == 0
    assert skipped == ["frontEnglish.bin", "gameEnglish.bin"]


def all_star_renames():
    return [
        teams.Rename(
            30,
            teams.Identity("Eastern All-Stars", "Eastern", "All-Stars", "EAS", "", ""),
            teams.ALL_STARS[30],
        ),
        teams.Rename(
            31,
            teams.Identity("Western All-Stars", "Western", "All-Stars", "WES", "", ""),
            teams.ALL_STARS[31],
        ),
    ]


def test_the_all_star_sides_names_change_by_string_id_whatever_the_text():
    # French splits "All Stars de l'Est" into "All Stars" + "de l'Est", and
    # "All-Stars" is every side's nickname, so the ids decide and not the text.
    ids = {**teams.ALL_STAR_STRINGS[30], **teams.ALL_STAR_STRINGS[31]}
    entries = [(sid, f"{part} {sid:x}") for sid, part in ids.items()]
    # North America's nickname, and an "Eastern" of no side.
    entries += [(0x06E41673, "All-Stars"), (99, "Eastern")]
    entries += [(100 + i, f"filler string number {i} with words") for i in range(60)]
    archive = local_viv(entries, padding=256)
    edited, changed, skipped = teams.rename_strings(archive, all_star_renames())
    assert (changed, skipped) == (2 * len(ids), [])
    member = next(m for m in bigf_parse(edited) if m.name == "frontEnglish.bin")
    texts = dict(
        parse_table(refpack_decompress(edited[member.offset : member.offset + member.size]))
    )
    for slot, strings in teams.ALL_STAR_STRINGS.items():
        for sid, part in strings.items():
            assert texts[sid] == getattr(teams.ALL_STARS[slot], part)
    assert (texts[0x06E41673], texts[99]) == ("All-Stars", "Eastern")


def _table(name, fields, size, rows):
    data = b"".join(pack_bits(fields, row, size) for row in rows)
    return TableSpec(name, fields, size, len(rows), len(rows), data)


def test_seattle_and_vegas_take_the_all_star_sides_names_and_leave_the_arenas():
    # All four All-Star sides play at the All-Star game's arena, SARE 30, and
    # SARE 31 is Austria's: a side's slot is not its arena's.
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
    ]
    arenas = [
        {"INDX": 30, "NAME": "Philips Arena", "CITY": "Atlanta, GA"},
        {"INDX": 31, "NAME": "EA SPORTS Arena", "CITY": "Austria"},
    ]
    tdb = TDBFile.parse(
        build_tdb(
            [
                _table(
                    "STEA",
                    stea,
                    69,
                    [
                        {"INDX": 30, "FNME": "Eastern All-Stars", "SNME": "Eastern", "ABBR": "EAS"},
                        {"INDX": 31, "FNME": "Western All-Stars", "SNME": "Western", "ABBR": "WES"},
                    ],
                ),
                _table("SARE", sare, 68, arenas),
            ]
        )
    )
    renames = teams.rename_tables(tdb, [30, 31])
    assert renames == all_star_renames()
    rows = [tdb.get_table("STEA").read_record(i) for i in range(2)]
    assert [(r["FNME"], r["SNME"], r["ABBR"]) for r in rows] == [
        ("Seattle Kraken", "Seattle", "EAS"),
        ("Vegas Golden Knights", "Vegas", "WES"),
    ]
    assert [tdb.get_table("SARE").read_record(i) for i in range(2)] == arenas


def wordmark_member(name: str, glyph_widths, joined_first=False) -> bytes:
    """A `T_<name>.big` whose shape shows one grey block per glyph."""
    from tests.formats.test_ssh import shape

    picture = PIL.new("RGBA", (256, 32), (0, 0, 0, 0))
    x = 20
    for i, width in enumerate(glyph_widths):
        top = 3 if i == 0 else 8
        for px in range(x, x + width):
            for py in range(top, 29):
                picture.putpixel((px, py), (200, 200, 200, 255))
        x += width + (0 if joined_first and i == 0 else 3)
    from retro_roster_patcher.formats import ssh

    data = ssh.encode(shape(256, 32), picture)
    inner = bigf_build([BigfEntry(f"{name}.ssh", 0, 0)], {f"{name}.ssh": data})
    return refpack_compress(inner)


def test_letters_are_cut_from_the_discs_wordmarks_and_a_joined_pair_apart():
    members = {
        "T_PHO.big": wordmark_member("T_PHO", [18, 17, 18, 13, 17, 6, 16]),
        "T_WSH.big": wordmark_member(
            "T_WSH", [29, 18, 14, 17, 6, 17, 17, 15, 18, 16], joined_first=True
        ),
    }
    archive = bigf_build([BigfEntry(n, 0, 0) for n in members], members)
    atlas = wordmarks.letters(archive)
    assert (
        ("P", True) in atlas
        and ("X", False) in atlas
        and ("W", True) in atlas
        and ("G", False) in atlas
    )
    assert atlas[("W", True)].width in (28, 29, 30) and atlas[("A", False)].width in (17, 18, 19)
    assert ("W", False) not in atlas


def test_a_wordmark_is_centred_and_a_missing_capital_is_grown_from_the_small_one():
    members = {"T_PHO.big": wordmark_member("T_PHO", [18, 17, 18, 13, 17, 6, 16])}
    archive = bigf_build([BigfEntry(n, 0, 0) for n in members], members)
    atlas = wordmarks.letters(archive)
    picture = wordmarks.render("Hone", atlas)
    assert picture is not None and picture.size == (256, 32)
    box = picture.getchannel("A").point(lambda v: 255 if v >= 128 else 0).getbbox()
    assert abs((box[0] + box[2]) - 256) <= 3 and box[1] == 3
    assert wordmarks.render("Utah", atlas) is None
