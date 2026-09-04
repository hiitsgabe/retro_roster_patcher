"""`NHL05PS2Patcher` against the unified interface.

Six things here are not in the ported code at all, and each has its own section:

  * `_db_viv_extent_fits`, the arithmetic bound, without which a truncated
    archive is decompressed short, serialised shorter still, and written back to
    the disc under a success report;
  * `_live_records`, the `min(num_records, capacity)` bound that
    `formats/ea_tdb.py` hands to its consumers;
  * the alias guard, without which an empty `SJS` wipes a populated `SJ`;
  * `season` threaded into both ESPN calls, which the source omitted from both;
  * `TeamRoster.extra["leaders"]` in place of `self.team_stats`;
  * `teams_patched` counting slots that placed a player.

And two are NHL 2005 rather than NHL 07, and are what a copied test file would
leave unmeasured:

  * **thirty patchable slots, not thirty-two.** `SEA` and `VGK` map to the two
    All-Star sides and are then dropped, so this game never writes them.
  * **one mirror, not two.** There is no `nhlbioatt.tdb`, so every bio and
    attribute write happens once.

Every read-back of a patched image goes through the fixture's own decoder --
`iso_read_file`, `unpack_bits` -- and never through the reader and writer that
produced it.
"""

from __future__ import annotations

import os

import pytest

from retro_roster_patcher.core.errors import ApiError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot
from retro_roster_patcher.core.registry import get_patcher
from retro_roster_patcher.games.nhl05_ps2.models import (
    NAMED_SLOT_COUNT,
    NHL05_TEAM_NAMES,
    PATCHABLE_SLOT_COUNT,
    TDB_MASTER,
    TDB_ROSTER,
    NHL05PlayerRecord,
)
from retro_roster_patcher.games.nhl05_ps2.patcher import (
    NHL05PS2Patcher,
    _db_viv_extent,
    _db_viv_extent_fits,
    _index_map,
    _live_records,
    _play_id_by_indx,
)
from retro_roster_patcher.games.nhl05_ps2.rom_reader import ISO_SECTOR_SIZE, NHL05PS2RomReader
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nhl import NhlApiClient
from tests.fixtures import synthetic_nhl05_iso as fixture

#: Slots the fixture disc carries roster rows for. The disc has four teams;
#: `MODERN_NHL_TO_NHL05` names them ANA, ATL, BOS and BUF.
DISC_SLOTS = list(range(fixture.TEAM_COUNT))


class FakeApi:
    """Stands in for `EspnClient` and `NhlApiClient`.

    Records every call, because `season` reaches the two ESPN endpoints
    differently -- a cache key on the squad call, a URL path segment on the
    leaders call -- and the source passed it to neither.
    """

    def __init__(self, teams=None, squads=None, leaders=None):
        self._teams = teams if teams is not None else default_teams()
        self._squads = squads or {}
        self._leaders = leaders or {}
        self.squad_calls: list[tuple] = []
        self.leader_calls: list[tuple] = []

    def get_nhl_teams(self):
        return list(self._teams)

    def get_hockey_squad(self, key, season=None):
        self.squad_calls.append((key, season))
        return list(self._squads.get(key, []))

    def get_hockey_team_leaders(self, key, season=None):
        self.leader_calls.append((key, season))
        return dict(self._leaders.get(key, {}))


def default_teams():
    return [
        Team(id=1, name="Anaheim Mighty Ducks", code="ANA", logo_url=""),
        Team(id=2, name="Atlanta Thrashers", code="ATL", logo_url=""),
        Team(id=3, name="Boston Bruins", code="BOS", logo_url=""),
        Team(id=4, name="Buffalo Sabres", code="BUF", logo_url=""),
    ]


def make_player(pid, position, name=None, number=None):
    return Player(
        id=pid,
        name=name or f"Given{pid} Family{pid}",
        first_name=f"Given{pid}",
        last_name=f"Family{pid}",
        age=25,
        nationality="CAN",
        position=position,
        number=number if number is not None else (pid % 60) + 1,
        photo_url="",
        weight=195.0,
        handedness="L" if pid % 2 else "R",
    )


def squad(base):
    """Two goalies, twelve forwards and eight defencemen, deterministically."""
    positions = ["G", "G"] + ["C", "LW", "RW"] * 4 + ["D"] * 8
    return [make_player(base + i, p) for i, p in enumerate(positions)]


def league(teams=None, squads=None, leaders=None, season=2025):
    teams = teams if teams is not None else default_teams()
    rosters = [
        TeamRoster(
            team=team,
            players=(squads or {}).get(team.code, squad(1000 * (index + 1))),
            extra={"leaders": (leaders or {}).get(team.code, {})},
        )
        for index, team in enumerate(teams)
    ]
    return LeagueData(
        league=League(
            id=0,
            name="NHL",
            country="USA",
            country_code="US",
            logo_url="",
            season=season,
            teams_count=len(rosters),
        ),
        teams=rosters,
    )


def build(tmp_path, api=None, **kw):
    patcher = NHL05PS2Patcher(tmp_path / "cache", **kw)
    if api is not None:
        patcher.api = api
    return patcher


def iso(tmp_path, spec=None, name="game.iso"):
    path = tmp_path / name
    fixture.write_iso(path, spec)
    return path


def _records(path, table, fields, size, member=TDB_MASTER):
    return fixture.read_table_records(
        fixture.read_member(path.read_bytes(), member), table, fields, size
    )


def spbt_of(path, member=TDB_MASTER):
    return _records(path, "SPBT", fixture.SPBT_FIELDS, fixture.SPBT_RECORD_SIZE, member)


def rost_of(path, member=TDB_MASTER):
    return _records(path, "ROST", fixture.ROST_FIELDS, fixture.ROST_RECORD_SIZE, member)


def spai_of(path, member=TDB_MASTER):
    return _records(path, "SPAI", fixture.SPAI_FIELDS, fixture.SPAI_RECORD_SIZE, member)


def sgai_of(path, member=TDB_MASTER):
    return _records(path, "SGAI", fixture.SGAI_FIELDS, fixture.SGAI_RECORD_SIZE, member)


