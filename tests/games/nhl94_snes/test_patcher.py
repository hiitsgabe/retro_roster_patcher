"""The NHL94 SNES patcher against the unified interface.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `NHL94SNESRomWriter.__init__` builds its own reader over the *input* file,
so `writer.reader.data` is the pre-write image for the writer's whole lifetime
and asserting against it would assert nothing.
"""

import json
import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, MappingError, RomError
from retro_roster_patcher.core.models import MappedRosters, PatchResult, SlotMapping
from retro_roster_patcher.games.nhl94_snes.models import (
    DEFAULT_ROSTER_COUNTS,
    RETAIL_ROSTER_COUNTS,
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
    """Stands in for EspnClient / NhlApiClient, recording what it was asked for.

    The ESPN and NHL branches of `fetch` key on different things -- team id
    versus three-letter code -- and upstream forwarded the season on only one.
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
            Player(id=i, name=f"P{i}", position="G" if i < 2 else "C", number=i + 1)
            for i in range(self._squad_size)
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


def _standings(body):
    """A transport serving `body` as ESPN's standings, recording the URLs."""

    def transport(url, headers, timeout):
        transport.urls.append(url)
        return json.dumps(body).encode()

    transport.urls = []
    return transport


def _standings_body(*rows):
    """ESPN's standings shape: conferences, divisions, then entries."""
    entries = [
        {
            "team": {"abbreviation": code},
            "stats": [
                {"name": "pointsFor", "value": gf},
                {"name": "pointsAgainst", "value": ga},
                {"name": "gamesPlayed", "value": gp},
            ],
        }
        for code, gf, ga, gp in rows
    ]
    return {"children": [{"children": [{"standings": {"entries": entries}}]}]}


@pytest.fixture
def patcher(tmp_path):
    """A patcher wired to a fake API covering two real NHL94 slots."""
    p = NHL94SNESPatcher(cache_dir=tmp_path / "cache", transport=_standings({}))
    p.api = FakeApi(_teams())
    return p


@pytest.fixture
def rom(tmp_path):
    return fixture.write_nhl94_snes_rom(tmp_path / "nhl94.sfc")


def _league_data(*entries, season=2025):
    """`LeagueData` with one `TeamRoster` per `(code, squad size)`, in order.

    The order decides which of two colliding aliases lands in the shared slot, so
    it is a parameter rather than an accident of a dict.
    """
    return LeagueData(
        league=League(id=0, name="NHL", country="USA", country_code="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=i + 1, name=code, code=code),
                players=[
                    Player(
                        id=i * 100 + n,
                        name=f"{code} Player{n:02d}",
                        position=_position(n),
                        number=n + 1,
                    )
                    for n in range(size)
                ],
                extra={"leaders": {}},
            )
            for i, (code, size) in enumerate(entries)
        ],
    )


def _position(n):
    """Two goalies first, then two centres for every defenceman."""
    return "G" if n < 2 else ("D" if n % 3 == 2 else "C")


def _base(image, slot):
    """A block's file offset in a written image, read from the pointer table:
    a patch rebuilds the blocks, so the fixture's own offsets no longer hold."""
    entry = fixture.team_pointer_offset(slot)
    return fixture.BANK_WINDOW_START + ((image[entry] | image[entry + 1] << 8) & 0x7FFF)


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


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("nhl94-snes")
    assert cls is NHL94SNESPatcher
    assert cls.platform == "snes"
    assert cls.sport == "hockey"
    assert cls.requires_slot_mapping is False
    assert cls.providers == ("espn", "nhl")


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # Registration is a side-effect import at the bottom of the package `__init__`.
    # Dropping it leaves `get_patcher` green for anyone who imported the game module
    # first -- which every other test here does -- and broken for the CLI, which only
    # imports the root. Hence the subprocess.
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

    `validate()` tests size alone and the Genesis image is 1 MB, so upstream's
    test says yes to it. `analyze` probes every registered patcher against one
    file, so a yes here puts NHL 94 (SNES) beside NHL 94 (Genesis) for a Genesis
    cartridge.
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
    """A header under 85 bytes would put the line table over the first record."""
    rom = fixture.build_nhl94_snes_rom()
    rom[fixture.team_base(13)] = 84
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
    """`analyze_rom` applies the structural check and `patch` does not.

    Do not make `patch` repeat it. Every bound in the check comes from this
    package's own reader and writer and none has been run against a real dump. A
    false positive costs the user every unrelated ROM they own; a false negative
    costs only auto-detection, and `--game nhl94-snes` still patches.
    """
    rom = fixture.build_nhl94_snes_rom()
    # Slot 13's header word is one byte short of what the line table needs, so the
    # structural check refuses the image -- while every pointer still reads.
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


