"""`MVPPSPPatcher` against the unified interface.

Six things here are not in the ported code at all, and each has its own section:

  * `_database_big_extent_fits`, the arithmetic bound, which guards `patch` as
    well as `analyze_rom`;
  * `validate_deep`, the heuristic, which guards `analyze_rom` only -- and the
    asymmetry between the two is pinned so nobody harmonises it;
  * `TeamRoster.extra["leaders"]` in place of the `self.team_stats` instance
    side channel the pygame front end had to copy between two patcher
    instances by hand;
  * `season` threaded into the squad call, which the source omitted;
  * the alias guard, without which an empty `ATH` wipes a populated `OAK`;
  * `fetch -> map_rosters -> patch` as three separable steps, where
    `patch_rom` re-ran the mapping itself.

And the three bug fixes reach the disc here: the pitcher arsenal, the section
that does not fit, and height and weight.

Every read-back of a patched image goes through the fixture's own
`read_database_big`, `decompress_section_at` and `parse_table`, never through
the reader and writer that produced it.
"""

from __future__ import annotations

import os
import random

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, RomSlot
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.mvp_psp import models as mvp_models
from retro_roster_patcher.games.mvp_psp.models import (
    AL_SLOT_COUNT,
    ATTRIB_BIRTHDAY,
    ATTRIB_FIRST_NAME,
    ATTRIB_HEIGHT,
    ATTRIB_JERSEY,
    ATTRIB_LAST_NAME,
    ATTRIB_PRIMARY_POS,
    ATTRIB_SALARY,
    ATTRIB_SECONDARY_POS,
    ATTRIB_SPEED,
    ATTRIB_WEIGHT,
    BULLPEN_POSITIONS,
    HASH_ID_CHARS,
    LINEUP_POSITIONS,
    LR_CONTACT,
    LR_FIRST_NAME,
    LR_POWER,
    LR_SPRAY_UL,
    MAX_EXTRA_PITCHES,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    NOT_IN_LINEUP,
    PA_PITCH1_VELOCITY,
    PA_PITCH2_TYPE,
    PA_PITCH_STRIDE,
    PA_PITCHER_DELIVERY,
    PA_STAMINA,
    PITCH_CHANGEUP,
    PITCH_FASTBALL,
    PITCH_SLIDER,
    ROSTER_LH_AL_ORDER,
    ROSTER_LH_NL_ORDER,
    ROSTER_PLAYERID,
    ROSTER_RH_AL_ORDER,
    ROSTER_RH_AL_POS,
    ROSTER_RH_NL_ORDER,
    ROSTER_TEAMID,
    ROTATION_POSITIONS,
    TEAM_COUNT,
    TEAM_HASHES,
    MVPPitch,
    MVPPlayerRecord,
    database_big_extent,
)
from retro_roster_patcher.games.mvp_psp.patcher import (
    PROGRESS_RECORDS_END,
    MVPPSPPatcher,
    _database_big_extent_fits,
    _HashPool,
)
from retro_roster_patcher.games.mvp_psp.rom_writer import SectionTooLargeError
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_mvp_iso as fixture


@pytest.fixture(autouse=True)
def small_layout(monkeypatch):
    """Every test here but the extent-bound section uses the shrunken layout."""
    fixture.use_small_layout(monkeypatch)


class FakeApi:
    """Stands in for `EspnClient`, recording every call.

    `season` reaches the two endpoints differently -- a cache key on the squad
    call, a URL path segment on the leaders call -- and the source passed it
    only to the second, so both call lists are kept.
    """

    def __init__(self, teams=None, squads=None, leaders=None):
        self._teams = default_teams() if teams is None else teams
        self._squads = squads or {}
        self._leaders = leaders or {}
        self.squad_calls: list[tuple] = []
        self.leader_calls: list[tuple] = []

    def get_mlb_teams(self):
        return list(self._teams)

    def get_baseball_squad(self, team_id, season=None):
        self.squad_calls.append((team_id, season))
        return list(self._squads.get(team_id, []))

    def get_baseball_team_leaders(self, team_id, season=None):
        self.leader_calls.append((team_id, season))
        return dict(self._leaders.get(team_id, {}))


def default_teams():
    """Four clubs spanning the American League boundary at slot 14."""
    return [
        Team(id=1, name="Los Angeles Angels", code="LAA"),  # slot 0, AL
        Team(id=2, name="Toronto Blue Jays", code="TOR"),  # slot 13, AL
        Team(id=3, name="Arizona Diamondbacks", code="ARI"),  # slot 14, NL
        Team(id=4, name="Colorado Rockies", code="COL"),  # slot 15, NL
    ]


TEAM_SLOTS = {1: 0, 2: 13, 3: 14, 4: 15}


def make_player(pid, position, *, weight=0.0, number=None, name=None):
    return Player(
        id=pid,
        name=name or f"Given{pid} Family{pid}",
        position=position,
        number=number if number is not None else (pid % 60) + 1,
        weight=weight,
        handedness="L" if pid % 2 else "R",
        bats="S" if pid % 5 == 0 else "R",
    )


#: Fifteen batters, five listed starters and five relievers, so `select_roster`
#: fills all twenty-five slots and the last bullpen role is really assigned.
FULL_SQUAD_POSITIONS = (
    ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH", "OF", "IF", "C", "1B", "LF", "RF"]
    + ["SP"] * 5
    + ["RP", "CL", "CP", "P", "RP"]
)


def full_squad(base, *, weight=0.0):
    return [make_player(base + i, p, weight=weight) for i, p in enumerate(FULL_SQUAD_POSITIONS)]


def make_patcher(tmp_path, api=None, **kwargs):
    patcher = MVPPSPPatcher(tmp_path / "cache", **kwargs)
    patcher.api = api if api is not None else FakeApi()
    return patcher


def write_iso(tmp_path, spec=None, *, name="game.iso"):
    path = tmp_path / name
    path.write_bytes(fixture.build_iso(fixture.build_database_big(spec)))
    return path


DISC = fixture.DiscSpec(teams=30, players_per_team=20)

#: A disc whose `attrib` table is padded to within 600 bytes of its 61 448-byte
#: allocation. It is a disc that exists -- every section fits -- and on which a
#: roster patch that lengthens any name cannot be stored. That is the condition
#: the source swallowed by keeping the original table and reporting success.
FULL_DISC = fixture.DiscSpec(teams=4, players_per_team=6, attrib_headroom_bytes=800)


def long_named_squad():
    """Twenty-five players with long names that do not compress.

    Repeating a word twenty-five times would not do: RefPack is LZ77, so the
    second copy of a long name costs almost nothing and the section would not
    grow. These are distinct pseudo-random hex, seeded so the disc is the same
    on every run, which is the case a real roster of 750 different surnames
    approximates.
    """
    rng = random.Random(0x424C4F42)
    return [
        Player(
            id=5000 + i,
            name="".join(rng.choice("0123456789abcdef") for _ in range(40))
            + " "
            + "".join(rng.choice("0123456789abcdef") for _ in range(40)),
            position=p,
        )
        for i, p in enumerate(FULL_SQUAD_POSITIONS)
    ]


def league(*, squads, leaders=None):
    """A `LeagueData` shaped the way `fetch` builds one."""
    return LeagueData(
        league=League(id=0, name="MLB", season=2025, teams_count=len(squads)),
        teams=[
            TeamRoster(
                team=team,
                players=squads.get(team.id, []),
                extra={"leaders": (leaders or {}).get(team.id, {})},
            )
            for team in default_teams()
        ],
    )


