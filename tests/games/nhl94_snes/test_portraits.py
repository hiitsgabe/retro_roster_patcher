"""Player portraits on synthetic data: the cartridge's table layout, built here."""

from __future__ import annotations

import random
import sys
from collections import Counter

import pytest

from retro_roster_patcher.core.models import MappedRosters
from retro_roster_patcher.formats import snes
from retro_roster_patcher.formats.ea_tdb import refpack_compress, refpack_decompress
from retro_roster_patcher.games.nhl94_snes import portraits
from retro_roster_patcher.games.nhl94_snes.models import NHL94PlayerRecord, NHL94TeamRecord
from retro_roster_patcher.games.nhl94_snes.patcher import NHL94SNESPatcher
from retro_roster_patcher.games.nhl94_snes.rom_reader import NHL94SNESRomReader
from retro_roster_patcher.games.nhl94_snes.rom_writer import LINE_ASSIGN_OFFSET
from tests.fixtures import synthetic_snes_rom as fixture

SKIN = (240, 184, 152)
TEAM_GREY = (200, 200, 200)
PALETTE_AT = 0xED940  # free in the fixture, after the palette pointers
PNG = b"\x89PNG\r\n\x1a\n"


def _word(rgb):
    r, g, b = rgb
    return (r >> 3 | (g >> 3) << 5 | (b >> 3) << 10).to_bytes(2, "little")


def with_portraits(rom: bytearray, flipped: frozenset[int] = frozenset()) -> bytearray:
    """`rom` (headerless) with the cartridge's portrait layout: every entry on
    a skater drawing, the four drawings as stream headers, one palette for
    every team (1-12 a skin ramp from `SKIN` down, 13 grey, 14 red, 15 blue)
    but the `flipped` teams', whose ramp runs the other way."""
    for address in (portraits.GENERIC_GOALIE, *portraits.GENERIC_SKATERS):
        at = portraits._off(address)
        rom[at : at + 5] = b"\x30\xfb" + portraits.RAW_SIZE.to_bytes(3, "big")
    for i in range(portraits.TEAMS * portraits.RECORDS):
        at = portraits.TABLE + 4 * i
        rom[at : at + 4] = portraits.GENERIC_SKATERS[0].to_bytes(3, "little") + b"\0"
    ramp = [(SKIN[0] - 16 * i, SKIN[1] - 14 * i, SKIN[2] - 12 * i) for i in range(12)]
    colours = [(248, 0, 248), *ramp, TEAM_GREY, (200, 0, 0), (0, 0, 200)]
    flip = [colours[0], *ramp[::-1], *colours[13:]]
    rom[PALETTE_AT : PALETTE_AT + 64] = b"".join(_word(c) for c in colours + flip)
    for team in range(portraits.TEAMS):
        at = portraits.PALETTES + 4 * team
        own = PALETTE_AT + 32 * (team in flipped)
        rom[at : at + 4] = portraits._cpu(own).to_bytes(3, "little") + b"\0"
    return rom


def entry(rom, team, record):
    at = portraits._entry(team, record)
    return int.from_bytes(rom[at : at + 3], "little")


# -- the table ---------------------------------------------------------------


def test_the_fixture_without_a_table_is_not_taken_for_one():
    assert not portraits.table_ok(fixture.build_nhl94_snes_rom())


def test_the_built_table_is_taken_for_one():
    assert portraits.table_ok(with_portraits(fixture.build_nhl94_snes_rom()))


def test_an_entry_pointing_at_anything_but_a_portrait_stream_spoils_the_table():
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    at = portraits._entry(5, 7)
    rom[at : at + 3] = portraits._cpu(PALETTE_AT).to_bytes(3, "little")
    assert not portraits.table_ok(rom)


def test_the_palette_is_read_through_the_teams_pointer():
    colours = portraits.palette(with_portraits(fixture.build_nhl94_snes_rom()), 11)
    assert colours[1] == SKIN and colours[13] == TEAM_GREY


# -- order ---------------------------------------------------------------------


def test_the_line_tables_records_come_first_and_teams_take_turns():
    best = bytes([1, 9, 10, 3, 4, 5, 6, 0, 11, 0])  # 11 is past the roster: ignored
    order = portraits.ranked({1: best}, {1: 10, 4: 3})
    assert order == [
        (1, 0), (4, 0), (1, 8), (4, 1), (1, 9), (4, 2),
        (1, 2), (1, 3), (1, 4), (1, 5), (1, 1), (1, 6), (1, 7),
    ]  # fmt: skip