def test_analyze_publishes_one_roster_count_triple_per_slot(patcher, rom):
    """Per slot, the larger of byte 17 and the cartridge's own counts: a patch
    writes what it filled into byte 17, and a slot one season left short must be
    filled again by the next."""
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    assert len(counts) == TEAM_COUNT
    image = list(fixture.TEAM_FD_COUNTS)
    # Slots 26 and 27 are under the reader's sanity floor and fall back.
    image[26] = image[27] = (14, 7)
    assert counts == [
        [2, max(f, rf), max(d, rd)]
        for (f, d), (rf, rd) in zip(image, RETAIL_ROSTER_COUNTS, strict=True)
    ]
    assert counts[2] == [2, 15, 8]  # Buffalo: the image's 15 forwards, the cartridge's 8 D
    assert counts[22] == [2, 14, 8]  # Toronto: the image says 5 and 7


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
    fetched was served forever; the leaders call's is a path segment, so without
    it a `--season 2024` run asked for another year.
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
    patcher.api = FakeApi([Team(id=9, name="Columbus Blue Jackets", code="CBJ")])
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
    assert seen[0] == "Fetching NHL teams..."


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
    """The shape a caller gets when it skips the first hop.

    Still the answer for a library caller with no ROM in hand, and for the CLI on
    an image `analyze_rom` declines to identify.
    """
    mapped = patcher.map_rosters(_league_data(("BOS", 40)))
    record = mapped.teams[BOS_SLOT]
    assert (record.num_goalies, record.num_forwards, record.num_defensemen) == (
        DEFAULT_ROSTER_COUNTS
    )
    assert len(record.players) == sum(DEFAULT_ROSTER_COUNTS)


def test_the_roms_counts_change_how_many_players_are_selected(patcher, rom):
    """Hop two: the counts `analyze_rom` published come back in.

    Buffalo's image says 15 forwards and the cartridge 8 defencemen, so 25
    players against the default's 23.
    """
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    mapped = patcher.map_rosters(_league_data(("BUF", 40)), roster_counts=counts)
    record = mapped.teams[2]
    assert (record.num_goalies, record.num_forwards, record.num_defensemen) == (2, 15, 8)
    assert len(record.players) == 25


def test_a_slot_one_patch_left_short_is_filled_again_by_the_next(patcher, rom, tmp_path):
    """A season with 6 forwards and 4 defencemen for Boston writes 6 and 4 into
    byte 17; the next patch still offers the cartridge's 15 and 7."""
    once = tmp_path / "once.sfc"
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    short = patcher.map_rosters(_league_data(("BOS", 12)), roster_counts=counts)
    assert (short.teams[BOS_SLOT].num_forwards, short.teams[BOS_SLOT].num_defensemen) == (6, 4)
    patcher.patch(rom_path=rom, output_path=once, rosters=short)
    assert NHL94SNESRomReader(str(once)).load()
    counts = patcher.analyze_rom(once).extra["roster_counts"]
    assert counts[BOS_SLOT] == [2, 15, 7]
    full = patcher.map_rosters(_league_data(("BOS", 40)), roster_counts=counts)
    assert (full.teams[BOS_SLOT].num_forwards, full.teams[BOS_SLOT].num_defensemen) == (15, 7)


def test_the_counts_used_are_recorded_on_every_mapped_record(patcher, rom):
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    mapped = patcher.map_rosters(_league_data(("BOS", 40), ("CHI", 40)), roster_counts=counts)
    recorded = {slot: team.num_forwards for slot, team in mapped.teams.items()}
    assert recorded == {BOS_SLOT: 15, CHI_SLOT: 14}


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

    Without the guard the empty roster arriving second wipes the populated one and
    `patch` reports success having left the 1994 roster in place.
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


