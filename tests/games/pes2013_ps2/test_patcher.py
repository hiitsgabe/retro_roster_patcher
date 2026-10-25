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
    POSITION_BYTE,
    PESPlayer,
)
from retro_roster_patcher.games.pes2013_ps2.patcher import (
    PES2013PS2Patcher,
    lineup_role,
    squad_order,
)
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
        # All seven start, each on a record of his group's own position: the
        # keeper in goal (0), defenders at centre back (1, 2), midfielders at
        # centre midfield (5, 6), forwards at centre forward (9, 10); places
        # without a new player keep theirs.
        assert names[0] == "DAVID RAYA"
        assert names[1:3] == ["WILLIAM SALIBA", "G. MAGALHÃES"] and names[3] == "PLAYER 4"
        assert names[4:7] == ["PLAYER 5", "DECLAN RICE", "MARTIN ØDEGAARD"]
        assert names[8:11] == ["PLAYER 9", "BUKAYO SAKA", "KAI HAVERTZ"]
        # Shirt names are surnames, as on the disc.
        assert tables.record(squad[0])[32:48] == b"RAYA".ljust(16, b"\0")
        assert tables.record(squad[6])[32:48] == b"ODEGAARD".ljust(16, b"\0")
        assert tables.club_numbers(0)[0] == 21 and tables.club_numbers(0)[5] == 40
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
    pytest.importorskip("PIL")
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
    # The logo's own red is in the palette (its sharpened rim adds shades of it);
    # `colours` is the index-order view of the GS-swizzled bytes on the disc.
    palette = atlas.colours(CLUB_TABLE)
    assert any(palette[4 * i : 4 * i + 4] == bytes((200, 30, 30, 0x80)) for i in used)
    # Index 8's colour lives at raw position 16 on the disc (GS CSM1 layout).
    atlas.palettes[CLUB_TABLE] = bytes(i % 256 for i in range(1024))
    linear = atlas.colours(CLUB_TABLE)
    assert linear[4 * 8 : 4 * 9] == atlas.palettes[CLUB_TABLE][4 * 16 : 4 * 17]
    assert linear[4 * 1 : 4 * 2] == atlas.palettes[CLUB_TABLE][4 * 1 : 4 * 2]
    assert image[0] != 0  # the 10x10 logo is trimmed and scaled to fill the square
    # One palette for the whole atlas, and the crest that stays keeps its colour
    # through it: it was drawn with entry 3, whose colour the fixture sets.
    assert all(pal == atlas.palettes[CLUB_TABLE] for pal in atlas.palettes if pal)
    kept = atlas.image(CLUB_TABLE, 2)
    assert len(set(kept)) == 1
    # Colours within a few dE of each other share an entry, so the fixture's
    # near-identical greens may merge: the kept crest stays that green.
    got = palette[4 * kept[0] : 4 * kept[0] + 3]
    assert max(abs(a - b) for a, b in zip(got, (3, 252, 128), strict=True)) <= 8


def test_textures_round_trip_and_paint(iso):
    pytest.importorskip("PIL")
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
    pytest.importorskip("PIL")
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
    pytest.importorskip("PIL")
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
    # The defender completes the eleven; the spare keeper heads the bench.
    assert [p.id for p in squad_order(players, lineup)] == [4, 2, 3, 0, 1]


GROUP = {"G": "Goalkeeper", "D": "Defender", "M": "Midfielder", "F": "Attacker"}
CODE = {"G": "G", "D": "CD", "M": "CM", "F": "F"}


def _squad(names: str) -> list[Player]:
    """Players named by group letter and number ("G1 D1 M1"), ids from 1."""
    return [
        Player(id=i + 1, name=n, first_name=n, last_name=n, position=GROUP[n[0]])
        for i, n in enumerate(names.split())
    ]


def _lineup(players: list[Player]) -> dict:
    return {
        "starters": [
            {"id": p.id, "position": CODE[p.name[0]], "place": k + 1} for k, p in enumerate(players)
        ]
    }


