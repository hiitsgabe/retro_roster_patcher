"""The ISS patcher against the unified interface.

The reader, writer and mapper below it are a faithful port; this layer is where
the migration's own decisions live, and five of them are the subject of this
file:

  * `requires_slot_mapping=True`, which upstream did not have. It assigned club
    team *i* to national slot *i* and gave the user no way to change it.
    `default_slot_mapping` reproduces that assignment, and is the mapping the
    differential audit was driven with, but it is now a suggestion.
  * no `api_key`, which upstream took positionally and never read.
  * the arithmetic bound guarding `patch` *and* `analyze_rom`, against the
    heuristic guarding only `analyze_rom`. The two tests named
    `..._asymmetry_...` are what hold that apart.
  * `RomError` for a missing or unreadable file against `is_valid=False` for a
    readable one that is not this game, which upstream conflated.
  * the collapse of the per-team `except` block, which used to *import* two
    exception classes from a module this library no longer has.

Every read-back of a patched ROM reads the output path fresh. The writer holds
the output handle for its whole lifetime and `finalize` is what flushes it.
"""

from __future__ import annotations

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot, SlotMapping
from retro_roster_patcher.core.registry import get_patcher, list_patchers
from retro_roster_patcher.games.iss_snes.models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from retro_roster_patcher.games.iss_snes.patcher import GK_KIT, ISSPatcher, _parse_hex_colour
from retro_roster_patcher.games.iss_snes.rom_writer import MIN_PATCHABLE_SIZE, _encode_iss_name
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, Player, PlayerStats, Team
from tests.fixtures import synthetic_iss_rom as fixture


class FakeApi:
    """Stands in for `EspnClient`.

    Records the season each call was given, because upstream threaded it into
    three of its four provider calls and dropped it from `get_squad`.
    """

    def __init__(self, teams=None, squad_size=18, fail_squad=(), fail_stats=()):
        self._teams = list(teams) if teams is not None else _teams()
        self._squad_size = squad_size
        self._fail_squad = set(fail_squad)
        self._fail_stats = set(fail_stats)
        self.league_calls = []
        self.team_calls = []
        self.squad_calls = []
        self.stats_calls = []

    def get_leagues(self, id=None, season=None):
        self.league_calls.append((id, season))
        if id == 999:
            return []
        return [League(id=id, name="Premier League", season=season or 0)]

    def get_teams(self, league_id, season=None):
        self.team_calls.append((league_id, season))
        return list(self._teams)

    def get_squad(self, team_id, season=None):
        self.squad_calls.append((team_id, season))
        if team_id in self._fail_squad:
            raise RuntimeError("upstream said no")
        return [
            Player(
                id=team_id * 100 + index,
                name=f"Given Surname{team_id:02d}{index:02d}",
                position=["Goalkeeper", "Defender", "Midfielder", "Attacker"][index % 4],
                number=index + 1,
                age=20 + index % 15,
            )
            for index in range(self._squad_size)
        ]

    def get_player_stats(self, team_id, season):
        self.stats_calls.append((team_id, season))
        if team_id in self._fail_stats:
            raise RuntimeError("no statistics document")
        return [
            PlayerStats(
                player_id=team_id * 100 + index,
                appearances=10 + index,
                minutes=90 * (5 + index),
                goals=index % 7,
                assists=index % 5,
                shots_total=index * 3,
                shots_on=index,
                passes_total=100 * index,
                passes_accuracy=50.0 + index,
                tackles_total=index,
                interceptions=index,
                blocks=index,
                duels_total=0,
                duels_won=0,
                dribbles_attempts=0,
                dribbles_success=0,
                fouls_committed=index,
                fouls_drawn=index,
                cards_yellow=index % 3,
                cards_red=0,
                rating=None,
                lineups=index,
            )
            for index in range(self._squad_size)
        ]


def _teams(count=4):
    palette = [("DA020E", "FBE122"), ("FEBE10", "00529F"), ("", ""), ("003399", "")]
    return [
        Team(
            id=index + 1,
            name=f"Test Club {index}",
            code=f"T{index:02d}",
            color=palette[index % len(palette)][0],
            alternate_color=palette[index % len(palette)][1],
        )
        for index in range(count)
    ]


