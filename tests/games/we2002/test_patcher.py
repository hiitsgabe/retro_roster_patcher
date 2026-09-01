"""The WE2002 patcher against the unified interface.

WE2002 is a 700 MB PlayStation image. There is no synthetic equivalent worth
fabricating at that size, so `patch` is covered from two directions instead of
one: an opt-in real-ROM test at the bottom of this file, and — for everything
`patch` itself decides — a recording stand-in for `RomWriter`. What that
stand-in pins is the sequence, which is where the ported writer's sharp edges
are: `write_team` writes no players unless it is handed a `players=` list, it
returns silently for a slot outside 0..31, and it only queues its 3D-jersey TEX
patch — `flush_tex_patches` is the one call that applies them.

`FakeApi` copies its signatures from `ApiFootballClient` rather than inventing
convenient ones, and `test_the_fake_api_matches_the_real_client_signatures`
keeps them copied. A fake that accepted a `season` on `get_squad` would leave
this suite green while production raised `TypeError`.
"""

import inspect
import os
from pathlib import Path

import pytest

from retro_roster_patcher.core.assets import MissingAssetError
from retro_roster_patcher.core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RomError,
)
from retro_roster_patcher.core.models import MappedRosters, SlotMapping
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002.patcher import MAX_ML_SLOTS, WE2002Patcher
from retro_roster_patcher.games.we2002.ppf import PPFError
from retro_roster_patcher.sports.api_football import ApiFootballClient
from retro_roster_patcher.sports.models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)


class FakeApi:
    """Stands in for ApiFootballClient.

    Records what it was asked for. `get_squad` takes no season and
    `get_player_stats` returns a list, both matching the real client exactly.
    """

    def __init__(self, team_count=4, squad_size=11, stats=None):
        self._team_count = team_count
        self._squad_size = squad_size
        self._stats = {} if stats is None else stats
        self.squad_calls = []
        self.stats_calls = []

    def get_leagues(self, country=None, season=None, id=None):
        return [League(id=id or 39, name="Premier League", country="England", season=season or 0)]

    def get_teams(self, league_id, season):
        return [Team(id=100 + i, name=f"Team {i}", code=f"T{i}") for i in range(self._team_count)]

    def get_squad(self, team_id):
        self.squad_calls.append(team_id)
        return [
            Player(id=team_id * 100 + i, name=f"P{i}", position="Midfielder")
            for i in range(self._squad_size)
        ]

    def get_player_stats(self, team_id, season):
        self.stats_calls.append((team_id, season))
        return list(self._stats.get(team_id, []))


def _stats(player_id, **overrides):
    """A `PlayerStats` with every counting field at zero.

    `appearances` defaults to 1 rather than 0 because `map_player` short-circuits
    to position defaults when a player has no appearances, which would bypass the
    percentile path these tests are aimed at.
    """
    fields = dict(
        player_id=player_id,
        appearances=1,
        minutes=0,
        goals=0,
        assists=0,
        shots_total=0,
        shots_on=0,
        passes_total=0,
        passes_accuracy=0.0,
        tackles_total=0,
        interceptions=0,
        blocks=0,
        duels_total=0,
        duels_won=0,
        dribbles_attempts=0,
        dribbles_success=0,
        fouls_committed=0,
        fouls_drawn=0,
        cards_yellow=0,
        cards_red=0,
        rating=None,
        lineups=0,
    )
    fields.update(overrides)
    return PlayerStats(**fields)


def _league_data(rosters):
    """Wrap team rosters in the `LeagueData` shape `fetch` returns."""
    return LeagueData(
        league=League(id=39, name="Premier League", country="England", season=2024),
        teams=rosters,
    )


def _roster(team_id, *, players=None, player_stats=None, **team_kwargs):
    return TeamRoster(
        team=Team(
            id=team_id,
            name=f"Team {team_id - 100}",
            code=f"T{team_id - 100}",
            **team_kwargs,
        ),
        players=[] if players is None else players,
        player_stats={} if player_stats is None else player_stats,
    )


def _make_patcher(tmp_path, **kwargs):
    p = WE2002Patcher(cache_dir=tmp_path / "cache", api_key="test-key", **kwargs)
    p.api = FakeApi()
    return p