def patched_table(path, name):
    """One table of a patched image, read back with the fixture's own walk."""
    blob = fixture.read_database_big(path.read_bytes(), lba=fixture.SMALL_LBA)
    return fixture.parse_table(fixture.decompress_section_at(blob, name))


def roster_rows(path, slot):
    """The `roster` rows a patched image holds for one team slot."""
    team_hash = TEAM_HASHES[MVP_TEAM_ABBREVS[slot]]
    table = patched_table(path, "roster")
    return {rid: cols for rid, cols in table.items() if cols.get(ROSTER_TEAMID) == team_hash}


# -- registration ----------------------------------------------------------


def test_the_patcher_is_registered_under_its_game_id():
    assert get_patcher("mvp-psp") is MVPPSPPatcher


def test_the_platform_is_the_psp():
    assert MVPPSPPatcher.platform == "psp"


def test_the_sport_is_baseball():
    assert MVPPSPPatcher.sport == "baseball"


def test_teams_are_matched_automatically():
    assert MVPPSPPatcher.requires_slot_mapping is False


def test_espn_is_the_only_provider():
    assert MVPPSPPatcher.providers == ("espn",)


# -- construction ----------------------------------------------------------


def test_the_client_is_built_eagerly(tmp_path):
    assert type(MVPPSPPatcher(tmp_path / "cache").api) is EspnClient


def test_the_cache_directory_accepts_a_string(tmp_path):
    patcher = MVPPSPPatcher(str(tmp_path / "cache"))
    assert patcher.cache_dir == tmp_path / "cache"


def test_an_api_key_is_refused(tmp_path):
    # Not accepted and ignored: a parameter that silently does nothing lets a
    # caller believe a credential is in use.
    with pytest.raises(TypeError):
        MVPPSPPatcher(tmp_path / "cache", api_key="secret")


def test_an_unsupported_provider_is_refused(tmp_path):
    with pytest.raises(CapabilityError):
        MVPPSPPatcher(tmp_path / "cache", provider="nhl")


def test_the_default_provider_is_espn(tmp_path):
    assert MVPPSPPatcher(tmp_path / "cache").provider == "espn"


# -- the arithmetic bound --------------------------------------------------


def test_a_file_the_size_of_the_extent_fits(tmp_path, monkeypatch):
    monkeypatch.undo()
    path = tmp_path / "exact.iso"
    _, end = database_big_extent()
    with open(path, "wb") as f:
        f.truncate(end)
    assert _database_big_extent_fits(path) is True


def test_a_file_one_byte_short_of_the_extent_does_not_fit(tmp_path, monkeypatch):
    monkeypatch.undo()
    path = tmp_path / "short.iso"
    _, end = database_big_extent()
    with open(path, "wb") as f:
        f.truncate(end - 1)
    assert _database_big_extent_fits(path) is False


def test_the_real_extent_needs_686122913_bytes(monkeypatch):
    # The arithmetic spelled out: 334832 * 2048 + 386977. Pinned against the
    # unpatched constants, on a sparse file that is never copied.
    monkeypatch.undo()
    _, end = database_big_extent()
    assert end == 686122913


def test_a_missing_file_does_not_fit(tmp_path):
    assert _database_big_extent_fits(tmp_path / "gone.iso") is False


def test_patching_a_file_shorter_than_the_extent_raises(tmp_path, monkeypatch):
    monkeypatch.undo()
    path = tmp_path / "short.iso"
    _, end = database_big_extent()
    with open(path, "wb") as f:
        f.truncate(end - 1)
    patcher = make_patcher(tmp_path)
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="mvp-psp", teams={}),
        )


def test_the_raised_message_names_both_numbers(tmp_path, monkeypatch):
    monkeypatch.undo()
    path = tmp_path / "short.iso"
    start, end = database_big_extent()
    with open(path, "wb") as f:
        f.truncate(end - 1)
    patcher = make_patcher(tmp_path)
    with pytest.raises(RomError) as excinfo:
        patcher.patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="mvp-psp", teams={}),
        )
    assert (str(start) in str(excinfo.value), str(end - 1) in str(excinfo.value)) == (True, True)


def test_analyzing_a_file_shorter_than_the_extent_is_not_an_error(tmp_path, monkeypatch):
    # `analyze` probes every registered patcher against one image, so a file
    # that is not this game must not raise.
    monkeypatch.undo()
    path = tmp_path / "short.iso"
    _, end = database_big_extent()
    with open(path, "wb") as f:
        f.truncate(end - 1)
    assert make_patcher(tmp_path).analyze_rom(path).is_valid is False


# -- analyze_rom -----------------------------------------------------------


def test_analyzing_a_missing_file_raises(tmp_path):
    with pytest.raises(RomError):
        make_patcher(tmp_path).analyze_rom(tmp_path / "gone.iso")


def test_analyzing_answers_the_libraries_own_type(tmp_path):
    assert type(make_patcher(tmp_path).analyze_rom(write_iso(tmp_path))) is RomInfo


def test_a_real_image_is_valid(tmp_path):
    assert make_patcher(tmp_path).analyze_rom(write_iso(tmp_path)).is_valid is True


def test_the_reported_game_id_is_this_game(tmp_path):
    assert make_patcher(tmp_path).analyze_rom(write_iso(tmp_path)).game_id == "mvp-psp"


def test_the_reported_size_is_the_files(tmp_path):
    path = write_iso(tmp_path)
    assert make_patcher(tmp_path).analyze_rom(path).size == os.path.getsize(path)


def test_analyzing_lists_every_slot(tmp_path):
    assert len(make_patcher(tmp_path).analyze_rom(write_iso(tmp_path)).slots) == TEAM_COUNT


def test_a_reported_slot_is_the_libraries_own_type(tmp_path):
    assert type(make_patcher(tmp_path).analyze_rom(write_iso(tmp_path)).slots[0]) is RomSlot


def test_slot_display_names_are_the_2005_club_names(tmp_path):
    slots = make_patcher(tmp_path).analyze_rom(write_iso(tmp_path)).slots
    assert [s.display_name for s in slots] == list(MVP_TEAM_ORDER)


def test_a_populated_slot_shows_the_player_the_disc_has(tmp_path):
    path = write_iso(tmp_path, fixture.DiscSpec(teams=4, players_per_team=3))
    slots = make_patcher(tmp_path).analyze_rom(path).slots
    assert slots[2].current_name == "Disc02 Player00"


def test_an_unpopulated_slot_shows_nobody(tmp_path):
    path = write_iso(tmp_path, fixture.DiscSpec(teams=4, players_per_team=3))
    slots = make_patcher(tmp_path).analyze_rom(path).slots
    assert slots[9].current_name == ""


def test_two_populated_slots_show_different_players(tmp_path):
    path = write_iso(tmp_path, fixture.DiscSpec(teams=4, players_per_team=3))
    slots = make_patcher(tmp_path).analyze_rom(path).slots
    assert slots[0].current_name != slots[1].current_name


def test_the_extra_reports_how_many_sections_were_read(tmp_path):
    info = make_patcher(tmp_path).analyze_rom(write_iso(tmp_path))
    assert info.extra["sections_read"] == mvp_models.SECTION_COUNT