@pytest.fixture
def patcher(tmp_path):
    p = ISSPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi()
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_iss_rom(tmp_path / "iss.sfc")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.sfc"


def _records(count, prefix="W"):
    return [
        ISSPlayerRecord(name=f"{prefix}{index:02d}", shirt_number=index + 1)
        for index in range(count)
    ]


def _mapped(*pairs, colours=None, game_id="iss-snes"):
    teams = {}
    for slot, count in pairs:
        record = ISSTeamRecord(
            name=f"Club {slot}",
            short_name=f"C{slot:02d}",
            players=_records(count),
        )
        if colours is not None:
            record.flag_colors = list(colours)
        teams[slot] = record
    return MappedRosters(game_id=game_id, teams=teams)


# -- registration -----------------------------------------------------------


def test_the_patcher_is_registered_under_its_game_id():
    assert get_patcher("iss-snes") is ISSPatcher


def test_the_registry_describes_the_game():
    info = next(i for i in list_patchers() if i.game_id == "iss-snes")
    assert info.platform == "snes"
    assert info.sport == "soccer"
    assert info.providers == ("espn",)


def test_the_registry_declares_that_a_slot_mapping_is_required():
    """DELIBERATE DIVERGENCE from upstream, which assigned club team `i` to
    national slot `i` and offered nothing else."""
    info = next(i for i in list_patchers() if i.game_id == "iss-snes")
    assert info.requires_slot_mapping is True


# -- construction -----------------------------------------------------------


def test_the_constructor_accepts_a_string_cache_directory(tmp_path):
    p = ISSPatcher(cache_dir=str(tmp_path / "cache"))
    assert p.cache_dir == tmp_path / "cache"


def test_the_constructor_creates_the_cache_directory(tmp_path):
    ISSPatcher(cache_dir=tmp_path / "cache")
    assert (tmp_path / "cache").is_dir() is True


def test_the_constructor_refuses_an_api_key(tmp_path):
    """Upstream's first positional parameter. It was never read on the branch
    the application used, and the provider it belonged to is gone."""
    with pytest.raises(TypeError):
        ISSPatcher(cache_dir=tmp_path / "cache", api_key="secret")


def test_the_constructor_refuses_a_provider_this_game_does_not_have(tmp_path):
    with pytest.raises(CapabilityError):
        ISSPatcher(cache_dir=tmp_path / "cache", provider="nhl")


def test_the_constructor_accepts_the_one_provider_it_does_have(tmp_path):
    p = ISSPatcher(cache_dir=tmp_path / "cache", provider="espn")
    assert p.provider == "espn"


def test_the_provider_defaults_to_espn(tmp_path):
    assert ISSPatcher(cache_dir=tmp_path / "cache").provider == "espn"


def test_the_client_is_built_eagerly(tmp_path):
    assert type(ISSPatcher(cache_dir=tmp_path / "cache").api) is EspnClient


# -- analyze_rom ------------------------------------------------------------


def test_analyze_reports_a_valid_rom(patcher, rom):
    info = patcher.analyze_rom(rom)
    assert info.is_valid is True
    assert info.game_id == "iss-snes"
    assert info.size == fixture.ROM_SIZE


def test_analyze_reports_twenty_seven_slots(patcher, rom):
    assert len(patcher.analyze_rom(rom).slots) == TOTAL_TEAMS


def test_the_slots_display_the_national_side_they_belong_to(patcher, rom):
    slots = patcher.analyze_rom(rom).slots
    assert [slot.display_name for slot in slots] == TEAM_ENUM_ORDER


def test_every_display_name_is_distinct(patcher, rom):
    """This is the game that *requires* a slot mapping, so it is the one whose
    slots a UI has to list."""
    slots = patcher.analyze_rom(rom).slots
    assert len({slot.display_name for slot in slots}) == TOTAL_TEAMS


