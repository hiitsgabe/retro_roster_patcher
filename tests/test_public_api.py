import retro_roster_patcher as rrp


def test_the_root_exports_exactly_these_names():
    expected = {
        "__version__",
        "ApiError",
        "CapabilityError",
        "DailyLimitError",
        "League",
        "LeagueData",
        "MappedRosters",
        "MappingError",
        "MissingAssetError",
        "Patcher",
        "PatcherInfo",
        "PatchResult",
        "Player",
        "PlayerStats",
        "RateLimitError",
        "RetroRosterError",
        "RomError",
        "RomInfo",
        "RomSlot",
        "SeasonNotAvailableError",
        "SlotMapping",
        "StorageError",
        "Team",
        "TeamRoster",
        "get_patcher",
        "league_data_from_dict",
        "league_data_to_dict",
        "list_patchers",
        "register",
    }
    # Equality, not `expected <= set(...)`. Under the subset form a name ADDED to
    # `__all__` shipped unguarded, which is how the five sports models — `League`,
    # `Player`, `PlayerStats`, `Team`, `TeamRoster` — were 20% of the real surface
    # and outside the only test that claims to describe it. Equality is what makes
    # this file able to fail on an accidental export as well as on a lost one.
    assert set(rrp.__all__) == expected
    # Iterate __all__, not `expected`: ruff's F822 (undefined name in __all__) is
    # suppressed inside __init__.py, so a stale or typo'd entry would otherwise ship
    # green and only blow up in consumer code as `from ... import *` -> AttributeError.
    for name in rrp.__all__:
        assert hasattr(rrp, name), name


def test_all_is_sorted_and_free_of_duplicates():
    # Comparing against the sorted *set* covers both: sorted(["A", "A", "B"]) equals
    # its input, so a duplicate surviving a merge would pass a plain sorted() check.
    assert rrp.__all__ == sorted(set(rrp.__all__))