def test_the_extra_reports_the_size_of_the_id_pool(tmp_path):
    # Which is how many players this disc can hold at all, and is unreachable
    # once the reader is gone.
    path = write_iso(tmp_path, fixture.DiscSpec(teams=6, players_per_team=7))
    info = make_patcher(tmp_path).analyze_rom(path)
    assert info.extra["attrib_records"] == 42


def test_the_extra_is_json_serialisable(tmp_path):
    import json

    info = make_patcher(tmp_path).analyze_rom(write_iso(tmp_path))
    assert type(json.dumps(info.extra)) is str


def test_an_unrelated_file_of_the_right_size_is_not_this_game(tmp_path):
    path = tmp_path / "other.iso"
    path.write_bytes(fixture.build_iso(bytes(mvp_models.DATABASE_BIG_SIZE)))
    assert make_patcher(tmp_path).analyze_rom(path).is_valid is False


# -- the heuristic, and the asymmetry it must keep -------------------------


def test_a_disc_whose_team_ids_are_not_this_games_analyzes_as_invalid(tmp_path):
    path = write_iso(tmp_path, fixture.DiscSpec(teams=4, team_records=False))
    assert make_patcher(tmp_path).analyze_rom(path).is_valid is False


def test_the_same_disc_can_still_be_patched_by_name(tmp_path):
    # THE ASYMMETRY. `validate_deep` is a guess about content: a false negative
    # must cost the user auto-detection and nothing more, because
    # `patch --game mvp-psp` is them saying they already know what the disc is.
    # `_database_big_extent_fits` is arithmetic and guards both.
    path = write_iso(tmp_path, fixture.DiscSpec(teams=4, team_records=False))
    patcher = make_patcher(tmp_path)
    result = patcher.patch(
        rom_path=path,
        output_path=tmp_path / "out.iso",
        rosters=MappedRosters(game_id="mvp-psp", teams={}),
    )
    assert type(result) is PatchResult


def test_a_disc_failing_the_shallow_header_check_cannot_be_patched(tmp_path):
    # The shallow check is not a guess about meaning -- those three bytes are
    # where every section of this file begins -- so `patch` does apply it.
    path = write_iso(tmp_path)
    data = bytearray(path.read_bytes())
    start, _ = database_big_extent()
    data[start] = 0x11
    path.write_bytes(bytes(data))
    with pytest.raises(RomError):
        make_patcher(tmp_path).patch(
            rom_path=path,
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="mvp-psp", teams={}),
        )


# -- fetch -----------------------------------------------------------------


def test_fetching_answers_a_league_of_the_teams_with_slots(tmp_path):
    patcher = make_patcher(tmp_path)
    assert len(patcher.fetch(season=2025).teams) == 4


def test_a_team_with_no_slot_is_never_fetched(tmp_path):
    api = FakeApi(teams=[*default_teams(), Team(id=99, name="Nowhere", code="ZZZ")])
    patcher = make_patcher(tmp_path, api)
    patcher.fetch(season=2025)
    assert [tid for tid, _ in api.squad_calls] == [1, 2, 3, 4]


def test_the_squad_call_carries_the_season(tmp_path):
    # DELIBERATE DIVERGENCE: the source called `get_baseball_squad(team.id)`
    # with no season. The endpoint has no season in its URL but does have one
    # in its cache key, so the first season ever fetched was served forever.
    api = FakeApi()
    make_patcher(tmp_path, api).fetch(season=2019)
    assert api.squad_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_the_leaders_call_carries_the_season(tmp_path):
    api = FakeApi()
    make_patcher(tmp_path, api).fetch(season=2019)
    assert api.leader_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_two_seasons_ask_for_two_different_squads(tmp_path):
    # The zero-over-zero check on the season fix: a patcher that passed a
    # constant would satisfy either test above on its own.
    api = FakeApi()
    patcher = make_patcher(tmp_path, api)
    patcher.fetch(season=2019)
    patcher.fetch(season=2024)
    assert sorted({season for _, season in api.squad_calls}) == [2019, 2024]


def test_the_season_is_keyword_required(tmp_path):
    with pytest.raises(TypeError):
        make_patcher(tmp_path).fetch(2025)


def test_a_provider_with_no_teams_raises(tmp_path):
    with pytest.raises(ApiError):
        make_patcher(tmp_path, FakeApi(teams=[])).fetch(season=2025)


def test_a_provider_whose_teams_have_no_slots_raises(tmp_path):
    api = FakeApi(teams=[Team(id=99, name="Nowhere", code="ZZZ")])
    with pytest.raises(ApiError):
        make_patcher(tmp_path, api).fetch(season=2025)


def test_the_leaders_travel_in_the_roster_extra(tmp_path):
    # DELIBERATE DIVERGENCE: the source left these on `self.team_stats`, so the
    # pygame front end copied them between two patcher instances by hand at
    # `app.py:11414` and `:11490`. Without that line every player silently took
    # position defaults.
    api = FakeApi(leaders={1: {"501": {"HR": 42}}})
    data = make_patcher(tmp_path, api).fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"501": {"HR": 42}}


def test_a_team_the_provider_had_no_leaders_for_gets_an_empty_dict(tmp_path):
    data = make_patcher(tmp_path).fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {}


def test_the_league_reports_the_season_asked_for(tmp_path):
    assert make_patcher(tmp_path).fetch(season=2019).league.season == 2019


def test_the_league_counts_the_rosters_built_and_not_the_teams_returned(tmp_path):
    api = FakeApi(teams=[*default_teams(), Team(id=99, name="Nowhere", code="ZZZ")])
    assert make_patcher(tmp_path, api).fetch(season=2025).league.teams_count == 4


def test_the_league_names_the_country_and_its_code_separately(tmp_path):
    data = make_patcher(tmp_path).fetch(season=2025)
    assert (data.league.country, data.league.country_code) == ("USA", "US")


def test_progress_is_reported_from_zero_to_one(tmp_path):
    seen = []
    make_patcher(tmp_path).fetch(season=2025, on_progress=lambda f, m: seen.append(f))
    assert (seen[0], seen[-1]) == (0.0, 1.0)


def test_a_status_message_is_published(tmp_path):
    seen = []
    patcher = MVPPSPPatcher(tmp_path / "cache", on_status=seen.append)
    patcher.api = FakeApi()
    patcher.fetch(season=2025)
    assert seen == ["Fetching MLB teams..."]


# -- map_rosters -----------------------------------------------------------


def mapped(tmp_path, squads, leaders=None):
    return make_patcher(tmp_path).map_rosters(league(squads=squads, leaders=leaders))


def test_a_slot_map_is_refused(tmp_path):
    from retro_roster_patcher.core.models import SlotMapping

    with pytest.raises(CapabilityError):
        make_patcher(tmp_path).map_rosters(
            league(squads={}), [SlotMapping(slot_index=0, team_id=1)]
        )


def test_the_mapping_is_stamped_with_this_game(tmp_path):
    assert mapped(tmp_path, {}).game_id == "mvp-psp"


def test_every_team_in_the_league_data_takes_its_slot(tmp_path):
    # Including the two with no players: an empty roster that collides with
    # nothing still records which slot its provider team matched, and `patch`
    # is what keeps the empty list away from the writer.
    result = mapped(tmp_path, {1: full_squad(1000), 3: full_squad(2000)})
    assert sorted(result.teams) == [0, 13, 14, 15]


def test_only_the_slots_with_players_are_populated(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000), 3: full_squad(2000)})
    assert sorted(slot for slot, players in result.teams.items() if players) == [0, 14]


