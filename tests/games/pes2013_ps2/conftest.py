"""The synthetic disc is not the retail one: its records have none of the retail disc's
looks (`looks.retail_looks`, packaged for the real disc). A test that wants some sets them."""

import pytest

from retro_roster_patcher.games.pes2013_ps2 import looks


@pytest.fixture(autouse=True)
def no_retail_looks(monkeypatch):
    monkeypatch.setattr(looks, "retail_looks", lambda: {})