@pytest.fixture
def patcher(tmp_path):
    return _make_patcher(tmp_path)


def _fake_writer_class(log, *, create_output=True):
    """Build a `RomWriter` stand-in that appends every call to `log`.

    `create_output` off models a writer that never produces its output file,
    which is the only failure `finalize` can be checked for: the real
    `finalize` returns `None` and its body is `pass`, so its return value
    carries no information at all.
    """

    class FakeRomWriter:
        def __init__(self, rom_path, output_path):
            log.append(("open", rom_path, output_path))
            self.output_path = output_path
            if create_output:
                # The real writer copies the ROM to `output_path` from its own
                # constructor, so the file exists before the translation runs.
                Path(output_path).write_bytes(b"")

        def write_team(self, slot_index, team, players=None, include_flag=True):
            log.append(("write_team", slot_index, team.name, len(players or []), include_flag))

        def flush_tex_patches(self):
            log.append(("flush_tex_patches",))

        def finalize(self):
            log.append(("finalize",))

    return FakeRomWriter


def _silence_translation(monkeypatch, log=None):
    """Replace `apply_ppf` with a recorder.

    The real applier writes the packaged English PPF at offsets past 2 MB, which
    would inflate every stand-in output file here from nothing to a sparse two
    megabytes and put the applier's behaviour — covered in `test_ppf.py` — in the
    middle of assertions about `patch`'s own call sequence.
    """

    def _apply(bin_path, ppf_path, skip_validation=False):
        if log is not None:
            log.append(("apply_ppf", skip_validation))
        return "fake description"

    monkeypatch.setattr(patcher_module, "apply_ppf", _apply)


# ── Registration ─────────────────────────────────────────────────────────


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("we2002")
    assert cls is WE2002Patcher
    assert cls.platform == "psx"
    assert cls.sport == "soccer"
    assert cls.requires_slot_mapping is True
    assert cls.requires_api_key is True
    assert cls.providers == ("api-football",)


def test_the_fake_api_matches_the_real_client_signatures():
    # The fake is the only thing standing between these tests and the real
    # client. If it accepts arguments the real one does not, `fetch` can call the
    # real client wrongly and every test here still passes.
    for name in ("get_leagues", "get_teams", "get_squad", "get_player_stats"):
        fake = list(inspect.signature(getattr(FakeApi, name)).parameters)
        real = list(inspect.signature(getattr(ApiFootballClient, name)).parameters)
        assert fake == real


# ── fetch ────────────────────────────────────────────────────────────────


def test_construction_creates_the_cache_directory(tmp_path):
    # `ApiFootballClient.__init__` does it, which is why this patcher's own
    # `__init__` does not. Construction is not free of side effects; it is free
    # of network I/O and of credentials, which is the property `analyze_rom`
    # depends on.
    cache = tmp_path / "cache"
    assert cache.exists() is False

    p = WE2002Patcher(cache_dir=cache)

    assert p.cache_dir.is_dir() is True


def test_an_api_key_is_mandatory_at_fetch_time(tmp_path):
    # Construction stays free of credentials so `analyze` can inspect a ROM.
    p = WE2002Patcher(cache_dir=tmp_path / "cache")
    with pytest.raises(CapabilityError, match="api_key"):
        p.fetch(season=2024, league_id=39)


def test_fetch_needs_a_league_id(patcher):
    with pytest.raises(CapabilityError, match="league_id"):
        patcher.fetch(season=2024)


def test_fetch_returns_league_data(patcher):
    data = patcher.fetch(season=2024, league_id=39)

    assert isinstance(data, LeagueData) is True
    assert data.league.name == "Premier League"
    assert len(data.teams) == 4
    assert len(data.teams[0].players) == 11
    assert [tr.team.id for tr in data.teams] == [100, 101, 102, 103]


def test_fetch_asks_for_each_squad_without_a_season(patcher):
    # `ApiFootballClient.get_squad` takes a team id and nothing else; its cache
    # key is `squad_{team_id}`, with no season in it.
    patcher.fetch(season=2024, league_id=39)

    assert patcher.api.squad_calls == [100, 101, 102, 103]
    assert patcher.api.stats_calls == [(100, 2024), (101, 2024), (102, 2024), (103, 2024)]