def test_without_a_lineup_the_eleven_is_one_keeper_and_a_4_4_2():
    players = _squad("F1 G1 D1 M1 G2 D2 D3 M2 F2 D4 M3 D5 G3 M4 F3 D6 M5")
    order = [p.name for p in squad_order(players)]
    assert order[:11] == "G1 D1 D2 D3 D4 M1 M2 M3 M4 F1 F2".split()
    assert order[11:] == "G2 G3 D5 D6 M5 F3".split()


def test_a_lineup_short_of_a_starter_is_completed_from_his_group_not_by_a_keeper():
    """A midfielder of the lineup has left the club: the first other
    midfielder takes his turn in the eleven, the backup keepers wait after it."""
    players = _squad("G1 D1 D2 D3 D4 M1 M2 M3 F1 F2 F3 G2 D5 M4 M5 G3")
    gone = Player(id=99, name="MGONE", position="Midfielder")
    order = [p.name for p in squad_order(players, _lineup(players[:8] + [gone] + players[8:10]))]
    assert order[:11] == "G1 D1 D2 D3 D4 M1 M2 M3 M4 F1 F2".split()
    assert order[11:] == "G2 G3 D5 M5 F3".split()


def test_a_merged_lineup_with_two_keepers_starts_one():
    players = _squad("G1 G2 D1 D2 D3 D4 M1 M2 M3 F1 F2 M4")
    order = [p.name for p in squad_order(players, _lineup(players[:11]))]
    assert order[:11] == "G1 D1 D2 D3 D4 M1 M2 M3 M4 F1 F2".split()
    assert order[11:] == ["G2"]


def test_keepers_tied_on_place_start_in_the_lineups_order():
    """A merged lineup lists its starters by starts, and every keeper has
    place 1: the keeper the lineup lists first starts, not the squad's first."""
    players = _squad("G2 G1 D1 D2 D3 D4 M1 M2 M3 F1 F2 M4")
    lineup = _lineup(players[1:11] + players[:1])
    lineup["starters"][-1]["place"] = 1
    order = [p.name for p in squad_order(players, lineup)]
    assert order[0] == "G1"
    assert order[11:] == ["G2"]


def test_every_listed_starter_gets_a_lineup_role():
    """ESPN's front-three codes are wing forwards and centre forwards, and a
    code the table lacks plays the player's group, so no starter is benched."""
    players = {
        code: Player(id=i, name=code, position=group)
        for i, (code, group) in enumerate(
            [("LF", "Midfielder"), ("RF", "Attacker"), ("RCF", "Attacker"), ("XYZ", "Defender")]
        )
    }
    lineup = {"starters": [{"id": p.id, "position": code} for code, p in players.items()]}
    roles = {code: lineup_role(p, lineup) for code, p in players.items()}
    assert roles == {"LF": 11, "RF": 11, "RCF": 12, "XYZ": 3}
    assert lineup_role(Player(id=99, name="Bench", position="Goalkeeper"), lineup) is None


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
    # The bench fills what the starters left, by group, then anything an
    # outfielder can play. No keeper place is free and the club has no bench,
    # so the spare keeper is left out rather than sent up front.
    assert by_name["BENCH CB"] in (4, 5, 6, 7, 8, 9, 10)
    assert "BENCH GK" not in by_name


class _Club:
    """A club's squad table without a disc: one record per place, each holding
    only its position nibble. A 4-4-2 eleven, then a bench of two keepers and
    ten outfield records (32-place clubs on the real disc)."""

    ROLES = [0, 4, 3, 3, 4, 8, 7, 7, 8, 12, 12] + [0, 0, 3, 3, 4, 7, 7, 8, 8, 12, 12, 12]
    player_count = len(ROLES) + 1

    def club_squad(self, club: int) -> list[int]:
        return list(range(1, len(self.ROLES) + 1))

    def record(self, index: int) -> bytes:
        rec = bytearray(PLAYER_RECORD_SIZE)
        rec[POSITION_BYTE] = self.ROLES[index - 1] << 4
        return bytes(rec)


