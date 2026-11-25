"""`MVPPSPPatcher` against the unified interface.

Every read-back of a patched image goes through the fixture's own
`read_database_big`, `decompress_section_at` and `parse_table`, never through
the reader and writer that produced it.
"""

from __future__ import annotations

import os
import random
from datetime import timedelta

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, RomSlot
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.formats.ea_tdb import refpack_decompress
from retro_roster_patcher.games.mvp_psp import models as mvp_models
from retro_roster_patcher.games.mvp_psp.models import (
    AL_SLOT_COUNT,
    ATTRIB_BIRTHDAY,
    ATTRIB_CONTRACT_LENGTH,
    ATTRIB_FIRST_NAME,
    ATTRIB_HEIGHT,
    ATTRIB_HIDDEN,
    ATTRIB_JERSEY,
    ATTRIB_LAST_NAME,
    ATTRIB_PRIMARY_POS,
    ATTRIB_SALARY,
    ATTRIB_SECONDARY_POS,
    ATTRIB_SPEED,
    ATTRIB_WEIGHT,
    BIRTHDAY_EPOCH,
    BULLPEN_POSITIONS,
    DISC_SEASON,
    HASH_ID_CHARS,
    LINEUP_POSITIONS,
    LR_DIRECTION,
    LR_FB_PCT,
    LR_GB_PCT,
    LR_HR_PCT,
    LR_LD_PCT,
    LR_ZONES,
    MVP_TEAM_ABBREVS,
    MVP_TEAM_ORDER,
    NO_PITCH,
    NOT_IN_LINEUP,
    PA_PITCH1_VELOCITY,
    PA_PITCH2_TYPE,
    PA_PITCH_STRIDE,
    PA_PITCHER_DELIVERY,
    PITCHER_STAT_TABLES,
    ROSTER_LH_AL_ORDER,
    ROSTER_LH_NL_ORDER,
    ROSTER_PLAYERID,
    ROSTER_RH_AL_ORDER,
    ROSTER_RH_AL_POS,
    ROSTER_RH_NL_ORDER,
    ROSTER_RH_NL_POS,
    ROSTER_TEAMID,
    ROTATION_POSITIONS,
    TEAM_COUNT,
    TEAM_HASHES,
    ZONE_HOT,
    MVPPlayerRecord,
    database_big_extent,
)
from retro_roster_patcher.games.mvp_psp.patcher import (
    PROGRESS_RECORDS_END,
    MVPPSPPatcher,
    _as_of_disc,
    _attrib_fields,
    _database_big_extent_fits,
    _HashPool,
    _pitch_fields,
    _split_fields,
)
from retro_roster_patcher.games.mvp_psp.rom_reader import MVPPSPRomReader
from retro_roster_patcher.games.mvp_psp.rom_writer import MVPPSPRomWriter, SectionTooLargeError
from retro_roster_patcher.games.mvp_psp.spreads import Spreads
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

    def get_baseball_lineup(self, team_id, season=None):
        return []

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
#: allocation. Every section fits, and a roster patch that lengthens any name
#: cannot be stored.
FULL_DISC = fixture.DiscSpec(teams=4, players_per_team=6, attrib_headroom_bytes=800)


