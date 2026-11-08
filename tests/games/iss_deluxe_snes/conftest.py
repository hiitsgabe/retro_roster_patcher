"""The real cartridge for the tests marked `real_rom`, found one way for all of them."""

import os
from pathlib import Path

import pytest

#: The cartridge's file name under RETRO_ROSTER_TEST_ROMS.
NAME = "iss-deluxe.sfc"


@pytest.fixture
def deluxe_rom() -> Path:
    """RETRO_ROSTER_TEST_ROMS/iss-deluxe.sfc: the test skips without the variable and
    fails when the variable is set but the file is missing."""
    if not os.environ.get("RETRO_ROSTER_TEST_ROMS"):
        pytest.skip(f"set RETRO_ROSTER_TEST_ROMS to a directory holding {NAME}")
    path = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / NAME
    if not path.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {path} is missing")
    return path