def test_the_current_name_is_labelled_as_a_player(patcher, rom):
    """DELIBERATE DIVERGENCE: upstream put the constant team name here and read
    nothing from the image. This reader parses no team name, so a player's is
    the only ROM-derived text available."""
    slots = patcher.analyze_rom(rom).slots
    assert slots[0].current_name == f"First player: {fixture.player_name(0, 0)}"


def test_scotlands_slot_reports_scotlands_first_player(patcher, rom):
    slots = patcher.analyze_rom(rom).slots
    assert slots[5].current_name == f"First player: {fixture.player_name(24, 0)}"


def test_no_two_slots_report_the_same_current_name(patcher, rom):
    slots = patcher.analyze_rom(rom).slots
    assert len({slot.current_name for slot in slots}) == TOTAL_TEAMS


def test_the_slots_are_the_public_rom_slot_type(patcher, rom):
    assert type(patcher.analyze_rom(rom).slots[0]) is RomSlot


def test_analyze_carries_the_copier_header_flag(patcher, tmp_path):
    headered = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    assert patcher.analyze_rom(headered).extra == {"has_header": True}


def test_analyze_reports_no_header_for_a_headerless_image(patcher, rom):
    assert patcher.analyze_rom(rom).extra == {"has_header": False}


def test_analyze_raises_for_a_missing_file(patcher, tmp_path):
    with pytest.raises(RomError, match="ROM not found"):
        patcher.analyze_rom(tmp_path / "nope.sfc")


def test_analyze_reports_a_readable_file_of_another_game_as_invalid(patcher, tmp_path):
    """The distinction `cmd_analyze` depends on: it catches `RomError` per
    patcher and continues, and treats `is_valid=False` as a considered no."""
    other = tmp_path / "other.bin"
    other.write_bytes(bytes(fixture._filler(fixture.ROM_SIZE)))
    info = patcher.analyze_rom(other)
    assert info.is_valid is False
    assert info.slots == []


def test_analyze_reports_a_small_file_as_invalid_rather_than_raising(patcher, tmp_path):
    other = tmp_path / "tiny.bin"
    other.write_bytes(b"\x00" * 4096)
    assert patcher.analyze_rom(other).is_valid is False


def test_analyze_raises_for_an_unreadable_file(patcher, tmp_path):
    import os

    path = fixture.write_iss_rom(tmp_path / "iss.sfc")
    path.chmod(0o000)
    try:
        if os.access(path, os.R_OK):  # pragma: no cover - root ignores the mode
            pytest.skip("running as a user the mode does not restrict")
        with pytest.raises(RomError):
            patcher.analyze_rom(path)
    finally:
        path.chmod(0o600)


# -- the two guards, and which entry point each one holds --------------------


def test_the_asymmetry_analyze_refuses_the_signature_but_patch_does_not(patcher, tmp_path, out):
    """The plan's rule, pinned. A content heuristic is a *guess*, so a false
    negative must not block a patch the user asked for by name. This image is
    the right size and its pointer tables are filler."""
    path = tmp_path / "unsigned.sfc"
    body = bytearray(fixture.build_iss_rom())
    # Break the description table only: enough for the heuristic, and it leaves
    # the writer able to complete.
    for i in range(TOTAL_TEAMS):
        body[fixture.OFS_DESC_PTRS + i * 2 : fixture.OFS_DESC_PTRS + i * 2 + 2] = b"\x00\x40"
    path.write_bytes(bytes(body))

    assert patcher.analyze_rom(path).is_valid is False
    result = patcher.patch(rom_path=path, output_path=out, rosters=_mapped((0, 15)))
    assert result.teams_patched == 1


def test_the_asymmetry_the_arithmetic_bound_stops_both(patcher, tmp_path, out):
    """The other half of the same rule. A file too short provably cannot be
    patched, so exempting `patch` would preserve the success-with-nothing-written
    lie the bound exists to kill."""
    path = tmp_path / "short.sfc"
    path.write_bytes(bytes(fixture.build_iss_rom(size=MIN_PATCHABLE_SIZE))[:-1])
    assert patcher.analyze_rom(path).is_valid is False
    with pytest.raises(RomError, match="Too small"):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((0, 15)))


