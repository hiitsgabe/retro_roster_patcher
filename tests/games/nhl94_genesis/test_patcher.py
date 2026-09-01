"""The NHL94 Genesis patcher against the unified interface.

The reader, writer and stat mapper below it are a faithful port of an untested
upstream, and Tasks 15-16 pinned three of its defects with `pytest.raises(
IndexError)`. Those exceptions are exactly what this layer exists to absorb, so
the interesting tests here are the ones that feed it a broken image and demand
`RomError` or `RomInfo(is_valid=False)` back.

Every read-back of a patched ROM goes through a *fresh* reader on the output
path. `NHL94GenesisRomWriter.__init__` builds its own reader over the *input*
file, so `writer.reader.data` is the pre-write image for the writer's whole
lifetime and asserting against it would assert nothing.

The progress sequences are asserted whole rather than bounded. A patcher that
reported `(0.0, ...)` forever, or that emitted its 26 slots in the wrong order,
satisfies `all(0.0 <= pct <= 1.0)`.
"""

import struct
import subprocess
import sys
import textwrap

import pytest

from retro_roster_patcher.core.errors import ApiError, CapabilityError, RomError
from retro_roster_patcher.core.models import SlotMapping
from retro_roster_patcher.games.nhl94_genesis.models import TEAM_COUNT, NHL94GenPlayerRecord
from retro_roster_patcher.games.nhl94_genesis.patcher import (
    MAX_PLAYERS_PER_SLOT,
    NHL94GenesisPatcher,
)
from retro_roster_patcher.games.nhl94_genesis.rom_reader import NHL94GenesisRomReader
from retro_roster_patcher.games.nhl94_genesis.rom_writer import NHL94GenesisRomWriter
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from retro_roster_patcher.sports.nhl import NhlApiClient
from tests.fixtures import synthetic_rom

# BOS and CHI are the two slots the fixture API covers, from
# `MODERN_NHL_TO_NHL94_GEN`. NHL94_GEN_TEAM_ORDER names them Boston and Chicago.
BOS_SLOT = 1
CHI_SLOT = 4


class FakeApi:
    """Stands in for EspnClient / NhlApiClient.

    Records what it was asked for, because the ESPN and NHL branches of `fetch`
    key on different things — team id versus three-letter code — and only one of
    them forwards the season.
    """

    def __init__(self, teams, squad_size=15, leaders=None):
        self._teams = teams
        self._squad_size = squad_size
        self._leaders = {"0": {"goals": 40}} if leaders is None else leaders
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
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi(_teams())
    return p


def _league_data(players, code="BOS", season=2025):
    """One team's worth of `LeagueData`, bypassing `fetch` and its fake API."""
    return LeagueData(
        league=League(id=0, name="NHL", country="US", season=season),
        teams=[
            TeamRoster(
                team=Team(id=1, name="Boston Bruins", code=code),
                players=players,
                extra={"leaders": {}},
            )
        ],
    )


def _read_back(path, slot):
    reader = NHL94GenesisRomReader(str(path))
    assert reader.load() is True
    return reader.read_team_roster(slot)


# ── Registration ─────────────────────────────────────────────────────────


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("nhl94-genesis")
    assert cls is NHL94GenesisPatcher
    assert cls.platform == "genesis"
    assert cls.sport == "hockey"
    assert cls.requires_slot_mapping is False
    assert cls.requires_api_key is False
    assert cls.providers == ("espn", "nhl")