def long_named_squad():
    """Twenty-five players with long names that do not compress, even by the
    slower parse a table that misses its allocation is given.

    Repeating a word twenty-five times would not do: RefPack is LZ77, so the
    second copy of a long name costs almost nothing and the section would not
    grow. These are distinct pseudo-random hex, seeded so the disc is the same on
    every run.
    """
    rng = random.Random(0x424C4F42)
    return [
        Player(
            id=5000 + i,
            name="".join(rng.choice("0123456789abcdef") for _ in range(60))
            + " "
            + "".join(rng.choice("0123456789abcdef") for _ in range(60)),
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


def test_the_client_is_built_eagerly(tmp_path):
    assert type(MVPPSPPatcher(tmp_path / "cache").api) is EspnClient


def test_the_cache_directory_accepts_a_string(tmp_path):
    patcher = MVPPSPPatcher(str(tmp_path / "cache"))
    assert patcher.cache_dir == tmp_path / "cache"


def test_an_api_key_is_refused(tmp_path):
    # Not accepted and ignored: a parameter that silently does nothing lets a caller
    # believe a credential is in use.
    with pytest.raises(TypeError):
        MVPPSPPatcher(tmp_path / "cache", api_key="secret")


def test_an_unsupported_provider_is_refused(tmp_path):
    with pytest.raises(CapabilityError):
        MVPPSPPatcher(tmp_path / "cache", provider="nhl")


def test_the_default_provider_is_espn(tmp_path):
    assert MVPPSPPatcher(tmp_path / "cache").provider == "espn"


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
    assert info.extra["sections_read"] == mvp_models.SECTION_COUNT - 1


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


def test_fetching_answers_a_league_of_the_teams_with_slots(tmp_path):
    patcher = make_patcher(tmp_path)
    assert len(patcher.fetch(season=2025).teams) == 4


def test_a_team_with_no_slot_is_never_fetched(tmp_path):
    api = FakeApi(teams=[*default_teams(), Team(id=99, name="Nowhere", code="ZZZ")])
    patcher = make_patcher(tmp_path, api)
    patcher.fetch(season=2025)
    assert [tid for tid, _ in api.squad_calls] == [1, 2, 3, 4]


def test_the_squad_call_carries_the_season(tmp_path):
    # The squad endpoint has no season in its URL but does have one in its cache key,
    # so without it the first season ever fetched was served forever.
    api = FakeApi()
    make_patcher(tmp_path, api).fetch(season=2019)
    assert api.squad_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_the_leaders_call_carries_the_season(tmp_path):
    api = FakeApi()
    make_patcher(tmp_path, api).fetch(season=2019)
    assert api.leader_calls == [(1, 2019), (2, 2019), (3, 2019), (4, 2019)]


def test_two_seasons_ask_for_two_different_squads(tmp_path):
    # A patcher that passed a constant season would satisfy either test above alone.
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
    # `TeamRoster.extra` rather than `self.team_stats`: the source's instance side
    # channel had to be copied between two patcher instances by hand, and without
    # that copy every player silently took position defaults.
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
    # Without `TeamRoster.extra` every player would silently take position defaults.
    squad = [make_player(1, "SP")]
    plain = mapped(tmp_path, {1: squad})
    with_stats = mapped(tmp_path, {1: squad}, {1: {"1": {"K": 250, "TBF": 800, "WAR": 5.0}}})
    assert plain.teams[0][0].ratings == {}
    assert {"pitch_velocity", "pitch_starpower"} <= set(with_stats.teams[0][0].ratings)


def test_an_empty_alias_does_not_wipe_a_populated_slot(tmp_path):
    # `OAK` and `ATH` name one slot, and the source assigned unconditionally, so an
    # empty alias arriving second left that club's 2005 roster on the disc under a
    # success report.
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


def test_a_cross_fall_leaves_the_ids_kept_rows_list():
    # Issue #18 in PPSSPP: a club's spare batter took its first pitcher id, which on
    # the disc is often its All-Star's, so the AL All-Stars opened with Nolan Gorman.
    pool = _HashPool(["0aaaaaaaa", "0bbbbbbbb"], {"0aaaaaaaa", "0bbbbbbbb"}, kept={"0aaaaaaaa"})
    assert pool.take(is_pitcher=False, team_index=0, player_index=0) == "0bbbbbbbb"


def test_a_player_of_the_right_kind_takes_a_kept_rows_id_first():
    pool = _HashPool(["0aaaaaaaa", "0bbbbbbbb"], set(), kept={"0bbbbbbbb"})
    assert pool.take(is_pitcher=False, team_index=0, player_index=0) == "0bbbbbbbb"


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
    # A synthesised id cannot collide with a real one: every id in this repository
    # is nine characters and this is eleven.
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


def patch_one(tmp_path, squads, leaders=None, spec=DISC, out="out.iso", *, order_as_given=False):
    """Map and patch in one step, returning (result, output path)."""
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(
        league(squads=squads, leaders=leaders), order_as_given=order_as_given
    )
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
    # `targets` is sorted, so progress and fresh roster row ids follow slot order.
    # A `MappedRosters` that crossed a JSON boundary carries whatever order its dict
    # was built in; this one is built backwards.
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
    # `i / len(targets)`, so the first club is reported before any of its work rather
    # than after. With one club `(i + 1) / len(targets)` is 1.0 times
    # `PROGRESS_RECORDS_END`, a number the run emits anyway, so it takes two clubs.
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
    # The merge preserves what this patcher has no source for: a player without
    # a birth date keeps the birthday of the id he took.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "attrib")
    written = [cols for cols in table.values() if cols.get(ATTRIB_LAST_NAME) == "Family1000"]
    assert ATTRIB_BIRTHDAY in written[0]


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


# The assertion above cannot fail: `table` is a dict, so `set(table)` is its own key
# set and the two lengths are equal by construction. What a reused id actually does
# is silently drop a row, so these count the rows.


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


def test_a_clubs_new_rows_take_its_old_rows_ids_then_fresh_ones(tmp_path):
    # The game reads a club's squad from its place in the table, so the new rows
    # keep the old ones' ids (and places); a longer squad's extra rows take ids
    # past the highest the disc held.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    old = [int(fixture.roster_row_id(0, i), 16) for i in range(DISC.players_per_team)]
    highest = int(fixture.roster_row_id(DISC.teams - 1, DISC.players_per_team - 1), 16)
    extra = len(FULL_SQUAD_POSITIONS) - len(old)
    expected = old[: len(FULL_SQUAD_POSITIONS)] + [highest + 1 + i for i in range(max(0, extra))]
    assert sorted(int(rid, 16) for rid in roster_rows(out, 0)) == sorted(expected)


def test_the_last_clubs_rows_keep_their_ids(tmp_path):
    patcher = make_patcher(tmp_path)
    mapped = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, DISC),
        output_path=tmp_path / "out.iso",
        rosters=MappedRosters(game_id=mapped.game_id, teams={TEAM_COUNT - 1: mapped.teams[0]}),
    )
    first = int(fixture.roster_row_id(DISC.teams - 1, 0), 16)
    written = roster_rows(tmp_path / "out.iso", TEAM_COUNT - 1)
    assert min(int(rid, 16) for rid in written) == first


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