def test_a_team_with_no_slot_never_appears(tmp_path):
    data = LeagueData(
        league=League(id=0, name="MLB", season=2025),
        teams=[TeamRoster(team=Team(id=9, name="Nowhere", code="ZZZ"), players=full_squad(1))],
    )
    assert make_patcher(tmp_path).map_rosters(data).teams == {}


def test_a_full_squad_maps_to_twenty_five_records(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert len(result.teams[0]) == 25


def test_a_mapped_record_is_this_games_type(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert type(result.teams[0][0]) is MVPPlayerRecord


def test_the_first_nine_records_take_the_lineup_positions(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.roster_position for r in result.teams[0][:9]] == list(LINEUP_POSITIONS)


def test_the_first_nine_records_bat_one_to_nine(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.batting_order for r in result.teams[0][:9]] == list(range(1, 10))


def test_the_bench_is_out_of_the_lineup(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.batting_order for r in result.teams[0][9:15]] == [NOT_IN_LINEUP] * 6


def test_the_bench_takes_the_bench_position(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.roster_position for r in result.teams[0][9:15]] == ["B"] * 6


def test_the_rotation_takes_the_rotation_positions(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.roster_position for r in result.teams[0][15:20]] == list(ROTATION_POSITIONS)


def test_the_bullpen_takes_the_bullpen_roles(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.roster_position for r in result.teams[0][20:25]] == list(BULLPEN_POSITIONS)


def test_the_rotation_is_mapped_as_starting_pitchers(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.primary_position for r in result.teams[0][15:20]] == ["SP"] * 5


def test_the_bullpen_is_mapped_as_relief_pitchers(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert [r.primary_position for r in result.teams[0][20:25]] == ["RP"] * 5


def test_a_short_squad_maps_to_what_it_has(tmp_path):
    result = mapped(tmp_path, {1: [make_player(1, "C"), make_player(2, "SS")]})
    assert len(result.teams[0]) == 2


def test_a_squad_of_batters_alone_maps_to_fifteen_records(tmp_path):
    # Selection caps the batters at fifteen and takes the rotation and the
    # bullpen only from pitchers, so a batter can never reach slot 15 -- which
    # is what makes the pair of tests below about *relievers*.
    squad = [make_player(100 + i, "LF") for i in range(20)]
    assert len(mapped(tmp_path, {1: squad}).teams[0]) == 15


def test_a_reliever_who_lands_in_a_rotation_slot_is_mapped_as_a_starter(tmp_path):
    # "Is he a pitcher" comes from the provider and "is he a starter" from the
    # slot, and the two can disagree: a staff of three relievers fills slots
    # 15, 16 and 17, so all three are given a starter's stamina. Inherited.
    squad = [make_player(100 + i, "LF") for i in range(15)]
    squad += [make_player(200 + i, "RP") for i in range(3)]
    result = mapped(tmp_path, {1: squad})
    assert result.teams[0][15].primary_position == "SP"


def test_that_reliever_still_takes_the_first_rotation_position(tmp_path):
    squad = [make_player(100 + i, "LF") for i in range(15)]
    squad += [make_player(200 + i, "RP") for i in range(3)]
    result = mapped(tmp_path, {1: squad})
    assert result.teams[0][15].roster_position == "SP1"


def test_the_leaders_reach_the_mapper(tmp_path):
    # Without `TeamRoster.extra` every player would silently take position
    # defaults, which is exactly what the source's side channel did when the
    # two calls happened out of order.
    squad = [make_player(1, "SP")]
    plain = mapped(tmp_path, {1: squad})
    with_stats = mapped(tmp_path, {1: squad}, {1: {"1": {"K": 250, "WHIP": 0.9, "ERA": 2.0}}})
    assert plain.teams[0][0].pitches != with_stats.teams[0][0].pitches


def test_an_empty_alias_does_not_wipe_a_populated_slot(tmp_path):
    # DELIBERATE DIVERGENCE. `OAK` and `ATH` name one slot, and the source
    # assigned unconditionally, so an empty alias arriving second left that
    # club's 2005 roster on the disc under a success report.
    data = LeagueData(
        league=League(id=0, name="MLB", season=2025),
        teams=[
            TeamRoster(team=Team(id=1, name="Athletics", code="OAK"), players=full_squad(1000)),
            TeamRoster(team=Team(id=2, name="Athletics", code="ATH"), players=[]),
        ],
    )
    assert len(make_patcher(tmp_path).map_rosters(data).teams[1]) == 25


def test_a_populated_alias_does_replace_an_earlier_one(tmp_path):
    data = LeagueData(
        league=League(id=0, name="MLB", season=2025),
        teams=[
            TeamRoster(team=Team(id=1, name="Athletics", code="OAK"), players=[]),
            TeamRoster(team=Team(id=2, name="Athletics", code="ATH"), players=full_squad(1000)),
        ],
    )
    assert len(make_patcher(tmp_path).map_rosters(data).teams[1]) == 25


def test_an_empty_roster_that_collides_with_nothing_still_takes_its_slot(tmp_path):
    data = LeagueData(
        league=League(id=0, name="MLB", season=2025),
        teams=[TeamRoster(team=Team(id=1, name="Athletics", code="OAK"), players=[])],
    )
    assert make_patcher(tmp_path).map_rosters(data).teams == {1: []}


# -- the hash pool ---------------------------------------------------------


def test_a_pitcher_takes_a_pitchers_id():
    pool = _HashPool(["0aaaaaaaa", "0bbbbbbbb"], {"0bbbbbbbb"})
    assert pool.take(is_pitcher=True, team_index=0, player_index=0) == "0bbbbbbbb"


def test_a_batter_takes_a_batters_id():
    pool = _HashPool(["0aaaaaaaa", "0bbbbbbbb"], {"0bbbbbbbb"})
    assert pool.take(is_pitcher=False, team_index=0, player_index=0) == "0aaaaaaaa"


def test_the_pool_hands_ids_out_in_the_attrib_tables_order():
    pool = _HashPool(["0aaaaaaaa", "0cccccccc"], set())
    first = pool.take(is_pitcher=False, team_index=0, player_index=0)
    second = pool.take(is_pitcher=False, team_index=0, player_index=1)
    assert [first, second] == ["0aaaaaaaa", "0cccccccc"]


def test_an_exhausted_pitcher_pool_borrows_from_the_batters():
    # Tier 2. It costs the pitcher a batter's career line, which is why it is
    # counted rather than silent.
    pool = _HashPool(["0aaaaaaaa"], set())
    assert pool.take(is_pitcher=True, team_index=0, player_index=0) == "0aaaaaaaa"


def test_a_cross_fall_is_counted():
    pool = _HashPool(["0aaaaaaaa"], set())
    pool.take(is_pitcher=True, team_index=0, player_index=0)
    assert pool.crossed == 1


def test_taking_from_the_right_pool_is_not_counted_as_a_cross_fall():
    pool = _HashPool(["0aaaaaaaa"], set())
    pool.take(is_pitcher=False, team_index=0, player_index=0)
    assert pool.crossed == 0


def test_two_exhausted_pools_synthesise_an_id():
    # Tier 3: `00`, two hex digits of team, five of player index, then `ff`.
    pool = _HashPool([], set())
    assert pool.take(is_pitcher=True, team_index=3, player_index=7) == "000300007ff"


def test_a_synthesised_id_is_counted():
    pool = _HashPool([], set())
    pool.take(is_pitcher=False, team_index=0, player_index=0)
    assert pool.synthesised == 1


def test_a_synthesised_id_encodes_its_team_and_its_slot():
    pool = _HashPool([], set())
    first = pool.take(is_pitcher=False, team_index=1, player_index=2)
    second = pool.take(is_pitcher=False, team_index=2, player_index=1)
    assert first != second


def test_a_synthesised_id_is_eleven_characters_and_not_nine():
    # The migration brief claimed a synthesised id may collide with a real one.
    # It cannot: every id in this repository is nine characters and this is
    # eleven. `_HashPool` argues it at the line.
    pool = _HashPool([], set())
    assert len(pool.take(is_pitcher=False, team_index=29, player_index=24)) == 11


def test_a_synthesised_id_cannot_be_one_of_the_discs_team_ids():
    pool = _HashPool([], set())
    made = {
        pool.take(is_pitcher=False, team_index=t, player_index=p)
        for t in range(TEAM_COUNT)
        for p in range(25)
    }
    assert made & set(TEAM_HASHES.values()) == set()


def test_no_two_synthesised_ids_collide():
    pool = _HashPool([], set())
    made = [
        pool.take(is_pitcher=False, team_index=t, player_index=p)
        for t in range(TEAM_COUNT)
        for p in range(25)
    ]
    assert len(set(made)) == len(made)


# -- patch -----------------------------------------------------------------


def patch_one(tmp_path, squads, leaders=None, spec=DISC, out="out.iso"):
    """Map and patch in one step, returning (result, output path)."""
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads=squads, leaders=leaders))
    path = write_iso(tmp_path, spec)
    result = patcher.patch(rom_path=path, output_path=tmp_path / out, rosters=rosters)
    return result, tmp_path / out


def test_rosters_mapped_for_another_game_are_refused(tmp_path):
    with pytest.raises(MappingError):
        make_patcher(tmp_path).patch(
            rom_path=write_iso(tmp_path),
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl05-ps2", teams={}),
        )


def test_the_game_check_happens_before_any_file_is_touched(tmp_path):
    with pytest.raises(MappingError):
        make_patcher(tmp_path).patch(
            rom_path=tmp_path / "gone.iso",
            output_path=tmp_path / "out.iso",
            rosters=MappedRosters(game_id="nhl05-ps2", teams={}),
        )


def test_patching_answers_the_libraries_own_type(tmp_path):
    result, _ = patch_one(tmp_path, {1: full_squad(1000)})
    assert type(result) is PatchResult


def test_the_result_names_the_output(tmp_path):
    result, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert result.output_path == str(out)


def test_one_team_patched_counts_one(tmp_path):
    result, _ = patch_one(tmp_path, {1: full_squad(1000)})
    assert result.teams_patched == 1


def test_two_teams_patched_count_two(tmp_path):
    result, _ = patch_one(tmp_path, {1: full_squad(1000), 3: full_squad(2000)})
    assert result.teams_patched == 2


def test_a_full_squad_counts_twenty_five_players(tmp_path):
    result, _ = patch_one(tmp_path, {1: full_squad(1000)})
    assert result.players_patched == 25


def test_two_full_squads_count_fifty_players(tmp_path):
    result, _ = patch_one(tmp_path, {1: full_squad(1000), 3: full_squad(2000)})
    assert result.players_patched == 50


def test_a_short_squad_counts_what_it_wrote(tmp_path):
    squad = [make_player(1, "C"), make_player(2, "SS"), make_player(3, "SP")]
    result, _ = patch_one(tmp_path, {1: squad})
    assert result.players_patched == 3


def test_patching_nothing_writes_a_copy(tmp_path):
    _, out = patch_one(tmp_path, {})
    assert out.read_bytes() == (tmp_path / "game.iso").read_bytes()


def test_patching_nothing_counts_nothing(tmp_path):
    result, _ = patch_one(tmp_path, {})
    assert (result.teams_patched, result.players_patched) == (0, 0)


def test_an_empty_roster_does_not_count_as_a_patched_team(tmp_path):
    data = LeagueData(
        league=League(id=0, name="MLB", season=2025),
        teams=[TeamRoster(team=Team(id=1, name="Angels", code="LAA"), players=[])],
    )
    patcher = make_patcher(tmp_path)
    result = patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=patcher.map_rosters(data),
    )
    assert result.teams_patched == 0