def patched(tmp_path, api=None, spec=None, on_progress=None, data=None):
    """Run the whole pipeline and return (result, output path)."""
    source = iso(tmp_path, spec)
    patcher = build(tmp_path, api or FakeApi())
    rosters = patcher.map_rosters(data if data is not None else league())
    out = tmp_path / "patched.iso"
    result = patcher.patch(
        rom_path=source, output_path=out, rosters=rosters, on_progress=on_progress
    )
    return result, out


# -- registration ----------------------------------------------------------


def test_the_game_is_registered_under_its_id():
    assert get_patcher("nhl05-ps2") is NHL05PS2Patcher


def test_the_platform_is_ps2():
    assert NHL05PS2Patcher.platform == "ps2"


def test_the_sport_is_hockey():
    assert NHL05PS2Patcher.sport == "hockey"


def test_the_game_needs_no_slot_mapping():
    # Every slot is a real club with a real abbreviation, so teams match by name
    # and there is nothing for a user to choose. Unlike `iss-snes`.
    assert NHL05PS2Patcher.requires_slot_mapping is False


def test_both_providers_are_offered():
    assert NHL05PS2Patcher.providers == ("espn", "nhl")


def test_the_default_provider_is_espn(tmp_path):
    assert build(tmp_path).provider == "espn"


def test_the_espn_branch_builds_an_espn_client(tmp_path):
    assert type(NHL05PS2Patcher(tmp_path / "c").api) is EspnClient


def test_the_nhl_branch_builds_an_nhl_client(tmp_path):
    assert type(NHL05PS2Patcher(tmp_path / "c", provider="nhl").api) is NhlApiClient


def test_an_api_key_is_refused_rather_than_accepted_and_ignored(tmp_path):
    with pytest.raises(TypeError):
        NHL05PS2Patcher(tmp_path / "c", api_key="secret")


def test_construction_creates_the_cache_directory(tmp_path):
    NHL05PS2Patcher(tmp_path / "cache")
    assert (tmp_path / "cache").is_dir() is True


# -- slot counts -----------------------------------------------------------


def test_thirty_slots_are_patchable():
    # NHL 07 patches 32. The source wrote `slot >= 30` here and `slot >= 32`
    # there, and it agrees with the reader's `INDX > 29` filter.
    assert PATCHABLE_SLOT_COUNT == 30


def test_thirty_two_slots_have_names():
    # The two All-Star sides have display names and are never written.
    assert NAMED_SLOT_COUNT == 32


def test_the_two_unpatchable_slots_are_the_all_star_sides():
    assert NHL05_TEAM_NAMES[PATCHABLE_SLOT_COUNT:] == ["East All-Star", "West All-Star"]


def test_every_display_name_is_distinct():
    # `RomSlot.display_name` is what a slot-picking UI lists.
    assert len(set(NHL05_TEAM_NAMES)) == NAMED_SLOT_COUNT


# -- analyze ---------------------------------------------------------------


def test_analyzing_a_well_formed_image_reports_it_as_valid(tmp_path):
    assert build(tmp_path).analyze_rom(iso(tmp_path)).is_valid is True


def test_analyzing_reports_the_game_id(tmp_path):
    assert build(tmp_path).analyze_rom(iso(tmp_path)).game_id == "nhl05-ps2"


def test_analyzing_reports_the_files_size(tmp_path):
    path = iso(tmp_path)
    assert build(tmp_path).analyze_rom(path).size == path.stat().st_size


def test_analyzing_lists_the_thirty_club_slots(tmp_path):
    assert len(build(tmp_path).analyze_rom(iso(tmp_path)).slots) == 30