def test_a_file_in_the_band_between_the_two_guards_patches(patcher, tmp_path, out):
    """Large enough for the writer, below the 1 MB cartridge floor. `analyze`
    says no and `patch` goes ahead, which is the asymmetry stated as one case."""
    path = fixture.write_iss_rom(tmp_path / "band.sfc", size=MIN_PATCHABLE_SIZE)
    assert patcher.analyze_rom(path).is_valid is False
    result = patcher.patch(rom_path=path, output_path=out, rosters=_mapped((0, 15)))
    assert result.teams_patched == 1


def test_a_refused_patch_writes_no_output_file(patcher, tmp_path, out):
    path = tmp_path / "short.sfc"
    path.write_bytes(b"\x00" * 4096)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((0, 15)))
    assert out.exists() is False


def test_patch_raises_for_a_missing_rom(patcher, tmp_path, out):
    with pytest.raises(RomError, match="ROM not found"):
        patcher.patch(rom_path=tmp_path / "nope.sfc", output_path=out, rosters=_mapped((0, 15)))


# -- fetch ------------------------------------------------------------------


def test_fetch_requires_a_league_id(patcher):
    with pytest.raises(CapabilityError, match="league_id"):
        patcher.fetch(season=2025)


def test_fetch_raises_when_the_league_is_unknown(patcher):
    """Upstream raised a bare `ValueError`, outside this library's hierarchy."""
    with pytest.raises(ApiError, match="not found"):
        patcher.fetch(season=2025, league_id=999)


def test_fetch_raises_when_the_league_has_no_teams(patcher):
    patcher.api = FakeApi(teams=[])
    with pytest.raises(ApiError, match="no teams"):
        patcher.fetch(season=2025, league_id=2001)


def test_fetch_returns_one_roster_per_team(patcher):
    data = patcher.fetch(season=2025, league_id=2001)
    assert len(data.teams) == 4


def test_fetch_threads_the_season_into_the_squad_call(patcher):
    """DELIBERATE DIVERGENCE: upstream called `get_squad(team.id)` with no
    season. The endpoint has none in its URL but the cache key does, so the
    first season ever fetched was served forever after."""
    patcher.fetch(season=2019, league_id=2001)
    assert patcher.api.squad_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_fetch_threads_the_season_into_the_statistics_call(patcher):
    patcher.fetch(season=2019, league_id=2001)
    assert patcher.api.stats_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_fetch_threads_the_season_into_the_league_and_team_calls(patcher):
    patcher.fetch(season=2019, league_id=2001)
    assert patcher.api.league_calls == [(2001, 2019)]
    assert patcher.api.team_calls == [(2001, 2019)]


def test_a_failing_squad_costs_that_team_and_no_other(patcher):
    """The arm that used to *import* `RateLimitError` from a deleted module,
    inside the handler, and so raised `ModuleNotFoundError` out of it."""
    patcher.api = FakeApi(fail_squad={2})
    data = patcher.fetch(season=2025, league_id=2001)
    failed = next(t for t in data.teams if t.team.id == 2)
    assert failed.error == "Failed: upstream said no"
    assert failed.players == []


def test_the_other_teams_survive_a_failing_squad(patcher):
    patcher.api = FakeApi(fail_squad={2})
    data = patcher.fetch(season=2025, league_id=2001)
    assert [len(t.players) for t in data.teams] == [18, 0, 18, 18]


def test_a_failing_statistics_call_costs_ratings_and_not_the_team(patcher):
    patcher.api = FakeApi(fail_stats={2})
    data = patcher.fetch(season=2025, league_id=2001)
    hurt = next(t for t in data.teams if t.team.id == 2)
    assert hurt.error == ""
    assert len(hurt.players) == 18
    assert hurt.player_stats == {}


def test_a_failing_statistics_call_is_reported_through_on_status(tmp_path):
    messages = []
    p = ISSPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi(fail_stats={2})
    p.fetch(season=2025, league_id=2001)
    assert any("stats unavailable" in message for message in messages)


