"""`formats.snes`: growing a LoROM image and its header checksum."""

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.formats import snes


def _rom(size: int) -> bytearray:
    rom = bytearray(range(256)) * (size // 256)
    rom[snes.SIZE_BYTE] = 0x0A
    return rom


@pytest.mark.parametrize(("size", "byte"), [(0x200000, 0x0B), (0x400000, 0x0C)])
def test_growing_pads_with_ff_and_sets_the_size_byte(size, byte):
    rom = _rom(0x100000)
    snes.grow(rom, size)
    assert len(rom) == size
    assert rom[snes.SIZE_BYTE] == byte
    assert set(rom[0x100000:]) == {0xFF}


def test_growing_to_the_size_it_has_changes_nothing():
    rom = _rom(0x200000)
    before = bytes(rom)
    snes.grow(rom, 0x200000)
    assert rom == before


@pytest.mark.parametrize("size", [0x80000, 0x300000, 0x800000])
def test_a_size_a_lorom_cannot_be_grown_to_is_refused(size):
    with pytest.raises(RomError):
        snes.grow(_rom(0x100000), size)


def test_the_checksum_and_its_complement_cover_the_whole_image():
    rom = snes.fix_checksum(_rom(0x100000))
    checksum = int.from_bytes(rom[snes.CHECKSUM + 2 : snes.CHECKSUM + 4], "little")
    complement = int.from_bytes(rom[snes.CHECKSUM : snes.CHECKSUM + 2], "little")
    assert checksum ^ complement == 0xFFFF
    assert sum(rom) & 0xFFFF == checksum