#: Every club's block as long as a full squad, which is what the retail disc has:
#: 36 sides of 25 rows each.
RETAIL_SHAPED_DISC = fixture.DiscSpec(teams=30, players_per_team=25)


def test_a_patch_never_writes_over_a_player_a_kept_row_lists(tmp_path):
    # Issue #18: ids came from one pool in `attrib` order, so a squad took ids that
    # rows the patch keeps still list (other clubs; on the disc also the All-Star,
    # Legends, Heroes and Burnaby sides), and those sides showed today's players.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    before = patched_table(tmp_path / "game.iso", "attrib")
    after = patched_table(out, "attrib")
    kept = {
        cols[ROSTER_PLAYERID]
        for slot in range(1, DISC.teams)
        for cols in roster_rows(out, slot).values()
    }
    assert [pid for pid in kept if after[pid] != before[pid]] == []


def roster_order(path):
    blob = fixture.read_database_big(path.read_bytes(), lba=fixture.SMALL_LBA)
    table = fixture.decompress_section_at(blob, "roster")
    rows = fixture.parse_table(table)
    return [(rid, rows[rid][ROSTER_TEAMID]) for rid in fixture.parse_table_order(table)]


def test_a_short_squad_keeps_every_clubs_rows_in_their_places(tmp_path):
    # Issue #18: the game reads a club's squad from its place in the table. A squad
    # shorter than its club's block dropped the rest of the block, so every later
    # club's rows moved up and the clubs showed each other's players.
    _, out = patch_one(tmp_path, {1: full_squad(1000)[:20]}, spec=RETAIL_SHAPED_DISC)
    assert roster_order(out) == roster_order(tmp_path / "game.iso")


ORDER_COLUMNS = (ROSTER_RH_AL_ORDER, ROSTER_RH_NL_ORDER, ROSTER_LH_AL_ORDER, ROSTER_LH_NL_ORDER)


def test_the_rows_a_short_squad_leaves_bat_nobody(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)[:20]}, spec=RETAIL_SHAPED_DISC)
    attrib = patched_table(out, "attrib")
    left = [
        cols
        for cols in roster_rows(out, 0).values()
        if not attrib[cols[ROSTER_PLAYERID]][ATTRIB_LAST_NAME].startswith("Family")
    ]
    assert len(left) == RETAIL_SHAPED_DISC.players_per_team - 20
    assert {cols[c] for cols in left for c in ORDER_COLUMNS} == {str(NOT_IN_LINEUP)}


real_disc = pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding 'MVP Baseball (USA).iso'",
)


@pytest.mark.real_rom
@real_disc
def test_the_retail_disc_keeps_its_other_sides_and_every_clubs_place(tmp_path, monkeypatch):
    # Issue #18 on the real disc, in memory (no image is written): all 30 clubs
    # patched, Milwaukee short of 25. The six other sides -- AL and NL All-Stars,
    # MVP Legends and Heroes, the Burnaby Argonauts and Chimaera -- keep their rows.
    import dataclasses
    from pathlib import Path

    monkeypatch.undo()  # the real disc sits at the real `database.big` sector
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "MVP Baseball (USA).iso"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")
    patcher = make_patcher(tmp_path)
    squad = patcher.map_rosters(league(squads={1: full_squad(1000)})).teams[0]
    teams = {slot: [dataclasses.replace(r) for r in squad] for slot in range(TEAM_COUNT)}
    teams[22] = teams[22][:20]
    writer = MVPPSPRomWriter(str(rom), os.devnull)
    assert writer.load()
    tables = writer.reader.records
    before = {name: dict(tables[name]) for name in ("attrib", "roster")}
    order = list(writer.reader.record_order["roster"])

    patcher._write_all_teams(writer, MappedRosters(game_id="mvp-psp", teams=teams), None)
    writer.rebuild_database_big()  # every rebuilt section still fits

    roster, attrib = tables["roster"], tables["attrib"]
    assert [(r, roster[r][ROSTER_TEAMID]) for r in order] == [
        (r, before["roster"][r][ROSTER_TEAMID]) for r in order
    ]
    clubs = set(TEAM_HASHES.values())
    all_stars = {"0005972f2", "00059749f"}
    others = [r for r in order if roster[r][ROSTER_TEAMID] not in clubs | all_stars]
    assert len(others) == 100
    assert all(roster[r] == before["roster"][r] for r in others)
    assert all(
        attrib[roster[r][ROSTER_PLAYERID]] == before["attrib"][roster[r][ROSTER_PLAYERID]]
        for r in others
    )
    assert attrib["0f58f3c1b"] == before["attrib"]["0f58f3c1b"]  # the "Default Default" template
    written = [roster[r][ROSTER_PLAYERID] for r in order if roster[r][ROSTER_TEAMID] in clubs]
    assert {attrib[p].get(42) for p in written} == {"0"}  # playerattrib_hidden
    # A pitcher's row on a kept side names a pitcher: a club's spare batter takes
    # an id no kept row lists.
    pitching = [
        roster[r][ROSTER_PLAYERID]
        for r in others + [r for r in order if roster[r][ROSTER_TEAMID] in all_stars]
        if roster[r][ROSTER_RH_AL_POS] in ROTATION_POSITIONS + BULLPEN_POSITIONS
    ]
    assert [p for p in pitching if attrib[p][ATTRIB_PRIMARY_POS] not in {"0", "10"}] == []
    # An All-Star now names today's player of his 2005 player's club.
    club_of = {}
    for r in order:
        if before["roster"][r][ROSTER_TEAMID] in clubs:
            club_of[before["roster"][r][ROSTER_PLAYERID]] = before["roster"][r][ROSTER_TEAMID]
    now_on = {
        roster[r][ROSTER_PLAYERID]: roster[r][ROSTER_TEAMID]
        for r in order
        if roster[r][ROSTER_TEAMID] in clubs
    }
    stars = [roster[r][ROSTER_PLAYERID] for r in order if roster[r][ROSTER_TEAMID] in all_stars]
    # One AL All-Star is a Burnaby Argonaut and on no club; he stays (checked above).
    assert all(now_on.get(p) == club_of[p] for p in stars if p in club_of)


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


