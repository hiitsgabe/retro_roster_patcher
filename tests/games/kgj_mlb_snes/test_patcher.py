"""The KGJ MLB patcher against the unified interface.

The reader, writer and stat mapper below it are a faithful port of an untested
upstream; this layer is where its contract violations are absorbed and where the
migration's own decisions live. Six things here are not in the ported code at
all:

  * `_team_data_fits`, without which an image whose marker matches too near the
    end is patched for none of its teams and reported as a success;
  * `_roster_type_for_slot`, which is where the roster-type stamping moved to
    when `write_team_roster` stopped mutating the caller's records, and which is
    also what retired `patcher.py`'s `is_starter = idx < 20`;
  * the alias guard, without which an empty `CHW` wipes a populated `CWS`;
  * `season` threaded into the squad call, which upstream omitted;
  * `TeamRoster.extra["leaders"]` in place of `self.team_stats`;
  * `RomError` against `RomInfo(is_valid=False)`, which upstream conflated.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `KGJRomWriter.__init__` builds its own reader over the *input* file, so
`writer.reader.data` is the pre-write image for the writer's whole lifetime and
asserting against it would assert nothing.
"""

import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot, SlotMapping
from retro_roster_patcher.games.kgj_mlb_snes.models import (
    BATTERS_PER_TEAM,
    KGJ_TEAM_ORDER,
    PLAYERS_PER_TEAM,
    ROSTER_TYPE_BATTER,
    ROSTER_TYPE_RELIEVER,
    ROSTER_TYPE_STARTER,
    STARTERS_PER_TEAM,
    TEAM_COUNT,
    KGJPlayerRecord,
    KGJTeamRecord,
)
from retro_roster_patcher.games.kgj_mlb_snes.patcher import (
    KGJMLBPatcher,
    _roster_type_for_slot,
    _team_data_fits,
)
from retro_roster_patcher.games.kgj_mlb_snes.rom_reader import TEAM_DATA_SPAN, KGJRomReader
from retro_roster_patcher.games.kgj_mlb_snes.stat_mapper import KGJStatMapper
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_kgj_rom as fixture
from tests.fixtures import synthetic_snes_rom as nhl94_snes_fixture

#: Slots from `MODERN_MLB_TO_KGJ`. Seattle and Chicago's two are the ones the
#: fake API covers; the White Sox slot is one of the two that two codes reach.
SEA_SLOT = 11
CWS_SLOT = 3
OAK_SLOT = 10


class FakeApi:
    """Stands in for `EspnClient`.

    Records what it was asked for, because `season` reaches the two endpoints
    differently -- a cache key on the squad call, a path segment on the leaders
    call -- and upstream passed it to only one of them.
    """

    def __init__(self, teams, squad_size=25, leaders=None):
        self._teams = teams
        self._squad_size = squad_size
        self._leaders = {"0": {"AVG": 0.311}} if leaders is None else leaders
        self.squad_calls = []
        self.leader_calls = []

    def get_mlb_teams(self):
        return list(self._teams)

    def get_baseball_squad(self, team_id, season=None):
        self.squad_calls.append((team_id, season))
        return [
            Player(
                id=index,
                name=f"First{index} Last{index:02d}",
                position="SP" if index >= BATTERS_PER_TEAM else "CF",
                number=index + 1,
            )
            for index in range(self._squad_size)
        ]

    def get_baseball_team_leaders(self, team_id, season=2025):
        self.leader_calls.append((team_id, season))
        return dict(self._leaders)


def _teams():
    return [
        Team(id=1, name="Seattle Mariners", code="SEA"),
        Team(id=2, name="Chicago White Sox", code="CWS"),
        Team(id=3, name="Arizona Diamondbacks", code="ARI"),
        Team(id=4, name="Not A Real Team", code="ZZZ"),
    ]