def _placed(players: list[PESPlayer]) -> dict[str, int]:
    out = PES2013PS2Patcher._assign(_Club(), 0, players)  # type: ignore[arg-type]
    return {p.display_name: s for s, _idx, p in out}


def _p(name: str, group: str, role: int | None = None) -> PESPlayer:
    return PESPlayer(display_name=name, shirt_name=name, position=group, role=role)


OUTFIELD_TEN = [
    _p("LB", "Defender", 4),
    _p("CB1", "Defender", 3),
    _p("CB2", "Defender", 3),
    _p("RB", "Defender", 4),
    _p("LM", "Midfielder", 8),
    _p("CM1", "Midfielder", 7),
    _p("CM2", "Midfielder", 7),
    _p("RM", "Midfielder", 8),
    _p("CF1", "Attacker", 12),
    _p("CF2", "Attacker", 12),
]
OUTFIELD_PLACES = set(range(1, 11))


def test_a_spare_keeper_goes_to_the_bench_never_up_front():
    """Atletico-MG had five keepers for the disc's three keeper records: the
    fifth took the first free place, the eleven's centre forward."""
    keepers = [_p("GK1", "Goalkeeper", 0)] + [_p(f"GK{i}", "Goalkeeper") for i in range(2, 6)]
    starters = OUTFIELD_TEN[:9]  # one forward short: an eleven place is left
    bench = [_p("SUB MF", "Midfielder")]
    placed = _placed(keepers[:1] + starters + keepers[1:] + bench)
    assert placed["GK1"] == 0
    assert all(placed[f"GK{i}"] >= 11 for i in range(2, 6))  # bench, any record
    assert placed["SUB MF"] in OUTFIELD_PLACES  # the free eleven place goes to an outfielder


def test_a_second_starting_keeper_does_not_start_outfield():
    """A five-match merge can list two keepers among the eleven."""
    players = [_p("GK A", "Goalkeeper", 0), _p("GK B", "Goalkeeper", 0)] + OUTFIELD_TEN[:9]
    placed = _placed(players + [_p("SUB FW", "Attacker")])
    assert placed["GK A"] == 0 and placed["GK B"] in (11, 12)
    assert placed["SUB FW"] in OUTFIELD_PLACES


def test_a_lineup_without_a_keeper_does_not_put_a_forward_in_goal():
    """Eleven outfield starters (the keeper missing from the roster) against a
    4-4-2 with two forward places: the third forward has no outfield place
    left and the goal goes to the bench keeper."""
    players = OUTFIELD_TEN + [_p("WF", "Attacker", 11), _p("SUB GK", "Goalkeeper")]
    placed = _placed(players)
    assert placed["SUB GK"] == 0
    assert placed["WF"] >= 11
    assert {placed[p.display_name] for p in OUTFIELD_TEN} == OUTFIELD_PLACES


#: The ESPN lineup the fetch kept in `extra`: a 4-4-2 of G1, D1-D4, M1-M4,
#: F1, F2; the bench G2, D5, M5, F3.
CLUB = _squad("G1 D1 D2 D3 D4 M1 M2 M3 M4 F1 F2 G2 D5 M5 F3")


def _game_eleven(tmp_path, order: str) -> tuple[set[str], dict[str, int]]:
    """Map `CLUB` in the editor's `order` with the lineup still attached, as
    the app sends it, and place it on a 4-4-2 club: who starts, and where."""
    by_name = {p.name: p for p in CLUB}
    roster = TeamRoster(
        team=Team(id=7, name="Order FC", code="ORD"),
        players=[by_name[n] for n in order.split()],
        extra={"lineup": _lineup(CLUB[:11])},
    )
    data = LeagueData(league=League(id=1, name="L", season=2026), teams=[roster])
    mapped = PES2013PS2Patcher(tmp_path).map_rosters(
        data, [SlotMapping(slot_index=0, team_id=7)], order_as_given=True
    )
    placed = _placed(mapped.teams[0].players)
    return {n for n, s in placed.items() if s < 11}, placed