def test_a_slot_out_of_range_is_ignored(tmp_path):
    # The keys come from a plain dict that may have crossed a JSON boundary.
    patcher = make_patcher(tmp_path)
    rosters = MappedRosters(game_id="mvp-psp", teams={99: [MVPPlayerRecord(last_name="X")]})
    result = patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert result.teams_patched == 0


def test_progress_reaches_one(tmp_path):
    seen = []
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
        on_progress=lambda f, m: seen.append(f),
    )
    assert seen[-1] == 1.0


def test_teams_are_written_in_slot_order_whatever_order_the_mapping_holds(tmp_path):
    # `targets` is sorted, and it has to be: `_HashPool` hands ids out in the
    # `attrib` table's order, so which player inherits which career depends on
    # the order the clubs are visited in. A `MappedRosters` that crossed a JSON
    # boundary carries whatever order its dict was built in, and every mapping
    # in this file happens to be built in ascending slot order -- which is why
    # dropping the sort survived. This one is built backwards.
    seen = []
    patcher = make_patcher(tmp_path)
    mapped = patcher.map_rosters(league(squads={2: full_squad(1000), 1: full_squad(2000)}))
    backwards = MappedRosters(
        game_id=mapped.game_id, teams={13: mapped.teams[13], 0: mapped.teams[0]}
    )
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=backwards,
        on_progress=lambda f, m: seen.append(m),
    )
    assert [m for m in seen if m.startswith("Writing ")] == [
        "Writing Anaheim Angels (25 players)...",
        "Writing Toronto Blue Jays (25 players)...",
    ]


def test_the_record_phase_ends_before_the_copy(tmp_path):
    seen = []
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
        on_progress=lambda f, m: seen.append(f),
    )
    assert seen[-2] == PROGRESS_RECORDS_END


def test_the_teams_divide_the_record_phase_between_them(tmp_path):
    # `i / len(targets)`, so the first club is reported before any of its work
    # rather than after. With one club `(i + 1) / len(targets)` is 1.0 times
    # `PROGRESS_RECORDS_END`, which is a number the run emits anyway, so it took
    # two clubs to see it.
    seen = []
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000), 2: full_squad(2000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
        on_progress=lambda f, m: seen.append((f, m)),
    )
    assert [f for f, m in seen if m.startswith("Writing ")] == [
        0.0,
        PROGRESS_RECORDS_END / 2,
    ]


# -- what reaches the disc -------------------------------------------------


def test_the_bytes_before_the_extent_are_untouched(tmp_path):
    start, _ = database_big_extent()
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert out.read_bytes()[:start] == (tmp_path / "game.iso").read_bytes()[:start]


def test_the_bytes_after_the_extent_are_untouched(tmp_path):
    _, end = database_big_extent()
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert out.read_bytes()[end:] == (tmp_path / "game.iso").read_bytes()[end:]


def test_the_extent_does_change(tmp_path):
    start, end = database_big_extent()
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    source = (tmp_path / "game.iso").read_bytes()
    assert out.read_bytes()[start:end] != source[start:end]


