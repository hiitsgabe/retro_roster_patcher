"""The NHL94 SNES patcher against the unified interface.

The reader, writer and stat mapper below it are a faithful port of an untested
upstream; this layer is where its contract violations are absorbed and where the
migration's own decisions live. Four things here are not in the ported code at
all and are the reason this file is the longest of the four:

  * the roster counts' two-hop journey from `RomInfo.extra` through
    `map_rosters(roster_counts=...)` onto `NHL94TeamRecord`, and out again in
    the header `patch` writes;
  * `players_patched` counting records that reached the image;
  * the alias guard, without which an empty `LA` wipes a populated `LAK`;
  * `analyze_rom`'s structural check, without which this patcher claims every
    ROM in the user's library.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `NHL94SNESRomWriter.__init__` builds its own reader over the *input* file,
so `writer.reader.data` is the pre-write image for the writer's whole lifetime
and asserting against it would assert nothing.
"""

import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, SlotMapping
from retro_roster_patcher.games.nhl94_snes.models import (
    DEFAULT_ROSTER_COUNTS,
    TEAM_COUNT,
    NHL94PlayerRecord,
    NHL94TeamRecord,
)
from retro_roster_patcher.games.nhl94_snes.patcher import (
    NHL94SNESPatcher,
    _looks_like_nhl94_snes,
    _pointer_table_fits,
)
from retro_roster_patcher.games.nhl94_snes.rom_reader import (
    ROM_SIZE_NO_HEADER,
    NHL94SNESRomReader,
)
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nhl import NhlApiClient
from tests.fixtures import synthetic_rom as genesis_fixture
from tests.fixtures import synthetic_snes_rom as fixture

# Slots from `MODERN_NHL_TO_NHL94`. Boston and Chicago are the two the fake API
# covers; San Jose is one of the four two codes reach ("SJS" and ESPN's "SJ").
BOS_SLOT = 1
CHI_SLOT = 4
SJS_SLOT = 19


class FakeApi:
    """Stands in for EspnClient / NhlApiClient.

    Records what it was asked for, because the ESPN and NHL branches of `fetch`
    key on different things -- team id versus three-letter code -- and upstream
    forwarded the season on only one of them.
    """

    def __init__(self, teams, squad_size=15, leaders=None):
        self._teams = teams
        self._squad_size = squad_size
        self._leaders = {"0": {"PTS": 40}} if leaders is None else leaders
        self.squad_calls = []
        self.leader_calls = []

    def get_nhl_teams(self):
        return list(self._teams)

    def get_hockey_squad(self, team_ref, season=None):
        self.squad_calls.append((team_ref, season))
        return [
            Player(id=i, name=f"P{i}", position="C", number=i + 1) for i in range(self._squad_size)
        ]

    def get_hockey_team_leaders(self, team_ref, season=None):
        self.leader_calls.append((team_ref, season))
        return dict(self._leaders)


def _teams():
    return [
        Team(id=1, name="Boston Bruins", code="BOS"),
        Team(id=2, name="Chicago Blackhawks", code="CHI"),
        Team(id=3, name="Not A Real Team", code="ZZZ"),
    ]


@pytest.fixture
def patcher(tmp_path):
    """A patcher wired to a fake API covering two real NHL94 slots."""
    p = NHL94SNESPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams())
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_nhl94_snes_rom(tmp_path / "nhl94.sfc")


def _league_data(*entries, season=2025):
    """`LeagueData` with one `TeamRoster` per `(code, squad size)`, in order.

    The order is what decides which of two colliding aliases lands in the shared
    slot, so it is a parameter rather than an accident of a dict.
    """
    return LeagueData(
        league=League(id=0, name="NHL", country="USA", country_code="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=i + 1, name=code, code=code),
                players=[
                    Player(id=i * 100 + n, name=f"{code} Player{n:02d}", position="C", number=n + 1)
                    for n in range(size)
                ],
                extra={"leaders": {}},
            )
            for i, (code, size) in enumerate(entries)
        ],
    )


