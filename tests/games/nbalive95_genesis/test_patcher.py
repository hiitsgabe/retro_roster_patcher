"""The NBA Live 95 patcher against the unified interface.

The reader, writer and stat mapper below it are a faithful port of an untested
upstream; this layer is where its contract violations are absorbed and where the
migration's own decisions live. Five things here are not in the ported code at
all and are the reason this file is the longest of the four:

  * `_looks_like_nbalive95`, without which this patcher claims most of a Genesis
    library -- the ported `validate` looks at team 0 alone, behind a title test
    that passes unconditionally on any header not mentioning the NBA;
  * `_pointer_tables_fit`, without which a file 491 740 bytes too short is
    patched for eighteen of its thirty teams and reported as a success;
  * the alias guard, without which an empty `GSW` wipes a populated `GS`;
  * `season` threaded into the squad call, which upstream omitted;
  * `RomError` against `RomInfo(is_valid=False)`, which upstream conflated.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `NBALive95RomWriter.__init__` builds its own reader over the *input* file,
so `writer.reader.data` is the pre-write image for the writer's whole lifetime
and asserting against it would assert nothing.
"""

import struct
import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomSlot, SlotMapping
from retro_roster_patcher.games.nbalive95_genesis.models import (
    NBA_TEAM_COUNT,
    NBALIVE95_TEAM_ORDER,
    TEAM_COUNT,
    NBALive95PlayerRecord,
    NBALive95TeamRecord,
)
from retro_roster_patcher.games.nbalive95_genesis.patcher import (
    _LAST_POINTER_END,
    NBALive95Patcher,
    _looks_like_nbalive95,
    _pointer_tables_fit,
)
from retro_roster_patcher.games.nbalive95_genesis.rom_reader import (
    ROM_SIZE_MIN,
    NBALive95RomReader,
)
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.fixtures import synthetic_nbalive95_rom as fixture
from tests.fixtures import synthetic_rom as genesis_fixture

# Slots from `MODERN_NBA_TO_NBALIVE95`. Boston and Chicago are the two the fake
# API covers; Golden State is one of the seven that two codes reach.
BOS_SLOT = 1
CHI_SLOT = 3
GS_SLOT = 8


class FakeApi:
    """Stands in for `EspnClient`.

    Records what it was asked for, because `season` reaches the two endpoints
    differently -- a cache key on the squad call, a path segment on the leaders
    call -- and upstream passed it to only one of them.
    """

    def __init__(self, teams, squad_size=14, leaders=None):
        self._teams = teams
        self._squad_size = squad_size
        self._leaders = {"0": {"PTS": 30.0}} if leaders is None else leaders
        self.squad_calls = []
        self.leader_calls = []

    def get_nba_teams(self):
        return list(self._teams)

    def get_basketball_squad(self, team_id, season=None):
        self.squad_calls.append((team_id, season))
        return [
            Player(id=index, name=f"First{index} Last{index}", position="SF", number=index + 1)
            for index in range(self._squad_size)
        ]

    def get_basketball_team_leaders(self, team_id, season=2026):
        self.leader_calls.append((team_id, season))
        return dict(self._leaders)


def _teams():
    return [
        Team(id=1, name="Boston Celtics", code="BOS"),
        Team(id=2, name="Chicago Bulls", code="CHI"),
        Team(id=3, name="Toronto Raptors", code="TOR"),
        Team(id=4, name="Not A Real Team", code="ZZZ"),
    ]


@pytest.fixture
def patcher(tmp_path):
    """A patcher wired to a fake API covering two real NBA Live 95 slots."""
    p = NBALive95Patcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams())
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_nbalive95_rom(tmp_path / "nbalive95.bin")


@pytest.fixture
def out(tmp_path):
    return tmp_path / "patched.bin"