def test_every_record_reaches_the_image_however_long_the_roster(patcher, rom, tmp_path):
    """The block is rebuilt around the roster, so nothing is dropped: 23 records
    that the slot's 1993 block could not hold all reach the image."""
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 23)})
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.players_patched == 23
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [f"Written {i:02d}" for i in range(23)]


def test_a_slot_whose_old_block_held_nobody_gets_its_whole_roster(patcher, tmp_path):
    empty = fixture.write_nhl94_snes_rom(tmp_path / "empty.sfc", players_per_team=0)
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    result = patcher.patch(rom_path=empty, output_path=out, rosters=mapped)
    assert (result.teams_patched, result.players_patched) == (1, 10)
    names, _ = _read_back(out, BOS_SLOT)
    assert len(names) == 10
    image = out.read_bytes()
    assert image[_base(image, BOS_SLOT) + fixture.PLAYER_COUNT_OFFSET] == (8 << 4) | 0


def test_an_empty_roster_never_reaches_the_writer(patcher, rom, tmp_path):
    """An empty list would wipe the slot's 1994 roster."""
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
    clamp: 2 + 12 + 7 is 21 and all 21 reach the image.
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


def test_the_header_counts_the_records_written(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 23)})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    image = out.read_bytes()
    base = _base(image, BOS_SLOT)
    assert image[base + fixture.PLAYER_COUNT_OFFSET] == (14 << 4) | 7
    # Defencemen 17-23 (1-based); the three pairs on the lines are 17-22.
    table = image[base + fixture.LINE_ASSIGN_OFFSET : base + fixture.TEAM_HEADER_SIZE]
    assert max(table) == 22


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
    image = out.read_bytes()
    assert image[_base(image, BOS_SLOT) + fixture.PLAYER_COUNT_OFFSET] == (7 << 4) | 0