# -- drawings, no Pillow needed --------------------------------------------------


def test_players_without_head_shots_get_the_drawings_and_the_image_stays_1_mib():
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    before = bytes(rom)
    players = {3: [(True, "u"), (True, "")] + [(False, "")] * 4}
    assert portraits.write(rom, players, {}) == (0, 6)  # no `load`: no head shots
    assert len(rom) == len(before)
    assert [entry(rom, 3, r) for r in range(6)] == [
        portraits.GENERIC_GOALIE,
        portraits.GENERIC_GOALIE,
        portraits.GENERIC_SKATERS[2],
        portraits.GENERIC_SKATERS[0],
        portraits.GENERIC_SKATERS[1],
        portraits.GENERIC_SKATERS[2],
    ]
    changed = {i for i, (a, b) in enumerate(zip(before, rom, strict=True)) if a != b}
    first, last = portraits._entry(3, 0), portraits._entry(3, 6)
    assert changed and all(first <= i < last for i in changed)


def test_records_past_the_26th_have_no_entry_and_are_left_alone():
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    before = entry(rom, 4, 0)
    portraits.write(rom, {3: [(False, "")] * 30}, {})
    assert entry(rom, 4, 0) == before


def test_a_record_an_earlier_patch_drew_and_this_one_does_not_gets_a_drawing():
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    snes.grow(rom, 0x200000)
    for record, offset in ((0, 0x1E0000), (5, 0x1E8000)):
        at = portraits._entry(9, record)
        rom[at : at + 3] = portraits._cpu(offset).to_bytes(3, "little")
    portraits.write(rom, {3: [(False, "")]}, {})
    assert entry(rom, 9, 0) == portraits.GENERIC_GOALIE
    assert entry(rom, 9, 5) == portraits.GENERIC_SKATERS[2]
    assert rom[0x1E0000:0x200000] == b"\xff" * 0x20000


# -- head shots ------------------------------------------------------------------


def _image(rgba):
    from PIL import Image

    return Image.frombytes("RGBA", (48, 48), bytes(rgba))


def test_encode_lays_out_6x6_tiles_of_packed_nibbles():
    pytest.importorskip("PIL")
    colours = [(0, 0, 0)] * 16
    colours[1], colours[2] = (10, 10, 10), (90, 90, 90)
    pixels = []
    for y in range(48):
        for x in range(48):
            pixels += [0, 0, 0, 0] if (x, y) == (0, 0) else [*colours[1 if x < 24 else 2], 255]
    raw = portraits.encode(_image(pixels), colours)
    assert len(raw) == 0x480
    assert raw[0] == 0x01 and raw[1:4] == b"\x11\x11\x11"  # tile 0, row 0
    assert raw[3 * 32 : 3 * 32 + 4] == b"\x22" * 4  # tile 3 is the right half
    assert raw[6 * 32] == 0x11  # tile 6 starts the second tile row


def test_a_skin_highlight_stays_skin_and_only_a_clear_match_takes_a_team_colour():
    pytest.importorskip("PIL")
    colours = [(0, 0, 0)] + [SKIN] * 12 + [TEAM_GREY] * 3
    pixels = [225, 200, 190, 255] * (48 * 24) + [*TEAM_GREY, 255] * (48 * 24)
    raw = portraits.encode(_image(pixels), colours)
    assert raw[0] >> 4 == 1  # top half: skin
    assert raw[-1] & 15 == 13  # bottom half: the team's grey


