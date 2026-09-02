import retro_roster_patcher as rrp


def test_the_documented_names_are_importable_from_the_root():
    expected = {
        "__version__",
        "ApiError",
        "CapabilityError",
        "DailyLimitError",
        "LeagueData",
        "MappedRosters",
        "MappingError",
        "MissingAssetError",
        "Patcher",
        "PatcherInfo",
        "PatchResult",
        "RateLimitError",
        "RetroRosterError",
        "RomError",
        "RomInfo",
        "RomSlot",
        "SeasonNotAvailableError",
        "SlotMapping",
        "StorageError",
        "get_patcher",
        "league_data_from_dict",
        "league_data_to_dict",
        "list_patchers",
        "register",
    }
    assert expected <= set(rrp.__all__)
    # Iterate __all__, not `expected`: ruff's F822 (undefined name in __all__) is
    # suppressed inside __init__.py, so a stale or typo'd entry would otherwise ship
    # green and only blow up in consumer code as `from ... import *` -> AttributeError.
    for name in rrp.__all__:
        assert hasattr(rrp, name), name


def test_all_is_sorted_and_free_of_duplicates():
    # Comparing against the sorted *set* covers both: sorted(["A", "A", "B"]) equals
    # its input, so a duplicate surviving a merge would pass a plain sorted() check.
    assert rrp.__all__ == sorted(set(rrp.__all__))