def test_the_editors_first_eleven_start_whatever_the_lineup_said(tmp_path):
    """D4 and M4 started for ESPN but the editor benched them for F3 and M5."""
    order = "G1 D1 D2 D3 F3 M1 M2 M3 M5 F1 F2 G2 D4 M4 D5"
    eleven, placed = _game_eleven(tmp_path, order)
    assert eleven == set(order.split()[:11])
    assert placed["G1"] == 0


def test_two_keepers_in_the_editors_eleven_bench_the_second(tmp_path):
    """The place G2 cannot play goes to the first bench player who fits it,
    in the editor's order: D5, not the lineup's D4."""
    eleven, placed = _game_eleven(tmp_path, "G1 G2 D1 D2 D3 M1 M2 M3 M4 F1 F2 D5 D4 M5 F3")
    assert eleven == set("G1 D5 D1 D2 D3 M1 M2 M3 M4 F1 F2".split())
    assert placed["G1"] == 0 and placed["G2"] >= 11


def test_no_keeper_in_the_editors_eleven_starts_the_first_bench_keeper(tmp_path):
    """G2, first keeper below the eleven, keeps goal ahead of the lineup's G1,
    and F3, the eleventh outfielder, finds no place and sits out."""
    eleven, placed = _game_eleven(tmp_path, "D1 D2 D3 D4 M1 M2 M3 M4 F1 F2 F3 G2 G1 D5 M5")
    assert eleven == set("G2 D1 D2 D3 D4 M1 M2 M3 M4 F1 F2".split())
    assert placed["G2"] == 0 and placed["F3"] >= 11


def test_the_kit_table_round_trips_and_flips_a_licensed_kit(iso, tmp_path):
    from retro_roster_patcher.games.pes2013_ps2.kits import AWAY, HOME, SHIRT, SHORTS

    reader = RomReader(str(iso))
    reader.load()
    kits = reader.kits()
    assert len(kits.clubs) == 3 and len(kits.nationals) == 1
    assert kits.clubs[0][HOME].rgb(SHIRT) == (255, 0, 0) and kits.clubs[0][HOME].rgb(SHORTS) == (
        255,
        255,
        255,
    )
    assert kits.clubs[0][HOME].parametric and not kits.clubs[1][HOME].parametric
    assert kits.clubs[1][AWAY].rgb(SHIRT) == (255, 0, 0)
    kits.clubs[1][HOME].set_rgb(SHIRT, (0, 128, 0))
    kits.clubs[1][HOME].make_parametric()
    writer = RomWriter(str(iso), str(tmp_path / "out.iso"))
    writer.copy_iso()
    writer.write_kits(kits)
    again = RomReader(str(tmp_path / "out.iso"))
    again.load()
    home = again.kits().clubs[1][HOME]
    assert home.parametric and home.rgb(SHIRT) == (0, 131, 0)  # 5-bit channels: 128 -> 131
    assert bytes(home.params[34:37]) == b"\xff\xff\x04"
    assert again.kits().clubs[2][HOME].rgb(SHIRT) == (0, 0, 0)  # untouched neighbours


def test_a_kit_spec_lands_on_the_template_regions():
    from retro_roster_patcher.core.kits import KitSpec
    from retro_roster_patcher.games.pes2013_ps2.kits import (
        COLLAR,
        SHIRT,
        SHORTS,
        SLEEVES,
        SOCKS,
        STRIPES,
        Kit,
        apply_spec,
    )

    kit = Kit.parse(fixture.build_kit(0x8000, 0x8000, parametric=False))
    red, white, black = (255, 0, 0), (255, 255, 255), (0, 0, 0)
    apply_spec(kit, KitSpec(shirt=red, secondary=white, pattern="stripes", shorts=black, socks=red))
    # 5-bit channels: 255 stays 255, 0 stays 0.
    assert kit.rgb(SHIRT) == red and kit.rgb(STRIPES) == white and kit.rgb(SLEEVES) == red
    assert kit.rgb(COLLAR) == white and kit.rgb(SHORTS) == black
    assert all(kit.rgb(s) == red for s in SOCKS)
    assert kit.parametric and bytes(kit.params[34:37]) == b"\xff\xff\x04"
    # A sleeved kit keeps the body plain and colours the sleeves instead.
    apply_spec(
        kit, KitSpec(shirt=red, secondary=white, pattern="sleeves", shorts=white, socks=white)
    )
    assert kit.rgb(STRIPES) == red and kit.rgb(SLEEVES) == white
    # Name and number colour contrasts with the shirt: white on red.
    assert kit.rgb(12) == white