def test_a_head_shot_is_cropped_to_48_square():
    pytest.importorskip("PIL")
    import io

    from PIL import Image, ImageDraw

    picture = Image.new("RGBA", (300, 220), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((120, 20, 200, 120), fill=SKIN)
    ImageDraw.Draw(picture).rectangle((60, 120, 260, 220), fill=TEAM_GREY)
    png = io.BytesIO()
    picture.save(png, "PNG")
    portrait = portraits.headshot_portrait(png.getvalue())
    assert portrait.size == (48, 48)
    assert portrait.getpixel((0, 0))[3] == 0  # background stays transparent
    assert portrait.getpixel((24, 10))[3] == 255  # the head, near the top centre


def _png(image):
    import io

    png = io.BytesIO()
    image.save(png, "PNG")
    return png.getvalue()


def _noise(seed):
    rng = random.Random(seed)
    return _image(rng.randrange(256) if i % 4 != 3 else 255 for i in range(48 * 48 * 4))


def test_head_shots_go_in_as_refpack_until_the_room_runs_out(monkeypatch):
    """Two 32 KiB banks' worth of edge: no stream crosses one, and the faces
    that find no room get the drawing."""
    pytest.importorskip("PIL")
    monkeypatch.setattr(portraits, "REGION", range(0x1F7800, 0x1F8800))
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    faces = [_noise(i) for i in range(6)]
    fetched = []

    def load(urls):
        fetched.extend(urls)
        return [faces[int(url)] for url in urls]

    monkeypatch.setattr(portraits, "_BATCH", 1)
    players = {7: [(False, str(i)) for i in range(6)]}
    drawn, drawings = portraits.write(rom, players, {}, load)
    assert len(rom) == 0x200000 and rom[snes.SIZE_BYTE] == 11
    assert drawn == 2 and drawings == 4
    colours = portraits.palette(rom, 7)
    for record in range(2):
        at = portraits._off(entry(rom, 7, record))
        assert at in portraits.REGION
        data = refpack_decompress(bytes(rom[at:]))
        assert data == portraits.encode(faces[record], colours)
    starts = [portraits._off(entry(rom, 7, r)) for r in range(2)]
    assert starts[0] == 0x1F7800 and starts[1] == 0x1F8000  # skipped the bank's end
    assert [entry(rom, 7, r) for r in range(2, 6)] == [
        portraits.GENERIC_SKATERS[r % 3] for r in range(2, 6)
    ]
    assert fetched == ["0", "1", "2"]  # the third did not fit: nothing more is fetched


def test_an_image_larger_than_2_mib_gets_drawings_and_keeps_its_region():
    """nhl94e grows the cartridge to 4 MiB: the region is its own."""
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    snes.grow(rom, 0x400000)
    rom[portraits.REGION.start] = 0x42
    drawn = portraits.write(rom, {3: [(False, "u")]}, {}, lambda urls: [None] * len(urls))
    assert drawn == (0, 1) and len(rom) == 0x400000
    assert rom[portraits.REGION.start] == 0x42
    assert entry(rom, 3, 0) == portraits.GENERIC_SKATERS[0]


def test_portraits_false_sends_an_earlier_patchs_faces_back_to_the_drawing():
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    snes.grow(rom, 0x200000)
    for team, record in ((3, 0), (3, 4), (9, 2)):
        at = portraits._entry(team, record)
        rom[at : at + 3] = portraits._cpu(0x1E0000).to_bytes(3, "little")
    region = bytes(rom[portraits.REGION.start : portraits.REGION.stop])
    portraits.forget(rom, {3: [(True, "u")] + [(False, "u")] * 5})
    assert entry(rom, 3, 0) == portraits.GENERIC_GOALIE
    assert entry(rom, 3, 4) == portraits.GENERIC_SKATERS[1]
    assert entry(rom, 3, 1) == portraits.GENERIC_SKATERS[0]  # the cartridge's, kept
    assert portraits._off(entry(rom, 9, 2)) in portraits.REGION  # not patched: still his
    assert rom[portraits.REGION.start : portraits.REGION.stop] == region


# -- through the patcher ------------------------------------------------------------


def _rosters(photo="https://example.test/head.png", slots=(1,)):
    """Eight players a slot, two goalies, three forwards and three defencemen;
    `photo` formatted with the slot and the player's index."""
    teams = {}
    for slot in slots:
        players = [
            NHL94PlayerRecord(
                name=f"Player {i}",
                jersey_number=i + 1,
                is_goalie=i < 2,
                photo_url=photo.format(slot=slot, i=i),
            )
            for i in range(8)
        ]
        teams[slot] = NHL94TeamRecord(
            index=slot, name="Boston", city="", acronym="", players=players, num_forwards=3
        )
    return MappedRosters(game_id="nhl94-snes", teams=teams)


def _rom_file(tmp_path):
    path = tmp_path / "nhl94.sfc"
    path.write_bytes(with_portraits(fixture.build_nhl94_snes_rom()))
    return path


def _head_shot(colour=SKIN):
    import io

    from PIL import Image, ImageDraw

    picture = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((40, 5, 80, 60), fill=colour)
    png = io.BytesIO()
    picture.save(png, "PNG")
    return png.getvalue()


def _colour(rom, team, record):
    """The colour index most of the record's head shot is drawn in."""
    raw = refpack_decompress(bytes(rom[portraits._off(entry(rom, team, record)) :]))
    return Counter(n for b in raw for n in (b >> 4, b & 15) if n).most_common(1)[0][0]


def test_patching_draws_each_players_head_shot(tmp_path):
    pytest.importorskip("PIL")
    shot = _head_shot()
    calls = []

    def transport(url, headers, timeout):
        calls.append(url)
        return shot

    seen = []
    patcher = NHL94SNESPatcher(tmp_path / "cache", on_status=seen.append, transport=transport)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=_rom_file(tmp_path), output_path=out, rosters=_rosters())
    rom = out.read_bytes()
    assert len(rom) == 0x200000
    assert all(portraits._off(entry(rom, 1, r)) in portraits.REGION for r in range(8))
    assert snes.fix_checksum(rom) == rom
    assert calls == ["https://example.test/head.png"]  # one download per head shot
    assert "Loading head shots..." in seen
    assert "Portraits drawn: 8 head shots, 0 drawings" in seen


