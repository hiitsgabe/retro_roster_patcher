"""What the package promises a consumer can import, and from where.

Every set below is compared with `==`, never with `<=`. The subset form let a
name ADDED to `__all__` ship unguarded, which is how the five sports models
were 20% of the root surface and outside the only test describing it.

The four `__all__`s here are the whole discoverable surface. A module that no
`__all__` names is reachable only by a consumer who already knows its private-
looking dotted path, which for two of the three consumers of this library — a
pygame launcher and a Flutter app over embedded CPython — means not reachable.
"""

import subprocess
import sys
import textwrap

import pytest

import retro_roster_patcher as rrp
from retro_roster_patcher import games, sports


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
        "RomFinder",
        "RomFinderConfig",
        "RomFinderResult",
        "RomInfo",
        "RomSlot",
        "SeasonNotAvailableError",
        "SlotMapping",
        "StorageError",
        "Team",
        "TeamRoster",
        "Transport",
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


def test_the_sports_package_exports_exactly_these_names():
    # `team_colors` is the palette cache a UI needs to offer the user when the
    # provider ships no team colours, and it was reachable only by its own
    # dotted path. It is exported as a module rather than as ten loose names:
    # `load_color_cache` and `save_color_cache` beside `League` and `Player`
    # would read as one namespace where there are two.
    #
    # The three clients are a stated deliverable of this extraction and this
    # package re-exported their exceptions but not them. `Transport` is the type
    # of a public keyword parameter on seven public callables and lived only in
    # the private `_http`.
    expected = {
        "ApiFootballClient",
        "DailyLimitError",
        "EspnClient",
        "League",
        "LeagueData",
        "NhlApiClient",
        "Player",
        "PlayerStats",
        "RateLimitError",
        "SeasonNotAvailableError",
        "Team",
        "TeamRoster",
        "Transport",
        "team_colors",
    }
    assert set(sports.__all__) == expected
    for name in sports.__all__:
        assert hasattr(sports, name), name


def test_the_sports_all_is_sorted_and_free_of_duplicates():
    assert sports.__all__ == sorted(set(sports.__all__))


def test_the_public_transport_type_is_the_one_the_clients_are_annotated_with():
    # Re-export, not a second alias. Two structurally identical Callable aliases
    # would type-check the same and drift the moment one is edited, and the
    # point of naming it publicly is that a consumer wiring its own HTTP — the
    # stated reason `_http` exists — writes the same type the library does.
    from retro_roster_patcher.sports import _http

    assert sports.Transport is _http.Transport


def test_core_assets_declares_its_own_surface():
    # `package_bytes` has no caller in src/ outside `package_path` beside it, so
    # nothing but this says it is API rather than a leftover. It is the function
    # a consumer wants: both target platforms run from inside an archive, where
    # reading an asset by path does not work.
    from retro_roster_patcher.core import assets

    assert set(assets.__all__) == {"MissingAssetError", "package_bytes", "package_path"}


def test_the_we2002_package_exports_exactly_these_names():
    expected = {"AfsHandler", "CsvHandler", "TimGenerator", "WE2002Patcher"}
    assert set(games.we2002.__all__) == expected
    for name in games.we2002.__all__:
        assert hasattr(games.we2002, name), name


def test_the_nhl94_package_exports_exactly_these_names():
    expected = {"NHL94GenesisPatcher"}
    assert set(games.nhl94_genesis.__all__) == expected
    for name in games.nhl94_genesis.__all__:
        assert hasattr(games.nhl94_genesis, name), name


def test_reaching_tim_generator_is_what_imports_it_and_a_plain_import_does_not(tmp_path):
    # `TimGenerator` is the sole reason the optional `images` extra exists: its
    # module does `try: from PIL import Image` at import time. Exporting it
    # eagerly would make every `import retro_roster_patcher` attempt a
    # third-party import — a sys.path scan on a package that is deliberately
    # absent — and the root docstring promises no I/O at import time. So the
    # export is a module `__getattr__`, and this is the test that says so.
    #
    # In a subprocess because `sys.modules` is process-global: this file's own
    # siblings import `tim_generator` directly, so an in-process check would
    # pass or fail on test ordering rather than on the export.
    source = textwrap.dedent(
        """
        import sys

        import retro_roster_patcher
        from retro_roster_patcher.games import we2002

        name = "retro_roster_patcher.games.we2002.tim_generator"
        print(name in sys.modules)
        cls = we2002.TimGenerator
        print(name in sys.modules)
        print(cls.__module__ + ":" + cls.__name__)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", source],
        # `-c` puts the child's cwd on its `sys.path`, so an inherited cwd
        # holding a `retro_roster_patcher/` directory would shadow the installed
        # package. `tmp_path` holds nothing.
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "False",
        "True",
        "retro_roster_patcher.games.we2002.tim_generator:TimGenerator",
    ]


def test_the_lazy_export_still_refuses_a_name_it_does_not_have():
    # A module `__getattr__` that answered every name would make a typo like
    # `we2002.WE2002Pacher` a silent success returning something wrong, and
    # would make the `hasattr` loop above vacuous.
    with pytest.raises(AttributeError, match="NotAThing"):
        _ = games.we2002.NotAThing