def test_a_patched_player_takes_his_name(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    names = {cols.get(ATTRIB_LAST_NAME) for cols in patched_table(out, "attrib").values()}
    assert "Family1000" in names


def test_a_patched_player_keeps_the_columns_nothing_wrote(tmp_path):
    # The merge is what preserves salary, contract length and birthday: this
    # patcher has no source for any of them.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "attrib")
    written = [cols for cols in table.values() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert ATTRIB_BIRTHDAY in written[0]


def test_a_patched_player_keeps_the_discs_salary(tmp_path):
    source = write_iso(tmp_path, DISC)
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    before = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    after = patched_table(tmp_path / "out.iso", "attrib")
    changed = [pid for pid, cols in after.items() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert after[changed[0]][ATTRIB_SALARY] == before[changed[0]][ATTRIB_SALARY]


def test_a_patched_player_keeps_the_discs_second_position(tmp_path):
    # `_build_attrib_fields` writes column 6 only when the mapper produced a
    # second position, and it never does -- `MVPPlayerRecord.secondary_position`
    # has no producer. The guard is there because writing an empty string erases
    # the disc's own value and leaves the player unable to be moved in the
    # field, and until the fixture carried a value in that column there was
    # nothing for it to protect: `if player.secondary_position` could be
    # loosened to `is not None` and every test still passed.
    source = write_iso(tmp_path, DISC)
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    before = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    after = patched_table(tmp_path / "out.iso", "attrib")
    changed = [pid for pid, cols in after.items() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert after[changed[0]][ATTRIB_SECONDARY_POS] == before[changed[0]][ATTRIB_SECONDARY_POS]


def test_a_patched_split_record_keeps_the_discs_spray_chart(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "lrattrib_rhp")
    written = [cols for cols in table.values() if cols.get(LR_FIRST_NAME) == "Given1000"]
    assert LR_SPRAY_UL in written[0]


def test_a_patched_pitcher_keeps_the_discs_delivery(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "pitchattrib")
    written = [cols for cols in table.values() if cols.get(ATTRIB_FIRST_NAME) == "Given1015"]
    assert PA_PITCHER_DELIVERY in written[0]


def test_the_teams_roster_rows_are_replaced_by_the_new_squad(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert len(roster_rows(out, 0)) == 25


def test_an_unpatched_teams_roster_rows_are_left_alone(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert len(roster_rows(out, 5)) == DISC.players_per_team


def test_no_roster_row_id_is_reused(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000), 3: full_squad(2000)})
    table = patched_table(out, "roster")
    assert len(table) == len(set(table))


# The assertion above cannot fail. `table` is a dict, so `set(table)` is its own
# key set and the two lengths are equal by construction -- zero over zero, and
# mutation testing said so by starting `roster_counter` on the highest id the
# disc already held instead of one past it and surviving the whole suite. What a
# reused id actually does is silently drop a row, so these count the rows.


def test_the_roster_table_holds_every_kept_row_and_every_new_one(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    kept = (DISC.teams - 1) * DISC.players_per_team
    assert len(patched_table(out, "roster")) == kept + len(FULL_SQUAD_POSITIONS)


def test_the_highest_numbered_row_the_disc_had_still_names_its_own_player(tmp_path):
    # New ids continue past the highest the disc held, so the row *holding* that
    # highest id -- which belongs to a club nobody patched -- is the one a
    # counter starting one too low would overwrite first.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    last_team, last_slot = DISC.teams - 1, DISC.players_per_team - 1
    row = patched_table(out, "roster")[fixture.roster_row_id(last_team, last_slot)]
    assert row[ROSTER_PLAYERID] == fixture.player_id(last_team, last_slot)


def test_the_new_rows_take_the_ids_that_follow_the_highest_the_disc_held(tmp_path):
    # The ids themselves, consecutively, from `max(old_roster) + 1`. Nothing
    # else in the file looks at a new row's id at all, so the counter could
    # start one low or step by two unremarked.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    highest = int(fixture.roster_row_id(DISC.teams - 1, DISC.players_per_team - 1), 16)
    assert sorted(int(rid, 16) for rid in roster_rows(out, 0)) == [
        highest + 1 + i for i in range(len(FULL_SQUAD_POSITIONS))
    ]


def test_the_counter_counts_the_rows_the_patch_drops_as_well(tmp_path):
    # `max(...)` runs over `old_roster` -- every row the disc held -- and not
    # over the rows this patch keeps, and patching the *last* club is what
    # separates the two: its rows carry the highest ids on the disc and they
    # are exactly the ones being dropped. A counter built from the survivors
    # restarts inside a range the disc has already used.
    patcher = make_patcher(tmp_path)
    mapped = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=MappedRosters(game_id=mapped.game_id, teams={TEAM_COUNT - 1: mapped.teams[0]}),
    )
    highest = int(fixture.roster_row_id(DISC.teams - 1, DISC.players_per_team - 1), 16)
    written = roster_rows(tmp_path / "out.iso", TEAM_COUNT - 1)
    assert min(int(rid, 16) for rid in written) == highest + 1


def test_every_new_roster_row_id_is_a_nine_character_id(tmp_path):
    # Hexadecimal and `HASH_ID_CHARS` wide, like every id the disc holds.
    # Formatting the counter as decimal instead yields eleven digit characters,
    # which `_looks_like_record_id` happily accepts as an id of the wrong shape.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    assert {len(rid) for rid in roster_rows(out, 0)} == {HASH_ID_CHARS}


def test_every_new_roster_row_names_a_player_the_patch_wrote(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    attrib = patched_table(out, "attrib")
    missing = [
        cols[ROSTER_PLAYERID]
        for cols in roster_rows(out, 0).values()
        if cols[ROSTER_PLAYERID] not in attrib
    ]
    assert missing == []


def test_a_patched_players_jersey_reaches_the_disc(tmp_path):
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "attrib")
    written = [c for c in table.values() if c.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert written[0][ATTRIB_JERSEY] == str(squad[0].number)


def test_a_patched_players_position_code_reaches_the_disc(tmp_path):
    # The catcher of a full squad is mapped at C, which is position code 1.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "attrib")
    written = [c for c in table.values() if c.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert written[0][ATTRIB_PRIMARY_POS] == "1"


def test_an_unmapped_primary_position_falls_back_to_centre_field():
    # Unreachable through `map_rosters`: `normalize_position` only ever returns
    # keys of `POS_STRING_TO_NUM`, and `map_pitcher` sets `SP` or `RP`, which
    # are keys too. So only a hand-built record reaches the fallback, and until
    # one did, the fallback could be anything -- 0, which files the player as a
    # starting pitcher, survived the suite.
    fields = MVPPSPPatcher._build_attrib_fields(MVPPlayerRecord(primary_position="QB"))
    assert fields[ATTRIB_PRIMARY_POS] == "7"


def test_the_progress_bar_gives_the_copy_its_last_tenth():
    # Read back through the constant everywhere else, so the number itself is
    # only stated here.
    assert PROGRESS_RECORDS_END == 0.9


def test_a_patched_players_speed_reaches_the_disc(tmp_path):
    # A catcher with no statistics takes the catcher default of 35, so this
    # would not pass on a patcher that wrote the record's own 50.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "attrib")
    written = [c for c in table.values() if c.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert written[0][ATTRIB_SPEED] == "35"


def test_a_patched_batters_contact_reaches_the_split_table(tmp_path):
    # The catcher default again, 55, and a different number from his speed --
    # so a writer that put one rating in every column would fail one of the two.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "lrattrib_rhp")
    written = [c for c in table.values() if c.get(LR_FIRST_NAME) == "Given1000"]
    assert written[0][LR_CONTACT] == "55"


# A batter with no statistics takes his position's default for both splits, and
# a pitcher takes the same pair for both, so on the squads above
# `lrattrib_rhp` and `lrattrib_lhp` hold identical numbers -- and swapping the
# two tables, or swapping the two ratings inside `_build_lr_attrib_fields`,
# survived every one of them. Zero over zero. These give one batter statistics,
# which is the only thing in the mapper that makes the two sides differ: the
# left-hand column is five points below the right.
#
# `full_squad`'s player 1001 rather than 1000, because `make_player` gives every
# fifth id a switch hitter's `bats`, and a switch hitter's two splits are
# averaged back together.

LR_SPLIT_LEADERS = {1: {"1001": {"AVG": 0.330, "OBP": 0.420, "SLG": 0.600, "HR": 45}}}


def _lr_split(tmp_path, table_name):
    _, out = patch_one(tmp_path, {1: full_squad(1000)}, LR_SPLIT_LEADERS)
    table = patched_table(out, table_name)
    return [c for c in table.values() if c.get(LR_FIRST_NAME) == "Given1001"][0]


def test_the_right_handed_split_table_holds_the_right_handed_contact(tmp_path):
    assert _lr_split(tmp_path, "lrattrib_rhp")[LR_CONTACT] == "99"


def test_the_left_handed_split_table_holds_the_left_handed_contact(tmp_path):
    assert _lr_split(tmp_path, "lrattrib_lhp")[LR_CONTACT] == "94"


def test_the_right_handed_split_table_holds_the_right_handed_power(tmp_path):
    assert _lr_split(tmp_path, "lrattrib_rhp")[LR_POWER] == "99"


def test_the_left_handed_split_table_holds_the_left_handed_power(tmp_path):
    assert _lr_split(tmp_path, "lrattrib_lhp")[LR_POWER] == "94"


def test_a_patched_starters_stamina_reaches_the_pitching_table(tmp_path):
    # A starter with no statistics takes 70, a reliever 35.
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "pitchattrib")
    written = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"]
    assert written[0][PA_STAMINA] == "70"


def test_a_patched_relievers_stamina_differs_from_a_starters(tmp_path):
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "pitchattrib")
    written = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[20].id}"]
    assert written[0][PA_STAMINA] == "35"


def test_a_mapped_pitchers_arsenal_is_the_frozen_pitch_type(tmp_path):
    result = mapped(tmp_path, {1: full_squad(1000)})
    assert type(result.teams[0][15].pitches[0]) is MVPPitch


def test_the_lineup_positions_reach_the_roster_table(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    positions = [cols[ROSTER_RH_AL_POS] for cols in roster_rows(out, 0).values()]
    assert sorted(positions[:9]) == sorted(LINEUP_POSITIONS)


# -- the American League split ---------------------------------------------


def test_an_american_league_club_bats_in_the_al_columns(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    orders = {cols[ROSTER_RH_AL_ORDER] for cols in roster_rows(out, 0).values()}
    assert orders == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "-1"}


def test_an_american_league_club_stores_minus_one_in_the_nl_columns(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    orders = {cols[ROSTER_RH_NL_ORDER] for cols in roster_rows(out, 0).values()}
    assert orders == {"-1"}


def test_a_national_league_club_bats_in_the_nl_columns(tmp_path):
    _, out = patch_one(tmp_path, {3: full_squad(2000)})
    orders = {cols[ROSTER_RH_NL_ORDER] for cols in roster_rows(out, 14).values()}
    assert orders == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "-1"}


def test_a_national_league_club_stores_minus_one_in_the_al_columns(tmp_path):
    _, out = patch_one(tmp_path, {3: full_squad(2000)})
    orders = {cols[ROSTER_RH_AL_ORDER] for cols in roster_rows(out, 14).values()}
    assert orders == {"-1"}


def test_the_last_american_league_slot_is_thirteen(tmp_path):
    # The boundary from the American side. Toronto is slot 13.
    _, out = patch_one(tmp_path, {2: full_squad(3000)})
    orders = {cols[ROSTER_RH_AL_ORDER] for cols in roster_rows(out, AL_SLOT_COUNT - 1).values()}
    assert "9" in orders


def test_the_first_national_league_slot_is_fourteen(tmp_path):
    # And from the National side. Arizona is slot 14, and this is the pair of
    # tests a wrong `AL_SLOT_COUNT` fails.
    _, out = patch_one(tmp_path, {3: full_squad(2000)})
    orders = {cols[ROSTER_RH_AL_ORDER] for cols in roster_rows(out, AL_SLOT_COUNT).values()}
    assert orders == {"-1"}


def test_the_two_handedness_variants_get_the_same_lineup(tmp_path):
    # Inherited: the game's platoon feature does nothing for a patched team,
    # and fixing it needs a per-handedness split no provider here has.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    rows = list(roster_rows(out, 0).values())
    assert [r[ROSTER_RH_AL_ORDER] for r in rows] == [r[ROSTER_LH_AL_ORDER] for r in rows]


def test_the_two_handedness_variants_agree_in_the_national_columns_too(tmp_path):
    _, out = patch_one(tmp_path, {3: full_squad(2000)})
    rows = list(roster_rows(out, 14).values())
    assert [r[ROSTER_RH_NL_ORDER] for r in rows] == [r[ROSTER_LH_NL_ORDER] for r in rows]


# -- bug 3 reaching the disc: height and weight ----------------------------


def test_no_patched_player_has_his_height_rewritten(tmp_path):
    # DELIBERATE DIVERGENCE. The source wrote column 9 for every player from a
    # field nothing ever set, so all 750 became exactly 6'0".
    source = write_iso(tmp_path, DISC)
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    before = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    after = patched_table(tmp_path / "out.iso", "attrib")
    changed = [pid for pid, cols in after.items() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert after[changed[0]][ATTRIB_HEIGHT] == before[changed[0]][ATTRIB_HEIGHT]


def test_the_disc_holds_more_than_one_height(tmp_path):
    # Which is what makes the test above mean something: if every disc player
    # were the same height, preserving and overwriting would look alike.
    source = write_iso(tmp_path, DISC)
    table = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    assert len({cols[ATTRIB_HEIGHT] for cols in table.values()}) > 1


def test_a_provider_weight_reaches_the_disc(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000, weight=217.0)})
    table = patched_table(out, "attrib")
    written = [cols for cols in table.values() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert written[0][ATTRIB_WEIGHT] == "217"


def test_a_player_with_no_provider_weight_keeps_the_discs(tmp_path):
    source = write_iso(tmp_path, DISC)
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(rom_path=source, output_path=tmp_path / "out.iso", rosters=rosters)
    before = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    after = patched_table(tmp_path / "out.iso", "attrib")
    changed = [pid for pid, cols in after.items() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert after[changed[0]][ATTRIB_WEIGHT] == before[changed[0]][ATTRIB_WEIGHT]


def test_the_disc_holds_more_than_one_weight(tmp_path):
    source = write_iso(tmp_path, DISC)
    table = fixture.parse_table(
        fixture.decompress_section_at(
            fixture.read_database_big(source.read_bytes(), lba=fixture.SMALL_LBA), "attrib"
        )
    )
    assert len({cols[ATTRIB_WEIGHT] for cols in table.values()}) > 1


# -- bug 1 reaching the disc: the arsenal ----------------------------------


def test_two_pitchers_with_different_statistics_get_different_velocities(tmp_path):
    # DELIBERATE DIVERGENCE, end to end. Under the source every pitcher on the
    # disc had the same 50/50 arsenal whatever his statistics were.
    squad = full_squad(1000)
    leaders = {
        1: {
            str(squad[15].id): {"K": 250, "WHIP": 0.90, "ERA": 2.00},
            str(squad[16].id): {"K": 61, "WHIP": 1.60, "ERA": 6.00},
        }
    }
    _, out = patch_one(tmp_path, {1: squad}, leaders)
    table = patched_table(out, "pitchattrib")
    first = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"]
    second = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[16].id}"]
    assert first[0][PA_PITCH1_VELOCITY] != second[0][PA_PITCH1_VELOCITY]


def test_a_pitcher_with_statistics_does_not_get_the_default_velocity(tmp_path):
    squad = full_squad(1000)
    leaders = {1: {str(squad[15].id): {"K": 250, "WHIP": 0.90, "ERA": 2.00}}}
    _, out = patch_one(tmp_path, {1: squad}, leaders)
    table = patched_table(out, "pitchattrib")
    written = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"]
    assert written[0][PA_PITCH1_VELOCITY] == "99"


def test_a_starters_second_pitch_reaches_the_repeating_block(tmp_path):
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "pitchattrib")
    written = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"]
    assert PA_PITCH2_TYPE in written[0]


# Membership alone does not say *which* pitch landed there. Pitch 1 is the
# asymmetric one -- always a fastball, four columns, no type column of its own
# -- so the repeating block starts at the arsenal's *second* entry, and slicing
# it from the first instead puts a fastball in the slider's type column and
# survived the test above.


def _starter_pitch_record(tmp_path):
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "pitchattrib")
    return [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"][0]


def test_the_first_entry_of_the_repeating_block_is_the_slider(tmp_path):
    assert _starter_pitch_record(tmp_path)[PA_PITCH2_TYPE] == str(PITCH_SLIDER)


def test_the_second_entry_of_the_repeating_block_is_the_changeup(tmp_path):
    record = _starter_pitch_record(tmp_path)
    assert record[PA_PITCH2_TYPE + PA_PITCH_STRIDE] == str(PITCH_CHANGEUP)


def test_the_fastball_never_reaches_a_type_column(tmp_path):
    # Its own columns are 4, 6 and 7, and column 8 is the first type column.
    record = _starter_pitch_record(tmp_path)
    types = {record[PA_PITCH2_TYPE + i * PA_PITCH_STRIDE] for i in range(MAX_EXTRA_PITCHES - 1)}
    assert str(PITCH_FASTBALL) not in types


def test_a_batter_gets_no_pitching_record_of_his_own(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "pitchattrib")
    batters = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == "Given1000"]
    assert batters == []


# -- bug 2 reaching the disc: a section that does not fit ------------------


def test_a_patch_that_cannot_be_stored_raises(tmp_path):
    # DELIBERATE DIVERGENCE. The source kept the original section, dropped
    # every edit to it, and returned success with a full count of teams and
    # players patched.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: long_named_squad()}))
    with pytest.raises(SectionTooLargeError):
        patcher.patch(
            rom_path=write_iso(tmp_path, FULL_DISC),
            output_path=tmp_path / "out.iso",
            rosters=rosters,
        )


def test_a_patch_that_cannot_be_stored_leaves_no_output(tmp_path):
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: long_named_squad()}))
    with pytest.raises(SectionTooLargeError):
        patcher.patch(
            rom_path=write_iso(tmp_path, FULL_DISC),
            output_path=tmp_path / "out.iso",
            rosters=rosters,
        )
    assert (tmp_path / "out.iso").exists() is False