SHOTS = "https://example.test/{slot}/{i}.png"


def _shots_by_ramp(rom):
    """Player i's head shot a disc of skin tone i + 1, the same for every slot."""
    colours = portraits.palette(rom, 0)
    return {i: _head_shot(colours[i + 1]) for i in range(8)}


def test_each_record_gets_its_own_players_face_in_its_teams_colours(tmp_path):
    pytest.importorskip("PIL")
    rom = with_portraits(fixture.build_nhl94_snes_rom(), flipped=frozenset({2}))
    source = tmp_path / "nhl94.sfc"
    source.write_bytes(rom)
    shots = _shots_by_ramp(rom)

    def transport(url, headers, timeout):
        return shots[int(url.rsplit("/", 1)[1].split(".")[0])]

    out = tmp_path / "out.sfc"
    NHL94SNESPatcher(tmp_path / "cache", transport=transport).patch(
        rom_path=source, output_path=out, rosters=_rosters(SHOTS, slots=(1, 2))
    )
    patched = out.read_bytes()
    assert [_colour(patched, 1, r) for r in range(8)] == [r + 1 for r in range(8)]
    assert [_colour(patched, 2, r) for r in range(8)] == [12 - r for r in range(8)]


def test_the_best_line_gets_the_room_first_and_nothing_past_it_is_fetched(tmp_path, monkeypatch):
    """Room for six faces: the six of the line table's Best line get them, the
    backup goalie the drawing, and only one head shot past the room is fetched."""
    pytest.importorskip("PIL")
    rom = with_portraits(fixture.build_nhl94_snes_rom())
    source = tmp_path / "nhl94.sfc"
    source.write_bytes(rom)
    shots = {i: _png(_noise(i)) for i in range(8)}  # incompressible: about one size
    sizes = [
        len(refpack_compress(portraits.encode(face, portraits.palette(rom, 1))))
        for face in map(portraits.headshot_portrait, shots.values())
        if face is not None
    ]
    assert len(sizes) == 8 and 7 * min(sizes) > 6 * max(sizes)  # any six fit, no seven
    monkeypatch.setattr(portraits, "REGION", range(0x1F0000, 0x1F0000 + 6 * max(sizes)))
    monkeypatch.setattr(portraits, "_BATCH", 1)
    fetched = []

    def transport(url, headers, timeout):
        fetched.append(int(url.rsplit("/", 1)[1].split(".")[0]))
        return shots[fetched[-1]]

    out = tmp_path / "out.sfc"
    NHL94SNESPatcher(tmp_path / "cache", transport=transport).patch(
        rom_path=source, output_path=out, rosters=_rosters(SHOTS)
    )
    reader = NHL94SNESRomReader(str(out))
    assert reader.load()
    patched = out.read_bytes()
    at = (reader._read_team_pointer(1) or 0) + LINE_ASSIGN_OFFSET
    best = {b - 1 for b in patched[at : at + 6]}
    assert len(best) == 6 and best != set(range(6))  # not simply the first six records
    faced = {r for r in range(8) if portraits._off(entry(patched, 1, r)) in portraits.REGION}
    assert faced == best
    assert entry(patched, 1, 1) == portraits.GENERIC_GOALIE
    assert set(fetched[:6]) == best and len(fetched) == 7


