import json

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
    message = str(excinfo.value)
    assert "nope" in message
    assert "dummy" in message


def test_get_patcher_says_none_when_nothing_is_registered():
    with pytest.raises(KeyError) as excinfo:
        get_patcher("nope")
    message = str(excinfo.value)
    assert "nope" in message
    assert "none" in message


def test_registering_the_same_id_twice_is_an_error():
    @register("dummy", platform="genesis", sport="hockey")
    class First:
        pass

    class Second:
        pass

    with pytest.raises(ValueError) as excinfo:
        register("dummy", platform="genesis", sport="hockey")(Second)

    message = str(excinfo.value)
    assert "dummy" in message
    assert "First" in message
    assert get_patcher("dummy") is First
    assert not hasattr(Second, "game_id")


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


def test_patcher_info_serialises_for_the_json_protocol():
    info = PatcherInfo(
        game_id="nhl94-genesis",
        platform="genesis",
        sport="hockey",
        requires_slot_mapping=True,
        requires_api_key=False,
        providers=("espn", "nhl"),
    )

    assert info.to_dict() == {
        "game_id": "nhl94-genesis",
        "platform": "genesis",
        "sport": "hockey",
        "requires_slot_mapping": True,
        "requires_api_key": False,
        "providers": ["espn", "nhl"],
    }

    round_tripped = json.loads(json.dumps(info.to_dict()))
    assert round_tripped == info.to_dict()
    assert round_tripped["providers"] == ["espn", "nhl"]
