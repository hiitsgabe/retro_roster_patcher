"""The package imports and exposes a version."""

import retro_roster_patcher


def test_version_is_exposed():
    assert isinstance(retro_roster_patcher.__version__, str)
    assert retro_roster_patcher.__version__ == "0.1.0.dev0"
