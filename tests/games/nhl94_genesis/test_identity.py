"""NHL 94 (Genesis) as 32 clubs: `identity.expand` and `identity.write`, on a
synthetic image laid out like the cartridge."""

from __future__ import annotations

import io
import struct
from dataclasses import replace

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.nhl94_genesis import identity
from retro_roster_patcher.games.nhl94_genesis.rom_reader import NHL94GenesisRomReader
from tests.fixtures import synthetic_rom


def _u32(rom, at):
    return struct.unpack_from(">I", rom, at)[0]


def _block(rom, slot):
    return _u32(rom, 0x30E + 4 * slot)


def _strings(rom, slot):
    base = _block(rom, slot)
    at = base + struct.unpack_from(">H", rom, base + 4)[0]
    out = []
    for _ in range(4):
        length = struct.unpack_from(">H", rom, at)[0]
        out.append(bytes(rom[at + 2 : at + length]).rstrip(b"\0").decode())
        at += length
    return out


@pytest.fixture
def cartridge():
    return synthetic_rom.build_nhl94_genesis_cartridge()


def test_expand_lays_out_the_clubs_six_new_ones_and_the_all_stars(cartridge):
    assert identity.expand(cartridge) is True
    assert len(cartridge) == 0x200000
    for slot, source in enumerate(identity.SOURCE):
        base = _block(cartridge, slot)
        assert base == identity.TEAM_REGION.start + identity.STRIDE * slot
        block = synthetic_rom._cartridge_block(source)
        # Slot 26's source is the block at $37E, which the grown table overlaps.
        assert bytes(cartridge[base : base + len(block)]) == block
    assert identity.SOURCE[26:32] == (26,) * 6
    assert identity.SOURCE[32:] == (26, 27)


def test_expand_copies_every_table_by_source_and_repoints_its_loads(cartridge):
    identity.expand(cartridge)
    for table in identity.TABLES:
        assert all(_u32(cartridge, site) == table.at for site in table.sites)
        for slot, source in enumerate(identity.SOURCE):
            at = table.at + table.size * slot
            if table is identity.RATINGS and 26 <= slot < 32:
                assert cartridge[at] == identity.NEW_RATING
            else:
                assert cartridge[at : at + table.size] == bytes([source]) * table.size


def test_expand_raises_the_counts_and_gives_each_banner_its_own_records(cartridge):
    identity.expand(cartridge)
    assert cartridge[0xF7922:0xF7926] == bytes.fromhex("00220022")
    assert cartridge[0xF79E4:0xF79E6] == bytes.fromhex("7820")
    assert cartridge[0x18032:0x18034] == bytes.fromhex("7020")
    # movea.l #BANNERS,a0 in the new routine at $17B78.
    assert _u32(cartridge, 0x17B82) == identity.BANNERS
    for slot in range(identity.SLOTS):
        home, away = (
            _u32(cartridge, identity.BANNERS + 8 * slot),
            _u32(cartridge, identity.BANNERS + 8 * slot + 4),
        )
        assert home in identity.HEAP and away in identity.HEAP
        assert struct.unpack_from(">H", cartridge, home + 8)[0] <= 22
    sheet = _u32(cartridge, identity.STRIPE_SITES[0])
    assert sheet in identity.HEAP
    assert _u32(cartridge, identity.STRIPE_TILE_SITES[0]) == sheet + 8
    width, height = struct.unpack_from(">HH", cartridge, sheet + _u32(cartridge, sheet + 4))
    assert (width, height) == (11, 2 * identity.SLOTS)


def test_expand_twice_changes_nothing(cartridge):
    identity.expand(cartridge)
    once = bytes(cartridge)
    assert identity.expand(cartridge) is True
    assert bytes(cartridge) == once


def test_an_image_whose_code_is_not_the_cartridges_is_left_alone(cartridge):
    cartridge[0xF79E4] = 0x4E
    before = bytes(cartridge)
    assert identity.expand(cartridge) is False
    assert bytes(cartridge) == before