def test_importing_the_package_root_is_what_registers_the_game(tmp_path):
    # The registration is a side-effect import at the bottom of the package
    # __init__. Dropping it leaves `get_patcher` green for anyone who imported
    # the game module first — which every other test in this file does — and
    # broken for the CLI, which only imports the root.
    #
    # Hence the subprocess: registration is a global side effect, so in this
    # process the game module is long since imported and any same-process
    # version of this test passes with the import deleted. The child imports the
    # root and nothing else. It also asserts on `get_patcher`, the behaviour,
    # rather than on the `_nhl94_genesis` alias — renaming the alias, or dropping
    # it for a plain `from .games import nhl94_genesis`, is a refactor with the
    # same registration side effect and must not fail this.
    source = textwrap.dedent(
        """
        import retro_roster_patcher

        cls = retro_roster_patcher.get_patcher("nhl94-genesis")
        print(cls.__module__ + ":" + cls.__name__)
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", source],
        # `-c` puts the child's cwd on its `sys.path`, so an inherited cwd that
        # happens to hold a `retro_roster_patcher/` directory would shadow the
        # installed package. `tmp_path` holds nothing.
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == (
        "retro_roster_patcher.games.nhl94_genesis.patcher:NHL94GenesisPatcher"
    )


def test_an_unknown_provider_is_rejected(tmp_path):
    with pytest.raises(CapabilityError, match="espn"):
        NHL94GenesisPatcher(cache_dir=tmp_path, provider="statsapi")


def test_the_default_provider_is_espn_and_nhl_can_be_chosen(tmp_path):
    assert NHL94GenesisPatcher(cache_dir=tmp_path / "a").provider == "espn"
    assert NHL94GenesisPatcher(cache_dir=tmp_path / "b", provider="nhl").provider == "nhl"


def test_each_provider_builds_its_own_client(tmp_path):
    # Every other test in this file swaps `patcher.api` for a fake, so without
    # this one the branch that chooses between the two real clients is never
    # executed and inverting it changes nothing.
    #
    # These are live clients built with `transport=None`, and so are the ones the
    # `patcher` fixture builds before it overwrites `p.api`: 41 of the 47 tests
    # in this file construct one that way, one more injects its own transport,
    # and 5 construct none. The autouse guard in `tests/conftest.py` makes the
    # fall-through to the real transport raise `TransportLeak` for all 41 — today
    # both constructors only assign attributes and makedirs, and that is what
    # keeps it true.
    espn = NHL94GenesisPatcher(cache_dir=tmp_path / "a")
    nhl = NHL94GenesisPatcher(cache_dir=tmp_path / "b", provider="nhl")
    assert type(espn.api) is EspnClient
    assert type(nhl.api) is NhlApiClient


@pytest.mark.parametrize("provider", ["espn", "nhl"])
def test_the_client_is_given_the_cache_directory_and_the_transport(tmp_path, provider):
    # Both branches, because they construct different classes: a transport that
    # reaches only one of them leaves the other free to open a real socket.
    def transport(url, headers, timeout):
        raise AssertionError("no test may reach the network")

    seen = []
    p = NHL94GenesisPatcher(
        cache_dir=tmp_path / provider,
        provider=provider,
        on_status=seen.append,
        transport=transport,
    )
    assert p.api.cache_dir == str(tmp_path / provider)
    assert p.api._transport is transport
    assert p.api.on_status == seen.append


def test_the_cache_directory_exists_once_the_patcher_is_constructed(tmp_path):
    # Constructing a patcher has to be enough to make the cache usable. The
    # patcher itself no longer calls mkdir: both clients do it in their own
    # constructors, and a second call here was invisible to every assertion.
    cache = tmp_path / "nested" / "cache"
    NHL94GenesisPatcher(cache_dir=cache)
    assert cache.is_dir() is True


# ── analyze_rom ──────────────────────────────────────────────────────────


def test_analyze_reports_twenty_six_slots(tmp_path, patcher):
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    info = patcher.analyze_rom(rom)

    assert info.game_id == "nhl94-genesis"
    assert info.is_valid is True
    assert info.size == synthetic_rom.ROM_SIZE
    # `path` crosses the NDJSON boundary verbatim and the two branches of
    # `analyze_rom` source it differently — the reader's own `rom_path` here,
    # `str(rom_path)` on the IndexError branch below. Asserted on both so they
    # cannot drift apart.
    assert info.path == str(rom)
    assert len(info.slots) == 26
    assert info.slots[BOS_SLOT].current_name == "Boston"


def test_analyze_reports_the_rom_name_and_the_canonical_name_separately(tmp_path, patcher):
    # The fixture writes "St Louis" into slot 20's strings section against the
    # "St. Louis" in NHL94_GEN_TEAM_ORDER, so a translation that filled both
    # RomSlot fields from one source cannot pass this.
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    slot = patcher.analyze_rom(rom).slots[20]
    assert (slot.index, slot.current_name, slot.display_name) == (20, "St Louis", "St. Louis")


def test_analyzing_a_missing_file_raises_rom_error(tmp_path, patcher):
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "nope.bin")


def test_a_readable_file_that_is_not_nhl94_is_reported_not_raised(tmp_path, patcher):
    # `core.patcher.analyze_rom` promises RomError only for missing or unreadable
    # files: `retro-roster analyze` probes every registered patcher against one
    # ROM, so "not my game" has to be a return value.
    other = tmp_path / "other.bin"
    other.write_bytes(b"\x00" * 1024)
    info = patcher.analyze_rom(other)
    assert (info.is_valid, info.size, info.slots) == (False, 1024, [])


def test_a_pointer_that_only_get_info_dereferences_does_not_escape_as_index_error(
    tmp_path, patcher
):
    # DEFECT (pinned in test_rom_reader.py): `validate` bounds-checks pointer 0
    # only, while `get_info` dereferences all 26 through `_read_team_city`. A
    # pointer inside the file but in its last five bytes validates and then
    # overruns the `_read_u16_be(team_base + 4)` that finds the strings section.
    # The reader is a faithful port and stays that way, so this layer catches the
    # IndexError and answers the contract: a RomInfo, never an exception.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    struct.pack_into(">I", rom, synthetic_rom.POINTER_TABLE_OFFSET + 4, 0x0FFFFF)
    path = tmp_path / "late_pointer.bin"
    path.write_bytes(bytes(rom))

    reader = NHL94GenesisRomReader(str(path))
    assert reader.load() is True
    assert reader.validate() is True
    with pytest.raises(IndexError):
        reader.get_info()

    info = patcher.analyze_rom(path)
    assert (info.is_valid, info.size, info.slots) == (False, synthetic_rom.ROM_SIZE, [])
    assert info.game_id == "nhl94-genesis"
    assert info.path == str(path)


# ── fetch ────────────────────────────────────────────────────────────────


def test_fetch_returns_league_data_for_the_teams_that_have_slots(patcher):
    data = patcher.fetch(season=2025)

    assert isinstance(data, LeagueData)
    # The whole `League`, not just name and season: `LeagueData` is what crosses
    # the fetch → JSON → map boundary, and `id` and `country` are synthesised
    # here — this game has no league endpoint to read them from — so nothing else
    # in the codebase would notice them changing.
    assert data.league == League(id=0, name="NHL", country="US", season=2025)
    assert [tr.team.code for tr in data.teams] == ["BOS", "CHI"]


def test_fetch_carries_leader_stats_in_extra(patcher):
    data = patcher.fetch(season=2025)
    assert data.teams[0].extra["leaders"] == {"0": {"goals": 40}}


def test_fetch_reports_progress(patcher):
    seen = []
    patcher.fetch(season=2025, on_progress=lambda pct, msg: seen.append((pct, msg)))
    assert seen == [
        (0.0, "Fetching Boston Bruins..."),
        (0.5, "Fetching Chicago Blackhawks..."),
        (1.0, "Complete"),
    ]


def test_fetch_forwards_status_messages(tmp_path):
    seen = []
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache", on_status=seen.append)
    p.api = FakeApi(_teams())
    p.fetch(season=2025)
    assert seen == ["Fetching NHL teams..."]


def test_the_espn_provider_is_asked_by_team_id_and_ignores_the_season(patcher):
    # ESPN's roster endpoint serves the current squad only; passing a season
    # would silently promise history it cannot deliver.
    patcher.fetch(season=2025)
    assert patcher.api.squad_calls == [(1, None), (2, None)]
    assert patcher.api.leader_calls == [(1, None), (2, None)]


def test_the_nhl_provider_is_asked_by_team_code_and_season(tmp_path):
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache", provider="nhl")
    p.api = FakeApi(_teams())
    p.fetch(season=1994)
    assert p.api.squad_calls == [("BOS", 1994), ("CHI", 1994)]
    assert p.api.leader_calls == [("BOS", 1994), ("CHI", 1994)]


def test_fetch_consults_the_declared_api_key_capability(patcher):
    # This game needs no key, so `check_api_key` is a no-op today and deleting
    # the call from `fetch` is invisible. Flipping the capability on the instance
    # is what makes the call observable — and the day the capability changes for
    # real, `fetch` is already wired to honour it.
    patcher.requires_api_key = True
    with pytest.raises(CapabilityError, match="requires an api_key"):
        patcher.fetch(season=2025)
    patcher.api_key = "dummy-not-a-real-key"
    assert [tr.team.code for tr in patcher.fetch(season=2025).teams] == ["BOS", "CHI"]


def test_fetch_with_no_teams_raises_api_error(tmp_path):
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([])
    with pytest.raises(ApiError, match="no NHL teams"):
        p.fetch(season=2025)


def test_fetch_with_no_team_matching_a_slot_raises_api_error(tmp_path):
    # Distinct from the empty-list case: the provider answered, but the 1994 ROM
    # has no room for any of what it returned.
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi([Team(id=9, name="Seattle Kraken", code="SEA")])
    with pytest.raises(ApiError, match="ROM slot"):
        p.fetch(season=2025)


# ── map_rosters ──────────────────────────────────────────────────────────


def test_map_rosters_keys_by_rom_slot_index(patcher):
    mapped = patcher.map_rosters(patcher.fetch(season=2025))

    assert mapped.game_id == "nhl94-genesis"
    assert mapped.filled_slots() == [BOS_SLOT, CHI_SLOT]
    assert sorted(mapped.teams) == [BOS_SLOT, CHI_SLOT]
    assert [len(mapped.teams[slot]) for slot in (BOS_SLOT, CHI_SLOT)] == [15, 15]


def test_map_rosters_produces_the_writer_s_record_type(patcher):
    records = patcher.map_rosters(patcher.fetch(season=2025)).teams[BOS_SLOT]
    first = records[0]
    assert isinstance(first, NHL94GenPlayerRecord)
    assert (first.name, first.position, first.jersey_number, first.is_goalie) == (
        "P0",
        "C",
        1,
        False,
    )


def test_map_rosters_selects_at_most_the_declared_cap(patcher):
    patcher.api = FakeApi(_teams(), squad_size=40)
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    assert MAX_PLAYERS_PER_SLOT == 23
    assert [len(mapped.teams[slot]) for slot in (BOS_SLOT, CHI_SLOT)] == [23, 23]


def test_map_rosters_drops_a_team_with_no_rom_slot(patcher):
    # `fetch` already filters these out, so the guard in `map_rosters` is only
    # reachable through the split-process path: fetch to JSON, map from JSON.
    mapped = patcher.map_rosters(_league_data([Player(id=1, name="X", position="C")], code="SEA"))
    assert mapped.teams == {}


def test_map_rosters_survives_a_team_roster_with_no_leaders_key(patcher):
    # `fetch` always writes `extra={"leaders": ...}`, but `map_rosters` is a
    # public entry point and the module docstring advertises the split-process
    # path — fetch to JSON in one process, map from JSON in another. A hand-built
    # or trimmed `LeagueData` arrives with `extra={}`, and without the `or {}` on
    # the lookup the very next line raises AttributeError on None.
    data = LeagueData(
        league=League(id=0, name="NHL", country="US", season=2025),
        teams=[
            TeamRoster(
                team=Team(id=1, name="Boston Bruins", code="BOS"),
                players=[Player(id=i, name=f"P{i}", position="C", number=i + 1) for i in range(3)],
                extra={},
            )
        ],
    )

    records = patcher.map_rosters(data).teams[BOS_SLOT]

    assert [r.name for r in records] == ["P0", "P1", "P2"]


def test_the_leader_stats_order_the_roster_and_shape_the_attributes(patcher):
    # Three separate paths for the same dict, all of them invisible to a test
    # that supplies no stats: `select_roster` sorts on PTS, `map_player` scales
    # the attributes from the per-player entry, and the entry has to be looked up
    # by the player's own id. Zero out any one of them and this test fails.
    players = [Player(id=i, name=f"C{i}", position="C", number=i + 1) for i in range(4)]
    leaders = {
        "0": {"G": 5, "A": 5, "PTS": 10},
        "1": {"G": 40, "A": 50, "PTS": 90},
        "2": {"G": 20, "A": 30, "PTS": 50},
    }
    data = _league_data(players)
    data.teams[0].extra["leaders"] = leaders

    records = patcher.map_rosters(data).teams[BOS_SLOT]

    # Descending points; the player with no entry at all sorts last.
    assert [r.name for r in records] == ["C1", "C2", "C0", "C3"]
    # shot_power is `_scale(G, 0, 40)`, against a positional default of 3.
    assert [r.attributes.shot_power for r in records] == [6, 3, 1, 3]


def test_map_rosters_refuses_a_slot_the_rom_does_not_have(patcher, monkeypatch):
    # `MODERN_NHL_TO_NHL94_GEN` only holds 0-25 today, so the range guard is
    # unreachable through the real table. It is still worth keeping: `patch` is
    # the only other thing standing between an out-of-range key and the writer's
    # missing lower bound on `team_index`, and MappedRosters is a public type.
    #
    # 26 is the value the guard exists for — one past the last real slot, and the
    # only one that tells `< TEAM_COUNT` apart from `<= TEAM_COUNT`. 99 and -1 are
    # far enough outside that either spelling rejects them.
    for slot in (99, 26, -1):
        monkeypatch.setattr(patcher.mapper, "get_team_slot", lambda code, s=slot: s)
        assert patcher.map_rosters(_league_data([Player(id=1, name="X", position="C")])).teams == {}


def test_map_rosters_rejects_a_slot_mapping(patcher):
    data = patcher.fetch(season=2025)
    with pytest.raises(CapabilityError, match="does not use slot mappings"):
        patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=1)])


def test_an_unrecognised_position_is_carried_through_and_counted_as_a_forward(tmp_path, patcher):
    # DEFECT (stat_mapper): `select_roster` sorts into C/LW/RW/D/G and lets
    # anything else fall through to `leftover`, where it is appended after the
    # goalies rather than in a line. `write_team_header` then derives
    # forward_count as "everything that is not a goalie or a D", so an
    # unexpected abbreviation silently becomes a forward and shifts the lines
    # table. Reported, not fixed: the mapper is a faithful port.
    #
    # ESPN preserves the exact position abbreviation and the NHL API maps L/R to
    # LW/RW, so today only an upstream change produces this. Pinned so that
    # change is visible.
    players = [Player(id=i, name=f"W{i}", position="W", number=i + 1) for i in range(6)]
    mapped = patcher.map_rosters(_league_data(players))
    assert [r.position for r in mapped.teams[BOS_SLOT]] == ["W"] * 6
    assert [r.is_goalie for r in mapped.teams[BOS_SLOT]] == [False] * 6

    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    # Count byte at ratings+3: high nibble forwards, low nibble defence.
    count_off = synthetic_rom.team_base(BOS_SLOT) + synthetic_rom.SEC_RATINGS + 3
    assert out.read_bytes()[count_off] == 0x60


# ── patch ────────────────────────────────────────────────────────────────


def test_patch_writes_a_rom_that_still_validates(tmp_path, patcher):
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    mapped = patcher.map_rosters(patcher.fetch(season=2025))

    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert result.output_path == str(out)
    assert result.teams_patched == 2
    assert result.players_patched == 30
    assert patcher.analyze_rom(out).is_valid is True


def test_patch_writes_the_mapped_names_into_the_right_slots(tmp_path, patcher):
    # `teams_patched` and `players_patched` are counters the patcher computes
    # itself; without a read-back a writer that wrote every team into slot 0
    # would report the same numbers.
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    expected = [f"P{i}" for i in range(15)]
    assert _read_back(out, BOS_SLOT)[0] == expected
    assert _read_back(out, CHI_SLOT)[0] == expected
    # An untouched slot still holds the fixture's own self-identifying names.
    assert _read_back(out, 0)[0][:2] == ["T00_PL00", "T00_PL01"]


def test_patch_disables_the_checksum_routine_and_rewrites_the_header(tmp_path, patcher):
    # Both are invisible to every other assertion here: an edited cartridge that
    # skips neither step boots to a checksum failure on real hardware.
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    data = out.read_bytes()
    assert data[0x0FFACA:0x0FFACC] == b"\x4e\x75"
    expected = 0
    for i in range(0x200, len(data), 2):
        expected = (expected + ((data[i] << 8) | data[i + 1])) & 0xFFFF
    assert data[0x18E:0x190] == struct.pack(">H", expected)


def test_patch_reports_progress_and_ends_at_one(tmp_path, patcher):
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    seen = []

    patcher.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=mapped,
        on_progress=lambda pct, msg: seen.append((pct, msg)),
    )

    # One event per slot that actually receives players, named for the ROM's own
    # team order rather than the provider's, plus the terminal event.
    assert seen == [
        (0.0, "Writing Boston..."),
        (0.5, "Writing Chicago..."),
        (1.0, "Saving patched ROM..."),
    ]


def test_patching_with_nothing_mapped_still_writes_an_output(tmp_path, patcher):
    # The checksum bypass alone is a worthwhile edit, and a zero-division on an
    # empty target list would be an odd way to fail.
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    mapped = patcher.map_rosters(_league_data([], code="SEA"))
    seen = []

    result = patcher.patch(
        rom_path=rom, output_path=out, rosters=mapped, on_progress=lambda p, m: seen.append(p)
    )

    assert (result.teams_patched, result.players_patched) == (0, 0)
    assert seen == [1.0]
    assert out.read_bytes()[0x0FFACA:0x0FFACC] == b"\x4e\x75"


def test_patch_forwards_status_messages(tmp_path):
    # `on_status` is a declared public constructor channel and `fetch`'s single
    # message is pinned whole; these three were not pinned at all. Asserted as a
    # whole sequence rather than by membership: a patch that emitted them in the
    # wrong order, or announced the save before writing a byte, satisfies any
    # `in` check.
    seen = []
    p = NHL94GenesisPatcher(cache_dir=tmp_path / "cache", on_status=seen.append)
    p.api = FakeApi(_teams())
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    mapped = p.map_rosters(p.fetch(season=2025))
    seen.clear()  # drops fetch's own message, which its test already pins

    p.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped)

    assert seen == ["Validating ROM...", "Initializing ROM writer...", "Saving patched ROM..."]


def test_a_slot_mapped_to_an_empty_squad_leaves_its_region_untouched(tmp_path, patcher):
    # `map_rosters` really does build `{slot: []}` for a team whose provider
    # squad came back empty — an off-season or stale-cache response — and the
    # truthiness filter in `patch` is the only thing between that list and
    # `write_team_roster`, which zero-fills the entire region it was going to
    # patch. That is 452 bytes of Boston's roster erased while `PatchResult`
    # still reports success.
    mapped = patcher.map_rosters(_league_data([]))
    assert mapped.teams == {BOS_SLOT: []}

    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert (result.teams_patched, result.players_patched) == (0, 0)
    # 25 records of 18 bytes plus the two-byte sentinel, the same literal the
    # reader tests use. Compared against the source rather than against zeros,
    # and the read-back anchors it: the region is full of the fixture's own
    # self-identifying records, so this cannot pass over a wiped image.
    start = synthetic_rom.team_base(BOS_SLOT) + synthetic_rom.SEC_PLAYERS
    region = 452
    assert out.read_bytes()[start : start + region] == rom.read_bytes()[start : start + region]
    assert _read_back(out, BOS_SLOT)[0][:2] == ["T01_PL00", "T01_PL01"]


def test_an_output_path_that_cannot_be_written_becomes_a_rom_error(tmp_path, patcher):
    # `finalize` is the only disk write in the module and it reports failure by
    # returning False, so the translation to `RomError` is the user-facing half.
    # Reachable with no monkeypatching: a directory already sitting at the output
    # path makes `open(..., "wb")` raise inside `finalize`, which swallows every
    # exception and answers False.
    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    out.mkdir()
    mapped = patcher.map_rosters(patcher.fetch(season=2025))

    with pytest.raises(RomError, match="Failed to write patched ROM"):
        patcher.patch(rom_path=rom, output_path=out, rosters=mapped)


def test_patching_an_invalid_rom_raises_rom_error(tmp_path, patcher):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\x00" * 1024)
    mapped = patcher.map_rosters(patcher.fetch(season=2025))

    with pytest.raises(RomError):
        patcher.patch(rom_path=bad, output_path=tmp_path / "out.bin", rosters=mapped)


def test_patching_a_missing_rom_raises_rom_error(tmp_path, patcher):
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    with pytest.raises(RomError, match="Not a valid"):
        patcher.patch(
            rom_path=tmp_path / "nope.bin", output_path=tmp_path / "out.bin", rosters=mapped
        )


def test_a_roster_region_running_past_the_end_of_the_image_becomes_a_rom_error(tmp_path, patcher):
    # DEFECT (pinned in test_rom_writer.py): `write_team_roster` documents a -1
    # error return and instead raises IndexError when the region the reader
    # measured overshoots the file. `validate` does not catch it — it checks the
    # size and pointer 0 and nothing else.
    #
    # Policy: translate and abort, do not skip the slot and carry on. The partial
    # write is already in the writer's buffer, so continuing would call
    # `finalize` on a damaged image and return a PatchResult claiming success.
    # Aborting before `finalize` means no output file exists at all.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    start = synthetic_rom.team_base(BOS_SLOT) + synthetic_rom.SEC_PLAYERS
    record = synthetic_rom.player_record(BOS_SLOT, 0)
    repeats = (len(rom) - start) // len(record) + 1
    rom[start:] = (record * repeats)[: len(rom) - start]
    path = tmp_path / "unterminated.bin"
    path.write_bytes(bytes(rom))
    out = tmp_path / "out.bin"

    reader = NHL94GenesisRomReader(str(path))
    assert reader.load() is True
    assert reader.validate() is True

    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    with pytest.raises(RomError, match="Corrupt team block at slot 1"):
        patcher.patch(rom_path=path, output_path=out, rosters=mapped)
    assert out.exists() is False


def test_an_out_of_range_slot_key_is_ignored_rather_than_written(tmp_path, patcher):
    # DEFECT (pinned in test_rom_writer.py): the writer's only bounds check is
    # `team_index >= TEAM_COUNT`, so a negative index is not rejected at all. It
    # is not a wrap either: `_read_team_pointer(-1)` computes `0x030E - 4` and
    # reads the four bytes *preceding* the pointer table, then treats that word
    # as a team pointer. Where the stray write lands is whatever that word says —
    # on this fixture it reads zero, on an image carrying anything else there it
    # is an arbitrary offset. `MappedRosters.teams` is a plain dict that may have
    # been rebuilt from JSON, so `patch` filters the keys `filled_slots()` hands
    # it through `0 <= slot < TEAM_COUNT` instead of trusting them.
    #
    # The landing site is baited first, exactly as the writer tests bait theirs.
    # On the plain fixture slot -1 resolves to a region at offset 0 that is
    # already zero, so the stray write leaves no trace and "the bytes are
    # unchanged" would pass against a patcher that made it. Here word 0 points
    # the region at offset 8, and the two bytes there say 2 — a sentinel the
    # stray write would overwrite with zeros.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    rom[0:2] = b"\x00\x08"
    rom[8:10] = b"\x00\x02"
    path = tmp_path / "baited.bin"
    path.write_bytes(bytes(rom))
    out = tmp_path / "out.bin"

    # TEAM_COUNT itself is in the list because it is the only key that tells
    # `< TEAM_COUNT` apart from `<= TEAM_COUNT`; 99 and -1 are far enough outside
    # that either spelling rejects them. It is the progress sequence that catches
    # it — the writer would refuse slot 26 with its documented -1, but
    # `NHL94_GEN_TEAM_ORDER[26]` raises IndexError one line earlier, outside the
    # try block, and that escapes `patch` untranslated.
    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    mapped.teams[-1] = mapped.teams[BOS_SLOT]
    mapped.teams[TEAM_COUNT] = mapped.teams[BOS_SLOT]
    mapped.teams[99] = mapped.teams[BOS_SLOT]
    seen = []

    result = patcher.patch(
        rom_path=path,
        output_path=out,
        rosters=mapped,
        on_progress=lambda pct, msg: seen.append((pct, msg)),
    )

    assert (result.teams_patched, result.players_patched) == (2, 30)
    assert seen == [
        (0.0, "Writing Boston..."),
        (0.5, "Writing Chicago..."),
        (1.0, "Saving patched ROM..."),
    ]
    assert out.read_bytes()[0:10] == b"\x00\x08\x00\x00\x00\x00\x00\x00\x00\x02"
    assert _read_back(out, BOS_SLOT)[0] == [f"P{i}" for i in range(15)]


def test_a_slot_whose_region_is_too_small_for_one_record_is_not_counted(
    tmp_path, patcher, monkeypatch
):
    # `write_team_roster` returns 0, not -1, when the region it found has no room
    # for a single record. Nothing reached the image, so the slot must not appear
    # in `teams_patched` and `write_team_header` must not be called for it — a
    # lines table built over players that were never written would index whatever
    # the region holds now.
    #
    # The header claim is pinned by spying on the call, not by reading a byte
    # back, because no such byte exists. Delete `patch`'s `continue` and
    # `write_team_header(slot, players, actual_count=0)` runs, slices `players`
    # empty and returns False before touching `self.data` — that guard lives in
    # `rom_writer.py`, not here, and the output is identical either way. The spy
    # is an implementation pin, and deliberately so: it is the only observable
    # difference `patch`'s own half of the contract has.
    header_calls = []
    unspied = NHL94GenesisRomWriter.write_team_header

    def spy(self, team_index, players, actual_count=-1):
        header_calls.append(team_index)
        return unspied(self, team_index, players, actual_count=actual_count)

    monkeypatch.setattr(NHL94GenesisRomWriter, "write_team_header", spy)

    rom = synthetic_rom.build_nhl94_genesis_rom()
    base = synthetic_rom.team_base(CHI_SLOT)
    # Point Chicago's player records at unused, already-zero space inside its own
    # 4 KB block: the reader reads a 0x0000 length there, calls it the sentinel,
    # and reports a two-byte region.
    struct.pack_into(">H", rom, base, 0x0400)
    path = tmp_path / "tiny_region.bin"
    path.write_bytes(bytes(rom))
    out = tmp_path / "out.bin"

    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    result = patcher.patch(rom_path=path, output_path=out, rosters=mapped)

    assert (result.teams_patched, result.players_patched) == (1, 15)
    assert header_calls == [BOS_SLOT]
    assert _read_back(out, BOS_SLOT)[0] == [f"P{i}" for i in range(15)]


def test_a_slot_the_writer_reports_an_error_for_is_not_counted(tmp_path, patcher):
    # The other half of the same guard: a zero-length region makes
    # `write_team_roster` take its documented `-1` return. Counting it would add
    # a team to `teams_patched` and *subtract* one from `players_patched`.
    rom = synthetic_rom.build_nhl94_genesis_rom()
    # Chicago's block moves to the last two bytes of the image with its player
    # records one byte further on, which is the only way to make the reader's
    # scan terminate before its first step and report a size of zero.
    base = len(rom) - 2
    struct.pack_into(">I", rom, synthetic_rom.POINTER_TABLE_OFFSET + CHI_SLOT * 4, base)
    struct.pack_into(">H", rom, base, 1)
    path = tmp_path / "zero_region.bin"
    path.write_bytes(bytes(rom))
    out = tmp_path / "out.bin"

    reader = NHL94GenesisRomReader(str(path))
    assert reader.load() is True
    assert reader.get_team_player_region(CHI_SLOT) == (len(rom) - 1, 0)

    mapped = patcher.map_rosters(patcher.fetch(season=2025))
    result = patcher.patch(rom_path=path, output_path=out, rosters=mapped)

    assert (result.teams_patched, result.players_patched) == (1, 15)
    assert _read_back(out, BOS_SLOT)[0] == [f"P{i}" for i in range(15)]


# ── Capacity ─────────────────────────────────────────────────────────────


def test_a_realistic_roster_overruns_the_rom_and_is_truncated(tmp_path, patcher):
    # MAX_PLAYERS_PER_SLOT is a selection cap, not a capacity. The fixture's
    # player region is 452 bytes and a record costs 2 + len(name) + 8, so 23
    # players fit only at an average name length of nine. `map_player` keeps 14
    # characters of "First Last", which fits 19 — and the writer drops the rest
    # in silence, defence first, because the ROM order is goalies, forwards,
    # defence.
    positions = ["G"] * 2 + ["C"] * 5 + ["LW"] * 5 + ["RW"] * 4 + ["D"] * 7
    players = [
        Player(id=i, name=f"{pos}{i:02d} Longsurname", position=pos, number=i + 1)
        for i, pos in enumerate(positions)
    ]
    assert len(players) == MAX_PLAYERS_PER_SLOT

    mapped = patcher.map_rosters(_league_data(players))
    records = mapped.teams[BOS_SLOT]
    assert len(records) == 23
    assert [len(r.name) for r in records] == [14] * 23

    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert (result.teams_patched, result.players_patched) == (1, 19)
    names = _read_back(out, BOS_SLOT)[0]
    # Two goalies, fourteen forwards in line order, and three of the seven
    # defencemen. The nineteenth name is cut to eight characters to leave room
    # for its stat bytes and the sentinel.
    assert names == [
        "G00 Longsurnam",
        "G01 Longsurnam",
        "C02 Longsurnam",
        "LW07 Longsurna",
        "RW12 Longsurna",
        "C03 Longsurnam",
        "LW08 Longsurna",
        "RW13 Longsurna",
        "C04 Longsurnam",
        "LW09 Longsurna",
        "RW14 Longsurna",
        "C05 Longsurnam",
        "LW10 Longsurna",
        "RW15 Longsurna",
        "C06 Longsurnam",
        "LW11 Longsurna",
        "D16 Longsurnam",
        "D17 Longsurnam",
        "D18 Long",
    ]
    # The header is told the truncated count, not the requested one, so the
    # lines table cannot index a player the writer never wrote.
    count_off = synthetic_rom.team_base(BOS_SLOT) + synthetic_rom.SEC_RATINGS + 3
    assert out.read_bytes()[count_off] == 0xE3


def test_short_names_reach_the_full_cap(tmp_path, patcher):
    # The other end of the same trade: eight-character names cost 18 bytes each,
    # so all 23 fit with the sentinel and 36 bytes to spare.
    positions = ["G"] * 2 + ["C"] * 5 + ["LW"] * 5 + ["RW"] * 4 + ["D"] * 7
    players = [
        Player(id=i, name=f"{pos}{i:02d}Name"[:8].ljust(8, "x"), position=pos, number=i + 1)
        for i, pos in enumerate(positions)
    ]
    mapped = patcher.map_rosters(_league_data(players))

    rom = synthetic_rom.write_nhl94_genesis_rom(tmp_path / "nhl94.bin")
    out = tmp_path / "out.bin"
    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert (result.teams_patched, result.players_patched) == (1, 23)
    assert [len(n) for n in _read_back(out, BOS_SLOT)[0]] == [8] * 23
