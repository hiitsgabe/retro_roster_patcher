import pytest

from retro_roster_patcher.core import registry
from retro_roster_patcher.core.registry import (
    PatcherInfo,
    get_patcher,
    list_patchers,
    register,
)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Each test gets an empty registry so real games do not leak in."""
    monkeypatch.setattr(registry, "_REGISTRY", {})


def test_register_returns_the_class_unchanged():
    class Dummy:
        pass

    decorated = register("dummy", platform="genesis", sport="hockey")(Dummy)
    assert decorated is Dummy


def test_register_stamps_capabilities_onto_the_class():
    @register(
        "nhl94-genesis",
        platform="genesis",
        sport="hockey",
        requires_slot_mapping=False,
        requires_api_key=False,
        providers=("espn", "nhl"),
    )
    class Dummy:
        pass

    assert Dummy.game_id == "nhl94-genesis"
    assert Dummy.platform == "genesis"
    assert Dummy.sport == "hockey"
    assert Dummy.requires_slot_mapping is False
    assert Dummy.requires_api_key is False
    assert Dummy.providers == ("espn", "nhl")


def test_capability_defaults_are_the_conservative_ones():
    @register("dummy", platform="psx", sport="soccer")
    class Dummy:
        pass

    assert Dummy.requires_slot_mapping is False
    assert Dummy.requires_api_key is False
    assert Dummy.providers == ()


def test_get_patcher_returns_the_registered_class():
    @register("dummy", platform="genesis", sport="hockey")
    class Dummy:
        pass

    assert get_patcher("dummy") is Dummy


def test_get_patcher_raises_a_helpful_error_for_unknown_ids():
    @register("dummy", platform="genesis", sport="hockey")
    class Dummy:
        pass

    with pytest.raises(KeyError) as excinfo:
        get_patcher("nope")
    assert "dummy" in str(excinfo.value)


def test_registering_the_same_id_twice_is_an_error():
    @register("dummy", platform="genesis", sport="hockey")
    class First:
        pass

    with pytest.raises(ValueError, match="dummy"):

        @register("dummy", platform="genesis", sport="hockey")
        class Second:
            pass


def test_list_patchers_is_sorted_and_describes_each_game():
    @register("zeta", platform="psx", sport="soccer", requires_slot_mapping=True)
    class Zeta:
        pass

    @register("alpha", platform="genesis", sport="hockey", providers=("espn",))
    class Alpha:
        pass

    infos = list_patchers()
    assert [i.game_id for i in infos] == ["alpha", "zeta"]
    assert infos[0] == PatcherInfo(
        game_id="alpha",
        platform="genesis",
        sport="hockey",
        requires_slot_mapping=False,
        requires_api_key=False,
        providers=("espn",),
    )
    assert infos[1].requires_slot_mapping is True