def test_an_out_of_range_slot_index_is_dropped_after_the_json_boundary(patcher, rom, tmp_path):
    """A negative key would read the bytes before the pointer table as a pointer."""
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(
        game_id="nhl94-snes",
        teams={-1: _team_record(-1, 5), TEAM_COUNT: _team_record(TEAM_COUNT, 5)},
    )
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    assert result.teams_patched == 0
    # And nothing was written for them either. `teams_patched` alone does not say
    # that: slot -1 gets past the writer's only bounds test, `team_index >=
    # TEAM_COUNT`, resolves the four bytes *before* the pointer table as a pointer,
    # and lands a two-byte terminator wherever that points.
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
    with no terminator yields a region reaching past the image. `patch` turns the
    resulting `IndexError` into `RomError`; carrying on would finalize a
    half-written ROM under a success return.
    """
    rom = fixture.build_nhl94_snes_rom()
    start = fixture.team_base(BOS_SLOT) + fixture.TEAM_HEADER_SIZE
    # A record chain with no terminator, at the same 47-byte stride the scan walks.
    # 47 does not divide the distance from `start` to the end of the file, so the last
    # step lands past it -- and it is the overshoot, not the missing terminator, that
    # makes the region unwritable.
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


def test_the_cli_threads_the_roms_counts_through_the_same_two_hops(rom, tmp_path):
    """The hop `cli/commands.py` did not make, and what it cost.

    `map_rosters(roster_counts=...)` is a keyword-only extra on top of the ABC's
    signature, so a caller working through `Patcher.map_rosters` could simply not
    pass it -- and the CLI did not, so every command-line run selected and
    headered `DEFAULT_ROSTER_COUNTS` for all 28 slots whatever byte 17 said.

    Slot 1 holds 15 forwards and 8 defencemen (the cartridge's 15, the image's
    8) and slot 4 14 and 8, so neither equals the 14/7 default and the two do
    not equal each other -- a run that fell back would write one number into
    both.
    """
    from retro_roster_patcher.cli.__main__ import main

    rosters = tmp_path / "rosters.json"
    rosters.write_text(
        json.dumps(
            {
                "league": {"id": 1, "name": "NHL", "season": 2025},
                "teams": [
                    {
                        "team": {"id": i + 1, "name": code, "code": code},
                        "players": [
                            {
                                "id": i * 100 + n,
                                "name": f"P{n:02d}",
                                "position": pos,
                                "number": n + 1,
                            }
                            for n, pos in enumerate(["G"] * 4 + ["C"] * 20 + ["D"] * 12)
                        ],
                        "extra": {"leaders": {}},
                    }
                    for i, code in enumerate(("BOS", "CHI"))
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out.sfc"
    code = main(
        [
            "--json",
            "patch",
            "--game",
            "nhl94-snes",
            "--rom",
            str(rom),
            "--out",
            str(out),
            "--rosters",
            str(rosters),
            "--cache-dir",
            str(tmp_path / "cache"),
        ]
    )
    assert code == 0
    written = out.read_bytes()
    bos = _base(written, BOS_SLOT) + fixture.PLAYER_COUNT_OFFSET
    chi = _base(written, CHI_SLOT) + fixture.PLAYER_COUNT_OFFSET
    assert written[bos] == (15 << 4) | 8
    assert written[chi] == (14 << 4) | 8
    # And the image really was patched, so neither byte is the input's by luck.
    assert written != rom.read_bytes()


def test_a_patched_rom_can_be_analysed_and_patched_again(patcher, rom, tmp_path):
    """The output stays a valid input: its blocks parse, and the room the first
    patch left is found again."""
    once = tmp_path / "once.sfc"
    twice = tmp_path / "twice.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=rom, output_path=once, rosters=mapped)
    assert patcher.analyze_rom(once).is_valid is True
    result = patcher.patch(rom_path=once, output_path=twice, rosters=mapped)
    assert result.teams_patched == 1
    names, _ = _read_back(twice, BOS_SLOT)
    assert names == [f"Written {i:02d}" for i in range(10)]


def _ranked_squad():
    """Two goalies, nine forwards and five defencemen, named by id."""
    positions = ["G"] * 2 + ["C", "LW", "RW"] * 3 + ["D"] * 5
    return [
        Player(id=n, name=f"P{n:02d}", position=pos, number=n + 1)
        for n, pos in enumerate(positions)
    ]


def _ranked_league(players):
    """One BOS roster whose leaders rank every player by id, highest first."""
    data = _league_data(("BOS", 0))
    data.teams[0].players = players
    data.teams[0].extra["leaders"] = {
        str(p.id): {"PTS": p.id, "SV%": p.id, "W": p.id} for p in players
    }
    return data


def _named_league(goalies, forwards, defence):
    """One BOS roster listed and ranked best first, each player named by position
    and rank: G1, G2, LW1, C1, RW1, LW2, ..., D1, D2, ..."""
    positions = ["G"] * goalies + (["LW", "C", "RW"] * 6)[:forwards] + ["D"] * defence
    players = [
        Player(id=n, name=f"{pos}{positions[: n + 1].count(pos)}", position=pos, number=n + 1)
        for n, pos in enumerate(positions)
    ]
    data = _league_data(("BOS", 0))
    data.teams[0].players = players
    data.teams[0].extra["leaders"] = {
        str(p.id): {"PTS": 99 - p.id, "W": 99 - p.id} for p in players
    }
    return data


def _small_counts():
    """Default counts everywhere but BOS, which holds 2 G, 5 F and 3 D."""
    counts = [list(DEFAULT_ROSTER_COUNTS) for _ in range(TEAM_COUNT)]
    counts[BOS_SLOT] = [2, 5, 3]
    return counts


def test_order_as_given_keeps_the_slots_blocks_and_the_callers_order_inside_them(patcher):
    squad = _ranked_squad()
    given = squad[::2] + squad[1::2]

    mapped = patcher.map_rosters(
        _ranked_league(given), roster_counts=_small_counts(), order_as_given=True
    )

    goalies = [p for p in given if p.position == "G"][:2]
    forwards = [p for p in given if p.position not in ("G", "D")][:5]
    defence = [p for p in given if p.position == "D"][:3]
    assert [r.name for r in mapped.teams[BOS_SLOT].players] == [
        p.name for p in goalies + forwards + defence
    ]


def test_order_as_given_tops_up_nothing_and_counts_what_each_block_got(patcher):
    """BOS holds 5 F and 3 D here, and the list has 3 forwards.

    Two more defencemen used to be topped up after the D block under a header
    still claiming 5 forwards, which made D1 and D2 forwards to the line table.
    """
    mapped = patcher.map_rosters(
        _named_league(2, 3, 6), roster_counts=_small_counts(), order_as_given=True
    )
    team = mapped.teams[BOS_SLOT]
    assert [r.name for r in team.players] == ["G1", "G2", "LW1", "C1", "RW1", "D1", "D2", "D3"]
    assert (team.num_forwards, team.num_defensemen) == (3, 3)


def test_order_as_given_skips_a_team_with_one_goalie(patcher):
    # The line table counts forwards from record 3, so a lone goalie would put
    # the first forward in the backup's place and shift every index after it.
    # The slot keeps the cartridge's team; the other teams are still mapped.
    seen = []
    patcher.on_status = seen.append
    data = _named_league(1, 12, 6)
    data.teams += _league_data(("CHI", 20)).teams
    mapped = patcher.map_rosters(data, order_as_given=True)
    assert sorted(mapped.teams) == [CHI_SLOT]
    assert any("BOS skipped: 1 goalie(s)" in line for line in seen)


def test_order_as_given_still_skips_an_empty_roster(patcher):
    mapped = patcher.map_rosters(_league_data(("BOS", 0)), order_as_given=True)
    assert mapped.teams[BOS_SLOT].players == []


def test_without_order_as_given_the_input_order_is_ignored(patcher):
    squad = _ranked_squad()
    counts = _small_counts()
    forward = patcher.map_rosters(_ranked_league(squad), roster_counts=counts)
    backward = patcher.map_rosters(_ranked_league(squad[::-1]), roster_counts=counts)
    assert [r.name for r in forward.teams[BOS_SLOT].players] == [
        r.name for r in backward.teams[BOS_SLOT].players
    ]


def test_the_suggested_order_puts_the_starting_six_first(patcher):
    roster = _named_league(3, 16, 9).teams[0]
    order = [p.name for p in patcher.suggest_squad_order(roster)]
    # G1, the first trio in its LW, C, RW slots and the first pair, then G2.
    assert order[:7] == ["G1", "LW1", "C1", "RW1", "D1", "D2", "G2"]
    assert sorted(order) == sorted(p.name for p in roster.players)


def test_the_suggested_order_maps_to_the_ranked_selection(patcher):
    # Moving the six to the front changes no block's order, so the ROM is the one
    # the ranked selection writes.
    data = _named_league(3, 16, 9)
    ranked = patcher.map_rosters(data).teams[BOS_SLOT].players
    data.teams[0].players = patcher.suggest_squad_order(data.teams[0])
    given = patcher.map_rosters(data, order_as_given=True).teams[BOS_SLOT].players
    assert [r.name for r in given] == [r.name for r in ranked]


def test_the_best_line_is_the_lists_starting_six(patcher, rom, tmp_path):
    """The editor's first six are the six the cartridge starts.

    BOS's image holds 13 F and 8 D and the list has 11 forwards, so the header
    must say 11 for the Best record to find the defencemen.
    """
    data = _named_league(2, 11, 10)
    data.teams[0].players = patcher.suggest_squad_order(data.teams[0])
    counts = patcher.analyze_rom(rom).extra["roster_counts"]
    mapped = patcher.map_rosters(data, roster_counts=counts, order_as_given=True)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    names, _ = _read_back(out, BOS_SLOT)
    image = out.read_bytes()
    base = _base(image, BOS_SLOT)
    assert image[base + fixture.PLAYER_COUNT_OFFSET] == (11 << 4) | 8
    best = image[base + fixture.LINE_ASSIGN_OFFSET : base + fixture.LINE_ASSIGN_OFFSET + 8]
    # [G, LD, RD, LW, C, RW], 1-based, then EA and the record's zero byte.
    assert [names[i - 1] for i in best[:6]] == ["G1", "D1", "D2", "LW1", "C1", "RW1"]
    assert best[7] == 0


SEA_SLOT = 26
VGK_SLOT = 27


def test_seattle_and_vegas_are_fetched_and_take_the_all_star_slots(patcher):
    patcher.api = FakeApi(
        [Team(id=124292, name="Seattle Kraken", code="SEA"), Team(id=37, name="Vegas", code="VGK")]
    )
    data = patcher.fetch(season=2026)
    assert [roster.team.code for roster in data.teams] == ["SEA", "VGK"]
    assert sorted(patcher.map_rosters(data).teams) == [SEA_SLOT, VGK_SLOT]


def test_espn_standings_set_each_teams_strength(tmp_path):
    """One standings request; goals for rank the high nibble (attack) and goals
    against the low one (leakiness), over 0-7, for the score simulator -- each
    club against the whole league, not against the teams this fetch maps."""
    transport = _standings(
        _standings_body(
            ("BOS", 272, 250, 82),
            ("CHI", 210, 290, 82),
            ("SEA", 240, 240, 82),
            ("EDM", 300, 200, 82),
            ("CBJ", 180, 310, 82),
        )
    )
    p = NHL94SNESPatcher(cache_dir=tmp_path / "cache", transport=transport)
    p.api = FakeApi(_teams() + [Team(id=124292, name="Seattle Kraken", code="SEA")])
    data = p.fetch(season=2026)
    assert transport.urls == [
        "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings?season=2026"
    ]
    # Five clubs: ranks 0-4 become 0, 1, 3, 4, 6.
    assert [roster.extra["strength"] for roster in data.teams] == [0x43, 0x14, 0x31]
    data.teams = data.teams[:1]
    assert p.map_rosters(data).teams[BOS_SLOT].strength == 0x43


def test_without_standings_the_strength_is_left_to_the_cartridge(patcher):
    seen = []
    patcher.on_status = seen.append
    teams = patcher.map_rosters(patcher.fetch(season=2026)).teams
    assert teams[BOS_SLOT].strength is None
    assert "Team strengths kept from the cartridge: no goals for and against" in seen


def test_nhl_club_stats_give_the_strength_without_a_request(tmp_path):
    """Every skater's goals and every goalie's goals against, per game played."""
    p = NHL94SNESPatcher(cache_dir=tmp_path, provider="nhl")
    p.api = FakeApi(_teams())
    goals = {"BOS": (40, 20), "CHI": (20, 40)}
    p.api.get_hockey_team_leaders = lambda code, season=None: {
        "1": {"G": goals[code][0], "GP": 10},
        "2": {"G": 0, "GP": 4},
        "3": {"GA": goals[code][1], "GP": 10},
    }
    teams = p.map_rosters(p.fetch(season=2025)).teams
    assert (teams[BOS_SLOT].strength, teams[CHI_SLOT].strength) == (0x40, 0x04)


def test_a_partial_stat_line_takes_the_teams_lowest_listed_value(patcher):
    """ESPN lists each category's top 25: a player missing from the points list
    scored fewer than its last entry, not the position's default (3 here)."""
    data = _league_data(("BOS", 6))
    players = data.teams[0].players
    leaders = data.teams[0].extra["leaders"]
    leaders[str(players[2].id)] = {"G": 40, "A": 50, "PTS": 90, "PIM": 80}
    leaders[str(players[4].id)] = {"G": 2, "A": 13, "PTS": 15, "PIM": 4}
    leaders[str(players[3].id)] = {"+/-": 5}

    def partial():
        team = patcher.map_rosters(data).teams[BOS_SLOT]
        return next(r for r in team.players if r.name == "BOS Player03").attributes

    assert partial().off_awareness == 1  # the lower of the two listed, 15
    leaders[str(players[2].id)]["PTS"] = 0
    assert partial().off_awareness == 0


