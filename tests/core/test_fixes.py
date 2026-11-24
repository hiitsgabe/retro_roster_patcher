"""`core.fixes.set_fix`, and every game's fix table against it."""

import pytest

from retro_roster_patcher.core.fixes import set_fix
from retro_roster_patcher.games.nhl94_genesis.rom_writer import FIXES as GENESIS
from retro_roster_patcher.games.nhl94_snes.rom_writer import FIXES as SNES

TABLE = {"a_fix": [(4, "0102", "0304"), (10, "ff" * 3, "aabbcc")]}


def _retail(base=0):
    rom = bytearray(32)
    rom[base + 4 : base + 6] = b"\x01\x02"
    rom[base + 10 : base + 13] = b"\xff" * 3
    return rom


@pytest.mark.parametrize("base", [0, 8])
def test_a_fix_is_written_past_the_base_and_undone(base):
    rom = _retail(base)
    retail = bytes(rom)
    assert set_fix(rom, TABLE, "a_fix", True, base)
    assert rom[base + 4 : base + 6] == b"\x03\x04" and rom[base + 10 : base + 13] == b"\xaa\xbb\xcc"
    assert set_fix(rom, TABLE, "a_fix", False, base)
    assert bytes(rom) == retail


def test_one_foreign_byte_and_nothing_is_written():
    rom = _retail()
    rom[11] = 0
    before = bytes(rom)
    assert not set_fix(rom, TABLE, "a_fix", True)
    assert bytes(rom) == before


@pytest.mark.parametrize("table", [GENESIS, SNES], ids=["genesis", "snes"])
def test_each_edit_keeps_its_length(table):
    for edits in table.values():
        for _, old, new in edits:
            assert len(old) == len(new) and len(old) % 2 == 0