def test_analyzing_lists_the_slots_in_index_order(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert [s.index for s in info.slots] == list(range(30))


def test_a_slots_current_name_comes_from_the_disc(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert info.slots[0].current_name == fixture.stea_full_name(0)


def test_a_slots_display_name_comes_from_the_constant(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert info.slots[0].display_name == NHL05_TEAM_NAMES[0]


def test_the_two_names_of_a_slot_differ(tmp_path):
    # Guards against zero-over-zero: if the fixture used real club names, a
    # patcher that ignored STEA would satisfy both tests above.
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert info.slots[0].current_name != info.slots[0].display_name


def test_every_slot_is_a_rom_slot(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert {type(s) for s in info.slots} == {RomSlot}


def test_analyzing_reports_the_archive_size_in_extra(tmp_path):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    info = build(tmp_path).analyze_rom(path)
    assert info.extra["db_viv_size"] == len(reader.get_db_viv())


def test_analyzing_reports_the_slot_count_in_extra(tmp_path):
    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert info.extra["team_slot_count"] == 30


def test_the_extra_mapping_is_json_serialisable(tmp_path):
    import json

    info = build(tmp_path).analyze_rom(iso(tmp_path))
    assert json.loads(json.dumps(info.extra)) == info.extra


def test_analyzing_a_missing_file_raises(tmp_path):
    with pytest.raises(RomError):
        build(tmp_path).analyze_rom(tmp_path / "absent.iso")


def test_analyzing_an_unreadable_file_raises(tmp_path):
    path = iso(tmp_path)
    path.chmod(0o000)
    try:
        with pytest.raises(RomError):
            build(tmp_path).analyze_rom(path)
    finally:
        path.chmod(0o644)


def test_analyzing_a_file_that_is_not_this_game_does_not_raise(tmp_path):
    # `analyze` probes every registered patcher against one image, so this
    # distinction is load-bearing: `RomError` for unreadable, `is_valid=False`
    # for readable-but-not-mine.
    path = tmp_path / "other.iso"
    path.write_bytes(b"\x00" * (ISO_SECTOR_SIZE * 40))
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_a_file_that_is_not_this_game_still_reports_its_size(tmp_path):
    path = tmp_path / "other.iso"
    path.write_bytes(b"\x00" * (ISO_SECTOR_SIZE * 40))
    assert build(tmp_path).analyze_rom(path).size == ISO_SECTOR_SIZE * 40


def test_a_file_that_is_not_this_game_lists_no_slots(tmp_path):
    path = tmp_path / "other.iso"
    path.write_bytes(b"\x00" * (ISO_SECTOR_SIZE * 40))
    assert build(tmp_path).analyze_rom(path).slots == []


def test_an_archive_without_the_master_tdb_is_not_valid(tmp_path):
    spec = fixture.DiscSpec(master_name=None)
    assert build(tmp_path).analyze_rom(iso(tmp_path, spec)).is_valid is False


def test_a_ps2_disc_that_is_not_a_bigf_is_not_valid(tmp_path):
    spec = fixture.DiscSpec(archive_magic=b"BIGX")
    assert build(tmp_path).analyze_rom(iso(tmp_path, spec)).is_valid is False


# -- the arithmetic bound --------------------------------------------------


def truncate_to(path, length):
    os.truncate(path, length)
    return path


def extent_end(path):
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    return _db_viv_extent(reader)[1]


def test_the_extent_starts_at_the_archives_logical_block(tmp_path):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert _db_viv_extent(reader)[0] == fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE


def test_the_extent_ends_at_the_block_plus_the_declared_length(tmp_path):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    start, end = _db_viv_extent(reader)
    assert end - start == len(reader.get_db_viv())


def test_an_image_with_no_archive_reports_a_zero_extent(tmp_path):
    path = iso(tmp_path, fixture.DiscSpec(db_dir_name="XX"))
    reader = NHL05PS2RomReader(str(path))
    assert _db_viv_extent(reader) == (0, 0)


def test_a_whole_image_satisfies_the_bound(tmp_path):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is True


def test_a_file_of_exactly_the_extents_length_satisfies_the_bound(tmp_path):
    # The boundary, and it is arithmetic: `lba * 2048 + size` bytes is enough.
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    truncate_to(path, extent_end(path))
    assert _db_viv_extent_fits(path, reader) is True


def test_a_file_one_byte_short_of_the_extent_fails_the_bound(tmp_path):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    truncate_to(path, extent_end(path) - 1)
    assert _db_viv_extent_fits(path, reader) is False


def test_a_file_with_no_archive_fails_the_bound(tmp_path):
    path = iso(tmp_path, fixture.DiscSpec(db_dir_name="XX"))
    reader = NHL05PS2RomReader(str(path))
    assert _db_viv_extent_fits(path, reader) is False


def test_an_over_declared_extent_fails_the_bound(tmp_path):
    # The directory record claims a longer archive than the image holds, which
    # is what a bad disc read produces and what nothing below this line detects.
    # The over-declaration has to reach past the padding file as well, or the
    # extent still lands inside the image and the bound is right to accept it.
    natural = len(fixture.build_db_viv())
    spec = fixture.DiscSpec(declared_db_viv_size=natural + ISO_SECTOR_SIZE * 64)
    path = iso(tmp_path, spec)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is False


def test_an_over_declaration_inside_the_image_still_satisfies_the_bound(tmp_path):
    # The other side: a declaration that runs into the gap and the padding file
    # is a lie about the archive, and the bound does not claim to detect it. It
    # checks one thing -- that the declared extent lies inside the file.
    natural = len(fixture.build_db_viv())
    spec = fixture.DiscSpec(declared_db_viv_size=natural + ISO_SECTOR_SIZE)
    path = iso(tmp_path, spec)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    assert _db_viv_extent_fits(path, reader) is True


def test_analyze_reports_a_truncated_archive_as_invalid(tmp_path):
    path = iso(tmp_path)
    truncate_to(path, extent_end(path) - 1)
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_patch_refuses_a_truncated_archive(tmp_path):
    # **The asymmetry, pinned.** An arithmetic bound guards both entry points,
    # because a file failing it provably cannot be patched -- upstream at this
    # exact length reported success with 25 players written.
    path = iso(tmp_path)
    truncate_to(path, extent_end(path) - 1)
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    with pytest.raises(RomError, match="truncated"):
        patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)


def test_the_truncation_message_names_the_declared_extent(tmp_path):
    path = iso(tmp_path)
    end = extent_end(path)
    truncate_to(path, end - 1)
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    with pytest.raises(RomError, match=f"{fixture.DB_VIV_SECTOR * ISO_SECTOR_SIZE}-{end}"):
        patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)


def test_patch_accepts_a_file_of_exactly_the_extents_length(tmp_path):
    # The other side of the boundary, so the constant is not merely "smaller
    # than the image".
    path = iso(tmp_path)
    truncate_to(path, extent_end(path))
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    result = patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)
    assert result.teams_patched == 4


def test_the_heuristic_guards_analyze_only(tmp_path):
    # A disc whose `nhl2005.tdb` is not a TDB: `validate(deep=True)` fails and
    # `analyze` says so. `patch` reaches the same fact through `_parse_tdbs`,
    # whose message names the archive's actual file list, which is strictly more
    # useful -- so the heuristic deliberately does not reach `patch`.
    spec = fixture.DiscSpec(master_payload=b"NOT A TDB AT ALL" * 512)
    path = iso(tmp_path, spec)
    assert build(tmp_path).analyze_rom(path).is_valid is False


def test_patch_on_the_same_file_fails_with_a_message_about_the_archive(tmp_path):
    # The pair: `analyze` said `is_valid=False`, `patch` raises -- and the two
    # reach the answer by different routes. `EaTdbError` is a `RomError`.
    spec = fixture.DiscSpec(master_payload=b"NOT A TDB AT ALL" * 512)
    path = iso(tmp_path, spec)
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)


def test_patch_names_the_archives_files_when_the_master_is_absent(tmp_path):
    spec = fixture.DiscSpec(master_name=None)
    path = iso(tmp_path, spec)
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    with pytest.raises(RomError, match=TDB_ROSTER):
        patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)


def test_patch_refuses_a_file_that_is_not_an_iso_at_all(tmp_path):
    path = tmp_path / "other.iso"
    path.write_bytes(b"\x00" * (ISO_SECTOR_SIZE * 40))
    patcher = build(tmp_path, FakeApi())
    rosters = patcher.map_rosters(league())
    with pytest.raises(RomError, match="Not a valid NHL 2005 PS2 ISO"):
        patcher.patch(rom_path=path, output_path=tmp_path / "o.iso", rosters=rosters)