def test_the_partial_callback_fires_before_the_squads_load(tmp_path):
    seen = []
    p = ISSPatcher(cache_dir=tmp_path / "cache", on_partial=seen.append)
    p.api = FakeApi()
    p.fetch(season=2025, league_id=2001)
    assert len(seen) == 1
    assert [t.loading for t in seen[0].teams] == [True] * 4


def test_the_partial_snapshot_is_not_the_object_fetch_returns(tmp_path):
    """A caller may still be rendering it, so `fetch` builds fresh rosters."""
    seen = []
    p = ISSPatcher(cache_dir=tmp_path / "cache", on_partial=seen.append)
    p.api = FakeApi()
    data = p.fetch(season=2025, league_id=2001)
    assert seen[0] is not data
    assert [t.loading for t in seen[0].teams] == [True] * 4


def test_fetch_reports_progress_from_start_to_finish(patcher):
    steps = []
    patcher.fetch(season=2025, league_id=2001, on_progress=lambda p, m: steps.append(p))
    assert steps[0] == 0.05
    assert steps[-1] == 1.0


def test_fetch_keys_the_player_stats_by_player_id(patcher):
    data = patcher.fetch(season=2025, league_id=2001)
    first = data.teams[0]
    assert set(first.player_stats) == {p.id for p in first.players}


# -- map_rosters ------------------------------------------------------------


def _league(patcher):
    return patcher.fetch(season=2025, league_id=2001)


def test_map_rosters_requires_a_slot_mapping(patcher):
    with pytest.raises(CapabilityError, match="requires a slot mapping"):
        patcher.map_rosters(_league(patcher))


def test_map_rosters_refuses_an_empty_slot_mapping(patcher):
    with pytest.raises(CapabilityError):
        patcher.map_rosters(_league(patcher), [])


def test_map_rosters_keys_the_result_by_slot_index(patcher):
    data = _league(patcher)
    mapping = [SlotMapping(slot_index=9, team_id=2), SlotMapping(slot_index=3, team_id=1)]
    assert set(patcher.map_rosters(data, mapping).teams) == {3, 9}


def test_map_rosters_stamps_the_game_id(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)])
    assert mapped.game_id == "iss-snes"


def test_map_rosters_selects_fifteen_players_a_side(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)])
    assert len(mapped.teams[0].players) == PLAYERS_PER_TEAM


def test_map_rosters_refuses_a_slot_past_the_last_one(patcher):
    data = _league(patcher)
    with pytest.raises(MappingError, match="outside the ISS range"):
        patcher.map_rosters(data, [SlotMapping(slot_index=TOTAL_TEAMS, team_id=1)])


def test_map_rosters_refuses_a_negative_slot(patcher):
    data = _league(patcher)
    with pytest.raises(MappingError, match="outside the ISS range"):
        patcher.map_rosters(data, [SlotMapping(slot_index=-1, team_id=1)])


def test_map_rosters_refuses_a_team_the_league_data_does_not_hold(patcher):
    data = _league(patcher)
    with pytest.raises(MappingError, match="not in the fetched league data"):
        patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=4242)])


def test_map_rosters_refuses_the_same_slot_twice(patcher):
    """DELIBERATE DIVERGENCE, with no upstream equivalent. Every entry here is
    something the caller typed, unlike the abbreviation-matched games where two
    provider aliases can legitimately reach one slot."""
    data = _league(patcher)
    mapping = [SlotMapping(slot_index=4, team_id=1), SlotMapping(slot_index=4, team_id=2)]
    with pytest.raises(MappingError, match="mapped more than once"):
        patcher.map_rosters(data, mapping)


def test_the_duplicate_message_names_the_national_side(patcher):
    data = _league(patcher)
    mapping = [SlotMapping(slot_index=4, team_id=1), SlotMapping(slot_index=4, team_id=2)]
    with pytest.raises(MappingError, match="England"):
        patcher.map_rosters(data, mapping)


def test_the_primary_colour_becomes_the_home_shirt_and_socks(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)])
    assert mapped.teams[0].kit_home == ((0xDA, 0x02, 0x0E), (255, 255, 255), (0xDA, 0x02, 0x0E))