def test_the_reader_counts_32_clubs_once_expanded(cartridge, tmp_path):
    path = tmp_path / "rom.md"
    path.write_bytes(bytes(cartridge))
    reader = NHL94GenesisRomReader(str(path))
    reader.load()
    assert reader.team_count() == 26
    identity.expand(cartridge)
    path.write_bytes(bytes(cartridge))
    reader = NHL94GenesisRomReader(str(path))
    reader.load()
    assert reader.team_count() == 32
    assert [s.display_name for s in reader.get_info().team_slots][26:] == [
        "Columbus", "Minnesota", "Nashville", "Seattle", "Utah", "Vegas",
    ]  # fmt: skip


def test_write_names_and_dresses_a_slot(cartridge):
    identity.expand(cartridge)
    team = identity.identity_of("SEA")
    seen = []
    identity.write(cartridge, {29: team}, seen.append)
    assert _strings(cartridge, 29) == ["Seattle", "SEA", "Kraken", "Climate Pledge Arena"]
    base = _block(cartridge, 29)
    palettes = struct.unpack_from(">32H", cartridge, base + 0xC)
    assert palettes[12] == 0x0EEE  # home: a white jersey
    assert palettes[16 + 12] != 0x0EEE  # away: the team colour
    logo_palette = struct.unpack_from(">16H", cartridge, identity.LOGO_PALETTES.at + 32 * 29)
    banner = struct.unpack_from(">32H", cartridge, identity.BANNER_PALETTES.at + 64 * 29)
    assert banner[1:3] == logo_palette[1:3] == banner[16 + 3 : 16 + 5]
    assert seen == []


def test_a_name_too_long_for_its_block_is_kept(cartridge):
    identity.expand(cartridge)
    team = identity.identity_of("SEA")
    long = identity.Identity(
        team.code, identity.Club("X" * 900, "Kraken", "Arena", "000000", "ffffff", False),
        team.color, team.alternate,
    )  # fmt: skip
    before = _strings(cartridge, 29)
    seen = []
    identity.write(cartridge, {29: long}, seen.append)
    assert _strings(cartridge, 29) == before
    assert any("too long" in line for line in seen)


def _png(colour):
    image = pytest.importorskip("PIL.Image")
    picture = image.new("RGBA", (64, 64), (0, 0, 0, 0))
    for x in range(8, 56):
        for y in range(8, 56):
            picture.putpixel((x, y), (*colour, 255))
    out = io.BytesIO()
    picture.save(out, "PNG")
    return out.getvalue()


def test_a_logo_goes_into_the_heap_and_survives_a_patch_without_one(cartridge):
    identity.expand(cartridge)
    team = identity.identity_of("VGK")
    identity.write(cartridge, {31: replace(team, logo=_png((180, 150, 90)))}, print)
    logo = _u32(cartridge, identity.LOGOS.at + 4 * 31)
    ice = _u32(cartridge, identity.ICE.at + 4 * 31)
    assert logo in identity.HEAP and ice in identity.HEAP
    tiles = struct.unpack_from(">H", cartridge, logo + 8)[0]
    assert 1 <= tiles <= 36
    assert struct.unpack_from(">H", cartridge, ice + 8)[0] == 24
    record = bytes(cartridge[logo : logo + identity._record_size(cartridge, logo)])

    identity.write(cartridge, {0: identity.identity_of("ANA")}, print)
    again = _u32(cartridge, identity.LOGOS.at + 4 * 31)
    assert bytes(cartridge[again : again + len(record)]) == record
    assert _u32(cartridge, identity.LOGOS.at) not in identity.HEAP


def test_write_refuses_an_image_that_is_not_expanded(cartridge):
    with pytest.raises(RomError, match="32 clubs"):
        identity.write(cartridge, {}, print)


def test_every_club_plays_in_some_playoff_bracket_of_its_conference(cartridge):
    identity.expand(cartridge)
    seeds = cartridge[identity.SEEDS : identity.SEEDS + 16 * identity.SEED_TABLES]
    tables = [seeds[16 * k : 16 * k + 16] for k in range(identity.SEED_TABLES)]
    assert all(len(set(t)) == 16 for t in tables)
    assert set(seeds) == set(range(identity.CLUB_SLOTS))
    east = {slot for slot, code in identity.SLOT_CODES.items() if identity.CLUBS[code].east}
    assert all(set(t[:8]) <= east and not set(t[8:]) & east for t in tables)
    assert cartridge[0x17D34:0x17D38] == bytes.fromhex("b47c001f")