# -- the record bound ------------------------------------------------------


def a_table(tmp_path, name):
    path = iso(tmp_path)
    reader = NHL05PS2RomReader(str(path))
    reader.load()
    return reader.get_tdb(TDB_MASTER).get_table(name)


def test_the_live_range_is_the_declared_record_count(tmp_path):
    table = a_table(tmp_path, "ROST")
    assert _live_records(table) == range(fixture.PLAYER_COUNT)


def test_the_live_range_is_bounded_by_the_allocation(tmp_path):
    # A header overstating its own live count is what `formats/ea_tdb.py`
    # deliberately does not clamp, and this is the consumer's policy for it.
    table = a_table(tmp_path, "ROST")
    table.num_records = table.capacity + 500
    assert _live_records(table) == range(table.capacity)


def test_the_live_range_follows_an_understated_count(tmp_path):
    # The other argument of the `min`, so a bound that always answered
    # `capacity` fails here.
    table = a_table(tmp_path, "ROST")
    table.num_records = 7
    assert _live_records(table) == range(7)


def test_an_index_map_keys_records_by_their_own_index_field(tmp_path):
    table = a_table(tmp_path, "SPBT")
    mapping = _index_map(table)
    assert mapping[fixture.player_id_for(1, 3)] == fixture.spbt_position(1, 3)


def test_an_index_map_drops_a_zero_index(tmp_path):
    # Zero is what an unused row holds; mapping it would make every unused row
    # look like the same player.
    table = a_table(tmp_path, "SPBT")
    table.write_record(0, {"INDX": 0})
    assert 0 not in _index_map(table)


def test_an_index_map_lets_a_later_record_win_a_tie(tmp_path):
    table = a_table(tmp_path, "SPBT")
    table.write_record(0, {"INDX": 4242})
    table.write_record(9, {"INDX": 4242})
    assert _index_map(table)[4242] == 9


def test_the_play_map_reaches_a_player_id_from_a_roster_index(tmp_path):
    table = a_table(tmp_path, "PLAY")
    mapping = _play_id_by_indx(table)
    assert mapping[fixture.rost_indx_for(2, 4)] == fixture.player_id_for(2, 4)


def test_the_play_map_keeps_a_zero_index(tmp_path):
    # Unlike `_index_map`, and the source did the same: a PLAY row is looked up
    # by a ROST row's `INDX`, and if both are zero the source paired them.
    table = a_table(tmp_path, "PLAY")
    table.write_record(0, {"INDX": 0, "ID__": 77})
    assert _play_id_by_indx(table)[0] == 77


def test_the_play_map_is_not_the_identity(tmp_path):
    # If it were, every test that walks the chain would pass against a patcher
    # that used a record's position for its index.
    table = a_table(tmp_path, "PLAY")
    mapping = _play_id_by_indx(table)
    assert [k for k, v in mapping.items() if k == v] == []


# -- fetch -----------------------------------------------------------------


def test_fetching_returns_one_roster_per_matched_team(tmp_path):
    data = build(tmp_path, FakeApi()).fetch(season=2025)
    assert len(data.teams) == 4


def test_fetching_reports_the_season_it_was_given(tmp_path):
    assert build(tmp_path, FakeApi()).fetch(season=2019).league.season == 2019


def test_fetching_counts_the_rosters_it_built(tmp_path):
    # Not `len(teams)`: a provider team with no slot is dropped before this.
    api = FakeApi(teams=[*default_teams(), Team(id=9, name="Nowhere", code="ZZZ", logo_url="")])
    assert build(tmp_path, api).fetch(season=2025).league.teams_count == 4


def test_fetching_drops_a_provider_team_with_no_rom_slot(tmp_path):
    api = FakeApi(teams=[*default_teams(), Team(id=9, name="Nowhere", code="ZZZ", logo_url="")])
    data = build(tmp_path, api).fetch(season=2025)
    assert [t.team.code for t in data.teams] == ["ANA", "ATL", "BOS", "BUF"]


def test_fetching_never_asks_for_a_team_it_would_drop(tmp_path):
    api = FakeApi(teams=[*default_teams(), Team(id=9, name="Nowhere", code="ZZZ", logo_url="")])
    build(tmp_path, api).fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == [1, 2, 3, 4]


def test_fetching_nothing_at_all_raises(tmp_path):
    with pytest.raises(ApiError, match="no NHL teams"):
        build(tmp_path, FakeApi(teams=[])).fetch(season=2025)


def test_fetching_only_unmapped_teams_raises(tmp_path):
    api = FakeApi(teams=[Team(id=9, name="Nowhere", code="ZZZ", logo_url="")])
    with pytest.raises(ApiError, match="ROM slot"):
        build(tmp_path, api).fetch(season=2025)


def test_the_espn_squad_call_carries_the_season(tmp_path):
    # DELIBERATE DIVERGENCE: the source omitted it. The endpoint has no season
    # in its URL but does have one in its cache key, so without it the first
    # season ever fetched was served forever.
    api = FakeApi()
    build(tmp_path, api).fetch(season=2021)
    assert api.squad_calls == [(1, 2021), (2, 2021), (3, 2021), (4, 2021)]


def test_the_espn_leaders_call_carries_the_season(tmp_path):
    # A different failure from the same omission: the season is a URL path
    # segment here, so its default meant a `--season 2024` run asked ESPN for
    # another year's statistics and stapled them to the squad.
    api = FakeApi()
    build(tmp_path, api).fetch(season=2021)
    assert api.leader_calls == [(1, 2021), (2, 2021), (3, 2021), (4, 2021)]


def test_the_espn_branch_keys_by_team_id(tmp_path):
    api = FakeApi()
    build(tmp_path, api).fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == [1, 2, 3, 4]


def test_the_nhl_branch_keys_by_team_code(tmp_path):
    api = FakeApi()
    build(tmp_path, api, provider="nhl").fetch(season=2025)
    assert [key for key, _ in api.squad_calls] == ["ANA", "ATL", "BOS", "BUF"]