def test_the_alternate_colour_becomes_the_away_shirt_and_socks(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)])
    assert mapped.teams[0].kit_away == ((0xFB, 0xE1, 0x22), (255, 255, 255), (0xFB, 0xE1, 0x22))


def test_every_team_gets_the_same_goalkeeper_kit(patcher):
    """A green shirt and black shorts, written out rather than compared against
    the constant this is meant to pin. No provider publishes a goalkeeper kit,
    so this is the same for all 27 slots."""
    data = _league(patcher)
    mapping = [SlotMapping(slot_index=i, team_id=i + 1) for i in range(4)]
    mapped = patcher.map_rosters(data, mapping)
    assert {record.kit_gk for record in mapped.teams.values()} == {((0, 128, 0), (0, 0, 0))}
    assert GK_KIT == ((0, 128, 0), (0, 0, 0))


def test_the_flag_colours_are_the_primary_and_the_alternate(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=1)])
    assert mapped.teams[0].flag_colors == [(0xDA, 0x02, 0x0E), (0xFB, 0xE1, 0x22)]


def test_a_team_with_no_alternate_colour_uses_its_primary_twice(patcher):
    """Upstream's `elif primary` branch, moved onto the record."""
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=4)])
    assert mapped.teams[0].flag_colors == [(0x00, 0x33, 0x99), (0x00, 0x33, 0x99)]


def test_a_team_with_no_colours_at_all_gets_no_flag(patcher):
    """Empty means the provider gave no primary, which is exactly when upstream
    wrote neither the flag nor the predominant-colour byte."""
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=3)])
    assert mapped.teams[0].flag_colors == []


def test_a_team_with_no_colours_keeps_the_roms_own_kit(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, [SlotMapping(slot_index=0, team_id=3)])
    assert mapped.teams[0].kit_home == ()
    assert mapped.teams[0].kit_away == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("DA020E", (0xDA, 0x02, 0x0E)),
        ("#DA020E", (0xDA, 0x02, 0x0E)),
        ("", None),
        ("F00", None),
        ("red", None),
        ("GGGGGG", None),
    ],
)
def test_the_colour_parser_accepts_only_six_hex_digits(text, expected):
    assert _parse_hex_colour(text) == expected


# -- default_slot_mapping ---------------------------------------------------


def test_the_default_mapping_is_upstreams_sequential_assignment(patcher):
    """The mapping the differential audit was driven with, so the byte
    comparison against upstream stayed meaningful."""
    data = _league(patcher)
    mapping = patcher.default_slot_mapping(data)
    assert [(m.slot_index, m.team_id) for m in mapping] == [(0, 1), (1, 2), (2, 3), (3, 4)]


def test_the_default_mapping_carries_the_team_name(patcher):
    data = _league(patcher)
    assert patcher.default_slot_mapping(data)[0].team_name == "Test Club 0"


def test_the_default_mapping_stops_at_twenty_seven(patcher):
    patcher.api = FakeApi(teams=_teams(40))
    data = _league(patcher)
    assert len(patcher.default_slot_mapping(data)) == TOTAL_TEAMS


def test_the_default_mapping_is_the_public_slot_mapping_type(patcher):
    data = _league(patcher)
    assert type(patcher.default_slot_mapping(data)[0]) is SlotMapping


def test_the_default_mapping_round_trips_through_map_rosters(patcher):
    data = _league(patcher)
    mapped = patcher.map_rosters(data, patcher.default_slot_mapping(data))
    assert sorted(mapped.teams) == [0, 1, 2, 3]


# -- patch ------------------------------------------------------------------


def test_patch_refuses_rosters_mapped_for_another_game(patcher, rom, out):
    rosters = _mapped((0, 15), game_id="we2002")
    with pytest.raises(MappingError, match="we2002"):
        patcher.patch(rom_path=rom, output_path=out, rosters=rosters)


def test_patch_returns_the_public_result_type(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert type(result) is PatchResult


def test_patch_reports_the_output_path(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert result.output_path == str(out)


def test_patch_counts_one_team_per_in_range_slot(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15), (9, 15)))
    assert result.teams_patched == 2