def test_the_full_disc_can_still_be_patched_with_ordinary_names(tmp_path):
    # The zero-over-zero check on the two tests above: they would both pass on
    # a disc that could not be patched at all.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    result = patcher.patch(
        rom_path=write_iso(tmp_path, FULL_DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert result.players_patched == 25


def test_the_same_patch_on_a_roomier_disc_succeeds(tmp_path):
    # Which is what makes the two tests above about *fitting* rather than
    # about long names being rejected outright.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: long_named_squad()}))
    result = patcher.patch(
        rom_path=write_iso(tmp_path, fixture.DiscSpec(teams=4, players_per_team=6)),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert result.players_patched == 25


# -- degradation is reported -----------------------------------------------


def test_an_exhausted_pool_is_reported(tmp_path):
    # The source degraded in silence. The degradation is preserved -- every
    # alternative drops a player -- and it is now said out loud.
    seen = []
    patcher = MVPPSPPatcher(tmp_path / "cache", on_status=seen.append)
    patcher.api = FakeApi()
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, fixture.DiscSpec(teams=2, players_per_team=2)),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert len([m for m in seen if "synthesised id" in m]) == 1


#: A disc holding exactly one team's worth of ids, with every third player a
#: pitcher: nine pitcher ids and sixteen batter ids. A squad of fifteen batters
#: and ten pitchers therefore exhausts the pitcher pool by exactly one and the
#: batter pool not at all, so the run crosses once and synthesises nothing --
#: the tier-2-alone case, which the test below is the only one to reach.
ONE_TEAM_DISC = fixture.DiscSpec(teams=1, players_per_team=25)


def test_a_run_that_only_crosses_still_reports(tmp_path):
    # `or`, not `and`. The exhausted-pool test above is a disc small enough that
    # both tiers fire, so reporting only when both had happened survived it.
    seen = []
    patcher = MVPPSPPatcher(tmp_path / "cache", on_status=seen.append)
    patcher.api = FakeApi()
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, ONE_TEAM_DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert [m for m in seen if "position pool" in m] == [
        "1 player(s) took an id from the other position pool and 0 were given a "
        "synthesised id; their career statistics in this game will not be their own"
    ]


def test_a_patch_that_does_not_degrade_says_nothing_about_it(tmp_path):
    seen = []
    patcher = MVPPSPPatcher(tmp_path / "cache", on_status=seen.append)
    patcher.api = FakeApi()
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert [m for m in seen if "synthesised id" in m] == []


# -- fetch, map and patch are separable -------------------------------------


def test_a_mapping_can_be_reused_for_two_patches(tmp_path):
    # `patch_rom` re-ran `map_rosters` internally, so the mapping could not be
    # inspected, cached or serialised. Two patches from one mapping must agree.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    source = write_iso(tmp_path, DISC)
    patcher.patch(rom_path=source, output_path=tmp_path / "a.iso", rosters=rosters)
    patcher.patch(rom_path=source, output_path=tmp_path / "b.iso", rosters=rosters)
    assert (tmp_path / "a.iso").read_bytes() == (tmp_path / "b.iso").read_bytes()


def test_a_mapping_survives_a_json_round_trip(tmp_path):
    # `MappedRosters.teams` is keyed by int and JSON keys are strings, so the
    # slot re-check in `_write_all_teams` is what makes this work.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    revived = MappedRosters(game_id=rosters.game_id, teams=dict(rosters.teams))
    result = patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=revived,
    )
    assert result.players_patched == 25