def test_the_progress_bar_gives_the_copy_its_last_tenth():
    assert PROGRESS_RECORDS_END == 0.9


# A batter with no statistics takes his position's default for both splits, and a
# pitcher takes the same pair for both, so on the squads above `lrattrib_rhp` and
# `lrattrib_lhp` hold identical numbers. These give one batter statistics, which is
# the only thing in the mapper that makes the two sides differ: the left-hand column
# is five points below the right.
#
# `full_squad`'s player 1001 rather than 1000, because `make_player` gives every
# fifth id a switch hitter's `bats`, and a switch hitter's two splits are averaged.

LR_SPLIT_LEADERS = {1: {"1001": {"AVG": 0.330, "OBP": 0.420, "SLG": 0.600, "HR": 45}}}


def test_the_lineup_positions_reach_the_roster_table(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    positions = [cols[ROSTER_RH_AL_POS] for cols in roster_rows(out, 0).values()]
    assert sorted(positions[:9]) == sorted(LINEUP_POSITIONS)


def test_an_american_league_club_bats_in_the_al_columns(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    orders = {cols[ROSTER_RH_AL_ORDER] for cols in roster_rows(out, 0).values()}
    assert orders == {"1", "2", "3", "4", "5", "6", "7", "8", "9", "-1"}


def lineup(out, slot, pos_col, order_col):
    """Each of a slot's roster rows as (position, batting order), in squad order."""
    return [(cols[pos_col], cols[order_col]) for cols in roster_rows(out, slot).values()]


FIELDERS = [pos for pos in LINEUP_POSITIONS if pos != "DH"]


def test_an_american_league_club_also_bats_a_lineup_without_the_dh(tmp_path):
    # As on the retail disc, where Giambi is the Yankees' DH batting 9th in the
    # AL columns and a bench player in the NL ones, and their SP1 bats 9th
    # there. Interleague and World Series games at an NL park read these.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    nl = lineup(out, 0, ROSTER_RH_NL_POS, ROSTER_RH_NL_ORDER)
    assert nl[8] == ("B", "-1")
    assert [row for row in nl if row[1] != "-1"] == [
        *zip(FIELDERS, "12345678", strict=True),
        ("SP1", "9"),
    ]


def test_a_national_league_club_also_bats_a_lineup_with_the_dh(tmp_path):
    _, out = patch_one(tmp_path, {3: full_squad(2000)})
    al = lineup(out, 14, ROSTER_RH_AL_POS, ROSTER_RH_AL_ORDER)
    assert [row for row in al if row[1] != "-1"] == list(
        zip(LINEUP_POSITIONS, "123456789", strict=True)
    )


def test_a_dh_batting_early_moves_the_rest_of_the_nl_lineup_up_one(tmp_path):
    # The retail Braves: Franco is DH 8th and Langerhans LF 9th in the AL
    # columns, and Langerhans bats 8th in the NL ones.
    squad = full_squad(1000)
    squad.insert(0, squad.pop(8))  # the DH leads off
    _, out = patch_one(tmp_path, {1: squad}, order_as_given=True)
    al = lineup(out, 0, ROSTER_RH_AL_POS, ROSTER_RH_AL_ORDER)
    nl = lineup(out, 0, ROSTER_RH_NL_POS, ROSTER_RH_NL_ORDER)
    fielders = "C 1B 2B 3B SS LF CF RF".split()
    assert al[:9] == [("DH", "1"), *zip(fielders, "23456789", strict=True)]
    assert nl[:9] == [("B", "-1"), *zip(fielders, "12345678", strict=True)]
    assert nl[15] == ("SP1", "9")


def test_a_clubs_league_changes_nothing_in_its_roster_rows(tmp_path):
    # Toronto is the last AL slot and Arizona the first NL one.
    _, out = patch_one(tmp_path, {2: full_squad(1000), 3: full_squad(1000)})
    columns = range(ROSTER_RH_AL_POS, ROSTER_LH_NL_ORDER + 1)
    toronto, arizona = (
        [[row[c] for c in columns] for row in roster_rows(out, slot).values()]
        for slot in (AL_SLOT_COUNT - 1, AL_SLOT_COUNT)
    )
    assert toronto == arizona


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


def test_a_starters_second_pitch_reaches_the_repeating_block(tmp_path):
    squad = full_squad(1000)
    _, out = patch_one(tmp_path, {1: squad})
    table = patched_table(out, "pitchattrib")
    written = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == f"Given{squad[15].id}"]
    assert PA_PITCH2_TYPE in written[0]


# Membership alone does not say *which* pitch landed there. Pitch 1 is the asymmetric
# one -- always a fastball, four columns, no type column of its own -- so the
# repeating block starts at the arsenal's *second* entry, and slicing it from the
# first instead puts a fastball in the slider's type column.


def test_a_batter_gets_no_pitching_record_of_his_own(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    table = patched_table(out, "pitchattrib")
    batters = [c for c in table.values() if c.get(ATTRIB_FIRST_NAME) == "Given1000"]
    assert batters == []


def test_a_patch_that_cannot_be_stored_raises(tmp_path):
    # The source kept the original section, dropped every edit to it, and returned
    # success with a full count of teams and players patched.
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
    # Both tests above would pass on a disc that could not be patched at all.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    result = patcher.patch(
        rom_path=write_iso(tmp_path, FULL_DISC),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert result.players_patched == 25


def test_the_same_patch_on_a_roomier_disc_succeeds(tmp_path):
    # Which makes the two tests above about *fitting* and not about long names.
    patcher = make_patcher(tmp_path)
    rosters = patcher.map_rosters(league(squads={1: long_named_squad()}))
    result = patcher.patch(
        rom_path=write_iso(tmp_path, fixture.DiscSpec(teams=4, players_per_team=6)),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert result.players_patched == 25


def test_an_exhausted_pool_is_reported(tmp_path):
    # The degradation is preserved -- every alternative drops a player -- but said
    # out loud rather than silent.
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
    # `or`, not `and`. The exhausted-pool test above is a disc small enough that both
    # tiers fire, so it cannot tell the two connectives apart.
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
        "1 player(s) took an id from the other position pool and 0 were given a synthesised id"
    ]


def test_a_patch_that_does_not_degrade_says_nothing_about_it(tmp_path):
    # A club block of 30 holds ten pitchers and twenty batters, room for a full
    # squad of each kind: the club's own ids cover everyone.
    seen = []
    patcher = MVPPSPPatcher(tmp_path / "cache", on_status=seen.append)
    patcher.api = FakeApi()
    rosters = patcher.map_rosters(league(squads={1: full_squad(1000)}))
    patcher.patch(
        rom_path=write_iso(tmp_path, fixture.DiscSpec(teams=1, players_per_team=30)),
        output_path=tmp_path / "out.iso",
        rosters=rosters,
    )
    assert [m for m in seen if "synthesised id" in m] == []


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


def _ranked_squad():
    """Eighteen batters, six starters and six relievers; jersey = id + 1."""
    positions = ["LF"] * 18 + ["SP"] * 6 + ["RP"] * 6
    return [make_player(pid, pos) for pid, pos in enumerate(positions)]


def _ranked_leaders(squad):
    """Leaders that rank every player by id, highest first."""
    return {1: {str(p.id): {"OPS": p.id, "H": p.id, "W": p.id, "SV": p.id} for p in squad}}


def test_order_as_given_keeps_the_blocks_and_the_callers_order_inside_them(tmp_path):
    squad = _ranked_squad()
    given = squad[::2] + squad[1::2]
    patcher = make_patcher(tmp_path)

    result = patcher.map_rosters(
        league(squads={1: given}, leaders=_ranked_leaders(squad)), order_as_given=True
    )

    batters = [p for p in given if p.position == "LF"][:15]
    pitchers = [p for p in given if p.position != "LF"][:10]
    assert [r.jersey for r in result.teams[0]] == [p.number for p in batters + pitchers]


def test_without_order_as_given_the_input_order_is_ignored(tmp_path):
    squad = _ranked_squad()
    leaders = _ranked_leaders(squad)
    forward = mapped(tmp_path, {1: squad}, leaders).teams[0]
    backward = mapped(tmp_path, {1: squad[::-1]}, leaders).teams[0]
    assert [r.jersey for r in forward] == [r.jersey for r in backward]


def given(tmp_path, squad):
    return make_patcher(tmp_path).map_rosters(league(squads={1: squad}), order_as_given=True)


def test_order_as_given_moves_a_batter_up_the_order_without_moving_him_in_the_field(tmp_path):
    squad = full_squad(1000)
    squad.insert(0, squad.pop(4))  # the shortstop leads off
    records = given(tmp_path, squad).teams[0][:9]
    assert [r.roster_position for r in records] == "SS C 1B 2B 3B LF CF RF DH".split()
    assert [r.batting_order for r in records] == list(range(1, 10))


def test_order_as_given_hands_a_missing_position_to_the_spare_starter(tmp_path):
    # The catcher swapped with the bench outfielder: someone has to catch.
    squad = full_squad(1000)
    squad[0], squad[9] = squad[9], squad[0]
    records = given(tmp_path, squad).teams[0]
    assert [r.roster_position for r in records[:10]] == "C 1B 2B 3B SS LF CF RF DH B".split()


@pytest.mark.parametrize("order_as_given", [False, True])
def test_a_short_bench_does_not_pull_the_rotation_into_it(tmp_path, order_as_given):
    squad = [make_player(100 + i, "LF") for i in range(13)]
    squad += [make_player(200 + i, "SP") for i in range(5)]
    squad += [make_player(300 + i, "RP") for i in range(5)]
    patcher = make_patcher(tmp_path)
    records = patcher.map_rosters(league(squads={1: squad}), order_as_given=order_as_given)
    pitchers = [r for r in records.teams[0] if r.is_pitcher]
    assert [r.roster_position for r in pitchers] == [*ROTATION_POSITIONS, *BULLPEN_POSITIONS]
    assert [r.primary_position for r in pitchers] == ["SP"] * 5 + ["RP"] * 5


def test_order_as_given_sends_spare_pitchers_to_long_relief(tmp_path):
    squad = [make_player(100 + i, "LF") for i in range(13)]
    squad += [make_player(200 + i, "P") for i in range(13)]
    records = given(tmp_path, squad).teams[0]
    assert len(records) == 25
    assert [r.roster_position for r in records[13:]] == [
        *ROTATION_POSITIONS,
        *BULLPEN_POSITIONS,
        "LR",
        "LR",
    ]


def test_order_as_given_sends_spare_batters_to_the_bench(tmp_path):
    squad = [make_player(100 + i, "LF") for i in range(17)]
    squad += [make_player(200 + i, "SP") for i in range(8)]
    records = given(tmp_path, squad).teams[0]
    assert [(r.roster_position, r.batting_order) for r in records[15:17]] == [("B", -1)] * 2
    assert len(records) == 25


def test_the_nine_bat_in_the_order_the_club_has_used_them():
    from retro_roster_patcher.games.mvp_psp.models import MVPPlayerRecord
    from retro_roster_patcher.games.mvp_psp.patcher import _bat_in_lineup_order

    nine = [MVPPlayerRecord(player_id=i, batting_order=i + 1) for i in range(9)]
    bench = MVPPlayerRecord(player_id=99, batting_order=-1)
    _bat_in_lineup_order(nine + [bench], [{"id": 8, "order": 1.0}, {"id": 0, "order": 4.2}])
    assert [r.batting_order for r in nine][:1] == [2] and nine[8].batting_order == 1
    assert bench.batting_order == -1


def test_the_editors_list_handed_back_bats_as_the_patch_alone_would(tmp_path):
    # The app shows `suggest_squad_order` and patches it back `order_as_given`:
    # the nine and their batting order must not change on the way.
    patcher = make_patcher(tmp_path)
    data = league(squads={default_teams()[0].id: full_squad(1000)})
    roster = data.teams[0]
    batters = [p for p in roster.players if not patcher.mapper.is_pitcher(p)]
    roster.extra["lineup"] = [
        {"id": batters[5].id, "order": 1.0},
        {"id": batters[0].id, "order": 3.0},
    ]
    alone = patcher.map_rosters(data)
    roster.players = patcher.suggest_squad_order(roster)
    edited = patcher.map_rosters(data, order_as_given=True)

    def nine(mapped):
        return sorted(
            (r.batting_order, r.player_id)
            for records in mapped.teams.values()
            for r in records
            if 1 <= r.batting_order <= 9
        )

    assert nine(edited) == nine(alone)
    assert nine(alone)[0][1] == batters[5].id


# What a player's ratings become on the disc: the value at his rank of the disc's
# own spread (`spreads`), his position's median without one.


def disc_spreads(tmp_path):
    reader = MVPPSPRomReader(str(write_iso(tmp_path, DISC)))
    reader.load()
    reader.decompress_all()
    reader.parse_all()
    return reader.records, Spreads.of(reader.records, "lrattrib_lhp", "lrattrib_rhp")


def disc_values(records, table, column, positions):
    """Every value the disc's players at `positions` hold in one column, sorted."""
    attrib = records["attrib"]
    return sorted(
        int(row[column])
        for pid, row in records[table].items()
        if attrib[pid][ATTRIB_PRIMARY_POS] in positions and row.get(column, "-").isdigit()
    )


BATTER_CODES = {str(code) for code in range(1, 9)}


def batter(**kw):
    record = MVPPlayerRecord(primary_position="SS", player_id=7, **kw)
    return record


def test_the_leagues_best_gets_the_discs_best_and_its_worst_the_discs_worst(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    speeds = disc_values(records, "attrib", ATTRIB_SPEED, BATTER_CODES)
    top = _attrib_fields(batter(ratings={"speed": 1.0}), spreads)
    bottom = _attrib_fields(batter(ratings={"speed": 0.0}), spreads)
    assert (int(top[ATTRIB_SPEED]), int(bottom[ATTRIB_SPEED])) == (speeds[-1], speeds[0])


def test_a_rating_without_a_rank_is_his_positions_median(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    shortstops = disc_values(records, "attrib", ATTRIB_SPEED, {"5"})
    fields = _attrib_fields(batter(), spreads)
    assert int(fields[ATTRIB_SPEED]) == shortstops[len(shortstops) // 2]


def test_height_and_weight_are_written_above_48_inches_and_100_pounds(tmp_path):
    _, spreads = disc_spreads(tmp_path)
    fields = _attrib_fields(batter(height=79, weight=282), spreads)
    assert (fields[ATTRIB_HEIGHT], fields[ATTRIB_WEIGHT]) == ("31", "182")
    assert fields[ATTRIB_HIDDEN] == "0"


def test_a_birthday_is_days_from_1948(tmp_path):
    _, spreads = disc_spreads(tmp_path)
    fields = _attrib_fields(batter(birth_date="1966-11-14"), spreads)
    assert fields[ATTRIB_BIRTHDAY] == "6892"


def test_a_birth_date_moves_back_to_the_discs_calendar():
    # The game ages players by 2005: born 1999, he would be 5.
    assert _as_of_disc("1999-06-21", 2025) == "1979-06-21"
    assert _as_of_disc("2000-02-29", 2025) == "1980-02-28"
    assert _as_of_disc("", 2025) == ""


def test_a_player_without_a_second_position_takes_his_positions_commonest(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    seconds = [
        row[ATTRIB_SECONDARY_POS]
        for row in records["attrib"].values()
        if row[ATTRIB_PRIMARY_POS] == "5"
    ]
    fields = _attrib_fields(batter(), spreads)
    assert fields[ATTRIB_SECONDARY_POS] == max(set(seconds), key=seconds.count)
    assert _attrib_fields(batter(secondary_position=4), spreads)[ATTRIB_SECONDARY_POS] == "4"


def test_a_split_rows_balls_in_play_and_fly_balls_each_sum_to_a_hundred(tmp_path):
    _, spreads = disc_spreads(tmp_path)
    for side, table in (("lhp", "lrattrib_lhp"), ("rhp", "lrattrib_rhp")):
        names = {"lrattrib_chasefb": 20}
        fields = _split_fields(batter(ratings={f"hr_{side}": 1.0}), spreads, table, side, names)
        assert sum(int(fields[c]) for c in (LR_HR_PCT, LR_FB_PCT, LR_LD_PCT, LR_GB_PCT)) == 100
        assert sum(int(fields[c]) for c in LR_DIRECTION) == 100
        assert 20 in fields


def test_the_best_hitter_has_more_hot_zones_than_the_worst(tmp_path):
    _, spreads = disc_spreads(tmp_path)

    def hot(share):
        record = batter(ratings={"zones_rhp": share})
        fields = _split_fields(record, spreads, "lrattrib_rhp", "rhp", {})
        return [fields[c] for c in LR_ZONES].count(str(ZONE_HOT))

    assert hot(1.0) > hot(0.0)


def test_a_pitchers_row_is_whole(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    pitcher = MVPPlayerRecord(
        primary_position="SP", is_pitcher=True, pitch_types=(1, 6), ratings={"pitch_velocity": 1.0}
    )
    fields = _pitch_fields(pitcher, spreads)
    fastballs = disc_values(records, "pitchattrib", PA_PITCH1_VELOCITY, {"0"})
    assert int(fields[PA_PITCH1_VELOCITY]) <= fastballs[-1]
    assert fields[PA_PITCH2_TYPE] == "1"
    assert fields[PA_PITCH2_TYPE + PA_PITCH_STRIDE] == "6"
    # The disc's changeups are 14 slower than the fastball, its sliders 9.
    assert int(fields[PA_PITCH2_TYPE + 4]) == int(fields[PA_PITCH1_VELOCITY]) - 14
    # No third or fourth pitch: the two slots hold nothing, not the old pitcher's.
    third = PA_PITCH2_TYPE + 2 * PA_PITCH_STRIDE
    assert {fields[c] for c in range(third, PA_PITCHER_DELIVERY)} == {NO_PITCH}
    assert fields[PA_PITCHER_DELIVERY] in {"0", "1", "2"}
    assert set(fields) == set(range(PA_PITCHER_DELIVERY + 1))


def test_a_batter_given_a_pitchers_id_loses_the_pitchers_row(tmp_path):
    # Squads hold one more batter than the club's disc block has batter ids, so
    # the spare batter takes a pitcher's.
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    attrib = patched_table(out, "attrib")
    batters = {
        pid
        for pid, row in attrib.items()
        if row[ATTRIB_FIRST_NAME].startswith("Given") and row[ATTRIB_PRIMARY_POS] not in ("0", "10")
    }
    assert len(batters) == 15
    assert not batters & set(patched_table(out, "pitchattrib"))


def test_a_patched_tables_directory_entry_is_its_new_length(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    blob = fixture.read_database_big(out.read_bytes(), lba=fixture.SMALL_LBA)
    offset, length = fixture.directory_entries(blob)["attrib"]
    assert fixture.parse_table(refpack_decompress(blob[offset : offset + length]))
    assert blob[offset + length : offset + length + 4] == bytes(4)


def test_a_players_build_is_the_disc_profile_nearest_his_height_and_weight():
    light = [(24, 80, 26, 1)] * 5
    heavy = [(27, 135, 36, 2)] * 5
    spreads = Spreads(bodies=light + heavy + [(24, 80, 99, 1)])
    assert spreads.build(25, 85) == (26, 1)
    assert spreads.build(27, 140) == (36, 2)
    assert Spreads().build(25, 85) is None


def disc_ages(records):
    """Each disc player's age in the disc's season, by id."""
    return {
        pid: DISC_SEASON - (BIRTHDAY_EPOCH + timedelta(int(row[ATTRIB_BIRTHDAY]))).year
        for pid, row in records["attrib"].items()
    }


def test_a_player_is_paid_what_the_discs_players_of_his_age_are(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    ages = disc_ages(records)
    born = "1975-06-01"  # 30 in 2005
    for column in (ATTRIB_SALARY, ATTRIB_CONTRACT_LENGTH):
        peers = sorted(
            int(row[column]) for pid, row in records["attrib"].items() if 29 <= ages[pid] <= 31
        )
        fields = _attrib_fields(batter(birth_date=born, experience=8), spreads)
        assert int(fields[column]) == peers[len(peers) // 2]


def test_a_player_before_arbitration_is_paid_the_discs_lowest_salary_for_a_year(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    fields = _attrib_fields(batter(birth_date="1975-06-01", experience=2), spreads)
    for column in (ATTRIB_SALARY, ATTRIB_CONTRACT_LENGTH):
        lowest = min(int(row[column]) for row in records["attrib"].values())
        assert int(fields[column]) == lowest


def test_a_player_without_a_birth_date_is_paid_his_positions_median(tmp_path):
    records, spreads = disc_spreads(tmp_path)
    shortstops = disc_values(records, "attrib", ATTRIB_SALARY, {"5"})
    fields = _attrib_fields(batter(experience=8), spreads)
    assert int(fields[ATTRIB_SALARY]) == shortstops[len(shortstops) // 2]


def test_a_left_handers_pitches_take_the_discs_left_handers_description(tmp_path):
    # The fixture's left-handers hold the mirror image of its right-handers'.
    _, spreads = disc_spreads(tmp_path)

    def description(throws):
        pitcher = MVPPlayerRecord(
            primary_position="SP", is_pitcher=True, pitch_types=(6,), throws=throws
        )
        return _pitch_fields(pitcher, spreads)[PA_PITCH2_TYPE + 2]

    assert (description(0), description(1)) == ("3", "5")


def test_a_left_hander_winds_up_in_one_of_the_discs_left_handers_deliveries(tmp_path):
    _, spreads = disc_spreads(tmp_path)

    def delivery(throws):
        pitcher = MVPPlayerRecord(primary_position="SP", is_pitcher=True, throws=throws)
        return _pitch_fields(pitcher, spreads)[PA_PITCHER_DELIVERY]

    assert (delivery(0) in {"0", "1", "2"}, delivery(1) in {"3", "4", "5"}) == (True, True)


def test_a_patched_players_statistics_are_his_own(tmp_path):
    squad = full_squad(1000)
    line = {"GP": 150, "AB": 500, "H": 150, "2B": 30, "3B": 5, "HR": 20, "R": 80, "SB": 9, "CS": 2}
    line |= {"RBI": 70, "SO": 100, "BB": 50, "HBP": 3, "IBB": 0, "SH": 0, "SF": 4}
    _, out = patch_one(tmp_path, {1: squad}, {1: {str(squad[0].id): line}})
    attrib = patched_table(out, "attrib")
    ids = {row[ATTRIB_FIRST_NAME]: pid for pid, row in attrib.items()}
    with_line, without = ids[f"Given{squad[0].id}"], ids[f"Given{squad[1].id}"]
    batstat = patched_table(out, "batstat")
    assert [batstat[with_line][c] for c in range(6)] == [
        f"Given{squad[0].id}",
        f"Family{squad[0].id}",
        "150",
        "80",
        "9",
        "2",
    ]
    # No line: zeros under his own name, not the 500s the old player had.
    assert [batstat[without][c] for c in (0, 2, 3)] == [f"Given{squad[1].id}", "0", "0"]
    assert patched_table(out, "lrbatstat_rhp")[with_line][13] == "557"
    assert patched_table(out, "careerstats")[with_line][3] == "500"


def test_a_batter_given_a_pitchers_id_loses_the_pitchers_statistics(tmp_path):
    _, out = patch_one(tmp_path, {1: full_squad(1000)})
    attrib = patched_table(out, "attrib")
    patched = {pid for pid, row in attrib.items() if row[ATTRIB_FIRST_NAME].startswith("Given")}
    pitchers = {pid for pid in patched if attrib[pid][ATTRIB_PRIMARY_POS] in ("0", "10")}
    for table in PITCHER_STAT_TABLES:
        assert set(patched_table(out, table)) & patched == pitchers
    assert patched <= set(patched_table(out, "fieldstat"))
