import retro_roster_patcher as rrp


def test_the_documented_names_are_importable_from_the_root():
    expected = {
        "__version__",
        "ApiError",
        "CapabilityError",
        "LeagueData",
        "MappedRosters",
        "MappingError",
        "Patcher",
        "PatcherInfo",
        "PatchResult",
        "RetroRosterError",
        "RomError",
        "RomInfo",
        "RomSlot",
        "SlotMapping",
        "get_patcher",
        "list_patchers",
        "register",
    }
    assert expected <= set(rrp.__all__)
    for name in expected:
        assert hasattr(rrp, name), name


def test_all_is_sorted_so_diffs_stay_readable():
    assert rrp.__all__ == sorted(rrp.__all__)
