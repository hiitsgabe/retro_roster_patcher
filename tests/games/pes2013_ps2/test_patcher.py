"""PES 2013 (PS2): reader, writer and patcher against a fabricated disc.

The image comes from `tests/fixtures/synthetic_pes2013_iso.py`; nothing here
has seen a retail disc. What is checked is the plumbing the layouts hang on:
the AFS/part-table round trip, where names land in a record, how squads are
reassigned by position, and that team names fit their overlay slots.
"""

from __future__ import annotations

import random
import struct
import zlib

import pytest

from retro_roster_patcher import get_patcher
from retro_roster_patcher.core.errors import MappingError, RomError
from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.pes2013_ps2 import afs
from retro_roster_patcher.games.pes2013_ps2.models import (
    CLUB_COUNT,
    DATABASE_OVERLAY_ENGLISH,
    NATIONAL_SLOT_BASE,
    PLAYER_ENTRIES,
    PLAYER_RECORD_SIZE,
    PESPlayer,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import PES2013PS2Patcher, squad_order
from retro_roster_patcher.games.pes2013_ps2.rom_reader import RomReader
from retro_roster_patcher.games.pes2013_ps2.rom_writer import (
    RomWriter,
    ascii_fold,
    display_name_bytes,
    shirt_name_bytes,
)
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_pes2013_iso as fixture


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / "pes.iso"
    fixture.write_iso(path)
    return path


def roster(team_id, name, code, players):
    return TeamRoster(
        team=Team(id=team_id, name=name, code=code),
        players=[
            Player(
                id=100 + i,
                name=n,
                first_name=n.split()[0],
                last_name=n.split()[-1],
                position=pos,
                number=num,
            )
            for i, (n, pos, num) in enumerate(players)
        ],
    )


LEAGUE = LeagueData(
    league=League(id=1, name="Fixture League", season=2026),
    teams=[
        roster(
            10,
            "Arsenal",
            "ARS",
            [
                ("David Raya", "Goalkeeper", 22),
                ("William Saliba", "Defender", 2),
                ("Gabriel Magalhães", "Defender", 6),
                ("Declan Rice", "Midfielder", 41),
                ("Martin Ødegaard", "Midfielder", 8),
                ("Bukayo Saka", "Forward", 7),
                ("Kai Havertz", "Forward", 29),
            ],
        ),
        roster(11, "Aston Villa", "AVL", [("Emiliano Martínez", "Goalkeeper", 1)]),
    ],
)


# -- containers ------------------------------------------------------------


def test_a_part_table_round_trips_with_the_games_padding():
    parts = [b"a" * 10, b"", b"b" * 40]
    data = afs.Table(4, [8, 48, 0, 64], parts).build()
    table = afs.Table.parse(data)
    assert table.offsets[0] == 8 and table.offsets[1] == 32  # 20-byte table, padded past it
    # Parsed parts run up to the next offset, so they carry their padding.
    assert [p.rstrip(b"\0") for p in table.parts] == parts
    assert table.build() == data


def test_wrap_and_unwrap_agree_on_both_block_kinds():
    header = bytes([0, 6, 1, 0]) + b"\0" * 28
    block = afs.wrap(header, b"payload" * 50)
    head, payload = afs.unwrap(block)
    assert payload == b"payload" * 50 and struct.unpack("<I", head[8:12])[0] == 350
    raw = afs.wrap(bytes([0, 6, 0, 0]) + b"\0" * 28, b"xyz")
    assert afs.unwrap(raw)[1] == b"xyz"
    with pytest.raises(RomError):
        afs.unwrap(header + b"not zlib at all")


# -- reader ----------------------------------------------------------------


def test_the_reader_lists_the_named_clubs_and_counts_players(iso):
    reader = RomReader(str(iso))
    info = reader.get_rom_info()
    assert info.is_valid
    assert [(c.index, c.name, c.code) for c in info.clubs] == [
        (0, "NORTH LONDON", "NLN"),
        (1, "WEST MIDLANDS VILLAGE", "WMV"),
        (2, "FC BARCELONA", "FCB"),
    ]
    assert info.player_count == fixture.PLAYER_COUNT
    tables = reader.player_tables()
    assert tables.club_squad(0)[:3] == [1, 2, 3]
    assert list(tables.club_numbers(0)[:3]) == [0, 1, 2]


def test_a_disc_without_the_archives_is_not_valid(tmp_path):
    path = tmp_path / "other.iso"
    fixture.write_iso(path, boot_name="SLES_000.00")
    assert not RomReader(str(path)).get_rom_info().is_valid


def test_every_language_overlay_with_a_table_is_parsed(iso):
    reader = RomReader(str(iso))
    reader.load()
    english = reader.overlay_teams(DATABASE_OVERLAY_ENGLISH)
    assert english.clubs[2][0].text == "FC BARCELONA" and english.clubs[2][0].capacity == 16
    assert english.clubs[2][1].text == "FCB" and english.clubs[2][1].capacity >= 8
    with pytest.raises(RomError):
        reader.overlay_teams(6)  # an empty entry is no overlay


# -- writer ----------------------------------------------------------------


def test_names_are_cut_to_their_fields_and_shirt_names_folded():
    assert display_name_bytes("A" * 20) == ("A" * 15).encode("utf-16-le") + b"\0\0"
    assert shirt_name_bytes("Ødegaard") == b"Odegaard".ljust(16, b"\0")
    assert ascii_fold("Gabriel Magalhães") == "Gabriel Magalhaes"


def test_the_writer_rewrites_the_player_entry_in_place(iso, tmp_path):
    out = tmp_path / "out.iso"
    writer = RomWriter(str(iso), str(out))
    writer.copy_iso()
    tables = writer.reader.player_tables()
    RomWriter.set_player_names(tables, 1, "Raya", "RAYA")
    RomWriter.set_club_number(tables, 0, 0, 22)
    writer.write_player_tables(PLAYER_ENTRIES[0], tables)
    again = RomReader(str(out))
    again.load()
    back = again.player_tables()
    assert back.record(1)[:8] == "Raya".encode("utf-16-le")
    assert back.record(1)[32:36] == b"RAYA"
    assert back.record(1)[48:] == tables.record(1)[48:]  # abilities untouched
    assert back.club_numbers(0)[0] == 21
    # The other entry is as it was.
    assert again.player_tables(PLAYER_ENTRIES[1]).record(1)[:16] == "PLAYER 1".encode("utf-16-le")


def test_an_entry_that_outgrows_its_slot_is_refused(iso, tmp_path):
    out = tmp_path / "out.iso"
    writer = RomWriter(str(iso), str(out))
    writer.copy_iso()
    tables = writer.reader.player_tables()
    # Random bytes do not compress: the entry no longer fits its slot.
    import random

    rng = random.Random(1)
    # Random bytes do not compress, and ten times the table is more than the
    # sector padding after the entry can absorb.
    tables.parts[0] = bytes(rng.randrange(256) for _ in range(10 * len(tables.parts[0])))
    with pytest.raises(RomError, match="available"):
        writer.write_player_tables(PLAYER_ENTRIES[0], tables)


def test_team_names_are_cut_to_the_room_before_the_next_string(iso, tmp_path):
    out = tmp_path / "out.iso"
    writer = RomWriter(str(iso), str(out))
    writer.copy_iso()
    teams = writer.reader.overlay_teams(DATABASE_OVERLAY_ENGLISH)
    writer.write_team_names(
        teams, {0: ("A VERY LONG CLUB NAME INDEED", "ARSENAL"), 2: ("BARÇA", "BAR")}
    )
    again = RomReader(str(out))
    again.load()
    clubs = again.overlay_teams(DATABASE_OVERLAY_ENGLISH).clubs
    # Whole words are dropped before characters are.
    assert clubs[0][0].text == "A VERY LONG" and clubs[0][1].text == "ARSENAL"
    assert clubs[1][0].text == "WEST MIDLANDS VILLAGE"  # the neighbour survived
    assert clubs[2][0].text == "BARÇA"


# -- patcher ---------------------------------------------------------------


def test_the_patcher_is_registered_with_its_capabilities():
    cls = get_patcher("pes2013-ps2")
    assert cls is PES2013PS2Patcher
    assert cls.platform == "ps2" and cls.sport == "soccer" and cls.requires_slot_mapping
    assert cls.providers == ("espn",)


def test_squad_order_puts_keepers_first_and_keeps_group_order():
    players = LEAGUE.teams[0].players
    ordered = squad_order(players[::-1])
    assert [p.position for p in ordered][:3] == ["Goalkeeper", "Defender", "Defender"]
    assert ordered[0].name == "David Raya"


def test_map_rosters_needs_a_mapping_inside_the_club_range(tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    with pytest.raises(MappingError):
        patcher.map_rosters(LEAGUE, [SlotMapping(slot_index=CLUB_COUNT, team_id=10)])
    with pytest.raises(MappingError):
        patcher.map_rosters(LEAGUE, [SlotMapping(slot_index=0, team_id=99)])


def test_analyze_lists_the_clubs_as_slots(iso, tmp_path):
    info = PES2013PS2Patcher(tmp_path).analyze_rom(iso)
    assert info.is_valid and info.game_id == "pes2013-ps2"
    assert [s.display_name for s in info.slots][:2] == [
        "NORTH LONDON (NLN)",
        "WEST MIDLANDS VILLAGE (WMV)",
    ]
    assert info.extra["player_count"] == fixture.PLAYER_COUNT


def test_patch_renames_the_squad_by_position_and_the_club_everywhere(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(
        LEAGUE,
        [SlotMapping(slot_index=0, team_id=10), SlotMapping(slot_index=2, team_id=11)],
    )
    out = tmp_path / "out.iso"
    result = patcher.patch(rom_path=iso, output_path=out, rosters=rosters)
    assert result.teams_patched == 2 and result.players_patched == 8

    reader = RomReader(str(out))
    reader.load()
    for entry in PLAYER_ENTRIES:
        tables = reader.player_tables(entry)
        squad = tables.club_squad(0)
        names = [tables.record(i)[:32].decode("utf-16-le").split("\0")[0] for i in squad[:11]]
        # The keeper took the keeper record (slot 0), defenders the defender
        # records (1..3), and so on; slots without a new player keep theirs.
        assert names[0] == "DAVID RAYA"
        assert names[1:3] == ["WILLIAM SALIBA", "G. MAGALHÃES"] and names[3] == "PLAYER 4"
        assert names[4:6] == ["DECLAN RICE", "MARTIN ØDEGAARD"]
        assert names[8:10] == ["BUKAYO SAKA", "KAI HAVERTZ"] and names[10] == "PLAYER 11"
        # Shirt names are surnames, as on the disc.
        assert tables.record(squad[0])[32:48] == b"RAYA".ljust(16, b"\0")
        assert tables.record(squad[5])[32:48] == b"ODEGAARD".ljust(16, b"\0")
        assert tables.club_numbers(0)[0] == 21 and tables.club_numbers(0)[4] == 40
        assert tables.record(squad[0])[48:] == tables.record(squad[0])[48:]  # abilities kept
    for overlay in (DATABASE_OVERLAY_ENGLISH, 5):
        clubs = reader.overlay_teams(overlay).clubs
        assert (clubs[0][0].text, clubs[0][1].text) == ("ARSENAL", "ARS")
        assert (clubs[2][0].text, clubs[2][1].text) == ("ASTON VILLA", "AVL")
        assert clubs[1][0].text == "WEST MIDLANDS VILLAGE"


def test_patch_refuses_rosters_mapped_for_another_game(iso, tmp_path):
    from retro_roster_patcher.core.models import MappedRosters

    patcher = PES2013PS2Patcher(tmp_path)
    with pytest.raises(MappingError):
        patcher.patch(
            rom_path=iso, output_path=tmp_path / "o.iso", rosters=MappedRosters(game_id="we2002")
        )


def test_the_fixture_player_entry_is_what_the_reader_expects():
    entry = fixture.build_player_entry()
    head, payload = afs.unwrap(entry)
    table = afs.Table.parse(payload)
    _h, players = afs.unwrap(table.parts[0])
    assert len(players) == fixture.PLAYER_COUNT * PLAYER_RECORD_SIZE
    assert zlib.decompress(table.parts[2][32:])[:4] == struct.pack("<HH", 1, 2)


# -- emblems, league art and screen images -----------------------------------


def test_the_atlas_round_trips_and_takes_a_logo_with_free_colours(iso, tmp_path):
    import io

    from PIL import Image

    from retro_roster_patcher.games.pes2013_ps2.afs import unwrap
    from retro_roster_patcher.games.pes2013_ps2.emblems import CLUB_TABLE, Atlas, paint_logos

    reader = RomReader(str(iso))
    reader.load()
    _h, payload = unwrap(reader.read("0_TEXT.AFS", 604))
    atlas = Atlas.parse(payload)
    assert atlas.build() == payload and atlas.side == 64
    assert atlas.image(CLUB_TABLE, 2) == bytes([3]) * 4096
    png = io.BytesIO()
    Image.new("RGBA", (10, 10), (200, 30, 30, 255)).save(png, "PNG")
    assert paint_logos(atlas, CLUB_TABLE, {1: png.getvalue(), 99: png.getvalue()}) == 1
    image = atlas.image(CLUB_TABLE, 1)
    used = {v for v in image if v}
    # The logo's own red is in the palette (its sharpened rim adds shades of it).
    palette = atlas.palettes[CLUB_TABLE]
    assert any(palette[4 * i : 4 * i + 4] == bytes((200, 30, 30, 0x80)) for i in used)
    assert image[0] != 0  # the 10x10 logo is trimmed and scaled to fill the square
    # One palette for the whole atlas, and the crest that stays keeps its colour
    # through it: it was drawn with entry 3, whose colour the fixture sets.
    assert all(pal == palette for pal in atlas.palettes if pal)
    kept = atlas.image(CLUB_TABLE, 2)
    assert len(set(kept)) == 1 and palette[4 * kept[0] : 4 * kept[0] + 3] == bytes((3, 252, 128))


def test_textures_round_trip_and_paint(iso):
    import io

    from PIL import Image

    from retro_roster_patcher.games.pes2013_ps2.textures import Texture, swizzle8, unswizzle8

    reader = RomReader(str(iso))
    reader.load()
    found = reader.bundles_with("U_TEXT.AFS", "mn_ts_eng_l")
    assert len(found) == 1
    _entry, _header, bundle = found[0]
    assert bundle.build() == unwrap_payload(reader, "U_TEXT.AFS", found[0][0].index)
    tex = bundle.texture("mn_ts_eng_l")
    assert (tex.width, tex.height) == (32, 32) and tex.build() == tex.build()
    linear = bytes(range(256)) * 4
    assert unswizzle8(swizzle8(linear, 32, 32), 32, 32) == linear
    png = io.BytesIO()
    Image.new("RGBA", (32, 32), (0, 0, 255, 255)).save(png, "PNG")
    tex.paint(png.getvalue())
    assert set(tex.pixels) == {1} and tex.palette[4:8] == bytes((0, 0, 255, 0x80))
    assert Texture.parse(tex.build()).pixels == tex.pixels


def unwrap_payload(reader, afs_name, index):
    from retro_roster_patcher.games.pes2013_ps2.afs import unwrap

    return unwrap(reader.read(afs_name, index))[1]


def test_patch_writes_crests_league_art_and_screen_images(iso, tmp_path):
    import io

    from PIL import Image

    from retro_roster_patcher.games.pes2013_ps2.afs import Table, unwrap
    from retro_roster_patcher.games.pes2013_ps2.emblems import CLUB_TABLE, Atlas

    png = io.BytesIO()
    Image.new("RGBA", (16, 16), (10, 200, 10, 255)).save(png, "PNG")
    served = {}

    def transport(url, headers, timeout):
        served[url] = served.get(url, 0) + 1
        return png.getvalue()

    league = LeagueData(
        league=League(id=1, name="Fixture League", season=2026, logo_url="http://x/league.png"),
        teams=[
            TeamRoster(
                team=Team(id=10, name="Arsenal", code="ARS", logo_url="http://x/ars.png"),
                players=[
                    Player(
                        id=100,
                        name="David Raya",
                        last_name="Raya",
                        position="Goalkeeper",
                        number=22,
                    )
                ],
            )
        ],
    )
    patcher = PES2013PS2Patcher(tmp_path, transport=transport)
    rosters = patcher.map_rosters(league, [SlotMapping(slot_index=1, team_id=10)])
    out = tmp_path / "out.iso"
    title = tmp_path / "title.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(title)
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, images={"title": str(title)})
    assert served == {"http://x/ars.png": 1, "http://x/league.png": 1}

    reader = RomReader(str(out))
    reader.load()
    for index in (600, 602, 604, 606):
        atlas = Atlas.parse(unwrap_payload(reader, "0_TEXT.AFS", index))
        image = atlas.image(CLUB_TABLE, 1)
        assert image is not None and set(image) - {0}
    for afs_name in ("U_TEXT.AFS", "B_TEXT.AFS"):
        _h, payload = unwrap(reader.read(afs_name, 15))
        table = Table.parse(payload)
        _ph, leaf = unwrap(table.parts[0])
        names = leaf.split(b"\0")
        # Cut to the room the original took: "Liga inglesa" has 12 bytes, so pt-BR gets "FIXTURE".
        assert any(n in (b"FIXTURE LEAGUE", b"FIXTURE") for n in names)
        assert b"English League" not in names and b"Liga inglesa" not in names
        assert b"Ligue 1" in names
        _e, _h2, bundle = reader.bundles_with(afs_name, "mn_ts_eng_l")[0]
        # The 16x16 logo is trimmed and scaled to fill the 32x32 icon.
        icon = set(bundle.texture("mn_ts_eng_l").pixels)
        assert 0 not in icon and icon != {3}
        assert set(bundle.texture("title_bgNS").pixels) == {1}  # the red title image
        assert set(bundle.texture("mo_sub_bgUS").pixels) == {6}  # untouched


def test_export_images_writes_one_png_per_slot(iso, tmp_path):
    from PIL import Image

    paths = PES2013PS2Patcher(tmp_path).export_images(iso, tmp_path / "art")
    assert [p.name for p in paths] == ["title.png", "menu.png", "loading.png", "match.png"]
    assert Image.open(paths[0]).size == (64, 64)


def test_squad_order_puts_the_lineup_first_in_formation_order():
    players = [
        Player(id=i, name=f"P{i}", position=pos)
        for i, pos in enumerate(["Attacker", "Goalkeeper", "Defender", "Midfielder", "Goalkeeper"])
    ]
    lineup = {
        "formation": "4-4-2",
        "starters": [
            {"id": 3, "position": "CM", "place": 8},
            {"id": 0, "position": "F", "place": 9},
            {"id": 4, "position": "G", "place": 1},
        ],
    }
    assert [p.id for p in squad_order(players, lineup)] == [4, 3, 0, 1, 2]


NATIONS = LeagueData(
    league=League(id=2021, name="International Friendlies", season=2026),
    teams=[
        roster(
            300,
            "Nation Zero",
            "N00",
            [
                ("Alisson Becker", "Goalkeeper", 1),
                ("Marquinhos Corrêa", "Defender", 4),
                ("Bruno Guimarães", "Midfielder", 5),
                ("Vinícius Júnior", "Forward", 7),
                ("Raphinha Belloli", "Forward", 11),
            ],
        ),
        roster(301, "Nation One", "N01", [("Emiliano Martínez", "Goalkeeper", 23)]),
        roster(302, "Elsewhere", "ELS", []),
    ],
)


def test_analyze_lists_the_nationals_after_the_clubs(iso, tmp_path):
    info = PES2013PS2Patcher(tmp_path).analyze_rom(iso)
    nationals = [s for s in info.slots if s.index >= NATIONAL_SLOT_BASE]
    assert nationals[0].index == NATIONAL_SLOT_BASE
    assert nationals[0].display_name == "National: NATION 0 (N00)"
    assert len(nationals) == 67


def test_the_national_slot_map_pairs_by_code(iso, tmp_path):
    mapping = PES2013PS2Patcher(tmp_path).national_slot_map(iso, NATIONS)
    assert [(m.slot_index, m.team_id) for m in mapping] == [
        (NATIONAL_SLOT_BASE, 300),
        (NATIONAL_SLOT_BASE + 1, 301),
    ]


def test_a_national_squad_takes_only_the_records_no_club_shares(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(
        NATIONS, [SlotMapping(slot_index=NATIONAL_SLOT_BASE, team_id=300)]
    )
    out = tmp_path / "out.iso"
    result = patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False)
    assert result.teams_patched == 1 and result.players_patched == 5

    reader = RomReader(str(out))
    reader.load()
    for entry in PLAYER_ENTRIES:
        tables = reader.player_tables(entry)
        row = tables.national_squad(0)
        # The three exclusive records first, then two from the free pool, then
        # the three records club 0 shares, which keep their names.
        assert row[:8] == [34, 35, 36, 37, 38, 1, 2, 3] and row[8:] == [0] * 15

        def name(i, tables=tables):
            return tables.record(i)[:32].decode("utf-16-le").split("\0")[0]

        assert [name(i) for i in row[:5]] == [
            "ALISSON BECKER",
            "M. CORRÊA",
            "BRUNO GUIMARÃES",
            "VINÍCIUS JÚNIOR",
            "R. BELLOLI",
        ]
        assert name(1) == "PLAYER 1"
        # Each taken record carries the player's position group.
        assert [tables.record(i)[53] >> 4 for i in row[:5]] == [0, 3, 7, 12, 12]
        # Club 0 is untouched.
        assert tables.club_squad(0)[:3] == [1, 2, 3]


def test_a_mapping_outside_both_ranges_is_refused(tmp_path):
    with pytest.raises(MappingError):
        PES2013PS2Patcher(tmp_path).map_rosters(
            NATIONS, [SlotMapping(slot_index=NATIONAL_SLOT_BASE + 67, team_id=300)]
        )


def test_merged_rosters_patch_clubs_and_nationals_together(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    clubs = patcher.map_rosters(LEAGUE, [SlotMapping(slot_index=0, team_id=10)])
    nations = patcher.map_rosters(
        NATIONS, [SlotMapping(slot_index=NATIONAL_SLOT_BASE + 1, team_id=301)]
    )
    merged = clubs.merge(nations)
    assert sorted(merged.teams) == [0, NATIONAL_SLOT_BASE + 1]
    out = tmp_path / "out.iso"
    result = patcher.patch(rom_path=iso, output_path=out, rosters=merged, logos=False)
    assert result.teams_patched == 2
    reader = RomReader(str(out))
    reader.load()
    tables = reader.player_tables(PLAYER_ENTRIES[0])
    # Nation 1 had no players of its own, so its keeper comes from the pool.
    assert tables.national_squad(1)[0] == 37
    assert reader.overlay_teams(DATABASE_OVERLAY_ENGLISH).clubs[0][0].text == "ARSENAL"


def test_a_block_that_runs_past_the_entry_size_is_read_whole(iso):
    """A rebuilt entry may spill into the sector padding before the next one;
    the reader follows the block header's size, not the table's."""
    reader = RomReader(str(iso))
    reader.load()
    entry = reader.entry("0_TEXT.AFS", PLAYER_ENTRIES[0])
    tables = reader.player_tables(PLAYER_ENTRIES[0])
    # Pad the payload so the block outgrows the entry's size but not its room.
    parts = list(tables.parts)
    parts[-1] = parts[-1] + random.Random(1).randbytes(entry.size + 100)  # incompressible
    tables.parts = parts
    block = tables.build()
    assert len(block) > entry.size
    writer = RomWriter(str(iso), str(iso))
    writer.copy_iso()
    writer.write_player_tables(PLAYER_ENTRIES[0], tables, block)
    again = RomReader(str(iso))
    again.load()
    assert again.player_tables(PLAYER_ENTRIES[0]).parts[-1] == parts[-1]


def test_blank_unused_empties_the_other_clubs_leagues_and_nationals(iso, tmp_path):
    patcher = PES2013PS2Patcher(tmp_path)
    clubs = patcher.map_rosters(LEAGUE, [SlotMapping(slot_index=0, team_id=10)])
    nations = patcher.map_rosters(
        NATIONS, [SlotMapping(slot_index=NATIONAL_SLOT_BASE, team_id=300)]
    )
    out = tmp_path / "out.iso"
    patcher.patch(
        rom_path=iso, output_path=out, rosters=clubs.merge(nations), logos=False, blank_unused=True
    )
    from retro_roster_patcher.games.pes2013_ps2.afs import Table
    from retro_roster_patcher.games.pes2013_ps2.emblems import CLUB_TABLE, Atlas

    reader = RomReader(str(out))
    reader.load()
    teams = reader.overlay_teams(DATABASE_OVERLAY_ENGLISH)
    assert teams.clubs[0][0].text == "ARSENAL" and teams.clubs[1][0].text == ""
    assert teams.clubs[1][1].text == "" and teams.clubs[2][0].text == ""
    assert teams.nationals[0][0].text == "NATION 0" and teams.nationals[1][0].text == ""
    # Crests of the unused clubs are transparent; the mapped one is untouched.
    atlas = Atlas.parse(unwrap_payload(reader, "0_TEXT.AFS", 604))
    assert set(atlas.image(CLUB_TABLE, 1)) == {0} and set(atlas.image(CLUB_TABLE, 0)) != {0}
    # The English group kept its league name; the groups without a team lost
    # theirs, and their team-select icons are transparent.
    table = Table.parse(unwrap_payload(reader, "U_TEXT.AFS", 15))
    names = afs.unwrap(table.parts[0])[1].split(b"\0")
    # "Ligue 1" itself (the French group) is blanked too; its room is NUL-padded.
    assert names[:7] == [b"League A", b"FIXTURE LEAGUE", b"", b"", b"", b"", b""]
    _e, _h, bundle = reader.bundles_with("U_TEXT.AFS", "mn_ts_eng_l")[0]
    assert set(bundle.texture("mn_ts_eng_l").pixels) == {3}  # untouched: its group has a team


def test_blanking_free_records_leaves_every_squad_member_alone(iso):
    reader = RomReader(str(iso))
    reader.load()
    tables = {index: reader.player_tables(index) for index in PLAYER_ENTRIES}
    # Players 1..33 belong to clubs, 34..36 to national row 0; 37..39 to nobody.
    assert PES2013PS2Patcher._blank_free_records(tables) == 3
    for t in tables.values():
        assert t.record(36)[:32].rstrip(b"\0") and not any(t.record(37))
        assert not any(t.record(39)) and t.record(33)[:32].rstrip(b"\0")


def test_starters_take_the_formation_places_holding_their_position(iso):
    """Club 0's eleven is laid out GK, CB, CB, SB, DMF, CMF, CMF, SMF, WF, CF, CF
    (the fixture's SLOT_POSITIONS): a right back listed before the centre
    backs still lands on the side-back place, and a starter whose position
    the formation lacks takes a place of his group inside the eleven."""
    reader = RomReader(str(iso))
    reader.load()
    tables = reader.player_tables(PLAYER_ENTRIES[0])

    def player(name, group, role=None):
        return PESPlayer(display_name=name, shirt_name=name, position=group, role=role)

    players = [
        player("GK", "Goalkeeper", 0),
        player("RB", "Defender", 4),
        player("CB1", "Defender", 3),
        player("CB2", "Defender", 3),
        player("AM", "Midfielder", 9),  # no AMF place: a midfield place instead
        player("ST", "Attacker", 12),
        player("BENCH GK", "Goalkeeper"),
        player("BENCH CB", "Defender"),
    ]
    out = PES2013PS2Patcher._assign(tables, 0, players)
    by_name = {p.display_name: s for s, _idx, p in out}
    assert by_name["GK"] == 0 and by_name["RB"] == 3
    assert {by_name["CB1"], by_name["CB2"]} == {1, 2}
    assert by_name["AM"] in (4, 5, 6, 7) and by_name["ST"] in (9, 10)
    # The bench fills what the starters left, by group: no keeper place is
    # free, so the bench keeper waits for whatever is left.
    assert by_name["BENCH CB"] in (4, 5, 6, 7, 8, 9, 10)
    assert by_name["BENCH GK"] in (4, 5, 6, 7, 8, 9, 10)