def test_leaders_travel_in_the_roster_rather_than_on_the_patcher(tmp_path):
    # DELIBERATE DIVERGENCE: the source left these on `self.team_stats`, read
    # later through `getattr(self, "team_stats", {})`, so calling `fetch` and
    # `map_rosters` out of order silently downgraded every player to defaults.
    # Keyed by team *id*, because the ESPN branch calls both endpoints with it.
    api = FakeApi(leaders={1: {"1000": {"PTS": 90}}})
    data = build(tmp_path, api).fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"1000": {"PTS": 90}}


def test_the_patcher_keeps_no_stats_side_channel(tmp_path):
    patcher = build(tmp_path, FakeApi())
    patcher.fetch(season=2025)
    assert hasattr(patcher, "team_stats") is False


def test_fetching_reports_progress_to_completion(tmp_path):
    seen: list[float] = []
    build(tmp_path, FakeApi()).fetch(season=2025, on_progress=lambda f, _m: seen.append(f))
    assert seen[-1] == 1.0


def test_fetching_reports_progress_once_per_team_plus_the_end(tmp_path):
    seen: list[float] = []
    build(tmp_path, FakeApi()).fetch(season=2025, on_progress=lambda f, _m: seen.append(f))
    assert len(seen) == 5


# -- map_rosters -----------------------------------------------------------