def test_fetch_keys_player_stats_by_player_id(tmp_path):
    # The client returns a list; `TeamRoster.player_stats` is a dict keyed by
    # player id, and `map_team_with_league_context` calls `.items()` on it.
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=1, stats={100: [_stats(10000, goals=3), _stats(10001)]})

    data = p.fetch(season=2024, league_id=39)

    assert sorted(data.teams[0].player_stats) == [10000, 10001]
    assert data.teams[0].player_stats[10000].goals == 3


def test_fetch_publishes_the_team_list_before_the_squads(tmp_path):
    seen = []
    p = WE2002Patcher(cache_dir=tmp_path / "cache", api_key="k", on_partial=seen.append)
    p.api = FakeApi()

    p.fetch(season=2024, league_id=39)

    assert len(seen) == 1
    assert len(seen[0].teams) == 4
    assert [tr.loading for tr in seen[0].teams] == [True, True, True, True]
    assert [len(tr.players) for tr in seen[0].teams] == [0, 0, 0, 0]


def test_fetch_reports_progress(tmp_path):
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=2)
    seen = []

    p.fetch(season=2024, league_id=39, on_progress=lambda pct, msg: seen.append((pct, msg)))

    # Two teams rather than four so every fraction is exactly representable.
    assert seen == [
        (0.05, "Fetching league info..."),
        (0.1, "Fetching teams for Premier League..."),
        (0.1, "Fetching Team 0..."),
        (0.5, "Fetching Team 1..."),
        (1.0, "Complete"),
    ]


def test_an_unknown_league_raises_api_error(patcher):
    patcher.api.get_leagues = lambda country=None, season=None, id=None: []
    with pytest.raises(ApiError, match="9999"):
        patcher.fetch(season=2024, league_id=9999)


def test_a_league_with_no_teams_raises_api_error(tmp_path):
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=0)
    with pytest.raises(ApiError, match="no teams"):
        p.fetch(season=2024, league_id=39)


# ── default_slot_mapping ─────────────────────────────────────────────────


def test_default_slot_mapping_is_sequential_and_serialisable(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    mapping = patcher.default_slot_mapping(data)

    assert [m.slot_index for m in mapping] == [0, 1, 2, 3]
    assert [m.team_id for m in mapping] == [100, 101, 102, 103]
    assert mapping[0].team_name == "Team 0"
    assert SlotMapping.from_dict(mapping[0].to_dict()) == mapping[0]


def test_default_slot_mapping_stops_at_the_master_league_slot_count(patcher):
    # Upstream piled every team past the 32nd onto a sentinel slot, where the
    # writer silently discarded them. Dropping them is the same outcome, stated.
    data = _league_data([_roster(100 + i) for i in range(40)])

    mapping = patcher.default_slot_mapping(data)

    assert MAX_ML_SLOTS == 32
    assert len(mapping) == 32
    assert mapping[-1].slot_index == 31


# ── map_rosters ──────────────────────────────────────────────────────────


def test_map_rosters_requires_a_slot_mapping(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    with pytest.raises(CapabilityError, match="requires a slot mapping"):
        patcher.map_rosters(data)


def test_map_rosters_keys_by_the_requested_slot(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(
        data,
        slot_mapping=[
            SlotMapping(slot_index=5, team_id=100, team_name="Team 0"),
            SlotMapping(slot_index=9, team_id=102, team_name="Team 2"),
        ],
    )

    assert mapped.game_id == "we2002"
    assert sorted(mapped.teams) == [5, 9]
    assert mapped.filled_slots() == [5, 9]
    assert mapped.teams[5].name == "Team 0"
    assert mapped.teams[9].name == "Team 2"


def test_a_slot_counts_as_filled_even_with_no_players(patcher):
    # `filled_slots()` keys on truthiness and a `WETeamRecord` is a dataclass
    # instance, so it is truthy however empty it is. NHL94 stores lists there and
    # gets the "slots that received players" reading; WE2002 does not, which is
    # why `patch` below iterates `rosters.teams` instead.
    data = _league_data([_roster(100)])

    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])

    assert mapped.teams[0].players == []
    assert mapped.filled_slots() == [0]


def test_mapping_an_unknown_team_id_raises(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    with pytest.raises(MappingError, match="777"):
        patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=777)])