def test_patch_counts_a_slot_with_no_players_as_patched(patcher, rom, out):
    """As WE2002 does, and `PatchResult` says why: the name, the kit and the
    description land whether or not a squad was supplied."""
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 0)))
    assert result.teams_patched == 1
    assert result.players_patched == 0


def test_patch_counts_the_players_that_reached_the_image(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15), (9, 4)))
    assert result.players_patched == 19


def test_patch_does_not_count_players_beyond_the_fifteenth(patcher, rom, out):
    """`core/models.py` defines the field as records that reached the image."""
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 22)))
    assert result.players_patched == PLAYERS_PER_TEAM


def test_patch_skips_a_slot_outside_the_range(patcher, rom, out):
    """The keys come from a plain dict that may have crossed a JSON boundary."""
    rosters = _mapped((0, 15))
    rosters.teams[99] = ISSTeamRecord(name="X", short_name="XXX", players=_records(15))
    rosters.teams[-2] = ISSTeamRecord(name="Y", short_name="YYY", players=_records(15))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_the_patched_names_reach_the_slots_own_storage_block(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((5, 15)))
    scotland = fixture.OFS_PLAYER_NAMES + 24 * PLAYERS_PER_TEAM * 8
    assert out.read_bytes()[scotland : scotland + 8] == _encode_iss_name("W00")


