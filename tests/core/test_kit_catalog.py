"""The packaged kit catalogue and the defaults that read it."""

import json
from importlib import resources

from retro_roster_patcher.core.kits import (
    PATTERNS,
    KitSpec,
    catalog_kits,
    default_kits,
    kit_catalog,
)
from retro_roster_patcher.sports.models import Team


def test_every_entry_is_a_valid_pair_of_kits_with_unique_ids():
    doc = json.loads(
        (resources.files("retro_roster_patcher.data") / "kit_catalog.json").read_text()
    )
    assert doc["format"] == 1 and doc["patterns"] == list(PATTERNS)
    clubs = doc["clubs"]
    assert len(clubs) >= 100
    ids = [c["espn_id"] for c in clubs if c["espn_id"] is not None]
    assert len(ids) == len(set(ids))
    for c in clubs:
        for side in ("home", "away"):
            spec = KitSpec.from_dict(c[side])
            assert spec.pattern in PATTERNS, c["name"]
    assert kit_catalog()["clubs"] == clubs


def test_a_catalogued_club_starts_in_its_real_kits_whatever_the_provider_colours():
    flamengo = Team(id=819, name="Flamengo", color="000000", alternate_color="FFFFFF")
    home, away = default_kits(flamengo)
    assert home is not None and away is not None
    assert home.pattern == "hoops" and {home.shirt, home.secondary} == {(4, 4, 4), (236, 2, 2)}
    assert away.shirt[0] > 240  # white away


def test_an_entry_with_an_espn_id_answers_only_to_that_id():
    # Same name as a catalogued club, another id: not that club's kit.
    impostor = Team(id=999_999, name="Flamengo", color="0000FF", alternate_color="FFFFFF")
    assert catalog_kits(impostor) is None
    home, _ = default_kits(impostor)
    assert home is not None and home.shirt == (0, 0, 255) and home.pattern == "plain"


def test_an_entry_without_an_espn_id_answers_to_its_names():
    flora = next(c for c in kit_catalog()["clubs"] if c["espn_id"] is None and "Flora" in c["name"])
    found = catalog_kits(Team(id=123, name="FC Flora"))
    assert found is not None and found[0] == KitSpec.from_dict(flora["home"])


def test_an_unknown_club_keeps_the_colour_defaults():
    home, away = default_kits(
        Team(id=42, name="Nowhere Rovers", color="00FF00", alternate_color="000000")
    )
    assert home == KitSpec(
        shirt=(0, 255, 0),
        secondary=(0, 0, 0),
        pattern="plain",
        shorts=(255, 255, 255),
        socks=(0, 255, 0),
    )
    assert away is not None and away.shirt == (0, 0, 0)
