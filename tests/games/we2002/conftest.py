"""Fixtures the WE2002 tests share."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def disc_path():
    """The retail disc's path (RETRO_ROSTER_TEST_ROMS has we2002.bin: Track 1, the MODE2/2352
    data track)."""
    root = os.environ.get("RETRO_ROSTER_TEST_ROMS")
    if not root:
        pytest.skip("set RETRO_ROSTER_TEST_ROMS to a directory holding we2002.bin")
    path = Path(root) / "we2002.bin"
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return str(path)


@pytest.fixture
def disc(disc_path):
    """`read(offset, size)` on the retail disc."""
    with open(disc_path, "rb") as handle:

        def read(offset, size):
            handle.seek(offset)
            return handle.read(size)

        yield read