@pytest.fixture
def patcher(tmp_path):
    """A patcher wired to a fake API covering two real KGJ MLB slots."""
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams())
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_kgj_rom(tmp_path / "kgj.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def _league_data(*entries, season=2025):
    """`LeagueData` with one `TeamRoster` per `(code, squad size)`, in order.

    The order is what decides which of two colliding aliases lands in the shared
    slot, so it is a parameter rather than an accident of a dict.
    """
    return LeagueData(
        league=League(id=0, name="MLB", country="USA", country_code="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=index + 1, name=code, code=code),
                players=[
                    Player(
                        id=index * 100 + n,
                        name=f"{code}First{n:02d} {code}Last{n:02d}",
                        position="SP" if n >= BATTERS_PER_TEAM else "CF",
                        number=n + 1,
                    )
                    for n in range(size)
                ],
                extra={"leaders": {}},
            )
            for index, (code, size) in enumerate(entries)
        ],
    )


def _records(count):
    return [
        KGJPlayerRecord(
            first_initial="W",
            last_name=f"WRITE{index:02d}",
            position="CF",
            jersey_number=index + 1,
            roster_type=_roster_type_for_slot(index),
        )
        for index in range(count)
    ]


def _mapped(*pairs):
    return MappedRosters(
        game_id="kgj-mlb-snes",
        teams={
            slot: KGJTeamRecord(index=slot, name=KGJ_TEAM_ORDER[slot], players=_records(count))
            for slot, count in pairs
        },
    )


def _read_back(path, slot):
    reader = KGJRomReader(str(path))
    assert reader.load() is True
    assert reader.validate() is True
    return reader.read_team_roster(slot)


# -- registration ------------------------------------------------------------


def test_the_patcher_is_registered_with_its_id():
    from retro_roster_patcher import get_patcher

    assert get_patcher("kgj-mlb-snes") is KGJMLBPatcher


def test_the_patcher_declares_its_platform():
    assert KGJMLBPatcher.platform == "snes"


def test_the_patcher_declares_its_sport():
    assert KGJMLBPatcher.sport == "baseball"


def test_the_patcher_maps_teams_without_a_slot_mapping():
    assert KGJMLBPatcher.requires_slot_mapping is False


def test_the_patcher_declares_espn_as_its_only_provider():
    assert KGJMLBPatcher.providers == ("espn",)