def test_an_unmapped_slot_keeps_its_original_players(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    block = fixture.OFS_PLAYER_NAMES + 10 * PLAYERS_PER_TEAM * 8
    assert out.read_bytes()[block : block + 8] == fixture.encode_name(fixture.player_name(10, 0))


def test_the_machine_code_patch_is_applied_by_a_whole_patch_run(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    data = out.read_bytes()
    assert [data[p] for p in fixture.DISPLACEMENT_PATCH_POINTS] == [fixture.PATCHED_BANK_BYTE] * 10


def test_a_patch_run_writes_the_flag_and_predominant_colour_for_a_coloured_team(patcher, rom, out):
    rosters = _mapped((0, 15), colours=[(255, 0, 0), (0, 0, 255)])
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert out.read_bytes()[fixture.OFS_PREDOMINANT_COLOR] == 2


def test_the_second_flag_colour_reaches_the_palette_as_the_alternate(patcher, rom, out):
    """Both entries, not just the first: `patch` hands the writer a pair, and a
    pair built from the primary twice would still fill the table."""
    rosters = _mapped((0, 15), colours=[(255, 0, 0), (0, 0, 255)])
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    # Germany is position 0 of the first flag-colour range.
    start = fixture.OFS_FLAG_COLORS_RANGE1
    palette = out.read_bytes()[start : start + 4]
    assert palette[0:2] == (0x001F).to_bytes(2, "little")
    assert palette[2:4] == (0x7C00).to_bytes(2, "little")


def test_a_team_with_no_flag_colours_leaves_the_predominant_byte_alone(patcher, rom, out):
    before = rom.read_bytes()[fixture.OFS_PREDOMINANT_COLOR]
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert out.read_bytes()[fixture.OFS_PREDOMINANT_COLOR] == before


def test_patch_reports_progress_from_start_to_finish(patcher, rom, out):
    steps = []
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=_mapped((0, 15)),
        on_progress=lambda p, m: steps.append(p),
    )
    assert steps[0] == 0.0
    assert steps[-1] == 1.0


def test_patch_writes_an_output_that_differs_from_the_input(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert out.read_bytes() != rom.read_bytes()


def test_patch_leaves_the_input_untouched(patcher, rom, out):
    before = rom.read_bytes()
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert rom.read_bytes() == before


def test_the_output_is_the_same_size_as_the_input(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert out.stat().st_size == rom.stat().st_size


def test_a_headered_image_patches_through_the_header(patcher, tmp_path, out):
    headered = fixture.write_iss_rom(tmp_path / "iss.smc", with_header=True)
    patcher.patch(rom_path=headered, output_path=out, rosters=_mapped((0, 15)))
    data = out.read_bytes()
    assert data[fixture.OFS_PLAYER_NAMES + 512 : fixture.OFS_PLAYER_NAMES + 520] == (
        _encode_iss_name("W00")
    )


def test_a_patch_that_cannot_fit_its_name_tiles_raises(patcher, tmp_path, out):
    rom = fixture.write_iss_rom(tmp_path / "fat.sfc", name_tile_blob_size=91)
    with pytest.raises(RomError, match="Name tiles too large"):
        patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))


def test_a_patch_that_raises_leaves_no_open_handle(patcher, tmp_path, out):
    """The context manager. Upstream's only release was `finalize`, so a raise
    between the constructor and it leaked the descriptor."""
    rom = fixture.write_iss_rom(tmp_path / "fat.sfc", name_tile_blob_size=91)
    with pytest.raises(RomError):
        patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    # The partial output exists -- the constructor copied it -- and is closed,
    # so it can be replaced.
    out.write_bytes(b"replaced")
    assert out.read_bytes() == b"replaced"


def test_the_whole_league_patches_in_one_run(patcher, rom, out):
    rosters = _mapped(*[(slot, 15) for slot in range(TOTAL_TEAMS)])
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == TOTAL_TEAMS
    assert result.players_patched == TOTAL_TEAMS * PLAYERS_PER_TEAM


def test_an_end_to_end_run_from_fetch_to_a_patched_image(patcher, rom, out):
    data = patcher.fetch(season=2025, league_id=2001)
    rosters = patcher.map_rosters(data, patcher.default_slot_mapping(data))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 4
    assert result.players_patched == 4 * PLAYERS_PER_TEAM


def test_the_end_to_end_run_reaches_the_image(patcher, rom, out):
    data = patcher.fetch(season=2025, league_id=2001)
    rosters = patcher.map_rosters(data, patcher.default_slot_mapping(data))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    first = rosters.teams[0].players[0].name
    assert out.read_bytes()[fixture.OFS_PLAYER_NAMES : fixture.OFS_PLAYER_NAMES + 8] == (
        _encode_iss_name(first)
    )


def test_the_patched_slot_reports_its_new_first_player_when_analysed_again(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert patcher.analyze_rom(out).slots[0].current_name == "First player: W00"


def test_analysing_a_patched_image_still_recognises_it(patcher, rom, out):
    """The three pointer tables the writer rewrites must still dereference."""
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert patcher.analyze_rom(out).is_valid is True


def test_patching_twice_gives_the_same_image(patcher, rom, tmp_path):
    """Deterministic, and the second run reads a ROM the first one rewrote --
    the name-tile pointer table now holds P17000 pointers, and `write_name_tiles`
    reads it back as P48000."""
    first = tmp_path / "first.sfc"
    second = tmp_path / "second.sfc"
    patcher.patch(rom_path=rom, output_path=first, rosters=_mapped((0, 15)))
    patcher.patch(rom_path=rom, output_path=second, rosters=_mapped((0, 15)))
    assert first.read_bytes() == second.read_bytes()


def test_a_different_roster_gives_a_different_image(patcher, rom, tmp_path):
    """Guards every equality above from being satisfied by a writer that wrote
    the same thing whatever it was handed."""
    first = tmp_path / "first.sfc"
    second = tmp_path / "second.sfc"
    patcher.patch(rom_path=rom, output_path=first, rosters=_mapped((0, 15), colours=None))
    other = _mapped((0, 15))
    other.teams[0].players = _records(15, prefix="Z")
    patcher.patch(rom_path=rom, output_path=second, rosters=other)
    assert first.read_bytes() != second.read_bytes()


def test_the_status_channel_narrates_a_patch(tmp_path, rom, out):
    messages = []
    p = ISSPatcher(cache_dir=tmp_path / "cache", on_status=messages.append)
    p.api = FakeApi()
    p.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)))
    assert messages == ["Preparing ROM...", "Saving patched ROM..."]


def test_patch_ignores_an_option_it_does_not_understand(patcher, rom, out):
    """`**options` is the interface's shape; this game honours none of them."""
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((0, 15)), language="fr")
    assert result.teams_patched == 1


def test_this_game_ships_no_translations(patcher):
    assert hasattr(patcher, "languages") is False