def test_an_out_of_range_slot_raises_before_anything_is_written(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    with pytest.raises(MappingError, match="outside the WE2002 range"):
        patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=63, team_id=100)])


def test_the_last_master_league_slot_is_accepted_and_the_next_one_is_not(patcher):
    data = patcher.fetch(season=2024, league_id=39)

    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=31, team_id=100)])
    assert sorted(mapped.teams) == [31]

    with pytest.raises(MappingError, match=r"0\.\.31"):
        patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=32, team_id=100)])


def test_a_negative_slot_is_rejected(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    with pytest.raises(MappingError, match="outside the WE2002 range"):
        patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=-1, team_id=100)])


def test_map_rosters_carries_the_kit_colours_from_the_team(patcher):
    data = _league_data([_roster(100, color="C60000", alternate_color="#00FF80")])

    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    record = mapped.teams[0]

    assert record.kit_home == (198, 0, 0)
    assert record.kit_away == (0, 255, 128)
    # Upstream mirrored the home kit into the third slot. Nothing in the ported
    # writer reads `kit_third` — the maglia palette, the flag palette and the 3D
    # TEX patch all read `kit_home` and `kit_away` — so this pins parity, not an
    # effect on the ROM.
    assert record.kit_third == (198, 0, 0)


def test_a_team_with_no_colours_keeps_the_record_defaults(patcher):
    data = _league_data([_roster(100)])

    record = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)]).teams[
        0
    ]

    assert record.kit_home == (255, 255, 255)
    assert record.kit_away == (0, 0, 0)
    assert record.kit_third == (0, 0, 0)


def test_a_malformed_colour_is_ignored_rather_than_half_parsed(patcher):
    data = _league_data([_roster(100, color="C60", alternate_color="not a colour")])

    record = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)]).teams[
        0
    ]

    assert record.kit_home == (255, 255, 255)
    assert record.kit_away == (0, 0, 0)


def test_players_are_rated_against_the_whole_league_not_their_own_team(patcher):
    # One scorer in a twelve-player league sits above eleven of twelve samples,
    # a percentile of 91.7, which the 1-9 table rates 8. Rated against his own
    # team he is the only sample, lands at percentile 0, and rates 1. That gap is
    # what pins `all_rosters` being handed to the mapper.
    star = Player(id=1, name="Star", position="Midfielder")
    rest = [Player(id=i, name=f"P{i}", position="Midfielder") for i in range(2, 13)]
    scorers = _roster(100, players=[star], player_stats={1: _stats(1, goals=10)})
    others = _roster(101, players=rest, player_stats={p.id: _stats(p.id) for p in rest})
    data = _league_data([scorers, others])

    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])

    assert mapped.teams[0].players[0].attributes.offensive == 8
    own_team_only = patcher.mapper.map_team_with_league_context(scorers, [scorers])
    assert own_team_only.players[0].attributes.offensive == 1


# ── analyze_rom ──────────────────────────────────────────────────────────


def test_analyzing_a_missing_rom_raises_rom_error(patcher, tmp_path):
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "nope.bin")


def test_a_file_too_small_to_be_the_game_is_reported_rather_than_raised(patcher, tmp_path):
    # `retro-roster analyze` probes every registered patcher against one ROM, so
    # "not this game" has to answer rather than raise. `validate_rom` rejects
    # anything under 100 MB, and an invalid ROM reports no slots.
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    info = patcher.analyze_rom(rom)

    assert info.is_valid is False
    assert info.slots == []
    assert info.size == 4096
    assert info.game_id == "we2002"
    assert info.extra == {"version": "Unknown"}
    assert info.to_dict()["extra"] == {"version": "Unknown"}


# ── patch ────────────────────────────────────────────────────────────────


def test_an_unknown_language_is_rejected(patcher, tmp_path):
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    with pytest.raises(CapabilityError, match="klingon"):
        patcher.patch(
            rom_path=rom,
            output_path=tmp_path / "out.bin",
            rosters=mapped,
            language="klingon",
        )