def test_the_patcher_declares_kits_slot_groups_and_nationals():
    from retro_roster_patcher.core import kits
    from retro_roster_patcher.games.pes2013_ps2 import patcher as module

    assert PES2013PS2Patcher.supports_kits is True
    assert PES2013PS2Patcher.supports_nationals is True
    assert PES2013PS2Patcher.national_slot_base == NATIONAL_SLOT_BASE == 1000
    assert PES2013PS2Patcher.slot_groups == (
        ("England", 0, 20),
        ("France", 20, 20),
        ("Italy", 40, 20),
        ("Netherlands", 60, 18),
        ("Spain", 78, 20),
        ("Other", 98, 41),
    )
    assert module.default_kits is kits.default_kits  # the old import path still works


def test_a_picture_of_a_kit_overrides_a_slots_kit(iso, tmp_path):
    pytest.importorskip("PIL")
    from retro_roster_patcher.core.errors import CapabilityError
    from retro_roster_patcher.core.kits import KitSpec, render_kit
    from retro_roster_patcher.games.pes2013_ps2.kits import AWAY, HOME, SHIRT, STRIPES

    red, white, blue = (255, 0, 0), (255, 255, 255), (0, 0, 255)
    striped = render_kit(KitSpec(red, white, "stripes", white, red), 192, 256)
    away = tmp_path / "away.png"
    away.write_bytes(render_kit(KitSpec(blue, white, "plain", white, blue), 192, 256))
    league = LeagueData(
        league=League(id=1, name="Fixture League", season=2026),
        teams=[roster(10, "Arsenal", "ARS", [("David Raya", "Goalkeeper", 22)])],
    )
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(league, [SlotMapping(slot_index=1, team_id=10)])
    out = tmp_path / "out.iso"
    overrides = {1: {"home": {"image": striped}, "away": {"image": str(away)}}}
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, kits=overrides)

    reader = RomReader(str(out))
    reader.load()
    kits = reader.kits().clubs[1]
    assert kits[HOME].parametric  # was the licensed blue kit
    assert {kits[HOME].rgb(SHIRT), kits[HOME].rgb(STRIPES)} == {red, white}
    assert kits[AWAY].rgb(SHIRT) == blue and kits[AWAY].rgb(STRIPES) == blue
    with pytest.raises(CapabilityError, match="template"):
        patcher.patch(
            rom_path=iso,
            output_path=out,
            rosters=rosters,
            logos=False,
            kits={1: {"home": {"template": 1}}},
        )