def _skaters(forwards, defence, goalies=2):
    players = [Player(id=n, name=f"G{n}", position="G", number=n + 1) for n in range(goalies)]
    players += [
        Player(id=100 + n, name=f"F{n}", position="C", number=n + 1) for n in range(forwards)
    ]
    players += [
        Player(id=200 + n, name=f"D{n}", position="D", number=n + 1) for n in range(defence)
    ]
    data = _league_data(("BOS", 0))
    data.teams[0].players = players
    return data


def test_the_ranked_path_counts_what_the_selection_filled(patcher):
    """A 13 F / 8 D roster against a 15 F / 7 D slot: the header says 13 and 7,
    so the line table finds every defenceman where it put him, and nobody of
    another position is topped up into a block."""
    counts = [list(DEFAULT_ROSTER_COUNTS) for _ in range(TEAM_COUNT)]
    counts[BOS_SLOT] = [2, 15, 7]
    team = patcher.map_rosters(_skaters(13, 8, goalies=3), roster_counts=counts).teams[BOS_SLOT]
    assert (team.num_forwards, team.num_defensemen) == (13, 7)
    positions = [r.name[0] for r in team.players]
    assert positions == ["G"] * 2 + ["F"] * 13 + ["D"] * 7


def test_the_ranked_path_skips_a_team_with_one_goalie(patcher, rom, tmp_path):
    """One short team costs its own slot, which keeps the cartridge's roster,
    not the whole league's patch."""
    seen = []
    patcher.on_status = seen.append
    data = _skaters(14, 7, goalies=1)
    data.teams += _league_data(("CHI", 20)).teams
    mapped = patcher.map_rosters(data)
    assert sorted(mapped.teams) == [CHI_SLOT]
    assert any("BOS skipped: 1 goalie(s)" in line for line in seen)
    out = tmp_path / "out.sfc"
    assert patcher.patch(rom_path=rom, output_path=out, rosters=mapped).teams_patched == 1
    names, _ = _read_back(out, BOS_SLOT)
    assert names == [fixture.player_name(BOS_SLOT, i) for i in range(fixture.ROSTER_PLAYERS)]


