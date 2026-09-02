import pytest

from retro_roster_patcher.core.errors import MappingError
from retro_roster_patcher.core.models import (
    MappedRosters,
    PatchResult,
    RomInfo,
    RomSlot,
    SlotMapping,
)


def test_rom_info_defaults_are_empty_not_none():
    info = RomInfo(path="/roms/nhl94.bin", size=1048576, game_id="nhl94-genesis")
    assert info.slots == []
    assert info.extra == {}
    assert info.is_valid is True


def test_rom_info_carries_game_specific_data_in_extra():
    info = RomInfo(
        path="/roms/we2002.bin",
        size=700 * 1024 * 1024,
        game_id="we2002",
        extra={"region": "JP", "afs_offset": 0x1000},
    )
    assert info.extra["afs_offset"] == 0x1000


def test_rom_slot_round_trips_through_dict():
    slot = RomSlot(index=3, current_name="Boston", display_name="Boston Bruins")
    assert slot.to_dict() == {
        "index": 3,
        "current_name": "Boston",
        "display_name": "Boston Bruins",
    }


def test_slot_mapping_round_trips_through_dict():
    mapping = SlotMapping(slot_index=7, team_id=529, team_name="Barcelona")
    assert SlotMapping.from_dict(mapping.to_dict()) == mapping


def test_mapped_rosters_knows_how_many_slots_are_filled():
    rosters = MappedRosters(game_id="nhl94-genesis", teams={0: ["a", "b"], 4: []})
    assert rosters.filled_slots() == [0]


def test_require_game_accepts_the_game_that_mapped_them():
    rosters = MappedRosters(game_id="we2002", teams={0: object()})
    assert rosters.require_game("we2002") is None


def test_require_game_refuses_another_games_rosters():
    rosters = MappedRosters(game_id="nhl94-genesis", teams={0: ["a"]})
    with pytest.raises(MappingError, match="nhl94-genesis"):
        rosters.require_game("we2002")


def test_require_game_names_both_games_so_the_message_says_which_way_round():
    rosters = MappedRosters(game_id="nhl94-genesis", teams={0: ["a"]})
    with pytest.raises(MappingError, match="we2002"):
        rosters.require_game("we2002")


def test_patch_result_serialises_for_the_json_protocol():
    result = PatchResult(output_path="/roms/out.bin", teams_patched=26, players_patched=598)
    assert result.to_dict() == {
        "output_path": "/roms/out.bin",
        "teams_patched": 26,
        "players_patched": 598,
    }