def _league_data(*entries, season=2025):
    """`LeagueData` with one `TeamRoster` per `(code, squad size)`, in order.

    The order is what decides which of two colliding aliases lands in the shared
    slot, so it is a parameter rather than an accident of a dict.
    """
    return LeagueData(
        league=League(id=0, name="NBA", country="USA", country_code="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=index + 1, name=code, code=code),
                players=[
                    Player(
                        id=index * 100 + n,
                        name=f"{code}First{n:02d} {code}Last{n:02d}",
                        position="SF",
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
        NBALive95PlayerRecord(name_last=f"Written{index:02d}", name_first="A", jersey=index + 1)
        for index in range(count)
    ]


def _mapped(*pairs):
    return MappedRosters(
        game_id="nbalive95-genesis",
        teams={
            slot: NBALive95TeamRecord(
                index=slot, name=NBALIVE95_TEAM_ORDER[slot], players=_records(count)
            )
            for slot, count in pairs
        },
    )


def _read_back(path, slot):
    reader = NBALive95RomReader(str(path))
    assert reader.load() is True
    return reader.read_team_roster(slot)


def _corrupt(rom, offset, payload):
    data = bytearray(rom.read_bytes())
    data[offset : offset + len(payload)] = payload
    rom.write_bytes(bytes(data))
    return rom


# -- registration ------------------------------------------------------------


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("nbalive95-genesis")
    assert cls is NBALive95Patcher
    assert cls.platform == "genesis"
    assert cls.sport == "basketball"
    assert cls.requires_slot_mapping is False
    assert cls.providers == ("espn",)


def test_this_is_the_librarys_first_basketball_game():
    """The registration is what makes ESPN's three NBA methods reachable."""
    from retro_roster_patcher import list_patchers

    basketball = [info.game_id for info in list_patchers() if info.sport == "basketball"]
    assert basketball == ["nbalive95-genesis"]


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # Registration is a side-effect import at the bottom of the package
    # `__init__`. Dropping it leaves `get_patcher` green for anyone who imported
    # the game module first -- which every other test in this file does -- and
    # broken for the CLI, which only imports the root. Hence the subprocess.
    source = textwrap.dedent(
        """
        import retro_roster_patcher

        cls = retro_roster_patcher.get_patcher("nbalive95-genesis")
        print(cls.__module__ + ":" + cls.__name__)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == (
        "retro_roster_patcher.games.nbalive95_genesis.patcher:NBALive95Patcher"
    )


def test_the_two_genesis_patchers_are_distinct_registrations():
    from retro_roster_patcher import get_patcher

    assert get_patcher("nbalive95-genesis") is not get_patcher("nhl94-genesis")
    assert get_patcher("nbalive95-genesis").sport != get_patcher("nhl94-genesis").sport


# -- construction ------------------------------------------------------------


def test_the_only_provider_is_espn(tmp_path):
    p = NBALive95Patcher(cache_dir=tmp_path)
    assert p.provider == "espn"
    assert type(p.api) is EspnClient


def test_naming_espn_explicitly_is_accepted(tmp_path):
    assert NBALive95Patcher(cache_dir=tmp_path, provider="espn").provider == "espn"


def test_an_unsupported_provider_is_refused(tmp_path):
    with pytest.raises(CapabilityError):
        NBALive95Patcher(cache_dir=tmp_path, provider="nhl")


def test_an_api_key_argument_is_refused(tmp_path):
    """There is no credential to supply, so accepting one would be a lie."""
    with pytest.raises(TypeError):
        NBALive95Patcher(cache_dir=tmp_path, api_key="secret")


def test_a_string_cache_dir_is_normalised_to_a_path(tmp_path):
    assert NBALive95Patcher(cache_dir=str(tmp_path)).cache_dir == tmp_path


def test_the_transport_reaches_the_client(tmp_path):
    """Construction must reach no network, and this is the seam that proves it."""

    def transport(url, headers=None, timeout=None):
        raise AssertionError("no request should be made")

    p = NBALive95Patcher(cache_dir=tmp_path, transport=transport)
    assert p.api._transport is transport


# -- analyze -----------------------------------------------------------------


def test_a_synthetic_rom_is_recognised(patcher, rom):
    info = patcher.analyze_rom(rom)
    assert info.is_valid is True
    assert info.game_id == "nbalive95-genesis"
    assert info.size == fixture.ROM_SIZE
    assert len(info.slots) == TEAM_COUNT


def test_the_slot_display_names_come_from_the_games_own_team_order(patcher, rom):
    info = patcher.analyze_rom(rom)
    assert [slot.display_name for slot in info.slots] == NBALIVE95_TEAM_ORDER


def test_every_slot_display_name_is_distinct(patcher, rom):
    """`RomSlot.display_name` is what a slot-picking UI lists, and a repeated
    value leaves the user unable to tell two rows apart."""
    info = patcher.analyze_rom(rom)
    assert len({slot.display_name for slot in info.slots}) == TEAM_COUNT


def test_the_current_name_labels_the_first_player_it_carries(patcher, rom):
    """The migration's `RomSlot` decision, asserted.

    The game's own slot record is `(index, name, first_player)` and this reader
    never parses a team name, so `current_name` holds the roster's first player.
    It is labelled because a UI renders the field as the team's own name.
    """
    info = patcher.analyze_rom(rom)
    expected = f"First player: {fixture.player_first_name(0, 0)} {fixture.player_last_name(0, 0)}"
    assert info.slots[0].current_name == expected


def test_every_current_name_is_derived_from_the_image_and_not_from_the_table(patcher, rom):
    info = patcher.analyze_rom(rom)
    found = [slot.current_name for slot in info.slots]
    expected = [
        f"First player: {fixture.player_first_name(index, 0)} {fixture.player_last_name(index, 0)}"
        for index in range(TEAM_COUNT)
    ]
    assert found == expected


def test_a_slot_whose_first_record_is_unreadable_reports_an_empty_current_name(patcher, rom):
    """Rather than the label with nothing after it."""
    _corrupt(rom, fixture.TEAM_ROSTER_ADDRESSES[4], b"\x00\x00\x00\x00")
    # Zeroing one pointer costs the image `_looks_like_nbalive95`, but the slots
    # are read either way -- `get_info` gates them on the ported `validate`.
    info = patcher.analyze_rom(rom)
    assert info.slots[4].current_name == ""
    assert info.slots[3].current_name != ""


def test_a_slot_is_the_librarys_own_type_and_not_the_games(patcher, rom):
    assert type(patcher.analyze_rom(rom).slots[0]) is RomSlot


def test_analyze_publishes_no_extra_fields(patcher, rom):
    """Unlike the SNES sibling, nothing this game's `map_rosters` needs comes
    out of the ROM, so there is nothing to carry across the JSON boundary."""
    assert patcher.analyze_rom(rom).extra == {}


def test_a_missing_file_is_an_error_and_not_a_verdict(patcher, tmp_path):
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "absent.bin")


def test_an_unreadable_file_is_an_error_and_not_a_verdict(patcher, rom):
    rom.chmod(0o000)
    try:
        with pytest.raises(RomError):
            patcher.analyze_rom(rom)
    finally:
        rom.chmod(0o644)


def test_a_readable_file_that_is_not_this_game_is_a_verdict_and_not_an_error(patcher, tmp_path):
    """The distinction `cmd_analyze` needs: it catches `RomError` per patcher
    and continues, and treats `is_valid=False` as a considered no."""
    other = tmp_path / "garbage.bin"
    other.write_bytes(b"\x00" * 4096)
    info = patcher.analyze_rom(other)
    assert info.is_valid is False
    assert info.game_id == "nbalive95-genesis"


def test_the_reported_size_is_the_files_and_not_zero(patcher, tmp_path):
    """DELIBERATE DIVERGENCE: upstream answered `size=0` for anything it
    refused, which is a lie about a file the user can see on disk."""
    other = tmp_path / "garbage.bin"
    other.write_bytes(b"\x7f" * 4096)
    assert patcher.analyze_rom(other).size == 4096


def test_the_nhl94_genesis_image_is_not_claimed(patcher, tmp_path):
    """The failure a size-only check produces: two Genesis games, one image."""
    other = tmp_path / "nhl94.bin"
    other.write_bytes(bytes(genesis_fixture.build_nhl94_genesis_rom()))
    assert patcher.analyze_rom(other).is_valid is False


def test_an_image_whose_header_names_another_game_is_not_claimed(patcher, tmp_path):
    path = fixture.write_nbalive95_rom(tmp_path / "96.bin", title="NBA LIVE 96")
    assert patcher.analyze_rom(path).is_valid is False


# -- the structural check ----------------------------------------------------


def test_the_pointer_tables_end_where_the_address_table_says(rom):
    assert _LAST_POINTER_END == fixture.LAST_POINTER_END


def test_a_two_megabyte_image_holds_every_pointer_table(rom):
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _pointer_tables_fit(reader) is True


def test_a_file_at_the_ported_size_floor_does_not(tmp_path):
    """The self-contradiction: `validate` says yes, twelve teams are unreachable."""
    path = fixture.write_nbalive95_rom(tmp_path / "floor.bin", size=ROM_SIZE_MIN)
    reader = NBALive95RomReader(str(path))
    reader.load()
    assert reader.validate() is True
    assert _pointer_tables_fit(reader) is False


def test_the_gap_between_the_floor_and_the_last_table_is_half_a_megabyte():
    assert _LAST_POINTER_END - ROM_SIZE_MIN == 491740


def test_an_unloaded_reader_holds_no_pointer_tables():
    assert _pointer_tables_fit(NBALive95RomReader("nowhere")) is False


def test_the_structural_check_accepts_the_synthetic_image(rom):
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is True


def test_the_structural_check_rejects_a_single_zeroed_pointer(rom):
    _corrupt(rom, fixture.TEAM_ROSTER_ADDRESSES[29] + 11 * 4, b"\x00\x00\x00\x00")
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is False


def test_the_structural_check_rejects_a_position_byte_the_game_does_not_define(rom):
    _corrupt(rom, fixture.player_offset(12, 6) + fixture.OFF_POSITION, bytes([5]))
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is False


def test_the_structural_check_accepts_the_highest_position_the_game_defines(rom):
    _corrupt(rom, fixture.player_offset(12, 6) + fixture.OFF_POSITION, bytes([4]))
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is True


def test_the_structural_check_rejects_a_name_field_with_too_little_ascii(rom):
    _corrupt(rom, fixture.player_offset(20, 3) + fixture.OFF_NAME, b"AB" + bytes(22))
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is False


def test_the_structural_check_rejects_three_hundred_and_sixty_pointers_to_one_record(rom):
    """A file of constant bytes would otherwise aim every slot at one record
    that happened to parse."""
    data = bytearray(rom.read_bytes())
    target = fixture.player_offset(0, 0).to_bytes(4, "big")
    for team in range(TEAM_COUNT):
        table = fixture.TEAM_ROSTER_ADDRESSES[team]
        for slot in range(12):
            data[table + slot * 4 : table + slot * 4 + 4] = target
    rom.write_bytes(bytes(data))
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert _looks_like_nbalive95(reader) is False


def test_the_structural_check_reads_every_team_and_not_only_the_first(rom):
    """The ported `validate` looks at team 0 alone; this walks all thirty."""
    reader = NBALive95RomReader(str(rom))
    reader.load()
    assert reader.validate() is True
    _corrupt(rom, fixture.player_offset(29, 11) + fixture.OFF_POSITION, bytes([9]))
    later = NBALive95RomReader(str(rom))
    later.load()
    assert later.validate() is True
    assert _looks_like_nbalive95(later) is False


def test_the_structural_check_is_not_run_by_patch(rom, out, patcher):
    """The asymmetry the plan requires, pinned so nobody "fixes" it.

    A false positive costs the user every unrelated ROM they own being reported
    as NBA Live 95; a false negative costs only auto-detection. So `analyze_rom`
    runs the structural check and `patch` does not, and a file this project
    cannot recognise is still patchable by name.
    """
    _corrupt(rom, fixture.player_offset(29, 11) + fixture.OFF_POSITION, bytes([9]))
    assert patcher.analyze_rom(rom).is_valid is False
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 5)))
    assert result.teams_patched == 1


# -- fetch -------------------------------------------------------------------


def test_fetch_returns_one_roster_per_slot_mapped_team(patcher):
    data = patcher.fetch(season=2025)
    assert [roster.team.code for roster in data.teams] == ["BOS", "CHI"]


def test_fetch_skips_a_team_the_rom_has_no_slot_for(patcher):
    """Toronto and the unknown code cost two round trips each if not filtered."""
    patcher.fetch(season=2025)
    assert [team_id for team_id, _ in patcher.api.squad_calls] == [1, 2]


def test_fetch_threads_the_season_into_the_squad_call(patcher):
    """DELIBERATE DIVERGENCE: upstream called it with no season, so the first
    season ever fetched was served from the cache forever."""
    patcher.fetch(season=2003)
    assert patcher.api.squad_calls == [(1, 2003), (2, 2003)]


def test_fetch_threads_the_season_into_the_leaders_call(patcher):
    """A path segment of the request there, not merely a cache key."""
    patcher.fetch(season=2003)
    assert patcher.api.leader_calls == [(1, 2003), (2, 2003)]


def test_fetch_carries_the_leaders_in_the_roster_rather_than_on_the_patcher(patcher):
    """DELIBERATE DIVERGENCE: upstream left them on `self.team_stats`, an
    instance side channel no serialised rosters file could carry."""
    data = patcher.fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"0": {"PTS": 30.0}}
    assert hasattr(patcher, "team_stats") is False


def test_fetch_labels_the_league_with_the_season_it_was_asked_for(patcher):
    assert patcher.fetch(season=1999).league.season == 1999


def test_fetch_counts_the_rosters_it_built_and_not_the_teams_it_saw(patcher):
    league = patcher.fetch(season=2025).league
    assert league.teams_count == 2
    assert len(patcher.api.get_nba_teams()) == 4


def test_fetch_names_the_league_and_its_country(patcher):
    league = patcher.fetch(season=2025).league
    assert league.name == "NBA"
    assert league.country == "USA"
    assert league.country_code == "US"


def test_a_provider_returning_no_teams_is_an_error(patcher):
    patcher.api = FakeApi([])
    with pytest.raises(ApiError):
        patcher.fetch(season=2025)


def test_a_provider_returning_only_teams_the_rom_lacks_is_an_error(patcher):
    patcher.api = FakeApi([Team(id=9, name="Toronto Raptors", code="TOR")])
    with pytest.raises(ApiError):
        patcher.fetch(season=2025)


def test_an_empty_squad_still_produces_a_roster(patcher):
    patcher.api = FakeApi(_teams(), squad_size=0)
    data = patcher.fetch(season=2025)
    assert [len(roster.players) for roster in data.teams] == [0, 0]


def test_fetch_reports_progress_from_zero_to_one(patcher):
    seen = []
    patcher.fetch(season=2025, on_progress=lambda fraction, message: seen.append(fraction))
    assert seen == [0.0, 0.5, 1.0]


def test_fetch_reports_its_first_status(patcher):
    seen = []
    p = NBALive95Patcher(cache_dir=patcher.cache_dir, on_status=seen.append)
    p.api = FakeApi(_teams())
    p.fetch(season=2025)
    assert seen[0] == "Fetching NBA teams..."


# -- map_rosters -------------------------------------------------------------


def test_map_rosters_keys_the_result_by_rom_slot(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 14), ("CHI", 14)))
    assert sorted(mapped.teams) == [BOS_SLOT, CHI_SLOT]


def test_map_rosters_stamps_the_game_it_mapped_for(patcher):
    assert patcher.map_rosters(_league_data(("BOS", 14))).game_id == "nbalive95-genesis"


def test_map_rosters_is_sparse_rather_than_thirty_records(patcher):
    """Upstream always built all 30 and left 27-29 empty."""
    assert len(patcher.map_rosters(_league_data(("BOS", 14))).teams) == 1


def test_a_mapped_team_is_cut_to_the_twelve_slots_the_rom_has(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 25)))
    assert len(mapped.teams[BOS_SLOT].players) == 12


def test_a_short_squad_is_mapped_whole(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 4)))
    assert len(mapped.teams[BOS_SLOT].players) == 4


def test_a_mapped_team_carries_the_name_from_the_games_own_order(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 14)))
    assert mapped.teams[BOS_SLOT].name == "Boston Celtics"


def test_a_team_the_rom_has_no_slot_for_is_dropped(patcher):
    assert patcher.map_rosters(_league_data(("TOR", 14))).teams == {}


def test_an_unknown_team_code_is_dropped(patcher):
    assert patcher.map_rosters(_league_data(("ZZZ", 14))).teams == {}


def test_a_slot_mapping_is_refused(patcher):
    with pytest.raises(CapabilityError):
        patcher.map_rosters(_league_data(("BOS", 14)), [SlotMapping(slot_index=0, team_id=1)])


def test_the_two_all_star_slots_and_the_slammers_are_never_mapped(patcher):
    """`map_rosters` caps at 27, not 30, exactly as upstream did."""
    mapped = patcher.map_rosters(_league_data(*[(code, 14) for code in ("BOS", "CHI", "GS")]))
    assert [slot for slot in mapped.teams if slot >= NBA_TEAM_COUNT] == []


def test_a_team_mapped_to_an_all_star_slot_is_dropped(patcher, monkeypatch):
    """The `NBA_TEAM_COUNT` cap itself, rather than the table's silence about it.

    No entry in `MODERN_NBA_TO_NBALIVE95` reaches 27-29 today, so widening the
    guard to `TEAM_COUNT` changes nothing observable -- which makes the guard
    untested unless the table is made to reach there. Injecting one entry is
    what turns it into a real branch: without the cap this slot is mapped, and
    `patch` then writes a real squad over the East All-Stars.
    """
    from retro_roster_patcher.games.nbalive95_genesis import models

    monkeypatch.setitem(models.MODERN_NBA_TO_NBALIVE95, "EAS", 27)
    assert patcher.mapper.get_team_slot("EAS") == 27
    assert patcher.map_rosters(_league_data(("EAS", 14))).teams == {}


def test_a_populated_alias_takes_the_slot(patcher):
    mapped = patcher.map_rosters(_league_data(("GS", 14)))
    assert len(mapped.teams[GS_SLOT].players) == 12


def test_an_empty_alias_arriving_second_does_not_wipe_a_populated_slot(patcher):
    """DELIBERATE DIVERGENCE: upstream assigned the slot unconditionally.

    Upstream's rosters were a dict keyed by team code holding only non-empty
    squads, so an empty alias could never displace a populated one. Here the
    slot is assigned directly, and without the guard `patch` would skip it and
    report success with the 1994 roster still in place.
    """
    mapped = patcher.map_rosters(_league_data(("GS", 14), ("GSW", 0)))
    assert len(mapped.teams[GS_SLOT].players) == 12


def test_an_empty_alias_arriving_first_is_replaced(patcher):
    mapped = patcher.map_rosters(_league_data(("GSW", 0), ("GS", 14)))
    assert len(mapped.teams[GS_SLOT].players) == 12


def test_two_populated_aliases_leave_the_later_one_in_the_slot(patcher):
    """The guard is about emptiness alone; it does not arbitrate two real squads."""
    mapped = patcher.map_rosters(_league_data(("GS", 14), ("GSW", 14)))
    assert mapped.teams[GS_SLOT].players[0].name_last == "GSWLast00"


def test_an_empty_squad_that_collides_with_nothing_still_takes_its_slot(patcher):
    """The mapped result keeps showing which slots a provider team matched."""
    mapped = patcher.map_rosters(_league_data(("BOS", 0)))
    assert mapped.teams[BOS_SLOT].players == []


def test_every_alias_pair_names_one_slot(patcher):
    pairs = [
        ("GS", "GSW"),
        ("BKN", "NJN"),
        ("NYK", "NY"),
        ("SA", "SAS"),
        ("OKC", "SEA"),
        ("UTA", "UTAH"),
        ("WAS", "WSH"),
    ]
    collisions = [len(patcher.map_rosters(_league_data((a, 14), (b, 14))).teams) for a, b in pairs]
    assert collisions == [1, 1, 1, 1, 1, 1, 1]


def test_the_leaders_reach_the_mapped_ratings(patcher):
    """`extra["leaders"]` is keyed by player id as a string, and only the
    players it names get stat-derived ratings."""
    data = _league_data(("BOS", 3))
    data.teams[0].extra["leaders"] = {"0": {"FG%": 46.5}}
    mapped = patcher.map_rosters(data)
    rated = [record for record in mapped.teams[BOS_SLOT].players if record.ratings[0] == 62]
    assert len(rated) == 1


# -- patch -------------------------------------------------------------------


def test_patch_writes_an_image_of_the_same_length(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    assert len(out.read_bytes()) == fixture.ROM_SIZE


def test_patch_returns_the_librarys_result_type(patcher, rom, out):
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    assert type(result) is PatchResult
    assert result.output_path == str(out)


def test_patch_counts_the_slots_it_reached(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12), (CHI_SLOT, 5))
    )
    assert result.teams_patched == 2


def test_patch_counts_the_records_that_reached_the_image(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12), (CHI_SLOT, 5))
    )
    assert result.players_patched == 17


def test_patch_counts_records_written_and_not_records_requested(patcher, rom, out):
    """Three of the twelve pointers are gone, so nine of twelve records land."""
    table = fixture.TEAM_ROSTER_ADDRESSES[BOS_SLOT]
    for slot in (2, 6, 10):
        _corrupt(rom, table + slot * 4, b"\x00\x00\x00\x00")
    result = patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    assert result.players_patched == 9


def test_the_written_names_reach_the_records_the_pointers_address(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((CHI_SLOT, 12)))
    written = out.read_bytes()
    found = [
        fixture.decode_player_record(written, fixture.player_offset(CHI_SLOT, slot))["last_name"]
        for slot in range(12)
    ]
    assert found == [f"Written{index:02d}" for index in range(12)]


def test_a_patched_team_keeps_the_twelve_different_faces_the_cartridge_shipped_with(
    patcher, rom, out
):
    """DELIBERATE DIVERGENCE, end to end: patched players no longer look alike.

    `map_player` sets neither `skin_color` nor `hair_style`, so every record
    reaches the writer carrying 0 for both. Upstream, and this port until now,
    wrote those zeros: all twelve Bulls came out with one skin tone and one
    hair style. The image's own twelve styles now survive the patch, while the
    names beside them are all replaced.
    """
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((CHI_SLOT, 12)))
    roster = _read_back(out, CHI_SLOT)
    assert [player["hair_style"] for player in roster] == [
        fixture.player_hair(CHI_SLOT, slot) for slot in range(12)
    ]
    assert [player["skin_color"] for player in roster] == [
        fixture.player_skin(CHI_SLOT, slot) for slot in range(12)
    ]
    # Not vacuous in either direction: the styles really do differ from each
    # other, and these are the records the patch rewrote.
    assert len({player["hair_style"] for player in roster}) == 12
    assert [player["last_name"] for player in roster] == [
        f"Written{index:02d}" for index in range(12)
    ]


def test_an_unpatched_slot_keeps_the_roster_the_cartridge_shipped_with(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((CHI_SLOT, 12)))
    roster = _read_back(out, BOS_SLOT)
    assert [player["last_name"] for player in roster] == [
        fixture.player_last_name(BOS_SLOT, slot) for slot in range(12)
    ]


def test_patch_disables_the_cartridges_own_checksum_routine(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    written = out.read_bytes()
    assert written[0x690:0x696] == b"\x4e\x71\x4e\x71\x4e\x71"


def test_the_bypass_is_applied_even_when_no_slot_receives_players(patcher, rom, out):
    """A ROM whose bytes changed and whose self-check did not refuses to boot,
    and the header checksum below changes on every run."""
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 0)))
    assert out.read_bytes()[0x690:0x696] == b"\x4e\x71\x4e\x71\x4e\x71"


def test_patch_recomputes_the_header_checksum(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    written = out.read_bytes()
    total = 0
    for index in range(0x200, len(written) - 1, 2):
        total += (written[index] << 8) | written[index + 1]
    assert struct.unpack_from(">H", written, 0x18E)[0] == total & 0xFFFF


def test_the_header_checksum_was_wrong_before_the_patch(patcher, rom, out):
    """Guards the test above from passing on an image that needed no change."""
    assert struct.unpack_from(">H", rom.read_bytes(), 0x18E)[0] == fixture.CHECKSUM_FILLER


def test_patch_writes_the_checksum_word_once(patcher, rom, out):
    """`finalize` owns the recomputation and `patch` must not repeat it.

    Repeating it would sum the 2 MB image twice; the observable half is that the
    word is the sum of the *final* bytes either way, so this pins the value
    rather than the call count -- a second call after the first would still have
    to land on the same answer, and a call placed before the record writes would
    not.
    """
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    written = bytearray(out.read_bytes())
    stored = struct.unpack_from(">H", written, 0x18E)[0]
    struct.pack_into(">H", written, 0x18E, 0)
    total = 0
    for index in range(0x200, len(written) - 1, 2):
        total += (written[index] << 8) | written[index + 1]
    assert stored == total & 0xFFFF


def test_rosters_mapped_for_another_game_are_refused(patcher, rom, out):
    foreign = MappedRosters(game_id="nhl94-genesis", teams={0: []})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=rom, output_path=out, rosters=foreign)


def test_foreign_rosters_are_refused_before_the_rom_is_touched(patcher, tmp_path, out):
    """The guard costs no I/O, so it runs ahead of the missing-file check."""
    foreign = MappedRosters(game_id="we2002", teams={})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=tmp_path / "absent.bin", output_path=out, rosters=foreign)


def test_patching_a_file_that_is_not_there_raises(patcher, tmp_path, out):
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=tmp_path / "absent.bin", output_path=out, rosters=_mapped((BOS_SLOT, 3))
        )


def test_patching_an_image_of_another_game_raises(patcher, tmp_path, out):
    other = tmp_path / "nhl94.bin"
    other.write_bytes(bytes(genesis_fixture.build_nhl94_genesis_rom()))
    with pytest.raises(RomError):
        patcher.patch(rom_path=other, output_path=out, rosters=_mapped((BOS_SLOT, 3)))


def test_patching_a_file_at_the_ported_size_floor_raises(patcher, tmp_path, out):
    """Upstream patched eighteen of thirty teams here and reported success."""
    path = fixture.write_nbalive95_rom(tmp_path / "floor.bin", size=ROM_SIZE_MIN)
    with pytest.raises(RomError):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((BOS_SLOT, 3)))


def test_the_size_failure_names_the_offset_and_the_file_length(patcher, tmp_path, out):
    path = fixture.write_nbalive95_rom(tmp_path / "floor.bin", size=ROM_SIZE_MIN)
    with pytest.raises(RomError, match=r"0x1f80dc.*1572864-byte"):
        patcher.patch(rom_path=path, output_path=out, rosters=_mapped((BOS_SLOT, 3)))


def test_an_unwritable_output_path_raises(patcher, rom, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(RomError):
            patcher.patch(
                rom_path=rom, output_path=locked / "p.bin", rosters=_mapped((BOS_SLOT, 3))
            )
    finally:
        locked.chmod(0o700)


def test_a_slot_holding_an_empty_roster_is_not_counted(patcher, rom, out):
    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 0), (CHI_SLOT, 4))
    )
    assert result.teams_patched == 1


def test_a_slot_holding_an_empty_roster_keeps_its_1994_players(patcher, rom, out):
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 0)))
    roster = _read_back(out, BOS_SLOT)
    assert roster[0]["last_name"] == fixture.player_last_name(BOS_SLOT, 0)


def test_a_negative_slot_key_is_ignored_rather_than_read_as_an_offset(patcher, rom, out):
    """The keys arrive from a plain dict that may have crossed a JSON boundary.

    `_get_team_roster_offset` answers 0 for a negative index, which would send
    the writer at the Genesis interrupt vectors at the head of the file.
    """
    rosters = _mapped((BOS_SLOT, 3))
    rosters.teams[-1] = NBALive95TeamRecord(index=-1, name="x", players=_records(3))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1
    assert out.read_bytes()[:0x100] == rom.read_bytes()[:0x100]


def test_a_slot_past_the_nba_teams_is_ignored(patcher, rom, out):
    rosters = _mapped((BOS_SLOT, 3))
    rosters.teams[27] = NBALive95TeamRecord(index=27, name="East", players=_records(3))
    result = patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    assert result.teams_patched == 1


def test_a_slot_past_the_nba_teams_keeps_its_own_records(patcher, rom, out):
    rosters = _mapped((BOS_SLOT, 3))
    rosters.teams[27] = NBALive95TeamRecord(index=27, name="East", players=_records(3))
    patcher.patch(rom_path=rom, output_path=out, rosters=rosters)
    roster = _read_back(out, 27)
    assert roster[0]["last_name"] == fixture.player_last_name(27, 0)


def test_patch_reports_progress_from_zero_to_one(patcher, rom, out):
    seen = []
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=_mapped((BOS_SLOT, 3), (CHI_SLOT, 3)),
        on_progress=lambda fraction, message: seen.append(fraction),
    )
    assert seen == [0.0, 0.5, 1.0]


def test_patch_names_the_team_it_is_writing(patcher, rom, out):
    seen = []
    patcher.patch(
        rom_path=rom,
        output_path=out,
        rosters=_mapped((BOS_SLOT, 3)),
        on_progress=lambda fraction, message: seen.append(message),
    )
    assert seen[0] == "Writing Boston Celtics..."


def test_patch_reports_its_statuses_in_order(patcher, rom, out):
    seen = []
    p = NBALive95Patcher(cache_dir=patcher.cache_dir, on_status=seen.append)
    p.api = FakeApi(_teams())
    p.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 3)))
    assert seen == ["Validating ROM...", "Initializing ROM writer...", "Saving patched ROM..."]


def test_patch_leaves_the_input_image_untouched(patcher, rom, out):
    before = rom.read_bytes()
    patcher.patch(rom_path=rom, output_path=out, rosters=_mapped((BOS_SLOT, 12)))
    assert rom.read_bytes() == before


# -- the four steps together -------------------------------------------------


def test_the_whole_interface_runs_end_to_end(patcher, rom, out):
    info = patcher.analyze_rom(rom)
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data)
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert info.is_valid is True
    assert result.teams_patched == 2
    assert result.players_patched == 24


def test_the_names_the_provider_supplied_are_the_ones_in_the_patched_image(patcher, rom, out):
    patcher.patch(
        rom_path=rom, output_path=out, rosters=patcher.map_rosters(patcher.fetch(season=2025))
    )
    roster = _read_back(out, BOS_SLOT)
    assert [player["last_name"] for player in roster[:3]] == ["Last0", "Last1", "Last2"]


def test_a_patched_image_still_analyses_as_this_game(patcher, rom, out):
    patcher.patch(
        rom_path=rom, output_path=out, rosters=patcher.map_rosters(patcher.fetch(season=2025))
    )
    assert patcher.analyze_rom(out).is_valid is True


def test_a_patched_image_reports_the_new_first_player(patcher, rom, out):
    patcher.patch(
        rom_path=rom, output_path=out, rosters=patcher.map_rosters(patcher.fetch(season=2025))
    )
    assert patcher.analyze_rom(out).slots[BOS_SLOT].current_name == "First player: First0 Last0"