def test_patch_fixes_the_hot_cold_sign_bug_by_default(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    image = out.read_bytes()
    at = fixture.HOT_COLD_OFFSET
    assert (image[at + 8], image[at + 12]) == (0x20, 0xC0)


def test_hot_cold_fix_false_keeps_the_retail_behaviour(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped, hot_cold_fix=False)
    at = fixture.HOT_COLD_OFFSET
    assert out.read_bytes()[at : at + 14] == fixture.HOT_COLD_ROUTINE


def test_an_image_without_the_routine_says_the_fix_was_skipped(patcher, tmp_path):
    rom = fixture.build_nhl94_snes_rom()
    rom[fixture.HOT_COLD_OFFSET] = 0xEA
    odd = tmp_path / "odd.sfc"
    odd.write_bytes(bytes(rom))
    seen = []
    patcher.on_status = seen.append
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: _team_record(BOS_SLOT, 10)})
    patcher.patch(rom_path=odd, output_path=tmp_path / "out.sfc", rosters=mapped)
    assert any("Hot/cold fix skipped" in line for line in seen)


def test_patch_writes_the_team_bytes_14_to_18(patcher, rom, tmp_path):
    out = tmp_path / "out.sfc"
    team = _team_record(BOS_SLOT, 23)
    team.strength = 0x63
    mapped = MappedRosters(game_id="nhl94-snes", teams={BOS_SLOT: team})
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)
    image = out.read_bytes()
    base = _base(image, BOS_SLOT)
    assert image[base + 14 : base + 17] == b"\x63\x00\x00"
    # Every `_records` player has the default attributes: the five skaters of
    # line record 0 rate 60, its goalie (read on goalie weights) 50.
    assert image[base + 18] == round((50 + 5 * 60) / 6) - 2


def test_an_accented_name_reaches_the_image_as_ascii(patcher, rom, tmp_path):
    data = _league_data(("BOS", 3))
    data.teams[0].players[2] = Player(id=2, name="Tim Stützle", position="C", number=18)
    out = tmp_path / "out.sfc"
    patcher.patch(rom_path=rom, output_path=out, rosters=patcher.map_rosters(data))
    names, _ = _read_back(out, BOS_SLOT)
    assert "Tim Stutzle" in names