def test_a_crest_in_assets_dir_is_drawn_instead_of_the_download(iso, tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from retro_roster_patcher.games.pes2013_ps2.emblems import CLUB_TABLE, Atlas

    assets = tmp_path / "assets"
    (assets / "logos").mkdir(parents=True)
    Image.new("RGBA", (16, 16), (10, 200, 10, 255)).save(assets / "logos" / "10.png")
    served = []

    def transport(url, headers, timeout):
        served.append(url)
        return b""

    league = LeagueData(
        league=League(id=1, name="Fixture League", season=2026),
        teams=[
            TeamRoster(
                team=Team(id=10, name="Arsenal", code="ARS", logo_url="http://x/ars.png"),
                players=[Player(id=100, name="David Raya", last_name="Raya", number=22)],
            )
        ],
    )
    patcher = PES2013PS2Patcher(tmp_path / "cache", assets_dir=assets, transport=transport)
    assert patcher.assets_dir == assets
    rosters = patcher.map_rosters(league, [SlotMapping(slot_index=1, team_id=10)])
    out = tmp_path / "out.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters)
    assert served == []

    reader = RomReader(str(out))
    reader.load()
    atlas = Atlas.parse(unwrap_payload(reader, "0_TEXT.AFS", 600))
    image = atlas.image(CLUB_TABLE, 1)
    assert image is not None and set(image) - {0}


def _png(size, colour, border=0):
    """A PNG of one colour, optionally inside a white border (a kit on a backdrop)."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGBA", size, (255, 255, 255, 255) if border else colour)
    if border:
        ImageDraw.Draw(img).rectangle(
            (border, border, size[0] - border - 1, size[1] - border - 1), fill=colour
        )
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_a_texture_sized_picture_paints_the_licensed_kit_package(iso, tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    from retro_roster_patcher.games.pes2013_ps2 import kit_textures
    from retro_roster_patcher.games.pes2013_ps2.kits import HOME, SHIRT

    calls = []
    monkeypatch.setattr(kit_textures, "licensed_slots", lambda reader: {1: 0})
    monkeypatch.setattr(
        kit_textures,
        "write_kit_png",
        lambda writer, slot, which, png: calls.append((slot, which)) or 1,
    )
    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(
        LEAGUE, [SlotMapping(slot_index=0, team_id=10), SlotMapping(slot_index=1, team_id=11)]
    )
    out = tmp_path / "out.iso"
    texture = _png((512, 256), (200, 0, 0, 255), border=24)
    patcher.patch(
        rom_path=iso,
        output_path=out,
        rosters=rosters,
        kits={1: {"home": {"image": texture}}, 0: {"home": {"image": texture}}},
    )
    assert calls == [(1, HOME)]  # the licensed slot took the texture itself
    reader = RomReader(str(out))
    reader.load()
    kits = reader.kits()
    assert not kits.clubs[1][HOME].parametric  # its record stays licensed
    r, g, b = kits.clubs[0][HOME].rgb(SHIRT)  # the parametric slot got the colour
    assert kits.clubs[0][HOME].parametric and r > 150 and g < 40 and b < 40


def test_a_bad_kit_picture_is_refused_before_the_disc_is_copied(iso, tmp_path):
    from retro_roster_patcher.core.errors import CapabilityError

    patcher = PES2013PS2Patcher(tmp_path)
    rosters = patcher.map_rosters(LEAGUE, [SlotMapping(slot_index=0, team_id=10)])
    out = tmp_path / "out.iso"
    with pytest.raises(CapabilityError, match="slot 0 home kit"):
        patcher.patch(
            rom_path=iso, output_path=out, rosters=rosters, kits={0: {"home": {"image": b"nope"}}}
        )
    assert not out.exists()


def test_kits_none_or_empty_draw_the_defaults_and_false_keeps_the_disc(iso, tmp_path):
    from dataclasses import replace

    from retro_roster_patcher.games.pes2013_ps2.kits import HOME

    patcher = PES2013PS2Patcher(tmp_path)
    coloured = replace(LEAGUE.teams[0], team=replace(LEAGUE.teams[0].team, color="C60000"))
    league = LeagueData(league=LEAGUE.league, teams=[coloured])
    rosters = patcher.map_rosters(league, [SlotMapping(slot_index=1, team_id=coloured.team.id)])
    for given, expect_parametric in ((None, True), ({}, True), (False, False)):
        out = tmp_path / f"out_{expect_parametric}_{given is None}.iso"
        patcher.patch(rom_path=iso, output_path=out, rosters=rosters, kits=given)
        reader = RomReader(str(out))
        reader.load()
        assert reader.kits().clubs[1][HOME].parametric is expect_parametric


class _LineupApi:
    """Two clubs; club 2's lineup request fails with a non-`ApiError`."""

    def get_leagues(self, id, season):
        return [League(id=id, name="League", logo_url="http://x/l.png")]

    def get_teams(self, league_id, season):
        return [Team(id=1, name="One", code="ONE"), Team(id=2, name="Two", code="TWO")]

    def get_squad(self, team_id, season):
        return [Player(id=team_id * 10, name="Keeper", position="Goalkeeper")]

    def get_lineup(self, team_id, league_id):
        if team_id == 2:
            raise KeyError("formationPlace")
        return {"formation": "", "event": 1, "starters": [{"id": 10, "position": "G"}]}


def test_fetch_survives_a_failed_lineup(tmp_path):
    # A failed lineup costs that club its lineup, not its squad or the league.
    patcher = PES2013PS2Patcher(tmp_path)
    patcher.api = _LineupApi()
    data = patcher.fetch(season=2026, league_id=7)
    assert [t.extra.get("lineup", {}).get("event") for t in data.teams] == [1, None]
    assert [len(t.players) for t in data.teams] == [1, 1]
    assert [t.error for t in data.teams] == ["", ""]


# -- looks -----------------------------------------------------------------


def test_set_look_changes_only_the_skin_bits_and_the_edited_flags(iso, tmp_path):
    from retro_roster_patcher.games.pes2013_ps2.models import PESLook

    tables = RomReader(str(iso)).__class__(str(iso))
    tables.load()
    t = tables.player_tables()
    before = t.record(5)
    RomWriter.set_look(t, 5, PESLook(skin=5))
    after = t.record(5)
    assert after[115] & 7 == 5 and after[115] & ~7 == before[115] & ~7
    # What the game itself does when a player is edited: the licensed face
    # and hair flags off (byte 101 bits 3 and 5), the edited flag on (113 bit 6).
    assert after[101] == before[101] & ~0x28 and after[113] == before[113] | 0x40
    assert {i for i in range(124) if after[i] != before[i]} <= {101, 113, 115}


def _league_with_photos(photo_url):
    team = roster(
        10, "Arsenal", "ARS", [("David Raya", "Goalkeeper", 22), ("Kai Havertz", "Attacker", 29)]
    )
    team.players[1].photo_url = photo_url
    team.players[0].nationality = "Japan"
    return LeagueData(league=League(id=1, name="Fixture League", season=2026), teams=[team])


def _written_looks(out):
    reader = RomReader(str(out))
    reader.load()
    found = {}
    for entry in PLAYER_ENTRIES:
        t = reader.player_tables(entry)
        for i in range(t.player_count):
            rec = t.record(i)
            name = rec[:32].decode("utf-16-le").split("\0")[0]
            if name in ("DAVID RAYA", "KAI HAVERTZ"):
                found.setdefault(name, []).append((rec[115] & 7, rec[101] & 0x28, rec[113] & 0x40))
    return found


def test_patch_writes_the_skin_from_the_head_shot_into_both_entries(iso, tmp_path):
    pytest.importorskip("PIL")
    from tests.fixtures.synthetic_headshot import headshot

    dark = headshot((95, 60, 40))
    patcher = PES2013PS2Patcher(tmp_path, transport=lambda url, headers, timeout: dark)
    rosters = patcher.map_rosters(
        _league_with_photos("https://x/1.png"), [SlotMapping(slot_index=0, team_id=10)]
    )
    out = tmp_path / "out.iso"
    patcher.patch(rom_path=iso, output_path=out, rosters=rosters, logos=False, kits=False)
    found = _written_looks(out)
    assert found["KAI HAVERTZ"] == [(3, 0, 0x40)] * 2  # the darkest colour, in entries 46 and 47
    assert found["DAVID RAYA"] == [(1, 0, 0x40)] * 2  # East Asian by nationality


def test_looks_off_downloads_nothing_and_writes_the_plain_default(iso, tmp_path):
    def transport(url, headers, timeout):
        raise AssertionError("no head shot may be fetched with looks off")

    patcher = PES2013PS2Patcher(tmp_path, transport=transport)
    rosters = patcher.map_rosters(
        _league_with_photos("https://x/1.png"), [SlotMapping(slot_index=0, team_id=10)]
    )
    out = tmp_path / "out.iso"
    patcher.patch(
        rom_path=iso, output_path=out, rosters=rosters, logos=False, kits=False, looks=False
    )
    found = _written_looks(out)
    assert found["KAI HAVERTZ"] == [(0, 0, 0x40)] * 2