def test_mapping_keys_by_rom_slot(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    assert sorted(mapped.teams) == DISC_SLOTS


def test_mapping_stamps_the_game_id(tmp_path):
    assert build(tmp_path).map_rosters(league()).game_id == "nhl05-ps2"


def test_mapping_selects_at_most_twenty_five_players(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    assert len(mapped.teams[0]) == 22


def test_mapping_produces_this_games_record_type(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    assert {type(r) for r in mapped.teams[0]} == {NHL05PlayerRecord}


def test_mapping_refuses_a_slot_mapping(tmp_path):
    from retro_roster_patcher.core.errors import CapabilityError
    from retro_roster_patcher.core.models import SlotMapping

    with pytest.raises(CapabilityError):
        build(tmp_path).map_rosters(
            league(), [SlotMapping(slot_index=0, team_id=1, team_name="ANA")]
        )


def test_mapping_drops_a_team_with_no_slot(tmp_path):
    teams = [*default_teams(), Team(id=9, name="Nowhere", code="ZZZ", logo_url="")]
    mapped = build(tmp_path).map_rosters(league(teams=teams))
    assert sorted(mapped.teams) == DISC_SLOTS


def test_seattle_maps_to_a_slot_and_is_then_dropped(tmp_path):
    # `MODERN_NHL_TO_NHL05["SEA"]` is 30, an All-Star side, and this game does
    # not patch those. So the entry is dead: the team is fetched, mapped, and
    # discarded here. NHL 07 patches it.
    teams = [Team(id=9, name="Seattle Kraken", code="SEA", logo_url="")]
    mapped = build(tmp_path).map_rosters(league(teams=teams))
    assert mapped.teams == {}


def test_seattle_does_have_a_slot_in_the_table(tmp_path):
    # The other half of the previous claim, so it cannot be read as "SEA is
    # unknown to this game".
    assert build(tmp_path).mapper.get_team_slot("SEA") == 30


def test_vegas_is_dropped_for_the_same_reason(tmp_path):
    teams = [Team(id=9, name="Vegas Golden Knights", code="VGK", logo_url="")]
    mapped = build(tmp_path).map_rosters(league(teams=teams))
    assert mapped.teams == {}


def test_a_populated_alias_survives_an_empty_one_arriving_second(tmp_path):
    # DELIBERATE DIVERGENCE. Without the guard the empty `SJS` overwrites the
    # populated `SJ`, `patch` skips slot 24, and the run reports success with
    # `teams_patched` short by one and the 2004 roster still on the disc.
    # Measured on the source: 0 records against this port's 25.
    teams = [
        Team(id=1, name="San Jose Sharks", code="SJ", logo_url=""),
        Team(id=2, name="San Jose Sharks", code="SJS", logo_url=""),
    ]
    data = league(teams=teams, squads={"SJS": []})
    mapped = build(tmp_path).map_rosters(data)
    assert len(mapped.teams[24]) == 22


def test_the_two_aliases_do_name_one_slot(tmp_path):
    # Guards the previous test from passing because the two codes never met.
    patcher = build(tmp_path)
    assert patcher.mapper.get_team_slot("SJ") == patcher.mapper.get_team_slot("SJS")


def test_a_populated_alias_arriving_second_replaces_an_empty_one(tmp_path):
    # The guard is one-sided on purpose: emptiness is what it refuses to let
    # win, not lateness.
    teams = [
        Team(id=1, name="San Jose Sharks", code="SJ", logo_url=""),
        Team(id=2, name="San Jose Sharks", code="SJS", logo_url=""),
    ]
    data = league(teams=teams, squads={"SJ": []})
    mapped = build(tmp_path).map_rosters(data)
    assert len(mapped.teams[24]) == 22


def test_an_empty_roster_that_collides_with_nothing_still_takes_its_slot(tmp_path):
    # The mapped result keeps showing which slots a provider team matched;
    # `patch` is what keeps the empty list away from the writer.
    data = league(squads={"ANA": []})
    mapped = build(tmp_path).map_rosters(data)
    assert mapped.teams[0] == []


def test_mapping_reads_the_leaders_out_of_the_roster(tmp_path):
    # A 90-point season saturates the offensive scale; without the leaders the
    # player takes the centre's default of 35 for `deking`.
    top = squad(1000)
    leaders = {"ANA": {str(top[2].id): {"PTS": 90}}}
    data = league(squads={"ANA": top}, leaders=leaders)
    mapped = build(tmp_path).map_rosters(data)
    best = next(r for r in mapped.teams[0] if r.position == "C")
    assert best.skater_attrs.deking == 63


def test_without_leaders_the_same_player_takes_the_position_default(tmp_path):
    mapped = build(tmp_path).map_rosters(league())
    best = next(r for r in mapped.teams[0] if r.position == "C")
    assert best.skater_attrs.deking == 35


# -- patch: the record chain -----------------------------------------------


def test_patching_reports_the_output_path(tmp_path):
    result, out = patched(tmp_path)
    assert result.output_path == str(out)


def test_patching_returns_a_patch_result(tmp_path):
    result, _ = patched(tmp_path)
    assert type(result) is PatchResult


def test_patching_counts_the_four_teams_the_disc_carries(tmp_path):
    result, _ = patched(tmp_path)
    assert result.teams_patched == 4


def test_patching_counts_every_player_it_placed(tmp_path):
    # Twenty-five rows a team, of which two are goalie rows. The mapped roster
    # has 22 players, 2 of them goalies, so all 22 are placed on each of four
    # teams.
    result, _ = patched(tmp_path)
    assert result.players_patched == 88


def test_patching_leaves_the_source_image_untouched(tmp_path):
    source = iso(tmp_path)
    before = source.read_bytes()
    patcher = build(tmp_path, FakeApi())
    patcher.patch(
        rom_path=source, output_path=tmp_path / "o.iso", rosters=patcher.map_rosters(league())
    )
    assert source.read_bytes() == before


def test_patching_changes_the_output(tmp_path):
    # The floor under every "the patcher wrote X" assertion below.
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    out = tmp_path / "o.iso"
    patcher.patch(rom_path=source, output_path=out, rosters=patcher.map_rosters(league()))
    assert out.read_bytes() != source.read_bytes()


def test_a_players_name_reaches_the_bio_the_chain_points_at(tmp_path):
    # The four-hop chain, end to end. Roster row (0, 2) is the first skater row
    # of the first team; its bio lives at a position given by a stride-7
    # permutation, so a patcher that used the row's own position misses.
    _, out = patched(tmp_path)
    record = spbt_of(out)[fixture.spbt_position(0, 2)]
    assert record["FNME"].startswith("Given") is True


def test_the_bio_the_chain_points_at_is_not_the_one_at_the_rows_position(tmp_path):
    # Guards the previous test: if the two positions coincided it would pass
    # against a patcher that ignored the chain.
    assert fixture.spbt_position(0, 2) != fixture.rost_position(0, 2)


def test_a_bio_belonging_to_an_unpatched_team_is_untouched(tmp_path):
    # Only Anaheim is fetched, so every other team's bios must survive whole.
    _, out = patched(tmp_path, data=league(teams=default_teams()[:1]))
    assert spbt_of(out)[fixture.spbt_position(3, 4)] == fixture.disc_bio_values(3, 4)


def test_a_row_a_short_roster_never_reached_keeps_its_bio(tmp_path):
    # Twenty-two players into twenty-five rows, so rows 22-24 of a patched team
    # are undressed and their bios are left alone. A patcher that wrote by row
    # position rather than by the chain would have overwritten one of them.
    _, out = patched(tmp_path)
    assert spbt_of(out)[fixture.spbt_position(0, 24)] == fixture.disc_bio_values(0, 24)


def test_a_goalies_ratings_reach_the_goalie_table(tmp_path):
    # Roster row 0 of team 0 is a goalie row, and a goalie's attributes must go
    # to SGAI because that is the table with a row for him.
    _, out = patched(tmp_path)
    player_id = fixture.player_id_for(0, 0)
    record = sgai_of(out)[fixture.sgai_position(player_id)]
    assert record["INDX"] == player_id


def test_a_goalies_ratings_are_not_the_ones_the_disc_shipped(tmp_path):
    player_id = fixture.player_id_for(0, 0)
    before = (player_id * 11 + 0 * 5) % 64
    _, out = patched(tmp_path)
    assert sgai_of(out)[fixture.sgai_position(player_id)]["BRKA"] != before


def test_a_skaters_ratings_reach_the_skater_table(tmp_path):
    _, out = patched(tmp_path)
    player_id = fixture.player_id_for(0, 2)
    record = spai_of(out)[fixture.spai_position(player_id)]
    assert record["INDX"] == player_id


def test_a_skaters_ratings_are_not_the_ones_the_disc_shipped(tmp_path):
    player_id = fixture.player_id_for(0, 2)
    before = (player_id * 11 + 0 * 5) % 64
    _, out = patched(tmp_path)
    assert spai_of(out)[fixture.spai_position(player_id)]["BALA"] != before


def test_the_first_paired_player_wears_the_c(tmp_path):
    # Goalies are paired first, so the starting goalie is captain. Unusual on a
    # real team, and what the source did.
    _, out = patched(tmp_path)
    assert rost_of(out)[fixture.rost_position(0, 0)]["CAPT"] == 2


def test_the_next_two_paired_players_wear_an_a(tmp_path):
    _, out = patched(tmp_path)
    rows = rost_of(out)
    assert [rows[fixture.rost_position(0, r)]["CAPT"] for r in (1, 2)] == [1, 1]


def test_a_later_player_wears_neither(tmp_path):
    _, out = patched(tmp_path)
    assert rost_of(out)[fixture.rost_position(0, 5)]["CAPT"] == 0


def test_every_patched_row_is_dressed(tmp_path):
    _, out = patched(tmp_path)
    rows = rost_of(out)
    dressed = [rows[fixture.rost_position(0, r)]["DRES"] for r in range(22)]
    assert dressed == [1] * 22


def test_the_rows_a_short_roster_did_not_reach_are_undressed(tmp_path):
    # 22 players into 25 rows. The last three rows keep their 2004 occupants and
    # must not take the ice beside a 2025 one.
    _, out = patched(tmp_path)
    rows = rost_of(out)
    undressed = [rows[fixture.rost_position(0, r)]["DRES"] for r in (22, 23, 24)]
    assert undressed == [0, 0, 0]


def test_an_undressed_row_keeps_the_team_it_belonged_to(tmp_path):
    _, out = patched(tmp_path)
    assert rost_of(out)[fixture.rost_position(0, 24)]["TEAM"] == 0


def test_a_patched_row_keeps_its_own_index(tmp_path):
    # Rewriting `INDX` would break the ROST -> PLAY -> SPBT chain that located
    # the row in the first place.
    _, out = patched(tmp_path)
    row = rost_of(out)[fixture.rost_position(0, 3)]
    assert row["INDX"] == fixture.rost_indx_for(0, 3)


def test_a_patched_row_keeps_its_team(tmp_path):
    _, out = patched(tmp_path)
    assert rost_of(out)[fixture.rost_position(0, 3)]["TEAM"] == 0


def test_a_patched_row_has_only_the_flags_the_mapper_gave_it(tmp_path):
    # Every row of the fixture ships with one flag already set, so a patcher
    # that failed to clear the sixty-four would leave it behind.
    _, out = patched(tmp_path)
    row = rost_of(out)[fixture.rost_position(0, 0)]
    assert [f for f in fixture.LINE_FLAG_NAMES if row[f] == 1] == ["G1__"]


def test_a_skater_row_carries_its_line_and_its_special_teams_unit(tmp_path):
    # The starting goalie above carries exactly one flag, which on its own does
    # not show that two can coexist. Row 2 is the first skater row: the first
    # centre, so first line and first power-play unit.
    _, out = patched(tmp_path)
    row = rost_of(out)[fixture.rost_position(0, 2)]
    assert [f for f in fixture.LINE_FLAG_NAMES if row[f] == 1] == ["L1C_", "H1__"]


def test_the_third_defence_pair_is_never_assigned(tmp_path):
    # THE INHERITED DEFECT, at the layer where it becomes visible on the disc.
    # The mapper emits `33LD`/`33RD` for the fifth and sixth defenceman;
    # neither is in this game's ROST, so no row ends up carrying a third pair.
    # `L3LD`/`L3RD` exist and stay zero.
    _, out = patched(tmp_path)
    rows = rost_of(out)
    third = [
        r
        for r in range(fixture.ROWS_PER_TEAM)
        if rows[fixture.rost_position(0, r)]["L3LD"] == 1
        or rows[fixture.rost_position(0, r)]["L3RD"] == 1
    ]
    assert third == []


def test_the_first_two_defence_pairs_are_assigned(tmp_path):
    # The other half: four of the mapper's six defence flags do land, so the
    # previous test is about the third pair and not about defencemen at large.
    _, out = patched(tmp_path)
    rows = rost_of(out)
    assigned = sorted(
        f
        for r in range(fixture.ROWS_PER_TEAM)
        for f in ("31LD", "31RD", "32LD", "32RD")
        if rows[fixture.rost_position(0, r)][f] == 1
    )
    assert assigned == ["31LD", "31RD", "32LD", "32RD"]


def test_the_unreachable_flags_are_all_zero(tmp_path):
    # `X1__` and `X2__` have no counterpart in the mapper at all, so a patched
    # row carries them cleared -- which is a write, not an omission.
    _, out = patched(tmp_path)
    rows = rost_of(out)
    set_flags = [
        f
        for r in range(fixture.ROWS_PER_TEAM)
        for f in fixture.UNREACHABLE_FLAGS
        if rows[fixture.rost_position(0, r)][f] == 1
    ]
    assert set_flags == []


# -- patch: the single ROST mirror -----------------------------------------


def test_the_roster_mirror_receives_the_same_row(tmp_path):
    _, out = patched(tmp_path)
    position = fixture.rost_position(0, 3)
    assert rost_of(out, TDB_ROSTER)[position] == rost_of(out)[position]


def test_the_roster_mirror_receives_the_undressed_rows_too(tmp_path):
    _, out = patched(tmp_path)
    assert rost_of(out, TDB_ROSTER)[fixture.rost_position(0, 24)]["DRES"] == 0


def test_a_disc_without_the_roster_mirror_still_patches(tmp_path):
    result, _ = patched(tmp_path, spec=fixture.DiscSpec(roster_name=None))
    assert result.teams_patched == 4


def test_the_master_is_still_written_without_the_mirror(tmp_path):
    _, out = patched(tmp_path, spec=fixture.DiscSpec(roster_name=None))
    assert rost_of(out)[fixture.rost_position(0, 0)]["CAPT"] == 2


def test_this_game_has_no_bio_mirror_to_write(tmp_path):
    # NHL 07's `nhlbioatt.tdb`. A port that copied its `_MirrorTables` would
    # carry a field that is always None and two writes that never run.
    _, out = patched(tmp_path)
    from retro_roster_patcher.formats.ea_tdb import bigf_parse

    viv = fixture.iso_read_file(out.read_bytes(), fixture.DB_VIV_ISO_PATH)
    assert [e.name for e in bigf_parse(viv)] == [TDB_MASTER, TDB_ROSTER]


# -- patch: counting -------------------------------------------------------


def test_a_slot_with_no_rows_on_the_disc_is_not_counted(tmp_path):
    # DELIBERATE DIVERGENCE: the source incremented `teams_patched` for every
    # slot it looked at. `CAR` is slot 5 and the fixture disc has no rows for
    # it, so nothing reached the ROM for that team.
    teams = [
        Team(id=1, name="Anaheim", code="ANA", logo_url=""),
        Team(id=5, name="Carolina", code="CAR", logo_url=""),
    ]
    result, _ = patched(tmp_path, data=league(teams=teams))
    assert result.teams_patched == 1


def test_a_slot_with_no_rows_contributes_no_players(tmp_path):
    teams = [
        Team(id=1, name="Anaheim", code="ANA", logo_url=""),
        Team(id=5, name="Carolina", code="CAR", logo_url=""),
    ]
    result, _ = patched(tmp_path, data=league(teams=teams))
    assert result.players_patched == 22


def test_a_slot_with_an_empty_roster_is_skipped(tmp_path):
    result, _ = patched(tmp_path, data=league(squads={"ANA": []}))
    assert result.teams_patched == 3


def test_an_out_of_range_slot_arriving_through_json_is_ignored(tmp_path):
    # The keys of `MappedRosters.teams` may have crossed a JSON boundary since
    # `map_rosters` bounded them.
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    mapped = patcher.map_rosters(league())
    mapped.teams[99] = squad(9000) and [NHL05PlayerRecord(first_name="Ghost")]
    result = patcher.patch(rom_path=source, output_path=tmp_path / "o.iso", rosters=mapped)
    assert result.teams_patched == 4


def test_an_all_star_slot_arriving_through_json_is_ignored(tmp_path):
    # 30 and 31 are inside `NHL05_TEAM_NAMES` and outside `PATCHABLE_SLOT_COUNT`,
    # which is the pair of bounds a copied NHL 07 patcher would collapse.
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    mapped = patcher.map_rosters(league())
    mapped.teams[30] = [NHL05PlayerRecord(first_name="Ghost")]
    result = patcher.patch(rom_path=source, output_path=tmp_path / "o.iso", rosters=mapped)
    assert result.teams_patched == 4


def test_patching_with_another_games_rosters_raises(tmp_path):
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    foreign = MappedRosters(game_id="nhl07-psp", teams={0: []})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=source, output_path=tmp_path / "o.iso", rosters=foreign)


def test_the_game_check_happens_before_any_file_is_touched(tmp_path):
    patcher = build(tmp_path, FakeApi())
    foreign = MappedRosters(game_id="nhl07-psp", teams={0: []})
    out = tmp_path / "o.iso"
    with pytest.raises(MappingError):
        patcher.patch(rom_path=tmp_path / "absent.iso", output_path=out, rosters=foreign)
    assert out.exists() is False


# -- patch: progress and status --------------------------------------------


def test_patching_reports_progress_to_completion(tmp_path):
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda f, _m: seen.append(f))
    assert seen[-1] == 1.0


def test_patching_reports_progress_monotonically(tmp_path):
    # IMPROVEMENT: the source's spans ran forwards to 60%, jumped back to 30%,
    # and finished at 70% reporting "Complete".
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda f, _m: seen.append(f))
    assert seen == sorted(seen)