def test_patching_a_missing_rom_raises_rom_error(patcher, tmp_path):
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])

    with pytest.raises(RomError, match="ROM not found"):
        patcher.patch(
            rom_path=tmp_path / "nope.bin", output_path=tmp_path / "out.bin", rosters=mapped
        )


def test_patch_writes_every_slot_with_its_players_then_flushes_then_finalises(
    patcher, tmp_path, monkeypatch
):
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch, log)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(
        data,
        slot_mapping=[
            SlotMapping(slot_index=5, team_id=101),
            SlotMapping(slot_index=0, team_id=100),
        ],
    )
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)
    out = tmp_path / "out.bin"

    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    # Slots ascending regardless of the mapping's order; players handed over
    # explicitly, because `write_team` writes none without them; the TEX flush
    # before finalisation, because queued 3D-jersey patches are dropped otherwise.
    assert log == [
        ("open", str(rom), str(out)),
        ("apply_ppf", True),
        ("write_team", 0, "Team 0", 11, True),
        ("write_team", 5, "Team 1", 11, True),
        ("flush_tex_patches",),
        ("finalize",),
    ]
    assert result.output_path == str(out)
    assert (result.teams_patched, result.players_patched) == (2, 22)


def test_patch_reports_progress_and_ends_at_one(patcher, tmp_path, monkeypatch):
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    _silence_translation(monkeypatch)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(
        data,
        slot_mapping=[
            SlotMapping(slot_index=0, team_id=100),
            SlotMapping(slot_index=1, team_id=101),
        ],
    )
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)
    seen = []

    patcher.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=mapped,
        on_progress=lambda pct, msg: seen.append((pct, msg)),
    )

    # Two slots rather than four so every fraction is exactly representable.
    assert seen == [
        (0.02, "Applying English translation..."),
        (0.05, "English translation applied"),
        (0.05, "Writing slot 0..."),
        (0.5, "Writing slot 1..."),
        (1.0, "Saving patched ROM..."),
    ]


def test_patching_with_nothing_mapped_still_writes_an_output(patcher, tmp_path, monkeypatch):
    # The translation alone is a worthwhile edit, and a zero-division on an empty
    # slot list would be an odd way to fail.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch, log)
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    result = patcher.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert (result.teams_patched, result.players_patched) == (0, 0)
    assert log[-2:] == [("flush_tex_patches",), ("finalize",)]


def test_a_slot_the_writer_would_silently_drop_is_not_counted(patcher, tmp_path, monkeypatch):
    # `map_rosters` rejects these, but `MappedRosters.teams` is a plain dict that
    # a caller can build by hand. `RomWriter.write_team` returns without writing
    # for any slot outside 0..31, so counting one would report a patch that never
    # happened.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    mapped.teams[40] = mapped.teams[0]
    mapped.teams[-1] = mapped.teams[0]
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    result = patcher.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped)

    assert [entry[1] for entry in log if entry[0] == "write_team"] == [0]
    assert (result.teams_patched, result.players_patched) == (1, 11)


def test_patch_raises_when_finalisation_leaves_no_output(patcher, tmp_path, monkeypatch):
    # `RomWriter.finalize` has `pass` for a body and returns `None`, so its
    # return value cannot be checked. The output file existing can be.
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([], create_output=False))
    _silence_translation(monkeypatch)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    with pytest.raises(RomError, match="Failed to write patched ROM"):
        patcher.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped)


@pytest.mark.parametrize(
    ("language", "name"),
    [("en", "English"), ("es", "Spanish"), ("fr", "French"), ("pt", "Portuguese")],
)
def test_each_supported_language_names_itself_in_the_status(tmp_path, monkeypatch, language, name):
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    _silence_translation(monkeypatch)
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: "unused.ppf")
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)
    seen = []

    p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
        language=language,
        on_progress=lambda pct, msg: seen.append(msg),
    )

    assert seen == [
        f"Applying {name} translation...",
        f"{name} translation applied",
        "Saving patched ROM...",
    ]
    assert status == ["Preparing ROM...", "Saving patched ROM..."]


