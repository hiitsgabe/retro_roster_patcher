"""`games.nhl94_snes.league`: the cartridge laid out as 32 clubs, on the
synthetic ROM given the cartridge's code and table addresses."""

from __future__ import annotations

import struct

import pytest

from retro_roster_patcher.formats.ea_tdb import refpack_decompress
from retro_roster_patcher.games.nhl94_snes import league
from tests.fixtures import synthetic_snes_rom as fixture


def _retail():
    """The fixture laid out like the cartridge, not yet expanded, each moved
    table's rows numbered by slot, and slot 26's block right after the team
    list, where the cartridge has All-Stars East's."""
    rom = fixture.build_nhl94_snes_rom()
    # Packed together, as on the cartridge, leaving the bank's padding free.
    table = league._file(league.TEAM_LIST)
    bank = table - table % league.BANK
    bases = [bank + (league._u16(rom, table + 4 * s) & 0x7FFF) for s in range(28)]
    blocks = [bytes(rom[b : league._block_end(rom, b)]) for b in bases]
    end = fixture.BANK_WINDOW_START + fixture.BANK_WINDOW_SIZE
    rom[fixture.TEAM_BLOCK_BASE : end] = b"\xff" * (end - fixture.TEAM_BLOCK_BASE)
    at = fixture.TEAM_BLOCK_BASE
    for slot, block in enumerate(blocks):
        rom[at : at + len(block)] = block
        rom[table + 4 * slot : table + 4 * slot + 2] = (0x8000 + at % 0x8000).to_bytes(2, "little")
        at += len(block)
    real = league.expand
    league.expand = lambda r: True  # lay out without expanding
    try:
        fixture.expand_like_the_cartridge(rom)
    finally:
        league.expand = real
    for t in league.MOVED:
        src = league._file(t.retail)
        for slot in range(league.RETAIL_SLOTS + t.extra):
            rom[src + t.size * slot : src + t.size * (slot + 1)] = bytes([slot]) * t.size
    base = bank + (league._u16(rom, table + 4 * 26) & 0x7FFF)
    block = bytes(rom[base : league._block_end(rom, base)])
    at = table + 4 * league.RETAIL_SLOTS
    rom[base : base + len(block)] = b"\xff" * len(block)
    rom[at : at + len(block)] = block
    rom[table + 4 * 26 : table + 4 * 26 + 4] = league._cpu(at).to_bytes(3, "little") + b"\0"
    return rom, block


def test_the_fixture_is_the_cartridges_layout():
    rom, _ = _retail()
    assert league._state(rom) == "retail" and not league.expanded(rom)


def test_every_table_grows_to_32_and_its_loads_follow_it():
    rom, _ = _retail()
    assert league.expand(rom) is True
    assert len(rom) == league.TWO_MIB and league.expanded(rom)
    for t in league.MOVED:
        for at, value in zip(league._operands(t), league._sites(t, t.cpu), strict=True):
            assert rom[at : at + len(value)] == value
        rows = [rom[t.at + t.size * s : t.at + t.size * (s + 1)] for s in range(t.entries)]
        if t is league.BRACKET_COLOURS:
            assert rows[:27] == [bytes([s]) * 2 for s in range(27)]
            assert rows[27] == b"\x7c\x7e"
            assert rows[28:] == [rows[14], rows[5], rows[2], rows[26]]
            continue
        assert rows[:28] == [bytes([s]) * t.size for s in range(28)]
        new = b"\xff" * t.size if t is league.SONGS else bytes([26]) * t.size
        assert rows[28:32] == [new] * 4
        assert rows[32:] == [bytes([28 + k]) * t.size for k in range(t.extra)]
    for cpu, _old, new in league.CODE:
        at = league._file(cpu)
        assert rom[at : at + len(new) // 2].hex() == new


def test_the_block_the_longer_list_overlaps_moves_and_the_new_slots_share_it():
    rom, block = _retail()
    league.expand(rom)
    table = league._file(league.TEAM_LIST)
    pointers = [league._long(rom, table + 4 * s) for s in range(league.SLOTS)]
    moved = league._file(pointers[26])
    assert moved == league._file(league.BANK_9C_PADDING)
    assert rom[moved : moved + len(block)] == block
    assert pointers[28:] == [pointers[26]] * 4
    assert all(rom[table + 4 * s + 3] == 0 for s in range(league.SLOTS))


def test_the_banner_names_follow_the_longer_pointer_list():
    rom, _ = _retail()
    league.expand(rom)
    pointers = league._file(league.BANNER_POINTERS)
    bank = pointers - pointers % league.BANK
    names = [
        bytes(rom[bank + league._u16(rom, pointers + 2 * s) - 0x8000 :].split(b"\0", 1)[0])
        for s in range(league.SLOTS)
    ]
    assert names[:28] == [f"Old {s}".encode() for s in range(28)]
    assert names[28:] == [b"Old 26"] * 4
    starts = {league._u16(rom, pointers + 2 * s) for s in range(league.SLOTS)}
    assert min(starts) >= league.BANNER_LIST & 0xFFFF


def test_the_logo_archive_has_34_entries_the_league_logos_last():
    rom, _ = _retail()
    league.expand(rom)
    site = league._file(league.LOGO_LOADERS[0])
    address = rom[site + 1] << 16 | league._u16(rom, site + 6)
    assert league._file(address) in league.TABLES
    archive = refpack_decompress(bytes(rom[league._file(address) :]))
    assert len(archive) == league.ARCHIVE_SIZE
    entries = league.archive_entries(archive, league.SLOTS + 2)
    old = [bytes([k + 1]) * (40 + k) for k in range(30)]
    assert entries[:27] == old[:27]
    assert entries[27:32] == [old[26]] * 5
    assert entries[32:] == old[28:]


def test_expanding_twice_changes_nothing():
    rom, _ = _retail()
    league.expand(rom)
    once = bytes(rom)
    assert league.expand(rom) is True
    assert bytes(rom) == once


@pytest.mark.parametrize("where", [league.CODE[0][0], league.CENTER_ICE.longs[0][0]])
def test_an_image_not_laid_out_like_the_cartridge_is_left_alone(where):
    rom, _ = _retail()
    rom[league._file(where) + 1] ^= 0xFF
    before = bytes(rom)
    assert league.expand(rom) is False
    assert bytes(rom) == before


def test_a_shared_entry_is_stored_once():
    archive = league.build_archive([b"aa", b"bbb", b"aa"])
    assert struct.unpack_from("<3I", archive) == (12, 14, 12)
    assert league.archive_entries(archive, 3) == [b"aa", b"bbb", b"aa"]