def test_patching_reports_more_than_a_handful_of_progress_steps(tmp_path):
    # Guards the monotonicity test against a one-element list.
    seen: list[float] = []
    patched(tmp_path, on_progress=lambda f, _m: seen.append(f))
    assert len(seen) > 8


def test_patching_names_each_team_as_it_writes_it(tmp_path):
    seen: list[str] = []
    patched(tmp_path, on_progress=lambda _f, m: seen.append(m))
    assert f"Writing {NHL05_TEAM_NAMES[0]} (22 players)..." in seen


def test_patching_reports_status_messages(tmp_path):
    seen: list[str] = []
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi(), on_status=seen.append)
    patcher.patch(
        rom_path=source, output_path=tmp_path / "o.iso", rosters=patcher.map_rosters(league())
    )
    assert seen[0] == "Validating ROM..."


def test_the_last_status_message_is_the_rebuild(tmp_path):
    seen: list[str] = []
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi(), on_status=seen.append)
    patcher.patch(
        rom_path=source, output_path=tmp_path / "o.iso", rosters=patcher.map_rosters(league())
    )
    assert seen[-1] == "Rebuilding DB.VIV..."


# -- patch: the archive's own spelling -------------------------------------


def test_patching_an_archive_spelled_in_capitals_works(tmp_path):
    spec = fixture.DiscSpec(master_name="NHL2005.TDB", roster_name="NHLROST.TDB")
    result, _ = patched(tmp_path, spec=spec)
    assert result.teams_patched == 4