def test_a_missing_translation_asset_is_reported_and_the_patch_continues(tmp_path, monkeypatch):
    # `ensure_ppf` raises `MissingAssetError`, which is a `RetroRosterError` and
    # not an `OSError`. A translation is cosmetic; the roster patch under it is
    # the point, so this degrades to Japanese menus rather than aborting.
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))

    def _missing(cache_dir, lang="en", assets_dir=""):
        raise MissingAssetError("no such asset")

    monkeypatch.setattr(patcher_module, "ensure_ppf", _missing)
    data = p.fetch(season=2024, league_id=39)
    mapped = p.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)
    seen = []

    result = p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=mapped,
        on_progress=lambda pct, msg: seen.append((pct, msg)),
    )

    assert result.teams_patched == 1
    assert status == [
        "Preparing ROM...",
        "English translation skipped: no such asset",
        "Saving patched ROM...",
    ]
    assert seen == [
        (0.02, "Applying English translation..."),
        (0.05, "English translation skipped"),
        (0.05, "Writing slot 0..."),
        (1.0, "Saving patched ROM..."),
    ]
    assert log[-2:] == [("flush_tex_patches",), ("finalize",)]


def test_a_broken_patch_file_is_reported_and_the_patch_continues(tmp_path, monkeypatch):
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: "unused.ppf")

    def _broken(bin_path, ppf_path, skip_validation=False):
        raise PPFError("Unsupported PPF format")

    monkeypatch.setattr(patcher_module, "apply_ppf", _broken)
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    result = p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert result.teams_patched == 0
    assert status == [
        "Preparing ROM...",
        "English translation skipped: Unsupported PPF format",
        "Saving patched ROM...",
    ]


def test_an_unreadable_translation_file_is_reported_and_the_patch_continues(tmp_path, monkeypatch):
    # `apply_ppf` opens the PPF itself, so a path that is not there surfaces as
    # `FileNotFoundError` — an `OSError`, not a `PPFError`.
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: str(tmp_path / "gone.ppf"))
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    result = p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert result.teams_patched == 0
    assert status[1].startswith("English translation skipped: ")


def test_the_assets_directory_is_forwarded_to_the_translation(tmp_path, monkeypatch):
    seen = []
    p = WE2002Patcher(cache_dir=tmp_path / "cache", api_key="k", assets_dir=tmp_path / "assets")
    p.api = FakeApi()
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    _silence_translation(monkeypatch)

    def _record(cache_dir, lang="en", assets_dir=""):
        seen.append((cache_dir, lang, assets_dir))
        return "unused.ppf"

    monkeypatch.setattr(patcher_module, "ensure_ppf", _record)
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
        language="fr",
    )

    assert seen == [(str(tmp_path / "cache" / "translations"), "fr", str(tmp_path / "assets"))]


def test_without_an_assets_directory_the_translation_is_asked_for_none(tmp_path, monkeypatch):
    seen = []
    p = _make_patcher(tmp_path)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    _silence_translation(monkeypatch)

    def _record(cache_dir, lang="en", assets_dir=""):
        seen.append(assets_dir)
        return "unused.ppf"

    monkeypatch.setattr(patcher_module, "ensure_ppf", _record)
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert seen == [""]


# ── real ROM ─────────────────────────────────────────────────────────────


@pytest.mark.real_rom
@pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding we2002.bin",
)
def test_patching_a_real_rom_produces_a_readable_output(patcher, tmp_path):
    # Fails rather than skips when the variable is set and the file is not there.
    # A silent skip is how the WE2002 writer reached this repository with no
    # coverage of its write path at all.
    rom = Path(os.environ["RETRO_ROSTER_TEST_ROMS"]) / "we2002.bin"
    if not rom.exists():
        pytest.fail(f"RETRO_ROSTER_TEST_ROMS is set but {rom} is missing")

    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=patcher.default_slot_mapping(data))
    out = tmp_path / "out.bin"

    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert result.output_path == str(out)
    assert result.teams_patched == 4
    assert out.stat().st_size == rom.stat().st_size
    assert patcher.analyze_rom(out).is_valid is True