def _grey_silhouette():
    import io

    from PIL import Image, ImageDraw

    picture = Image.new("RGBA", (120, 100), (0, 0, 0, 0))
    ImageDraw.Draw(picture).ellipse((40, 5, 80, 60), fill=(150, 150, 150))
    ImageDraw.Draw(picture).rectangle((20, 60, 100, 100), fill=(190, 190, 190))
    png = io.BytesIO()
    picture.save(png, "PNG")
    return png.getvalue()


def test_the_nhl_apis_grey_silhouette_is_no_head_shot(tmp_path):
    pytest.importorskip("PIL")
    assert portraits.headshot_portrait(_grey_silhouette()) is None
    assert portraits.headshot_portrait(_head_shot()) is not None
    seen = []

    def transport(url, headers, timeout):
        return _grey_silhouette() if "/1/2." in url else _head_shot()

    out = tmp_path / "out.sfc"
    NHL94SNESPatcher(tmp_path / "cache", on_status=seen.append, transport=transport).patch(
        rom_path=_rom_file(tmp_path), output_path=out, rosters=_rosters(SHOTS)
    )
    rom = out.read_bytes()
    assert entry(rom, 1, 2) == portraits.GENERIC_SKATERS[2]
    assert portraits._off(entry(rom, 1, 3)) in portraits.REGION
    assert "Portraits drawn: 7 head shots, 1 drawings" in seen


def test_without_pillow_the_new_players_get_the_drawings(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    seen = []
    patcher = NHL94SNESPatcher(tmp_path / "cache", on_status=seen.append)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=_rom_file(tmp_path), output_path=out, rosters=_rosters())
    rom = out.read_bytes()
    assert len(rom) == fixture.ROM_SIZE
    assert entry(rom, 1, 0) == portraits.GENERIC_GOALIE
    assert entry(rom, 1, 2) == portraits.GENERIC_SKATERS[2]
    assert "Portraits: Pillow is not installed; new players get the game's drawings" in seen


def test_without_head_shots_there_is_nothing_to_say(tmp_path):
    seen = []
    patcher = NHL94SNESPatcher(tmp_path / "cache", on_status=seen.append)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=_rom_file(tmp_path), output_path=out, rosters=_rosters(photo=""))
    assert entry(out.read_bytes(), 1, 1) == portraits.GENERIC_GOALIE
    assert not [line for line in seen if "ortrait" in line or "head shot" in line]


def test_a_copier_header_is_stepped_over(tmp_path):
    source = tmp_path / "nhl94.smc"
    source.write_bytes(
        bytes(fixture.SMC_HEADER_SIZE) + with_portraits(fixture.build_nhl94_snes_rom())
    )
    out = tmp_path / "out.smc"
    NHL94SNESPatcher(tmp_path / "cache").patch(
        rom_path=source, output_path=out, rosters=_rosters(photo="")
    )
    assert entry(out.read_bytes()[fixture.SMC_HEADER_SIZE :], 1, 0) == portraits.GENERIC_GOALIE


def test_portraits_false_on_a_patched_rom_keeps_no_earlier_face(tmp_path):
    pytest.importorskip("PIL")
    first = tmp_path / "first.sfc"
    NHL94SNESPatcher(tmp_path / "cache", transport=lambda *_: _head_shot()).patch(
        rom_path=_rom_file(tmp_path), output_path=first, rosters=_rosters()
    )
    assert portraits._off(entry(first.read_bytes(), 1, 0)) in portraits.REGION
    out = tmp_path / "out.sfc"
    NHL94SNESPatcher(tmp_path / "cache").patch(
        rom_path=first, output_path=out, rosters=_rosters(), portraits=False
    )
    rom = out.read_bytes()
    assert entry(rom, 1, 0) == portraits.GENERIC_GOALIE
    assert entry(rom, 1, 3) == portraits.GENERIC_SKATERS[0]


def test_portraits_false_keeps_the_cartridges(tmp_path):
    source = _rom_file(tmp_path)
    out = tmp_path / "out.sfc"
    NHL94SNESPatcher(tmp_path / "cache").patch(
        rom_path=source, output_path=out, rosters=_rosters(photo=""), portraits=False
    )
    table = slice(portraits.TABLE, portraits.PALETTES)
    assert out.read_bytes()[table] == source.read_bytes()[table]