def test_the_capitalised_archive_receives_the_writes(tmp_path):
    spec = fixture.DiscSpec(master_name="NHL2005.TDB", roster_name="NHLROST.TDB")
    _, out = patched(tmp_path, spec=spec)
    assert rost_of(out, "NHL2005.TDB")[fixture.rost_position(0, 0)]["CAPT"] == 2


def test_patching_a_sparse_multi_megabyte_image_works(tmp_path):
    # The copy path over something larger than one 4 MB chunk. `truncate` makes
    # it a hole rather than real storage.
    result, out = patched(tmp_path, spec=fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    assert out.stat().st_size == 9 * 1024 * 1024


def test_a_padded_image_still_patches_every_team(tmp_path):
    result, _ = patched(tmp_path, spec=fixture.DiscSpec(pad_to=9 * 1024 * 1024))
    assert result.teams_patched == 4


def test_an_all_star_slot_is_not_even_named_in_the_progress_messages(tmp_path):
    # The `_write_all_teams` bound is `PATCHABLE_SLOT_COUNT`, not the 32 slots
    # the game has names for, and the two are only distinguishable here: the
    # fixture disc has no ROST rows for slot 30, so a patcher bounded by 32
    # would report the same `teams_patched` while announcing "Writing East
    # All-Star" to the user.
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    mapped = patcher.map_rosters(league())
    mapped.teams[30] = [NHL05PlayerRecord(first_name="Ghost")]
    seen: list[str] = []
    patcher.patch(
        rom_path=source,
        output_path=tmp_path / "o.iso",
        rosters=mapped,
        on_progress=lambda _f, m: seen.append(m),
    )
    assert [m for m in seen if "All-Star" in m] == []


def test_the_four_real_teams_are_named_in_the_progress_messages(tmp_path):
    # So the previous test cannot pass because no team was announced at all.
    source = iso(tmp_path)
    patcher = build(tmp_path, FakeApi())
    mapped = patcher.map_rosters(league())
    mapped.teams[30] = [NHL05PlayerRecord(first_name="Ghost")]
    seen: list[str] = []
    patcher.patch(
        rom_path=source,
        output_path=tmp_path / "o.iso",
        rosters=mapped,
        on_progress=lambda _f, m: seen.append(m),
    )
    named = [NHL05_TEAM_NAMES[s] for s in DISC_SLOTS if any(NHL05_TEAM_NAMES[s] in m for m in seen)]
    assert named == [NHL05_TEAM_NAMES[s] for s in DISC_SLOTS]


def test_the_all_star_slot_does_have_a_display_name(tmp_path):
    # The other half of the bound: slot 30 is inside `NAMED_SLOT_COUNT`, so a
    # patcher that used that bound would find a name to print rather than
    # raising -- which is why the failure is silent and needs pinning.
    assert NHL05_TEAM_NAMES[30] == "East All-Star"
