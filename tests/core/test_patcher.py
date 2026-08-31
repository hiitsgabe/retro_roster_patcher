from pathlib import Path

import pytest

from retro_roster_patcher.core.errors import CapabilityError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, SlotMapping
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.sports import League, LeagueData


class FakePatcher(Patcher):
    """Minimal concrete patcher used to exercise the base class."""

    game_id = "fake"
    platform = "genesis"
    sport = "hockey"
    requires_slot_mapping = False
    requires_api_key = False
    # Widened from the inferred `tuple[str]` so subclasses may declare no providers.
    providers: tuple[str, ...] = ("espn",)

    def analyze_rom(self, rom_path):
        return RomInfo(path=str(rom_path), size=0, game_id=self.game_id)

    def fetch(self, *, season, league_id=None, on_progress=None):
        self.check_api_key()
        return LeagueData(league=League(id=1, name="NHL", country="US"), teams=[])

    def map_rosters(self, data, slot_mapping=None):
        self.check_slot_mapping(slot_mapping)
        return MappedRosters(game_id=self.game_id)

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options):
        return PatchResult(output_path=str(output_path))


class SlotPatcher(FakePatcher):
    game_id = "fake-slots"
    requires_slot_mapping = True
    requires_api_key = True


class NoProviderPatcher(FakePatcher):
    game_id = "fake-no-providers"
    providers = ()


class UndecoratedPatcher(Patcher):
    """A patcher written but not yet put through `@register`.

    Nothing stamps its capabilities, so `game_id` keeps the ABC's empty
    placeholder and `providers` stays empty. This is the state of anyone
    part-way through writing the first real patcher.
    """

    requires_slot_mapping = True
    requires_api_key = True

    def analyze_rom(self, rom_path): ...

    def fetch(self, *, season, league_id=None, on_progress=None): ...

    def map_rosters(self, data, slot_mapping=None): ...

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options): ...


def test_the_abc_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        Patcher(cache_dir=Path("/tmp"))  # type: ignore[abstract]


def test_the_four_interface_methods_are_all_abstract():
    # Without this, dropping @abstractmethod from one method still leaves the
    # instantiation test green, and a subclass forgetting it returns None.
    assert Patcher.__abstractmethods__ == frozenset(
        {"analyze_rom", "fetch", "map_rosters", "patch"}
    )


def test_constructor_stores_the_common_arguments():
    seen = []
    patcher = FakePatcher(
        cache_dir=Path("/tmp/cache"), api_key="k", provider="espn", on_status=seen.append
    )
    assert patcher.cache_dir == Path("/tmp/cache")
    assert patcher.api_key == "k"
    assert patcher.provider == "espn"
    patcher.status("hello")
    assert seen == ["hello"]


def test_a_string_cache_dir_is_normalised_to_a_path():
    # Callers across the JSON boundary (NDJSON IPC, CLI) can only pass strings.
    patcher = FakePatcher(cache_dir="/tmp/cache")
    assert patcher.cache_dir == Path("/tmp/cache")


def test_status_and_partial_are_no_ops_when_no_callback_was_given():
    patcher = FakePatcher(cache_dir=Path("/tmp"))
    patcher.status("no listener, no crash")
    patcher.partial({"teams": []})


def test_partial_forwards_to_the_callback():
    seen = []
    patcher = FakePatcher(cache_dir=Path("/tmp"), on_partial=seen.append)
    patcher.partial({"teams": []})
    assert seen == [{"teams": []}]


def test_a_key_requiring_patcher_can_still_be_constructed_without_a_key():
    # `retro-roster analyze` instantiates every registered patcher just to
    # inspect a ROM. Reading a ROM never touches the network, so construction
    # must not demand a key.
    SlotPatcher(cache_dir=Path("/tmp"))


def test_missing_api_key_is_rejected_when_fetch_is_called():
    patcher = SlotPatcher(cache_dir=Path("/tmp"))
    with pytest.raises(CapabilityError, match="api_key"):
        patcher.fetch(season=2024)


def test_fetch_succeeds_once_a_key_is_supplied():
    patcher = SlotPatcher(cache_dir=Path("/tmp"), api_key="k")
    data = patcher.fetch(season=2024)
    assert data.league.name == "NHL"
    assert data.teams == []


def test_unknown_provider_is_rejected():
    with pytest.raises(
        CapabilityError, match=r"does not support provider 'nope'\. Supported: espn"
    ):
        FakePatcher(cache_dir=Path("/tmp"), provider="nope")


def test_a_patcher_declaring_no_providers_rejects_any_provider():
    # An empty `providers` tuple means "takes no provider", not "takes anything".
    with pytest.raises(
        CapabilityError, match=r"does not support provider 'espn'\. Supported: none"
    ):
        NoProviderPatcher(cache_dir=Path("/tmp"), provider="espn")


def test_provider_is_none_when_none_are_declared():
    assert NoProviderPatcher(cache_dir=Path("/tmp")).provider is None


def test_guard_errors_name_the_class_when_game_id_is_unset():
    # Before `@register` stamps a game_id, an error built from it alone would
    # read " requires an api_key" — a leading space and no subject.
    patcher = UndecoratedPatcher(cache_dir=Path("/tmp"))
    assert patcher.game_id == ""
    with pytest.raises(CapabilityError, match="UndecoratedPatcher requires an api_key"):
        patcher.check_api_key()
    with pytest.raises(CapabilityError, match="UndecoratedPatcher requires a slot mapping"):
        patcher.check_slot_mapping(None)
    with pytest.raises(CapabilityError, match="UndecoratedPatcher does not support provider"):
        UndecoratedPatcher(cache_dir=Path("/tmp"), provider="espn")


def test_provider_defaults_to_the_first_declared_one():
    assert FakePatcher(cache_dir=Path("/tmp")).provider == "espn"


def test_slot_mapping_on_an_auto_mapping_patcher_raises():
    patcher = FakePatcher(cache_dir=Path("/tmp"))
    with pytest.raises(CapabilityError, match="does not use slot mappings"):
        patcher.map_rosters(
            LeagueData(league=League(id=1, name="NHL", country="US"), teams=[]),
            slot_mapping=[SlotMapping(slot_index=0, team_id=1)],
        )


def test_missing_slot_mapping_on_a_slot_patcher_raises():
    patcher = SlotPatcher(cache_dir=Path("/tmp"), api_key="k")
    with pytest.raises(CapabilityError, match="requires a slot mapping"):
        patcher.map_rosters(LeagueData(league=League(id=1, name="NHL", country="US"), teams=[]))


def test_an_empty_slot_mapping_list_counts_as_missing():
    patcher = SlotPatcher(cache_dir=Path("/tmp"), api_key="k")
    with pytest.raises(CapabilityError, match="requires a slot mapping"):
        patcher.map_rosters(
            LeagueData(league=League(id=1, name="NHL", country="US"), teams=[]),
            slot_mapping=[],
        )


def test_the_happy_paths_pass_the_guard():
    auto = FakePatcher(cache_dir=Path("/tmp")).map_rosters(
        LeagueData(league=League(id=1, name="NHL", country="US"), teams=[])
    )
    assert auto.game_id == "fake"
    slotted = SlotPatcher(cache_dir=Path("/tmp"), api_key="k").map_rosters(
        LeagueData(league=League(id=1, name="NHL", country="US"), teams=[]),
        slot_mapping=[SlotMapping(slot_index=0, team_id=1)],
    )
    assert slotted.game_id == "fake-slots"