def _records(count):
    return [NHL94PlayerRecord(name=f"Written {i:02d}", jersey_number=i + 1) for i in range(count)]


def _team_record(slot, count, *, forwards=14, defencemen=7):
    return NHL94TeamRecord(
        index=slot,
        name="Team",
        city="",
        acronym="",
        players=_records(count),
        num_goalies=2,
        num_forwards=forwards,
        num_defensemen=defencemen,
    )


def _read_back(path, slot):
    reader = NHL94SNESRomReader(str(path))
    assert reader.load() is True
    return reader.read_team_roster(slot)


# -- registration ----------------------------------------------------------


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("nhl94-snes")
    assert cls is NHL94SNESPatcher
    assert cls.platform == "snes"
    assert cls.sport == "hockey"
    assert cls.requires_slot_mapping is False
    assert cls.providers == ("espn", "nhl")


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # Registration is a side-effect import at the bottom of the package
    # `__init__`. Dropping it leaves `get_patcher` green for anyone who imported
    # the game module first -- which every other test in this file does -- and
    # broken for the CLI, which only imports the root. Hence the subprocess.
    source = textwrap.dedent(
        """
        import retro_roster_patcher

        cls = retro_roster_patcher.get_patcher("nhl94-snes")
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
    assert proc.stdout.strip() == ("retro_roster_patcher.games.nhl94_snes.patcher:NHL94SNESPatcher")


def test_the_two_nhl94_patchers_are_distinct_registrations():
    from retro_roster_patcher import get_patcher

    assert get_patcher("nhl94-snes") is not get_patcher("nhl94-genesis")
    assert get_patcher("nhl94-snes").platform != get_patcher("nhl94-genesis").platform


# -- construction ----------------------------------------------------------


def test_the_default_provider_is_espn(tmp_path):
    p = NHL94SNESPatcher(cache_dir=tmp_path)
    assert p.provider == "espn"
    assert type(p.api) is EspnClient


def test_naming_the_nhl_provider_builds_the_nhl_client(tmp_path):
    p = NHL94SNESPatcher(cache_dir=tmp_path, provider="nhl")
    assert p.provider == "nhl"
    assert type(p.api) is NhlApiClient


def test_an_unsupported_provider_is_refused(tmp_path):
    with pytest.raises(CapabilityError):
        NHL94SNESPatcher(cache_dir=tmp_path, provider="api-football")


def test_an_api_key_argument_is_refused(tmp_path):
    """There is no credential to supply, so accepting one would be a lie."""
    with pytest.raises(TypeError):
        NHL94SNESPatcher(cache_dir=tmp_path, api_key="secret")


def test_a_string_cache_dir_is_normalised_to_a_path(tmp_path):
    p = NHL94SNESPatcher(cache_dir=str(tmp_path))
    assert p.cache_dir == tmp_path


# -- analyze ---------------------------------------------------------------


def test_a_synthetic_rom_is_recognised(patcher, rom):
    info = patcher.analyze_rom(rom)
    assert info.is_valid is True
    assert info.game_id == "nhl94-snes"
    assert info.size == fixture.ROM_SIZE
    assert len(info.slots) == TEAM_COUNT


def test_the_slots_carry_the_names_in_the_image_and_the_names_in_the_code(patcher, rom):
    info = patcher.analyze_rom(rom)
    assert [slot.current_name for slot in info.slots] == fixture.CITIES
    # Slot 20 is the one place the two differ, so this tells them apart.
    assert info.slots[20].current_name == "St Louis"
    assert info.slots[20].display_name == "St. Louis"


def test_a_headered_image_is_recognised(patcher, tmp_path):
    headered = fixture.write_nhl94_snes_rom(tmp_path / "h.smc", with_smc_header=True)
    info = patcher.analyze_rom(headered)
    assert info.is_valid is True
    assert info.extra["has_header"] is True


def test_a_headerless_image_says_so(patcher, rom):
    assert patcher.analyze_rom(rom).extra["has_header"] is False


def test_a_missing_file_raises_rather_than_reporting_an_invalid_rom(patcher, tmp_path):
    """The distinction `analyze` is built on: unreadable raises, wrong game does not."""
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "absent.sfc")


def test_a_file_that_is_not_this_game_is_reported_rather_than_raised(patcher, tmp_path):
    other = tmp_path / "garbage.bin"
    other.write_bytes(b"\x00" * 4096)
    info = patcher.analyze_rom(other)
    assert info.is_valid is False
    assert info.game_id == "nhl94-snes"


def test_an_nhl94_genesis_rom_is_not_claimed_by_the_snes_patcher(patcher, tmp_path):
    """The reason `_looks_like_nhl94_snes` exists.

    `validate()` tests size alone, and the Genesis image is 1 MB, so upstream's
    test says yes to it. `analyze` probes every registered patcher against one
    file, so a yes here puts NHL 94 (SNES) beside NHL 94 (Genesis) in the
    matches for a Genesis cartridge.
    """
    genesis = tmp_path / "genesis.bin"
    genesis.write_bytes(bytes(genesis_fixture.build_nhl94_genesis_rom()))
    reader = NHL94SNESRomReader(str(genesis))
    assert reader.load() is True
    assert reader.validate() is True
    assert patcher.analyze_rom(genesis).is_valid is False


def test_a_file_of_the_declared_standard_size_is_not_claimed(patcher, tmp_path):
    """DEFECT worked around: `ROM_SIZE_NO_HEADER` is not this game's size.

    The pointer table is 927 207 bytes in and the constant is 649 728, so a file
    of exactly the size upstream called standard validates and then yields no
    pointer at all. Upstream patched it and reported success.
    """
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(ROM_SIZE_NO_HEADER))
    reader = NHL94SNESRomReader(str(short))
    assert reader.load() is True
    assert reader.validate() is True
    assert _pointer_table_fits(reader) is False
    assert patcher.analyze_rom(short).is_valid is False


def test_a_team_block_whose_header_is_too_short_is_not_claimed(patcher, tmp_path):
    """A header under 75 bytes would put the line table over the first record."""
    rom = fixture.build_nhl94_snes_rom()
    rom[fixture.team_base(13)] = 74
    rom[fixture.team_base(13) + 1] = 0
    broken = tmp_path / "broken.sfc"
    broken.write_bytes(bytes(rom))
    assert patcher.analyze_rom(broken).is_valid is False


def test_a_team_block_whose_first_record_is_unreadable_is_not_claimed(patcher, tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    first_record = fixture.team_base(13) + fixture.TEAM_HEADER_SIZE
    rom[first_record : first_record + 2] = (999).to_bytes(2, "little")
    broken = tmp_path / "broken.sfc"
    broken.write_bytes(bytes(rom))
    assert patcher.analyze_rom(broken).is_valid is False


def test_patch_still_runs_on_an_image_analyze_declines_to_claim(patcher, tmp_path):
    """The asymmetry is deliberate, so it is pinned rather than left to drift.

    `analyze_rom` applies the structural check and `patch` does not. Every bound
    in that check is derived from this package's own reader and writer and none
    of it has ever been run against a real dump, because no real ROM may enter
    this repository. A false positive would cost the user every unrelated ROM
    they own; a false negative costs only auto-detection, and `--game
    nhl94-snes` still patches. Making `patch` repeat the check would trade the
    cheap failure for the expensive one.
    """
    rom = fixture.build_nhl94_snes_rom()
    # Slot 13's header word is one byte short of what the line table needs, so
    # the structural check refuses the image -- while every pointer still reads
    # and slot 1 is untouched.
    rom[fixture.team_base(13)] = 74
    odd = tmp_path / "odd.sfc"
    odd.write_bytes(bytes(rom))
    out = tmp_path / "out.sfc"
    assert patcher.analyze_rom(odd).is_valid is False
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    result = patcher.patch(rom_path=odd, output_path=out, rosters=mapped)
    assert result.teams_patched == 1
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [f"Written {i:02d}" for i in range(10)]


def test_an_image_whose_teams_all_share_one_block_is_not_claimed(patcher, tmp_path):
    """Constant filler otherwise passes every per-block test 28 times over."""
    rom = fixture.build_nhl94_snes_rom()
    for i in range(TEAM_COUNT):
        offset = fixture.team_pointer_offset(i)
        rom[offset : offset + 2] = (fixture.team_base(0) - fixture.BANK_WINDOW_START).to_bytes(
            2, "little"
        )
    broken = tmp_path / "same.sfc"
    broken.write_bytes(bytes(rom))
    reader = NHL94SNESRomReader(str(broken))
    assert reader.load() is True
    assert _looks_like_nhl94_snes(reader) is False


def test_the_intact_image_passes_the_structural_check(patcher, rom):
    """The control for the four refusals above, so none of them is vacuous."""
    reader = NHL94SNESRomReader(str(rom))
    assert reader.load() is True
    assert _looks_like_nhl94_snes(reader) is True


# -- the roster counts, hop one: analyze publishes them --------------------


def test_analyze_publishes_one_roster_count_triple_per_slot(patcher, rom):
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    assert len(counts) == TEAM_COUNT
    expected = [[2, f, d] for f, d in fixture.TEAM_FD_COUNTS]
    # Slots 26 and 27 are under the reader's sanity floor and fall back.
    expected[26] = [2, 14, 7]
    expected[27] = [2, 14, 7]
    assert counts == expected
    # Not one repeated triple, which is what would make the equality above hold
    # for a reader that ignored the slot index.
    assert len({tuple(c) for c in counts}) == 26


def test_the_published_counts_are_json_safe(patcher, rom):
    """`RomInfo.extra` crosses the NDJSON boundary verbatim.

    A tuple would survive `json.dumps` as a list and come back unequal; every
    value here is already a list of ints, so the round trip is the identity.
    """
    import json

    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    assert json.loads(json.dumps(counts)) == counts
    assert {type(value) for row in counts for value in row} == {int}


def test_an_invalid_rom_publishes_no_counts_at_all(patcher, tmp_path):
    """28 copies of the fallback would read as 28 measurements."""
    other = tmp_path / "garbage.bin"
    other.write_bytes(b"\x00" * 4096)
    assert "roster_counts" not in patcher.analyze_rom(other).extra


# -- fetch -----------------------------------------------------------------


def test_fetch_returns_only_teams_that_have_a_rom_slot(patcher):
    data = patcher.fetch(season=2025)
    assert [roster.team.code for roster in data.teams] == ["BOS", "CHI"]
    assert data.league.teams_count == 2


def test_fetch_puts_the_leaders_in_the_roster_rather_than_on_the_patcher(patcher):
    """Upstream left them on `self.team_stats`, which no rosters file carries."""
    data = patcher.fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"0": {"PTS": 40}}
    assert hasattr(patcher, "team_stats") is False


def test_the_espn_branch_passes_the_season_to_both_calls(patcher):
    """Upstream passed it to neither.

    The squad call's season is a cache key, so without it the first season ever
    fetched was served forever; the leaders call's is a path segment of the
    request, so without it a `--season 2024` run asked for another year.
    """
    patcher.fetch(season=2024)
    assert patcher.api.squad_calls == [(1, 2024), (2, 2024)]
    assert patcher.api.leader_calls == [(1, 2024), (2, 2024)]


def test_the_nhl_branch_keys_on_the_team_code(tmp_path):
    p = NHL94SNESPatcher(cache_dir=tmp_path, provider="nhl")
    p.api = FakeApi(_teams())
    p.fetch(season=1994)
    assert p.api.squad_calls == [("BOS", 1994), ("CHI", 1994)]
    assert p.api.leader_calls == [("BOS", 1994), ("CHI", 1994)]


def test_a_provider_with_no_teams_raises(patcher):
    patcher.api = FakeApi([])
    with pytest.raises(ApiError):
        patcher.fetch(season=2025)


def test_a_provider_with_no_matching_team_raises(patcher):
    patcher.api = FakeApi([Team(id=9, name="Seattle Kraken", code="SEA")])
    with pytest.raises(ApiError):
        patcher.fetch(season=2025)


def test_fetch_reports_progress_once_per_team_and_once_at_the_end(patcher):
    seen = []
    patcher.fetch(season=2025, on_progress=lambda pct, msg: seen.append((pct, msg)))
    assert seen == [
        (0.0, "Fetching Boston Bruins..."),
        (0.5, "Fetching Chicago Blackhawks..."),
        (1.0, "Complete"),
    ]


def test_fetch_narrates_its_first_step(patcher):
    seen = []
    patcher.on_status = seen.append
    patcher.fetch(season=2025)
    assert seen == ["Fetching NHL teams..."]


# -- map -------------------------------------------------------------------


def test_mapping_puts_each_team_in_its_own_slot(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 20), ("CHI", 20)))
    assert type(mapped) is MappedRosters
    assert mapped.game_id == "nhl94-snes"
    assert sorted(mapped.teams) == [BOS_SLOT, CHI_SLOT]


def test_an_unmapped_team_code_is_dropped(patcher):
    mapped = patcher.map_rosters(_league_data(("ZZZ", 20)))
    assert mapped.teams == {}


def test_a_slot_mapping_is_refused(patcher):
    with pytest.raises(CapabilityError):
        patcher.map_rosters(_league_data(("BOS", 5)), [SlotMapping(slot_index=1, team_id=1)])


def test_without_counts_every_slot_is_cut_to_the_default(patcher):
    """The shape the CLI gets, because it calls `map_rosters` through the ABC."""
    mapped = patcher.map_rosters(_league_data(("BOS", 40)))
    record = mapped.teams[BOS_SLOT]
    assert (record.num_goalies, record.num_forwards, record.num_defensemen) == (
        DEFAULT_ROSTER_COUNTS
    )
    assert len(record.players) == sum(DEFAULT_ROSTER_COUNTS)


def test_the_roms_counts_change_how_many_players_are_selected(patcher, rom):
    """Hop two: the counts `analyze_rom` published come back in.

    Slot 4's image says 14 forwards and 6 defencemen, so 22 players, against the
    default's 23. That one-player difference is the whole observable effect and
    a fixture with uniform counts would hide it.
    """
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    assert counts[CHI_SLOT] == [2, 14, 6]
    mapped = patcher.map_rosters(_league_data(("CHI", 40)), roster_counts=counts)
    record = mapped.teams[CHI_SLOT]
    assert (record.num_goalies, record.num_forwards, record.num_defensemen) == (2, 14, 6)
    assert len(record.players) == 22


def test_the_counts_used_are_recorded_on_every_mapped_record(patcher, rom):
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    mapped = patcher.map_rosters(_league_data(("BOS", 40), ("CHI", 40)), roster_counts=counts)
    recorded = {slot: team.num_forwards for slot, team in mapped.teams.items()}
    assert recorded == {BOS_SLOT: 13, CHI_SLOT: 14}


def test_a_roster_counts_list_of_the_wrong_length_is_refused(patcher):
    with pytest.raises(MappingError):
        patcher.map_rosters(_league_data(("BOS", 5)), roster_counts=[[2, 14, 7]])


def test_a_ragged_roster_counts_row_is_refused(patcher):
    counts = [[2, 14, 7]] * TEAM_COUNT
    counts[3] = [2, 14]
    with pytest.raises(MappingError):
        patcher.map_rosters(_league_data(("BOS", 5)), roster_counts=counts)


def test_a_non_integer_roster_count_is_refused(patcher):
    counts = [[2, 14, 7]] * TEAM_COUNT
    counts[3] = [2, "14", 7]
    with pytest.raises(MappingError):
        patcher.map_rosters(_league_data(("BOS", 5)), roster_counts=counts)


def test_a_negative_roster_count_is_refused(patcher):
    counts = [[2, 14, 7]] * TEAM_COUNT
    counts[3] = [2, -1, 7]
    with pytest.raises(MappingError):
        patcher.map_rosters(_league_data(("BOS", 5)), roster_counts=counts)


def test_an_empty_alias_does_not_displace_a_populated_one(patcher):
    """`SJS` and ESPN's `SJ` are one slot, and upstream assigned it twice.

    Upstream kept its rosters in a dict keyed by team code and only stored a
    team whose squad was non-empty, so the collision could not cost anything.
    Here the slot is assigned directly, so without the guard the empty roster
    arriving second wipes the populated one and `patch` reports success having
    left the 1994 roster in place.
    """
    mapped = patcher.map_rosters(_league_data(("SJS", 20), ("SJ", 0)))
    assert len(mapped.teams[SJS_SLOT].players) == 20


def test_a_populated_alias_still_replaces_an_empty_one(patcher):
    mapped = patcher.map_rosters(_league_data(("SJ", 0), ("SJS", 20)))
    assert len(mapped.teams[SJS_SLOT].players) == 20


def test_an_empty_roster_that_collides_with_nothing_still_takes_its_slot(patcher):
    """It records which slot a provider team matched; `patch` is what skips it."""
    mapped = patcher.map_rosters(_league_data(("BOS", 0)))
    assert mapped.teams[BOS_SLOT].players == []


# -- patch -----------------------------------------------------------------


def test_a_patch_writes_the_mapped_names_into_the_image(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert type(result) is PatchResult
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [f"Written {i:02d}" for i in range(10)]


def test_a_patch_leaves_every_other_slot_as_it_found_it(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    names, _ = _read_back(out, CHI_SLOT)
    assert names == [fixture.player_name(CHI_SLOT, i) for i in range(fixture.ROSTER_PLAYERS)]


def test_a_patch_counts_the_slots_it_wrote(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(
        game_id="nhl94-snes",
        teams={BOS_SLOT: _team_record(BOS_SLOT, 10), CHI_SLOT: _team_record(CHI_SLOT, 10)},
    )
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 2
    assert result.players_patched == 20
    assert result.output_path == str(out)


def test_players_patched_counts_records_that_reached_the_image(patcher, rom, tmp_path):
    """The regression this migration exists to fix, on a roster that overflows.

    The image holds 23 records of an 8-byte name, so each region is 416 bytes.
    `_records` names are 10 bytes, costing 20 each, and the writer stops while
    fewer than 13 bytes remain, so 21 fit and 2 of the 23 are dropped. Upstream
    added `len(team.players)` here and reported 23.
    """
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 23)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 21
    names, _ = _read_back(out, BOS_SLOT)
    assert len(names) == 21
    # And the two numbers really do differ, so this is not a coincidence of the
    # fixture size.
    assert len(mapped.teams[BOS_SLOT].players) == 23


def test_a_slot_whose_region_takes_nothing_is_neither_written_nor_counted(patcher, tmp_path):
    """The header is skipped too: its line table would index absent players."""
    empty = fixture.write_nhl94_snes_rom(tmp_path / "empty.sfc", players_per_team=0)
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    result = patcher.patch(rom_path=empty, output_path=out, rosters=mapped)
    assert result.teams_patched == 0
    assert result.players_patched == 0


def test_an_empty_roster_never_reaches_the_writer(patcher, rom, tmp_path):
    """`write_team_roster([])` zero-fills the region, erasing the 1994 roster."""
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 0)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 0
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [fixture.player_name(BOS_SLOT, i) for i in range(fixture.ROSTER_PLAYERS)]


def test_the_header_is_written_from_the_counts_on_the_record(patcher, rom, tmp_path):
    """Hop three, and the reason the counts ride on the record.

    Slot 1's image says 13 forwards and 8 defencemen. A record cut to 12 and 7
    must produce a header saying 12 and 7, not the image's 13 and 8, because the
    line table indexes defencemen from `2 + forwards`.

    Twenty-one players and not ten, so `rom_writer.header_counts` has nothing to
    clamp and this test still says what it says: 2 + 12 + 7 is 21 and all 21
    reach the image. Ten would now produce a header of 8 and 0 -- correct, but
    it would no longer show the record's triple surviving to the ROM.
    """
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(
        game_id="nhl94-snes",
        teams={BOS_SLOT: _team_record(BOS_SLOT, 21, forwards=12, defencemen=7)},
    )
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    reader = NHL94SNESRomReader(str(out))
    assert reader.load() is True
    assert reader.read_team_player_counts(BOS_SLOT) == (2, 12, 7)


def test_the_header_does_not_claim_defencemen_the_writer_dropped(patcher, rom, tmp_path):
    """The filed defect, at the layer where it reaches the ROM.

    The same 23-into-21 overflow `test_players_patched_counts_records_that_
    reached_the_image` uses. Upstream, and this port until now, wrote the
    requested 14 and 7; 2 + 14 + 7 is 23 and only 21 records exist.
    """
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 23)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 21
    count_byte = out.read_bytes()[fixture.team_base(BOS_SLOT) + fixture.PLAYER_COUNT_OFFSET]
    assert count_byte == (14 << 4) | 5


def test_no_line_slot_names_a_record_the_writer_never_wrote(patcher, rom, tmp_path):
    """The consequence the header byte alone does not show.

    Under the requested 14 and 7 the table's highest index is 21; 21 records
    were written, so records 0..20 exist and 21 does not. Asserted as the exact
    maximum rather than as a bound: `max(...) <= 20` would also pass on a table
    of all zeroes, which is the failure a zero-filled header would produce.
    """
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 23)})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    base = fixture.team_base(BOS_SLOT) + fixture.LINE_ASSIGN_OFFSET
    table = out.read_bytes()[base : base + fixture.LINE_COUNT * fixture.LINE_SLOTS]
    assert max(table) == 20


def test_a_provider_short_of_skaters_does_not_get_a_full_teams_header(patcher, rom, tmp_path):
    """The commoner trigger, and the one the filed defect did not name.

    Nine players against the default 2/14/7 request. Nothing was dropped by the
    writer -- all nine fit -- but the selection was short, and upstream still
    wrote 14 and 7. Seven forwards and no defenceman is what exists.
    """
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 9)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 9
    count_byte = out.read_bytes()[fixture.team_base(BOS_SLOT) + fixture.PLAYER_COUNT_OFFSET]
    assert count_byte == (7 << 4) | 0


def test_an_out_of_range_slot_index_is_dropped_after_the_json_boundary(patcher, rom, tmp_path):
    """A negative key would read the bytes before the pointer table as a pointer."""
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(
        game_id="nhl94-snes",
        teams={-1: _team_record(-1, 5), TEAM_COUNT: _team_record(TEAM_COUNT, 5)},
    )
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 0
    # And nothing was written for them either. `teams_patched` alone does not
    # say that: slot -1 gets past the writer's only bounds test, which is
    # `team_index >= TEAM_COUNT`, resolves the four bytes *before* the pointer
    # table as a pointer, and lands a two-byte terminator wherever that points
    # -- then reports 0 written and is skipped here.
    assert out.read_bytes() == rom.read_bytes()


def test_rosters_mapped_for_another_game_are_refused(patcher, rom, tmp_path):
    mapped = MappedRosters(game_id="nhl94-genesis", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    with pytest.raises(MappingError):
        patcher.patch(rom_path=rom, output_path=tmp_path / "out.sfc", rosters=mapped)


def test_a_missing_input_rom_raises(patcher, tmp_path):
    mapped = MappedRosters(game_id="nhl94-snes", teams={})
    with pytest.raises(RomError):
        patcher.patch(
            rom_path=tmp_path / "absent.sfc", output_path=tmp_path / "out.sfc", rosters=mapped
        )


def test_a_rom_whose_pointer_table_is_past_the_end_raises(patcher, tmp_path):
    """Upstream wrote an unmodified copy and reported success."""
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(ROM_SIZE_NO_HEADER))
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    with pytest.raises(RomError):
        patcher.patch(rom_path=short, output_path=tmp_path / "out.sfc", rosters=mapped)


def test_a_roster_region_running_past_the_end_of_the_image_raises(patcher, tmp_path):
    """`_get_team_player_region` walks off the end and the writes then raise.

    Its loop test only checks the offset it is about to read, so a record chain
    with no terminator yields a region reaching past the image. `patch` turns
    the resulting `IndexError` into `RomError`; carrying on would finalize a
    half-written ROM under a success return.
    """
    rom = fixture.build_nhl94_snes_rom()
    start = fixture.team_base(BOS_SLOT) + fixture.TEAM_HEADER_SIZE
    # A record chain with no terminator, laid down at the same 47-byte stride
    # the scan walks. 47 does not divide the distance from `start` to the end of
    # the file, so the last step lands past it rather than exactly on it -- and
    # it is the overshoot, not the missing terminator, that makes the region
    # unwritable.
    length = 39
    offset = start
    while offset + 2 <= len(rom):
        rom[offset] = length & 0xFF
        rom[offset + 1] = length >> 8
        offset += length + 8
    broken = tmp_path / "broken.sfc"
    broken.write_bytes(bytes(rom))
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    with pytest.raises(RomError):
        patcher.patch(rom_path=broken, output_path=tmp_path / "out.sfc", rosters=mapped)


def test_nothing_is_written_to_disk_when_a_patch_raises(patcher, tmp_path):
    short = tmp_path / "short.sfc"
    short.write_bytes(bytes(ROM_SIZE_NO_HEADER))
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    with pytest.raises(RomError):
        patcher.patch(rom_path=short, output_path=out, rosters=mapped)
    assert out.exists() is False


def test_patch_reports_progress_once_per_written_slot_and_once_at_the_end(patcher, rom, tmp_path):
    seen = []
    mapped = MappedRosters(
        game_id="nhl94-snes",
        teams={CHI_SLOT: _team_record(CHI_SLOT, 5), BOS_SLOT: _team_record(BOS_SLOT, 5)},
    )
    patcher.patch(
        rom_path=rom,
        output_path=tmp_path / "out.sfc",
        rosters=mapped,
        on_progress=lambda pct, msg: seen.append((pct, msg)),
    )
    # Ascending slot order, whatever order the dict was built in.
    assert seen == [
        (0.0, "Writing Boston..."),
        (0.5, "Writing Chicago..."),
        (1.0, "Saving patched ROM..."),
    ]


def test_patch_narrates_its_three_steps(patcher, rom, tmp_path):
    seen = []
    patcher.on_status = seen.append
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    patcher.patch(rom_path=rom, output_path=tmp_path / "out.sfc", rosters=mapped)
    assert seen == ["Validating ROM...", "Initializing ROM writer...", "Saving patched ROM..."]


def test_an_unknown_option_is_ignored(patcher, rom, tmp_path):
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 5)})
    result = patcher.patch(
        rom_path=rom, output_path=tmp_path / "out.sfc", rosters=mapped, language="es"
    )
    assert result.teams_patched == 1


# -- the whole sequence ----------------------------------------------------


def test_fetch_map_and_patch_run_end_to_end(patcher, rom, tmp_path):
    """The four calls a wizard makes, in order, with the counts threaded."""
    out = tmp_path / "out.sfc"
    info = patcher.analyze_rom(rom)
    data = patcher.fetch(season=2025)
    mapped = patcher.map_rosters(data, roster_counts=info.extra["roster_counts"])
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert result.teams_patched == 2
    # 15 players offered per team and both slots take all of them.
    assert result.players_patched == 30
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [f"P{i}" for i in range(15)]


def test_a_patched_rom_can_be_analysed_and_patched_again(patcher, rom, tmp_path):
    """Nothing here touches a checksum, so the output stays a valid input.

    Which is only true because NHL '94 (SNES) does not verify one. A patcher
    that recomputed the SNES header checksum would have to keep it recomputed;
    one that verified it could not accept its own output at all.
    """
    once = tmp_path / "once.sfc"
    twice = tmp_path / "twice.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=rom, output_path=once, rosters=mapped)
    assert patcher.analyze_rom(once).is_valid is True
    result = patcher.patch(rom_path=once, output_path=twice, rosters=mapped)
    assert result.teams_patched == 1
    names, _ = _read_back(twice, BOS_SLOT)
    assert names == [f"Written {i:02d}" for i in range(10)]