def test_this_is_the_librarys_first_baseball_game():
    """The registration is what makes ESPN's three MLB methods reachable."""
    from retro_roster_patcher import list_patchers

    baseball = [info.game_id for info in list_patchers() if info.sport == "baseball"]
    assert baseball == ["kgj-mlb-snes"]


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # Registration is a side-effect import at the bottom of the package root, so
    # importing the game module directly must not be what a consumer relies on.
    script = textwrap.dedent(
        """
        import retro_roster_patcher as rrp
        print(",".join(sorted(info.game_id for info in rrp.list_patchers())))
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert "kgj-mlb-snes" in result.stdout.strip().split(",")


# -- construction ------------------------------------------------------------


def test_the_constructor_accepts_a_string_cache_directory(tmp_path):
    p = KGJMLBPatcher(cache_dir=str(tmp_path / "cache"))
    assert p.cache_dir == tmp_path / "cache"


def test_the_constructor_builds_its_client_eagerly(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert type(p.api) is EspnClient


def test_the_constructor_creates_the_cache_directory(tmp_path):
    KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert (tmp_path / "cache").is_dir() is True


def test_the_constructor_refuses_an_api_key(tmp_path):
    # No provider here takes a credential. A parameter that silently did nothing
    # would let a caller believe one was in use.
    with pytest.raises(TypeError):
        KGJMLBPatcher(cache_dir=tmp_path / "cache", api_key="secret")


def test_the_constructor_refuses_a_provider_it_does_not_have(tmp_path):
    with pytest.raises(CapabilityError):
        KGJMLBPatcher(cache_dir=tmp_path / "cache", provider="nhl")


def test_the_constructor_accepts_its_own_provider(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", provider="espn")
    assert p.provider == "espn"


def test_the_default_provider_is_the_first_declared(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    assert p.provider == "espn"


# -- _roster_type_for_slot ---------------------------------------------------


@pytest.mark.parametrize("slot", [0, 7, BATTERS_PER_TEAM - 1])
def test_a_batter_slot_takes_the_batter_nibble(slot):
    assert _roster_type_for_slot(slot) == ROSTER_TYPE_BATTER


@pytest.mark.parametrize("slot", [BATTERS_PER_TEAM, BATTERS_PER_TEAM + STARTERS_PER_TEAM - 1])
def test_a_starting_pitcher_slot_takes_the_starter_nibble(slot):
    assert _roster_type_for_slot(slot) == ROSTER_TYPE_STARTER


@pytest.mark.parametrize("slot", [BATTERS_PER_TEAM + STARTERS_PER_TEAM, PLAYERS_PER_TEAM - 1])
def test_a_relief_pitcher_slot_takes_the_reliever_nibble(slot):
    assert _roster_type_for_slot(slot) == ROSTER_TYPE_RELIEVER


def test_the_three_nibbles_are_distinct():
    # A helper that returned one constant would satisfy any single test above.
    values = {ROSTER_TYPE_BATTER, ROSTER_TYPE_STARTER, ROSTER_TYPE_RELIEVER}
    assert len(values) == 3


# -- _team_data_fits ---------------------------------------------------------


def _reader_for(path):
    reader = KGJRomReader(str(path))
    reader.load()
    reader.validate()
    return reader


def test_a_marker_at_the_head_of_the_file_leaves_room(rom):
    assert _team_data_fits(_reader_for(rom)) is True


def test_a_marker_exactly_far_enough_from_the_end_leaves_room(tmp_path):
    # The last byte of team 27 lands on the last byte of the file.
    offset = fixture.ROM_SIZE - TEAM_DATA_SPAN - len(fixture.FIRST_TEAM_MARKER)
    path = fixture.write_kgj_rom(tmp_path / "exact.sfc", marker_offset=offset)
    assert _team_data_fits(_reader_for(path)) is True


def test_a_marker_one_byte_too_late_does_not(tmp_path):
    offset = fixture.ROM_SIZE - TEAM_DATA_SPAN - len(fixture.FIRST_TEAM_MARKER) + 1
    path = fixture.write_kgj_rom(tmp_path / "off-by-one.sfc", marker_offset=offset)
    assert _team_data_fits(_reader_for(path)) is False


def test_a_reader_that_never_loaded_does_not_fit(tmp_path):
    assert _team_data_fits(KGJRomReader(str(tmp_path / "absent.sfc"))) is False


# -- analyze_rom -------------------------------------------------------------


def test_analyze_recognises_a_headerless_image(patcher, rom):
    assert patcher.analyze_rom(rom).is_valid is True


def test_analyze_recognises_a_headered_image(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    assert patcher.analyze_rom(path).is_valid is True


def test_analyze_stamps_its_own_game_id(patcher, rom):
    assert patcher.analyze_rom(rom).game_id == "kgj-mlb-snes"


def test_analyze_reports_the_files_size(patcher, rom):
    assert patcher.analyze_rom(rom).size == fixture.ROM_SIZE


def test_analyze_reports_the_path_it_was_given(patcher, rom):
    assert patcher.analyze_rom(rom).path == str(rom)


def test_analyze_reports_one_slot_per_team(patcher, rom):
    assert len(patcher.analyze_rom(rom).slots) == TEAM_COUNT


def test_a_slots_display_name_is_the_1994_team(patcher, rom):
    assert patcher.analyze_rom(rom).slots[10].display_name == "Oakland Athletics"


def test_every_slots_display_name_is_distinct(patcher, rom):
    # `RomSlot.display_name` is what a slot-picking UI lists, so a repeated
    # value leaves the user unable to tell two rows apart.
    names = [slot.display_name for slot in patcher.analyze_rom(rom).slots]
    assert len(set(names)) == TEAM_COUNT


def test_a_slots_current_name_says_it_is_a_player(patcher, rom):
    # The upstream slot record's only ROM-derived field is a *player* name. The
    # NBA Live 95 port faced the identical problem and labelled it the same way,
    # rather than filing a player under a field a UI renders as a team.
    name = f"{fixture.player_first_initial(10, 0)}. {fixture.player_last_name(10, 0)}"
    expected = f"First player: {name}"
    assert patcher.analyze_rom(rom).slots[10].current_name == expected


def test_two_slots_carry_different_current_names(patcher, rom):
    slots = patcher.analyze_rom(rom).slots
    assert slots[10].current_name != slots[11].current_name


def test_a_slot_is_a_rom_slot(patcher, rom):
    assert type(patcher.analyze_rom(rom).slots[0]) is RomSlot


def test_analyze_reports_whether_the_image_has_a_copier_header(patcher, rom):
    assert patcher.analyze_rom(rom).extra["has_header"] is False


def test_analyze_reports_the_copier_header_on_a_headered_image(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    assert patcher.analyze_rom(path).extra["has_header"] is True


def test_analyze_reports_where_the_marker_matched(patcher, rom):
    expected = fixture.MARKER_OFFSET + len(fixture.FIRST_TEAM_MARKER)
    assert patcher.analyze_rom(rom).extra["first_team_offset"] == expected


def test_the_extra_dict_survives_a_json_round_trip(patcher, rom):
    import json

    info = patcher.analyze_rom(rom)
    assert json.loads(json.dumps(info.to_dict()))["extra"] == info.extra


def test_analyze_refuses_an_image_with_no_marker(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_refuses_an_image_of_the_wrong_size(patcher, tmp_path):
    path = tmp_path / "wrongsize.sfc"
    path.write_bytes(bytes(fixture.build_kgj_rom())[: fixture.ROM_SIZE - 1])
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_refuses_an_image_whose_marker_leaves_no_room(patcher, tmp_path):
    # This is the file upstream accepted, patched nothing in, and reported as a
    # success.
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_still_reports_the_size_of_an_image_it_refuses(patcher, tmp_path):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).size == fixture.ROM_SIZE


def test_analyze_does_not_claim_the_other_snes_game_in_this_library(patcher, tmp_path):
    # `retro-roster analyze` probes every registered patcher against one image,
    # and NHL 94 SNES is the other cartridge here. Its 1 MB dump fails the size
    # test outright; this pins that the sweep stays honest.
    path = nhl94_snes_fixture.write_nhl94_snes_rom(tmp_path / "nhl94.sfc")
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_does_not_claim_an_arbitrary_two_megabyte_file(patcher, tmp_path):
    path = tmp_path / "other.sfc"
    path.write_bytes(bytes(fixture.ROM_SIZE))
    assert patcher.analyze_rom(path).is_valid is False


def test_analyze_raises_for_a_file_that_is_not_there(patcher, tmp_path):
    # DELIBERATE DIVERGENCE: upstream returned `KGJRomInfo(path, size=0)` here,
    # which is the same answer it gave for a readable image of another game.
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "absent.sfc")


def test_analyze_raises_for_a_path_that_cannot_be_read(patcher, tmp_path):
    directory = tmp_path / "adirectory"
    directory.mkdir()
    with pytest.raises(RomError):
        patcher.analyze_rom(directory)


def test_the_unreadable_error_names_the_file(patcher, tmp_path):
    missing = tmp_path / "absent.sfc"
    with pytest.raises(RomError, match="absent.sfc"):
        patcher.analyze_rom(missing)


# -- fetch -------------------------------------------------------------------


def test_fetch_returns_one_roster_per_slot_mapped_team(patcher):
    # SEA and CWS have slots; ARI never existed in 1994 and ZZZ is not a team.
    assert len(patcher.fetch(season=2025).teams) == 2


def test_fetch_names_the_league(patcher):
    assert patcher.fetch(season=2025).league.name == "MLB"


def test_fetch_records_the_season_it_was_asked_for(patcher):
    assert patcher.fetch(season=1994).league.season == 1994


def test_fetch_counts_the_rosters_it_built_and_not_the_teams_it_saw(patcher):
    assert patcher.fetch(season=2025).league.teams_count == 2


def test_fetch_threads_the_season_into_the_squad_call(patcher):
    # DELIBERATE DIVERGENCE: upstream called `get_baseball_squad(team.id)` with
    # no season at all, and the season is part of that endpoint's cache key, so
    # the first season ever fetched was served forever.
    patcher.fetch(season=1994)
    assert patcher.api.squad_calls == [(1, 1994), (2, 1994)]


def test_fetch_threads_the_season_into_the_leaders_call(patcher):
    patcher.fetch(season=1994)
    assert patcher.api.leader_calls == [(1, 1994), (2, 1994)]


def test_fetch_puts_the_leaders_where_map_rosters_can_reach_them(patcher):
    # DELIBERATE DIVERGENCE: upstream left these on `self.team_stats`, an
    # instance side channel no serialised rosters file could carry.
    data = patcher.fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"0": {"AVG": 0.311}}


def test_a_team_with_no_leaders_still_gets_an_empty_dict(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams(), leaders={})
    assert p.fetch(season=2025).teams[0].extra["leaders"] == {}


def test_fetch_raises_when_the_provider_returns_no_teams(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([])
    with pytest.raises(ApiError):
        p.fetch(season=2025)


def test_fetch_raises_when_no_team_maps_to_a_slot(tmp_path):
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([Team(id=1, name="Arizona", code="ARI"), Team(id=2, name="Rays", code="TB")])
    with pytest.raises(ApiError):
        p.fetch(season=2025)


def test_fetch_reports_progress_once_per_team_and_once_at_the_end(patcher):
    seen = []
    patcher.fetch(season=2025, on_progress=lambda f, m: seen.append(f))
    assert seen == [0.0, 0.5, 1.0]


def test_fetch_narrates_its_first_step(tmp_path):
    messages = []
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi(_teams())
    p.fetch(season=2025)
    assert messages == ["Fetching MLB teams..."]


def test_fetch_does_not_reach_the_provider_for_a_team_with_no_slot(patcher):
    patcher.fetch(season=2025)
    assert [call[0] for call in patcher.api.squad_calls] == [1, 2]


# -- map_rosters -------------------------------------------------------------


def test_map_rosters_refuses_a_slot_mapping(patcher):
    with pytest.raises(CapabilityError):
        patcher.map_rosters(_league_data(("SEA", 25)), [SlotMapping(slot_index=0, team_id=1)])


def test_map_rosters_stamps_its_own_game_id(patcher):
    assert patcher.map_rosters(_league_data(("SEA", 25))).game_id == "kgj-mlb-snes"


def test_map_rosters_keys_a_team_by_its_rom_slot(patcher):
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [SEA_SLOT]


def test_map_rosters_is_sparse(patcher):
    # Upstream built all 28 records and left the unmatched ones empty.
    assert len(patcher.map_rosters(_league_data(("SEA", 25))).teams) == 1


def test_a_mapped_team_carries_its_1994_name(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].name == "Seattle Mariners"


def test_a_mapped_team_holds_one_record_per_selected_player(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert len(mapped.teams[SEA_SLOT].players) == PLAYERS_PER_TEAM


def test_a_squad_of_more_than_25_is_cut_to_the_roster_size(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 60)))
    assert len(mapped.teams[SEA_SLOT].players) == PLAYERS_PER_TEAM


def test_a_team_with_no_rom_slot_is_dropped(patcher):
    assert patcher.map_rosters(_league_data(("ARI", 25))).teams == {}


def test_the_first_fifteen_records_carry_the_batter_nibble(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    kinds = [r.roster_type for r in mapped.teams[SEA_SLOT].players[:BATTERS_PER_TEAM]]
    assert kinds == [ROSTER_TYPE_BATTER] * BATTERS_PER_TEAM


def test_the_next_five_records_carry_the_starter_nibble(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    span = slice(BATTERS_PER_TEAM, BATTERS_PER_TEAM + STARTERS_PER_TEAM)
    kinds = [r.roster_type for r in mapped.teams[SEA_SLOT].players[span]]
    assert kinds == [ROSTER_TYPE_STARTER] * STARTERS_PER_TEAM


def test_the_last_five_records_carry_the_reliever_nibble(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    span = slice(BATTERS_PER_TEAM + STARTERS_PER_TEAM, None)
    kinds = [r.roster_type for r in mapped.teams[SEA_SLOT].players[span]]
    assert kinds == [ROSTER_TYPE_RELIEVER] * 5


def test_the_pitcher_slots_hold_pitcher_records(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    flags = [r.is_pitcher for r in mapped.teams[SEA_SLOT].players[BATTERS_PER_TEAM:]]
    assert flags == [True] * 10


def test_the_batter_slots_hold_batter_records(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    flags = [r.is_pitcher for r in mapped.teams[SEA_SLOT].players[:BATTERS_PER_TEAM]]
    assert flags == [False] * BATTERS_PER_TEAM


def test_a_starting_pitcher_gets_the_starter_defaults(patcher):
    # `is_starter` decides which default row a stat-less pitcher takes, and it
    # comes from the slot boundaries rather than from upstream's `idx < 20`.
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].players[BATTERS_PER_TEAM].pitcher_attrs.fatigue == 7


def test_a_relief_pitcher_gets_the_reliever_defaults(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25)))
    assert mapped.teams[SEA_SLOT].players[PLAYERS_PER_TEAM - 1].pitcher_attrs.fatigue == 3


def test_a_populated_slot_survives_an_empty_alias_arriving_after_it(patcher):
    # `MODERN_MLB_TO_KGJ` names slot 3 twice, CWS and CHW. Without the guard the
    # empty one would wipe the populated one, `patch` would skip the slot, and
    # the run would report success with the 1994 roster still in place.
    mapped = patcher.map_rosters(_league_data(("CWS", 25), ("CHW", 0)))
    assert len(mapped.teams[CWS_SLOT].players) == PLAYERS_PER_TEAM


def test_the_oakland_alias_pair_is_guarded_too(patcher):
    mapped = patcher.map_rosters(_league_data(("OAK", 25), ("ATH", 0)))
    assert len(mapped.teams[OAK_SLOT].players) == PLAYERS_PER_TEAM


def test_a_populated_alias_arriving_second_does_take_the_slot(patcher):
    # The guard is one-directional on purpose: it protects a populated roster
    # from an empty one, not the first arrival from the second.
    mapped = patcher.map_rosters(_league_data(("CHW", 0), ("CWS", 25)))
    assert len(mapped.teams[CWS_SLOT].players) == PLAYERS_PER_TEAM


def test_an_empty_roster_with_no_collision_still_takes_its_slot(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 0)))
    assert mapped.teams[SEA_SLOT].players == []


def test_two_different_teams_take_two_different_slots(patcher):
    mapped = patcher.map_rosters(_league_data(("SEA", 25), ("CWS", 25)))
    assert sorted(mapped.teams) == sorted([SEA_SLOT, CWS_SLOT])


# -- patch -------------------------------------------------------------------


def test_patch_refuses_another_games_rosters(patcher, rom, out):
    wrong = MappedRosters(game_id="nhl94-snes", teams={})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=rom, output_path=out, rosters=wrong)


def test_patch_returns_a_patch_result(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert type(result) is PatchResult


def test_patch_reports_the_output_path(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert result.output_path == str(out)


def test_patch_counts_the_slots_it_reached(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 25))
    )
    assert result.teams_patched == 2


def test_patch_counts_the_records_that_reached_the_image(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 12))
    )
    assert result.players_patched == 37


def test_patch_writes_the_output_file(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert out.exists() is True


def test_the_output_is_the_same_size_as_the_input(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert len(out.read_bytes()) == fixture.ROM_SIZE


def test_a_patched_slot_reads_back_the_names_that_were_written(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, SEA_SLOT)
    assert names[0] == "W. WRITE00"


def test_every_slot_of_a_patched_team_is_written(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, SEA_SLOT)
    assert names[-1] == "W. WRITE24"


def test_an_unpatched_slot_keeps_the_records_it_arrived_with(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    names, _ = _read_back(out, 0)
    expected = f"{fixture.player_first_initial(0, 0)}. {fixture.player_last_name(0, 0)}"
    assert names[0] == expected


def test_a_short_roster_leaves_the_slots_after_it_alone(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 3)))
    names, _ = _read_back(out, SEA_SLOT)
    initial = fixture.player_first_initial(SEA_SLOT, 4)
    expected = f"{initial}. {fixture.player_last_name(SEA_SLOT, 4)}"
    assert names[4] == expected


def test_patch_recomputes_the_snes_checksum(patcher, rom, out):
    # Upstream's orchestrator called `update_snes_checksum` explicitly, and so
    # does this one; the NBA Live 95 port does its equivalent inside `finalize`
    # instead. Both are deliberate.
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    checksum = written[fixture.CHECKSUM_OFFSET] | (written[fixture.CHECKSUM_OFFSET + 1] << 8)
    scratch = bytearray(written)
    scratch[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2] = b"\x00\x00"
    scratch[fixture.COMPLEMENT_OFFSET : fixture.COMPLEMENT_OFFSET + 2] = b"\xff\xff"
    assert checksum == sum(scratch) & 0xFFFF


def test_the_recomputed_checksum_is_not_the_one_the_image_arrived_with(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    before = rom.read_bytes()
    assert (
        written[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2]
        != before[fixture.CHECKSUM_OFFSET : fixture.CHECKSUM_OFFSET + 2]
    )


def test_patching_a_headered_image_shifts_the_checksum(patcher, tmp_path, out):
    source = fixture.write_kgj_rom(tmp_path / "kgj.smc", with_header=True)
    patcher.patch(rom_path=source, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    written = out.read_bytes()
    at = fixture.CHECKSUM_OFFSET + fixture.SMC_HEADER_SIZE
    checksum = written[at] | (written[at + 1] << 8)
    scratch = bytearray(written)
    scratch[at : at + 2] = b"\x00\x00"
    comp = fixture.COMPLEMENT_OFFSET + fixture.SMC_HEADER_SIZE
    scratch[comp : comp + 2] = b"\xff\xff"
    assert checksum == sum(scratch) & 0xFFFF


def test_an_empty_roster_is_not_written_and_not_counted(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 0))
    )
    assert result.teams_patched == 1


def test_a_negative_slot_index_is_dropped(patcher, rom, out):
    # These keys come from a plain dict that may have crossed a JSON boundary.
    # `write_team_roster` guards only the upper end, so a negative key would
    # compute an offset below the marker and overwrite whatever is there.
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[-3] = KGJTeamRecord(index=-3, name="Bogus", players=_records(25))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_a_negative_slot_index_does_not_reach_the_image(patcher, rom, out):
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[-3] = KGJTeamRecord(index=-3, name="Bogus", players=_records(25))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    below = fixture.MARKER_OFFSET - 3 * 0x320
    assert out.read_bytes()[below : below + 32] == rom.read_bytes()[below : below + 32]


def test_a_slot_index_past_the_league_is_dropped(patcher, rom, out):
    rosters = _mapped((SEA_SLOT, 25))
    rosters.teams[TEAM_COUNT] = KGJTeamRecord(index=TEAM_COUNT, name="Bogus", players=_records(25))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_patch_raises_for_an_image_with_no_marker(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_patch_raises_for_a_file_that_is_not_there(patcher, tmp_path, out):
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=tmp_path / "absent.sfc", output_path=out, rosters=_mapped((SEA_SLOT, 25))
        )


def test_patch_raises_when_the_marker_leaves_no_room(patcher, tmp_path, out):
    # DELIBERATE DIVERGENCE: upstream wrote an unmodified copy of the image and
    # returned success with zero teams patched.
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_the_no_room_error_says_how_far_the_tables_run(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError, match=str(TEAM_DATA_SPAN)):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_patch_writes_nothing_when_it_raises(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert out.exists() is False


def test_patch_raises_when_the_output_cannot_be_written(patcher, rom, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"not a directory")
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=rom, output_path=blocker / "out.sfc", rosters=_mapped((SEA_SLOT, 25))
        )


def test_patch_reports_progress_once_per_target_and_once_at_the_end(patcher, rom, out):
    seen = []
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=_mapped((SEA_SLOT, 25), (CWS_SLOT, 25)),
        on_progress=lambda f, m: seen.append(f),
    )
    assert seen == [0.0, 0.5, 1.0]


def test_patch_narrates_its_three_steps(tmp_path, rom, out):
    messages = []
    p = KGJMLBPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi(_teams())
    p.patch(rom_path=rom, output_path=out, rosters=_mapped((SEA_SLOT, 25)))
    assert messages == ["Validating ROM...", "Initializing ROM writer...", "Saving patched ROM..."]


def test_patch_does_not_mutate_the_rosters_it_was_given(patcher, rom, out, tmp_path):
    # DELIBERATE DIVERGENCE: upstream's `write_team_roster` set `roster_type` on
    # the caller's own records from the slot index, so a second patch from the
    # same `MappedRosters` could not be assumed to match the first.
    rosters = _mapped((SEA_SLOT, 25))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    again = tmp_path / "again.sfc"
    patcher.patch(rom_path=rom, output_path=again, rosters=rosters)
    assert again.read_bytes() == out.read_bytes()


def test_the_records_keep_the_roster_types_they_were_built_with(patcher, rom, out):
    rosters = _mapped((SEA_SLOT, 25))
    before = [r.roster_type for r in rosters.teams[SEA_SLOT].players]
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert [r.roster_type for r in rosters.teams[SEA_SLOT].players] == before


# -- the analyze/patch asymmetry ---------------------------------------------


def test_analyze_reports_a_readable_non_kgj_image_rather_than_raising(patcher, tmp_path):
    # `cmd_analyze` probes every registered patcher against one ROM and catches
    # `RomError` per patcher, so "not this game" must not be an exception --
    # otherwise a user's whole library reports errors instead of a clean miss.
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    assert patcher.analyze_rom(path).is_valid is False


def test_patch_raises_on_the_very_image_analyze_merely_reported(patcher, tmp_path, out):
    # The asymmetry, pinned as a pair: the same file, two different contracts.
    # `analyze` costs the user nothing by declining, and `patch` must not write
    # a copy of an image it cannot modify.
    path = fixture.write_kgj_rom(tmp_path / "nomarker.sfc", place_marker=False)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


def test_analyze_declines_and_patch_raises_on_a_marker_that_leaves_no_room(patcher, tmp_path, out):
    path = fixture.write_kgj_rom(tmp_path / "nofit.sfc", marker_offset=fixture.ROM_SIZE - 20)
    assert patcher.analyze_rom(path).is_valid is False
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((SEA_SLOT, 25)))


# -- the whole pipeline ------------------------------------------------------


def test_fetch_map_and_patch_run_end_to_end(patcher, rom, out):
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 2


def test_the_pipeline_writes_the_fetched_names_into_the_image(patcher, rom, out):
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data)
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    names, _ = _read_back(out, SEA_SLOT)
    # "First0 Last00" -> initial F, last name LAST00.
    assert names[0] == "F. LAST00"


def test_the_pipeline_leaves_an_unmatched_slot_alone(patcher, rom, out):
    data = patcher.fetch(season=2025)
    patcher.patch(rom_path=rom, output_path=out, rosters=patcher.map_rosters(data))
    names, _ = _read_back(out, 0)
    expected = f"{fixture.player_first_initial(0, 0)}. {fixture.player_last_name(0, 0)}"
    assert names[0] == expected


def test_the_pipeline_survives_a_json_round_trip_of_the_league_data(patcher, rom, out):
    from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict

    data = patcher.fetch(season=2025)
    reloaded = league_data_from_dict(league_data_to_dict(data))
    mapped = patcher.map_rosters(reloaded)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 50


# -- holes mutation testing found --------------------------------------------


class _StubMapper:
    """A mapper that answers one fixed slot, whatever team it is handed.

    `MODERN_MLB_TO_KGJ` holds no negative and no out-of-range value, so the two
    ends of `map_rosters`' bound cannot be reached through the real mapper. They
    are still a guard worth having -- `LeagueData` can arrive from a file -- and
    this is how a test reaches them.
    """

    def __init__(self, slot):
        self._slot = slot
        self.real = KGJStatMapper()

    def get_team_slot(self, code):
        return self._slot

    def select_roster(self, players, stats=None):
        return self.real.select_roster(players, stats)

    def is_pitcher(self, player):
        return self.real.is_pitcher(player)

    def map_batter(self, player, stats=None):
        return self.real.map_batter(player, stats)

    def map_pitcher(self, player, stats=None, is_starter=True):
        return self.real.map_pitcher(player, stats, is_starter=is_starter)


def test_map_rosters_drops_a_negative_slot(patcher):
    patcher.mapper = _StubMapper(-1)
    assert patcher.map_rosters(_league_data(("SEA", 25))).teams == {}


def test_map_rosters_drops_a_slot_past_the_league(patcher):
    patcher.mapper = _StubMapper(TEAM_COUNT)
    assert patcher.map_rosters(_league_data(("SEA", 25))).teams == {}


def test_map_rosters_keeps_the_last_slot_in_the_league(patcher):
    # The upper bound is exclusive, so slot 27 is in and 28 is out. Without this
    # the previous test also passes against `slot > TEAM_COUNT`.
    patcher.mapper = _StubMapper(TEAM_COUNT - 1)
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [TEAM_COUNT - 1]


def test_map_rosters_keeps_slot_zero(patcher):
    patcher.mapper = _StubMapper(0)
    assert list(patcher.map_rosters(_league_data(("SEA", 25))).teams) == [0]
