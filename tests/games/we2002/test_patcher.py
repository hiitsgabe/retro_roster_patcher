"""The WE2002 patcher against the unified interface.

No copy of the 700 MB PlayStation image may enter this repository, so `patch` is
covered by an opt-in real-ROM test at the bottom of this file plus a recording
stand-in for `RomWriter`. Every `patch` test hands over `_valid_rom`, a sparse
100 MB of zeroes, because `validate_rom`'s only test is `size >= 100 MB`.
"""

import inspect
import json
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
from retro_roster_patcher.core.models import MappedRosters, RomSlot, SlotMapping
from retro_roster_patcher.games.we2002 import patcher as patcher_module
from retro_roster_patcher.games.we2002.models import WETeamRecord
from retro_roster_patcher.games.we2002.patcher import MAX_ML_SLOTS, WE2002Patcher
from retro_roster_patcher.games.we2002.ppf import PPFError
from retro_roster_patcher.games.we2002.rom_writer import RomWriter, _slot_player_range
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)


def _signature_facts(fn):
    """Every parameter's name, kind and default, in declaration order."""
    return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]


class FakeApi:
    """Stands in for `EspnClient`, recording every call in order on `calls`."""

    def __init__(self, team_count=4, squad_size=11, stats=None, calls=None):
        self._team_count = team_count
        self._squad_size = squad_size
        self._stats = {} if stats is None else stats
        self.calls = [] if calls is None else calls

    def get_leagues(self, country=None, season=None, id=None):
        self.calls.append(("get_leagues", country, season, id))
        return [League(id=id or 39, name="Premier League", country="England", season=season or 0)]

    def get_teams(self, league_id, season):
        self.calls.append(("get_teams", league_id, season))
        return [Team(id=100 + i, name=f"Team {i}", code=f"T{i}") for i in range(self._team_count)]

    def get_squad(self, team_id, season=None):
        self.calls.append(("get_squad", team_id, season))
        return [
            Player(id=team_id * 100 + i, name=f"P{i}", position="Midfielder")
            for i in range(self._squad_size)
        ]

    def get_player_stats(self, team_id, season):
        self.calls.append(("get_player_stats", team_id, season))
        return list(self._stats.get(team_id, []))


class FailingApi(FakeApi):
    """A `FakeApi` that raises for named teams instead of answering."""

    def __init__(self, *, squad_errors=None, stats_errors=None, **kwargs):
        super().__init__(**kwargs)
        self._squad_errors = {} if squad_errors is None else squad_errors
        self._stats_errors = {} if stats_errors is None else stats_errors

    def get_squad(self, team_id, season=None):
        self.calls.append(("get_squad", team_id, season))
        if team_id in self._squad_errors:
            raise self._squad_errors[team_id]
        return [
            Player(id=team_id * 100 + i, name=f"P{i}", position="Midfielder")
            for i in range(self._squad_size)
        ]

    def get_player_stats(self, team_id, season):
        self.calls.append(("get_player_stats", team_id, season))
        if team_id in self._stats_errors:
            raise self._stats_errors[team_id]
        return list(self._stats.get(team_id, []))


def _stats(player_id, **overrides):
    """A `PlayerStats` with every counting field at zero.

    `appearances` defaults to 1: `map_player` short-circuits to position defaults
    when a player has none.
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
    p = WE2002Patcher(cache_dir=tmp_path / "cache", **kwargs)
    p.api = FakeApi()
    return p


@pytest.fixture
def patcher(tmp_path):
    return _make_patcher(tmp_path)


def _fake_writer_class(log, *, create_output=True):
    """Build a `RomWriter` stand-in that appends every call to `log`."""

    class FakeRomWriter:
        def __init__(self, rom_path, output_path):
            log.append(("open", rom_path, output_path))
            self.output_path = output_path
            if create_output:
                # The real writer copies the ROM to `output_path` in its own
                # constructor, so the file exists before the translation runs.
                Path(output_path).write_bytes(b"")

        def write_team(self, slot_index, team, players=None, include_flag=True):
            log.append(("write_team", slot_index, team.name, len(players or []), include_flag))
            # The real writer drops players past the slot's capacity, so ask the
            # real `_slot_player_range` rather than restating the rule here.
            return min(len(players or []), _slot_player_range(slot_index)[1])

        def flush_tex_patches(self):
            log.append(("flush_tex_patches",))

        def finalize(self):
            log.append(("finalize",))

    return FakeRomWriter


def _silence_translation(monkeypatch, log=None):
    """Replace `apply_ppf` with a recorder."""

    def _apply(bin_path, ppf_path, skip_validation=False):
        if log is not None:
            log.append(("apply_ppf", skip_validation))
        return "fake description"

    monkeypatch.setattr(patcher_module, "apply_ppf", _apply)


def _valid_rom(tmp_path, name="we2002.bin"):
    """An input file `patch` will accept: 100 MB of addressable zeroes.

    `validate_rom`'s only test is `size >= 100 MB`. `truncate` keeps the file
    sparse, so this costs neither disk nor time.
    """
    path = tmp_path / name
    with path.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)
    return path


def test_the_patcher_is_registered_with_its_capabilities():
    from retro_roster_patcher import get_patcher

    cls = get_patcher("we2002")
    assert cls is WE2002Patcher
    assert cls.platform == "psx"
    assert cls.sport == "soccer"
    assert cls.requires_slot_mapping is True
    assert cls.providers == ("espn",)


@pytest.mark.parametrize("name", ["get_leagues", "get_teams", "get_squad", "get_player_stats"])
def test_the_fake_api_accepts_every_call_the_real_client_accepts(name):
    # The fake must not accept arguments the real client refuses. A prefix and
    # not an equality: `EspnClient` appends an optional `league_code` to two of
    # these signatures.
    fake = [(p, k) for p, k, _ in _signature_facts(getattr(FakeApi, name))]
    real = [(p, k) for p, k, _ in _signature_facts(getattr(EspnClient, name))]
    assert real[: len(fake)] == fake


@pytest.mark.parametrize("name", ["get_leagues", "get_teams", "get_squad", "get_player_stats"])
def test_every_real_parameter_the_fake_omits_has_a_default(name):
    # A prefix match alone would allow a required fifth parameter that `fetch`
    # never passes, so everything past the prefix has to be optional.
    fake = _signature_facts(getattr(FakeApi, name))
    tail = _signature_facts(getattr(EspnClient, name))[len(fake) :]
    assert [p for p, _, default in tail if default is inspect.Parameter.empty] == []


def test_the_fake_writer_matches_the_real_writer_signatures():
    # The stand-in is the only `RomWriter` most of this file sees: keep its
    # names, kinds and defaults copied from the real one, `__init__` included.
    fake = _fake_writer_class([])
    for name in ("__init__", "write_team", "flush_tex_patches", "finalize"):
        assert _signature_facts(getattr(fake, name)) == _signature_facts(getattr(RomWriter, name))


def test_construction_creates_the_cache_directory(tmp_path):
    cache = tmp_path / "cache"
    assert cache.exists() is False

    p = WE2002Patcher(cache_dir=cache)

    assert p.cache_dir.is_dir() is True


def test_construction_builds_the_espn_client_with_the_cache_dir_and_the_transport(tmp_path):
    # `EspnClient` takes its cache directory and status callback positionally.
    def transport(url, headers, timeout):
        raise AssertionError("no test may reach the network")

    seen = []
    p = WE2002Patcher(cache_dir=tmp_path / "cache", on_status=seen.append, transport=transport)

    assert p.provider == "espn"
    assert type(p.api) is EspnClient
    assert p.api.cache_dir == str(tmp_path / "cache")
    assert p.api._transport is transport
    assert p.api.on_status == seen.append


def test_naming_the_one_supported_provider_builds_the_same_client(tmp_path):
    p = WE2002Patcher(cache_dir=tmp_path / "cache", provider="espn")

    assert p.provider == "espn"
    assert type(p.api) is EspnClient


def test_the_deleted_provider_is_refused_by_name(tmp_path):
    with pytest.raises(CapabilityError, match="does not support provider 'api-football'"):
        WE2002Patcher(cache_dir=tmp_path / "cache", provider="api-football")


def test_fetch_needs_no_credential_of_any_kind(tmp_path):
    p = WE2002Patcher(cache_dir=tmp_path / "cache")
    p.api = FakeApi()

    data = p.fetch(season=2024, league_id=2001)

    assert [tr.team.id for tr in data.teams] == [100, 101, 102, 103]


def test_fetch_needs_a_league_id(patcher):
    with pytest.raises(CapabilityError, match="league_id"):
        patcher.fetch(season=2024)


def test_fetch_returns_league_data(patcher):
    data = patcher.fetch(season=2024, league_id=39)

    assert isinstance(data, LeagueData) is True
    assert data.league.name == "Premier League"
    assert data.league.season == 2024
    assert len(data.teams) == 4
    assert len(data.teams[0].players) == 11
    assert [tr.team.id for tr in data.teams] == [100, 101, 102, 103]


def test_fetch_asks_the_provider_for_exactly_what_it_needs(tmp_path):
    # `get_squad`'s cache key is `squad_{team_id}`; no season goes into it. The
    # squad comes before the stats for each team: under a rate limiter the second
    # call is the throttled one, and a lost squad costs the whole team.
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=2)

    p.fetch(season=2024, league_id=39)

    assert p.api.calls == [
        ("get_leagues", None, 2024, 39),
        ("get_teams", 39, 2024),
        ("get_squad", 100, 2024),
        ("get_player_stats", 100, 2024),
        ("get_squad", 101, 2024),
        ("get_player_stats", 101, 2024),
    ]


def test_one_team_whose_squad_fails_costs_that_team_and_not_the_league(tmp_path):
    # A broken team keeps its place in the list — the slot mapping is positional
    # — and carries the reason on `TeamRoster.error`.
    p = _make_patcher(tmp_path)
    p.api = FailingApi(team_count=4, squad_errors={102: RuntimeError("connection reset")})
    status = []
    p.on_status = status.append

    data = p.fetch(season=2024, league_id=39)

    assert [tr.team.id for tr in data.teams] == [100, 101, 102, 103]
    assert [len(tr.players) for tr in data.teams] == [11, 11, 0, 11]
    assert data.teams[2].error == "Failed to load squad: connection reset"
    assert [tr.error for tr in data.teams] == ["", "", "Failed to load squad: connection reset", ""]
    assert status == ["Team 2: Failed to load squad: connection reset"]
    assert [tr.loading for tr in data.teams] == [False, False, False, False]


def test_two_squad_failures_partway_through_a_league_keep_the_teams_already_fetched(tmp_path):
    # Two distinct failures, not one: a single failing team cannot tell "the loop
    # continues" from "the loop stops after the first error".
    p = _make_patcher(tmp_path)
    p.api = FailingApi(
        team_count=3,
        squad_errors={
            101: RuntimeError("too fast"),
            102: ValueError("malformed squad document"),
        },
    )

    data = p.fetch(season=2024, league_id=39)

    assert [len(tr.players) for tr in data.teams] == [11, 0, 0]
    assert data.teams[0].error == ""
    assert data.teams[1].error == "Failed to load squad: too fast"
    assert data.teams[2].error == "Failed to load squad: malformed squad document"


def test_a_stats_failure_costs_the_ratings_and_not_the_squad(tmp_path):
    # A stats failure must not set `error` or drop the squad: a player without
    # stats maps to his position defaults.
    p = _make_patcher(tmp_path)
    p.api = FailingApi(
        team_count=2,
        stats={100: [_stats(10000, goals=3)]},
        stats_errors={101: RuntimeError("stats endpoint down")},
    )
    status = []
    p.on_status = status.append

    data = p.fetch(season=2024, league_id=39)

    assert [len(tr.players) for tr in data.teams] == [11, 11]
    assert sorted(data.teams[0].player_stats) == [10000]
    assert data.teams[1].player_stats == {}
    assert [tr.error for tr in data.teams] == ["", ""]
    assert status == ["Team 1: stats unavailable, ratings will use position defaults"]


def test_fetch_keys_player_stats_by_player_id(tmp_path):
    # The client returns a list; `TeamRoster.player_stats` is a dict keyed by
    # player id, and `map_team_with_league_context` calls `.items()` on it.
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=1, stats={100: [_stats(10000, goals=3), _stats(10001)]})

    data = p.fetch(season=2024, league_id=39)

    assert sorted(data.teams[0].player_stats) == [10000, 10001]
    assert data.teams[0].player_stats[10000].goals == 3


def test_fetch_publishes_the_team_list_before_the_squads(tmp_path):
    events = []
    published = []

    def _on_partial(data):
        events.append(("partial", len(data.teams)))
        published.append(data)

    p = WE2002Patcher(cache_dir=tmp_path / "cache", on_partial=_on_partial)
    p.api = FakeApi(team_count=2, calls=events)

    p.fetch(season=2024, league_id=39)

    assert events == [
        ("get_leagues", None, 2024, 39),
        ("get_teams", 39, 2024),
        ("partial", 2),
        ("get_squad", 100, 2024),
        ("get_player_stats", 100, 2024),
        ("get_squad", 101, 2024),
        ("get_player_stats", 101, 2024),
    ]
    assert len(published) == 1
    # The skeleton is a snapshot: the loop fills fresh `TeamRoster`s rather than
    # writing through the object the caller already holds.
    assert [tr.loading for tr in published[0].teams] == [True, True]
    assert [len(tr.players) for tr in published[0].teams] == [0, 0]


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


# One team of five, spread across positions and ages so the three attributes ESPN
# cannot measure have something to be derived from. Every statistics document
# differs in every field.
_ESPN_SQUAD = [
    (11, "Alisson Becker", "Goalkeeper", 32),
    (12, "Virgil Dijk", "Defender", 28),
    (13, "Ryan Gravenberch", "Midfielder", 22),
    (14, "Dominik Szoboszlai", "Midfielder", 31),
    (15, "Mohamed Salah", "Forward", 26),
]

_ESPN_DOCUMENTS = {
    11: {"appearances": 30.0, "minutes": 2700.0, "starts": 30.0, "totalGoals": 0.0},
    12: {"appearances": 28.0, "minutes": 2500.0, "starts": 28.0, "totalGoals": 3.0},
    13: {"appearances": 24.0, "minutes": 1600.0, "starts": 18.0, "totalGoals": 1.0},
    14: {"appearances": 20.0, "minutes": 1200.0, "starts": 12.0, "totalGoals": 6.0},
    15: {"appearances": 32.0, "minutes": 2800.0, "starts": 32.0, "totalGoals": 27.0},
}


def _espn_transport():
    """Serve the four soccer documents `fetch` asks ESPN for, by URL."""

    def transport(url, headers, timeout):
        transport.calls.append(url)
        if url.endswith("/eng.1/teams"):
            return json.dumps(
                {
                    "sports": [
                        {
                            "leagues": [
                                {
                                    "teams": [
                                        {
                                            "team": {
                                                "id": 364,
                                                "displayName": "Liverpool",
                                                "abbreviation": "LIV",
                                            }
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ).encode()
        if url.endswith("/roster"):
            return json.dumps(
                {
                    "athletes": [
                        {
                            "id": pid,
                            "displayName": name,
                            "position": {"name": position},
                            "age": age,
                            "jersey": str(pid),
                        }
                        for pid, name, position, age in _ESPN_SQUAD
                    ]
                }
            ).encode()
        if url.endswith("/leaders"):
            return json.dumps(
                {
                    "categories": [
                        {
                            "abbreviation": "G",
                            "leaders": [
                                {
                                    "athlete": {
                                        "$ref": "http://sports.core.api.espn.com/v2/sports"
                                        f"/soccer/leagues/eng.1/seasons/2025/athletes/{pid}"
                                        "?lang=en&region=us"
                                    },
                                    "value": 1.0,
                                }
                                for pid, _, _, _ in _ESPN_SQUAD
                            ],
                        }
                    ]
                }
            ).encode()
        athlete_id = int(url.split("/athletes/")[1].split("/")[0])
        stats = _ESPN_DOCUMENTS[athlete_id]
        return json.dumps(
            {
                "splits": {
                    "categories": [
                        {
                            "name": "general",
                            "stats": [
                                {"name": "appearances", "value": stats["appearances"]},
                                {"name": "minutes", "value": stats["minutes"]},
                                {"name": "starts", "value": stats["starts"]},
                                {"name": "passPct", "value": 0.8},
                            ],
                        },
                        {
                            "name": "offensive",
                            "stats": [{"name": "totalGoals", "value": stats["totalGoals"]}],
                        },
                    ]
                }
            }
        ).encode()

    transport.calls = []
    return transport


def _espn_patcher(tmp_path):
    return WE2002Patcher(cache_dir=tmp_path / "cache", transport=_espn_transport())


def test_fetch_over_espn_returns_a_squad_and_its_statistics(tmp_path):
    p = _espn_patcher(tmp_path)

    data = p.fetch(season=2025, league_id=2001)

    assert data.league.id == 2001
    assert data.league.name == "Premier League"
    # The season the caller asked for, not the calendar year.
    assert data.league.season == 2025
    assert [tr.team.id for tr in data.teams] == [364]
    assert [p.name for p in data.teams[0].players] == [name for _, name, _, _ in _ESPN_SQUAD]
    assert sorted(data.teams[0].player_stats) == [11, 12, 13, 14, 15]
    assert [tr.error for tr in data.teams] == [""]


def test_fetch_over_espn_asks_for_the_squad_before_the_statistics(tmp_path):
    # The whole request sequence, in order: teams, the roster, the leaders
    # document, then one statistics document per athlete. `get_squad` is handed
    # no league code, so the roster URL carrying `eng.1` is what shows
    # `_find_league_code_for_team` resolved it from the cached team list.
    p = _espn_patcher(tmp_path)

    p.fetch(season=2025, league_id=2001)

    assert p.api._transport.calls == [
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams",
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams/364/roster",
        "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"
        "/seasons/2025/types/1/teams/364/leaders",
    ] + [
        "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"
        f"/seasons/2025/types/1/teams/364/athletes/{pid}/statistics"
        for pid, _, _, _ in _ESPN_SQUAD
    ]


def test_every_espn_record_says_which_stats_were_not_measured(tmp_path):
    p = _espn_patcher(tmp_path)
    data = p.fetch(season=2025, league_id=2001)
    stats = data.teams[0].player_stats
    assert stats[15].unsupplied == (
        "duels_total",
        "duels_won",
        "dribbles_attempts",
        "dribbles_success",
    )
    assert stats[15].goals == 27
    assert stats[15].passes_accuracy == 80.0


def test_a_squad_fetched_from_espn_is_uniformly_clumsy_after_mapping(tmp_path):
    """Pins upstream fidelity deliberately; the behaviour is known to be wrong.

    All five records carry a filler zero for duels and dribbles, so the three
    attributes derived from them percentile to the floor for the whole league.
    `games/we2002/stat_mapper.py` argues why the fix was reverted. Do not
    restore it here.
    """
    p = _espn_patcher(tmp_path)
    data = p.fetch(season=2025, league_id=2001)

    mapped = p.map_rosters(data, [SlotMapping(slot_index=0, team_id=364, team_name="Liverpool")])
    by_name = {rec.last_name: rec.attributes for rec in mapped.teams[0].players}

    # Eight characters is the ROM's whole budget for a surname, hence the cuts.
    assert sorted(by_name) == ["A. Becke", "D. Szobo", "M. Salah", "R. Grave", "V. Dijk"]
    # Goalkeeper 32, defender 28, midfielders 22 and 31, forward 26 -- five
    # positions and five ages, and one rating between them.
    assert {a.body_balance for a in by_name.values()} == {1}
    assert {a.dribble for a in by_name.values()} == {1}
    # `technique` is 1 for everyone but the two midfielders, whom
    # `_apply_position_adjustments` gives a +1 after the floored percentile.
    assert by_name["R. Grave"].technique == 2
    assert by_name["A. Becke"].technique == 1
    # And the attribute that was never affected still tracks the goals scored: 27
    # against 0, 1, 3 and 6, so four of five below him -- the 80th percentile,
    # rating 7, and the +1 `_apply_position_adjustments` gives a forward.
    assert by_name["M. Salah"].offensive == 8
    assert by_name["A. Becke"].offensive == 1


def test_the_three_collapsed_attributes_take_one_value_across_the_squad(tmp_path):
    """Pins upstream fidelity deliberately; the behaviour is known to be wrong.

    `offensive` takes five values across the same five players, so this is a
    collapse of three specific attributes and not of the mapper.
    """
    p = _espn_patcher(tmp_path)
    data = p.fetch(season=2025, league_id=2001)
    mapped = p.map_rosters(data, [SlotMapping(slot_index=0, team_id=364, team_name="Liverpool")])
    attrs = [rec.attributes for rec in mapped.teams[0].players]

    assert len({a.body_balance for a in attrs}) == 1
    assert len({a.dribble for a in attrs}) == 1
    assert len({a.technique for a in attrs}) == 2
    assert len({a.offensive for a in attrs}) == 5


def test_default_slot_mapping_is_sequential_and_serialisable(patcher):
    data = patcher.fetch(season=2024, league_id=39)
    mapping = patcher.default_slot_mapping(data)

    assert [m.slot_index for m in mapping] == [0, 1, 2, 3]
    assert [m.team_id for m in mapping] == [100, 101, 102, 103]
    assert mapping[0].team_name == "Team 0"
    assert SlotMapping.from_dict(mapping[0].to_dict()) == mapping[0]


def test_default_slot_mapping_stops_at_the_master_league_slot_count(patcher):
    data = _league_data([_roster(100 + i) for i in range(40)])

    mapping = patcher.default_slot_mapping(data)

    assert MAX_ML_SLOTS == 32
    assert len(mapping) == 32
    assert mapping[-1].slot_index == 31


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
    # `filled_slots()` keys on truthiness and a `WETeamRecord` is truthy however
    # empty it is, which is why `patch` iterates `rosters.teams` instead.
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
    # The away value carries a leading `#`, which the provider sometimes sends
    # and sometimes does not.
    assert record.kit_away == (0, 255, 128)
    # Nothing in the ported writer reads `kit_third`: the maglia and flag
    # palettes read `kit_home` and `kit_away`, and the 3D jersey TEX patch reads
    # `kit_home` alone.
    assert record.kit_third == (198, 0, 0)


def _map_one(patcher, team_roster):
    """Map a single team into slot 0 and return the record it produced."""
    mapped = patcher.map_rosters(
        _league_data([team_roster]), slot_mapping=[SlotMapping(slot_index=0, team_id=100)]
    )
    return mapped.teams[0]


def test_a_team_with_no_colours_keeps_the_record_defaults(patcher):
    # `Team.color` and `Team.alternate_color` default to the empty string, which
    # `_parse_hex_colour` rejects on length, so these are `WETeamRecord`'s own
    # defaults — except `kit_third`, which takes `kit_home` and not its black one.
    record = _map_one(patcher, _roster(100))

    assert record.kit_home == (255, 255, 255)
    assert record.kit_away == (0, 0, 0)
    assert record.kit_third == (255, 255, 255)


def test_the_third_kit_mirrors_the_home_kit_whether_or_not_a_colour_arrived(patcher):
    # `kit_third` mirrors `kit_home` unconditionally: the record's own defaults
    # are white for the home kit and black for the third, so they would not match.
    supplied = _map_one(patcher, _roster(100, color="C60000"))
    absent = _map_one(patcher, _roster(100))

    assert supplied.kit_third == supplied.kit_home
    assert absent.kit_third == absent.kit_home
    assert supplied.kit_home == (198, 0, 0)
    assert absent.kit_home == (255, 255, 255)


def test_a_colour_too_short_to_be_a_triple_is_ignored_rather_than_half_parsed(patcher):
    # Three characters is a valid CSS shorthand and a valid hex integer, so a
    # length check that let it through would parse `C6` and `0` and only then
    # fail, on an empty third slice.
    record = _map_one(patcher, _roster(100, color="C60", alternate_color="#00FF80"))

    assert record.kit_home == (255, 255, 255)
    assert record.kit_third == (255, 255, 255)
    assert record.kit_away == (0, 255, 128)


def test_a_colour_longer_than_six_characters_is_ignored(patcher):
    # Eight characters is `RRGGBBAA`, which some providers send. A length test of
    # `< 6` rather than `!= 6` would accept it and guess at where the alpha sits.
    record = _map_one(patcher, _roster(100, color="C60000FF", alternate_color="#00FF80"))

    assert record.kit_home == (255, 255, 255)
    assert record.kit_third == (255, 255, 255)
    assert record.kit_away == (0, 255, 128)


def test_a_six_character_colour_that_is_not_hex_is_ignored(patcher):
    # `orange` and `purple` are exactly six characters, so they pass the length
    # check and reach `int(..., 16)`.
    record = _map_one(patcher, _roster(100, color="orange", alternate_color="purple"))

    assert record.kit_home == (255, 255, 255)
    assert record.kit_away == (0, 0, 0)
    assert record.kit_third == (255, 255, 255)


def test_a_bad_home_colour_does_not_suppress_a_good_away_colour(patcher):
    record = _map_one(patcher, _roster(100, color="orange", alternate_color="00FF80"))

    assert record.kit_home == (255, 255, 255)
    assert record.kit_away == (0, 255, 128)


def test_a_bad_away_colour_does_not_suppress_a_good_home_colour(patcher):
    record = _map_one(patcher, _roster(100, color="C60000", alternate_color="orange"))

    assert record.kit_home == (198, 0, 0)
    assert record.kit_third == (198, 0, 0)
    assert record.kit_away == (0, 0, 0)


def test_players_are_rated_against_the_whole_league_not_their_own_team(patcher):
    # One scorer in a twelve-player league sits above eleven of twelve samples: a
    # percentile of 91.7, which the 1-9 table rates 8. Rated against his own team
    # he is the only sample, lands at percentile 0, and rates 1.
    star = Player(id=1, name="Star", position="Midfielder")
    rest = [Player(id=i, name=f"P{i}", position="Midfielder") for i in range(2, 13)]
    scorers = _roster(100, players=[star], player_stats={1: _stats(1, goals=10)})
    others = _roster(101, players=rest, player_stats={p.id: _stats(p.id) for p in rest})
    data = _league_data([scorers, others])

    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])

    assert mapped.teams[0].players[0].attributes.offensive == 8
    own_team_only = patcher.mapper.map_team_with_league_context(scorers, [scorers])
    assert own_team_only.players[0].attributes.offensive == 1


def test_analyzing_a_missing_rom_raises_rom_error(patcher, tmp_path):
    with pytest.raises(RomError):
        patcher.analyze_rom(tmp_path / "nope.bin")


def test_a_file_too_small_to_be_the_game_is_reported_rather_than_raised(patcher, tmp_path):
    # `validate_rom` rejects anything under 100 MB, and an invalid ROM reports no
    # slots rather than raising.
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)

    info = patcher.analyze_rom(rom)

    assert info.is_valid is False
    assert info.slots == []
    assert info.size == 4096
    assert info.game_id == "we2002"
    assert info.extra == {"version": "Unknown"}
    assert info.to_dict()["extra"] == {"version": "Unknown"}


def test_a_file_large_enough_to_be_the_game_reports_its_thirty_two_slots(patcher, tmp_path):
    # `validate_rom`'s only test is `size >= 100 MB`, so a file of exactly that
    # size takes the valid branch. `truncate` makes it sparse: 100 MB of
    # addressable zeroes with no blocks allocated.
    rom = tmp_path / "we2002.bin"
    with rom.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)

    info = patcher.analyze_rom(rom)

    assert info.is_valid is True
    assert info.size == 104857600
    assert info.game_id == "we2002"
    assert info.path == str(rom)
    assert info.extra == {"version": "WE2002"}
    assert len(info.slots) == 32
    # `display_name` is the reader's `league_group` plus the slot's 1-based
    # number, where `index` is 0-based.
    assert info.slots[5] == RomSlot(
        index=5, current_name="ML Slot 6", display_name="Master League Slot 6"
    )
    assert info.slots[0].index == 0
    assert info.slots[31].current_name == "ML Slot 32"
    assert info.slots[31].display_name == "Master League Slot 32"


def test_every_slot_gets_its_own_display_name(patcher, tmp_path):
    # `display_name` has to stay distinct across one ROM's 32 slots: it is the
    # field a slot-picking UI lists, and WE2002 requires a slot mapping.
    rom = tmp_path / "we2002.bin"
    with rom.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)

    info = patcher.analyze_rom(rom)

    assert len({slot.display_name for slot in info.slots}) == 32


def test_an_unknown_language_is_rejected(patcher, tmp_path):
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = _valid_rom(tmp_path)

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


def test_patching_a_file_too_small_to_be_the_game_raises_rom_error(patcher, tmp_path):
    # `patch` applies `validate_rom`: without it a 4 KB file is copied to
    # `output_path` and written at offsets megabytes past its end, silently.
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = tmp_path / "we2002.bin"
    rom.write_bytes(b"\x00" * 4096)
    out = tmp_path / "out.bin"

    with pytest.raises(RomError, match="not a WE2002 ROM"):
        patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    assert out.exists() is False


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
    rom = _valid_rom(tmp_path)
    out = tmp_path / "out.bin"

    result = patcher.patch(rom_path=rom, output_path=out, rosters=mapped)

    # Slots ascending regardless of the mapping's order; players handed over
    # explicitly, because `write_team` writes none without them; the TEX flush
    # before finalisation, because queued 3D-jersey patches are dropped otherwise.
    # `skip_validation=False`: only a community `w202-english.ppf` is applied
    # unvalidated.
    assert log == [
        ("open", str(rom), str(out)),
        ("apply_ppf", False),
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
    rom = _valid_rom(tmp_path)
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


def test_patching_with_another_games_rosters_is_refused_before_the_rom_is_opened(
    patcher, tmp_path, monkeypatch
):
    # A `game_id` mismatch has to be refused: NHL94 stores a plain list per slot,
    # so the write loop raises `AttributeError`, outside `RetroRosterError`.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch, log)
    rom = _valid_rom(tmp_path)
    out = tmp_path / "out.bin"
    foreign = MappedRosters(game_id="nhl94-genesis", teams={0: ["not a WETeamRecord"]})

    with pytest.raises(MappingError, match="nhl94-genesis"):
        patcher.patch(rom_path=rom, output_path=out, rosters=foreign)

    assert log == []
    assert out.exists() is False


def test_a_slot_with_no_players_still_counts_as_a_team_patched(patcher, tmp_path, monkeypatch):
    # `teams_patched` counts slots something reached the ROM for, not slots that
    # got players: `write_team` writes the names, abbreviations, force bars, kit
    # colours and flag before it looks at `players`.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch, log)
    rom = _valid_rom(tmp_path)
    empty = MappedRosters(
        game_id="we2002",
        teams={
            0: WETeamRecord(name="Alpha", short_name="ALP", players=[]),
            1: WETeamRecord(name="Beta", short_name="BET", players=[]),
        },
    )

    result = patcher.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=empty)

    assert (result.teams_patched, result.players_patched) == (2, 0)
    assert [entry for entry in log if entry[0] == "write_team"] == [
        ("write_team", 0, "Alpha", 0, True),
        ("write_team", 1, "Beta", 0, True),
    ]


def test_patching_with_nothing_mapped_still_writes_an_output(patcher, tmp_path, monkeypatch):
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch, log)
    rom = _valid_rom(tmp_path)

    result = patcher.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert (result.teams_patched, result.players_patched) == (0, 0)
    assert log[-2:] == [("flush_tex_patches",), ("finalize",)]


def test_a_slot_the_writer_would_silently_drop_is_not_counted(patcher, tmp_path, monkeypatch):
    # `RomWriter.write_team` returns without writing for any slot outside 0..31,
    # so counting one would report a patch that never happened.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    mapped.teams[40] = mapped.teams[0]
    mapped.teams[-1] = mapped.teams[0]
    rom = _valid_rom(tmp_path)

    result = patcher.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped)

    assert [entry[1] for entry in log if entry[0] == "write_team"] == [0]
    assert (result.teams_patched, result.players_patched) == (1, 11)


def test_players_past_the_slot_capacity_are_counted_as_written_not_as_supplied(
    tmp_path, monkeypatch
):
    # `_slot_player_range` gives slot 0 fourteen places and slot 31 fifteen, and
    # `_write_players_impl` never looks past that count, so a 22-man squad in each
    # of those two slots puts 14 + 15 = 29 players into the image.
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch)
    p = _make_patcher(tmp_path)
    p.api = FakeApi(team_count=2, squad_size=22)
    data = p.fetch(season=2024, league_id=39)
    mapped = p.map_rosters(
        data,
        slot_mapping=[
            SlotMapping(slot_index=0, team_id=100),
            SlotMapping(slot_index=31, team_id=101),
        ],
    )
    rom = _valid_rom(tmp_path)

    result = p.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped)

    # All 22 are still handed over — the truncation is the writer's, not the
    # patcher's, and `patch` must not start second-guessing which ones fit.
    assert [entry[3] for entry in log if entry[0] == "write_team"] == [22, 22]
    assert result.players_patched == 29
    assert result.teams_patched == 2


def test_the_two_slot_capacities_differ_so_the_count_cannot_be_a_uniform_multiple(tmp_path):
    # Slot 0 and slot 31 have to keep different capacities, or the 29 above stops
    # separating "summed the writer's answers" from "doubled one capacity".
    assert _slot_player_range(0)[1] == 14
    assert _slot_player_range(31)[1] == 15


def test_patch_raises_when_finalisation_leaves_no_output(patcher, tmp_path, monkeypatch):
    # `RomWriter.finalize` has `pass` for a body and returns `None`, so its
    # return value cannot be checked. The output file existing can be.
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([], create_output=False))
    _silence_translation(monkeypatch)
    data = patcher.fetch(season=2024, league_id=39)
    mapped = patcher.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = _valid_rom(tmp_path)

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
    rom = _valid_rom(tmp_path)
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
    # `ensure_ppf` raises `MissingAssetError`, a `RetroRosterError` and not an
    # `OSError`; the roster patch under the translation is still the point.
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))

    def _missing(cache_dir, lang="en", assets_dir=""):
        raise MissingAssetError("no such asset")

    monkeypatch.setattr(patcher_module, "ensure_ppf", _missing)
    data = p.fetch(season=2024, league_id=39)
    mapped = p.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = _valid_rom(tmp_path)
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


def _record_applies(monkeypatch):
    """Record every `(ppf_path, skip_validation)` the patcher applies."""
    applied = []

    def _apply(bin_path, ppf_path, skip_validation=False):
        applied.append((str(ppf_path), skip_validation))
        return "fake description"

    monkeypatch.setattr(patcher_module, "apply_ppf", _apply)
    return applied


def test_a_community_ppf_is_applied_as_it_stands_and_unvalidated(tmp_path, monkeypatch):
    """Pins upstream fidelity deliberately.

    Upstream applied the operator's own `w202-english.ppf` directly, with
    validation skipped, and generated nothing for English when one was present.
    Validation is skipped because a community full translation is built against
    one specific dump: its stored size and its block at 0x9320 will not match
    every good image.
    """
    assets = tmp_path / "assets"
    assets.mkdir()
    community = assets / "w202-english.ppf"
    community.write_bytes(b"PPF20" + bytes(64))
    p = _make_patcher(tmp_path, assets_dir=assets)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(
        patcher_module, "ensure_ppf", lambda *a, **kw: pytest.fail("generated a patch instead")
    )
    applied = _record_applies(monkeypatch)

    p.patch(
        rom_path=_valid_rom(tmp_path),
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert applied == [(str(community), True)]


def test_the_generated_ppf_is_validated(tmp_path, monkeypatch):
    """Pins upstream fidelity deliberately.

    Every generator in this tree emits PPF1, which carries neither a stored file
    size nor the 0x9320 block, and `apply_ppf` checks neither for that format.
    This is the call shape that would speak up if a generator emitted PPF2.
    """
    p = _make_patcher(tmp_path)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: str(tmp_path / "gen.ppf"))
    applied = _record_applies(monkeypatch)

    p.patch(
        rom_path=_valid_rom(tmp_path),
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert applied == [(str(tmp_path / "gen.ppf"), False)]


def test_a_community_ppf_is_not_applied_whole_for_another_language(tmp_path, monkeypatch):
    # The file is an English full translation; applying it for a Spanish request
    # would give Spanish-requested menus in English.
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "w202-english.ppf").write_bytes(b"PPF20" + bytes(64))
    p = _make_patcher(tmp_path, assets_dir=assets)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: str(tmp_path / "es.ppf"))
    applied = _record_applies(monkeypatch)

    p.patch(
        rom_path=_valid_rom(tmp_path),
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
        language="es",
    )

    assert applied == [(str(tmp_path / "es.ppf"), False)]


def test_a_directory_named_like_the_community_ppf_is_not_a_community_ppf(tmp_path, monkeypatch):
    # `is_file`, not `exists`: a directory of that name reaches `apply_ppf`'s
    # `open` and raises `IsADirectoryError`, which is caught, so the ISO would
    # ship with Japanese menus under a silent "skipped".
    assets = tmp_path / "assets"
    (assets / "w202-english.ppf").mkdir(parents=True)
    p = _make_patcher(tmp_path, assets_dir=assets)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: str(tmp_path / "gen.ppf"))
    applied = _record_applies(monkeypatch)

    p.patch(
        rom_path=_valid_rom(tmp_path),
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert applied == [(str(tmp_path / "gen.ppf"), False)]


def test_no_assets_dir_means_no_community_ppf(tmp_path, monkeypatch):
    p = _make_patcher(tmp_path)
    assert p.assets_dir is None
    assert p._community_ppf("en") is None


def test_a_broken_patch_file_is_reported_and_the_patch_continues(tmp_path, monkeypatch):
    status = []
    p = _make_patcher(tmp_path, on_status=status.append)
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    monkeypatch.setattr(patcher_module, "ensure_ppf", lambda *a, **kw: "unused.ppf")

    def _broken(bin_path, ppf_path, skip_validation=False):
        raise PPFError("Unsupported PPF format")

    monkeypatch.setattr(patcher_module, "apply_ppf", _broken)
    rom = _valid_rom(tmp_path)

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
    rom = _valid_rom(tmp_path)

    result = p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert result.teams_patched == 0
    missing = tmp_path / "gone.ppf"
    assert status == [
        "Preparing ROM...",
        f"English translation skipped: [Errno 2] No such file or directory: '{missing}'",
        "Saving patched ROM...",
    ]


def test_a_community_file_that_is_not_ppf2_is_reported_and_the_patch_continues(
    tmp_path, monkeypatch
):
    # `menu_records._parse_ppf2` raises a bare `ValueError` for a community
    # `w202-english.ppf` that is not PPF2, and nothing between there and here
    # converts it. Spanish and not English: English with a community file present
    # applies it as it stands and never reaches `ensure_ppf`.
    #
    # The bytes below are this project's own; no community patch is in the tree.
    status = []
    p = WE2002Patcher(
        cache_dir=tmp_path / "cache",
        on_status=status.append,
        assets_dir=tmp_path / "assets",
    )
    p.api = FakeApi()
    (tmp_path / "assets").mkdir()
    community = tmp_path / "assets" / "w202-english.ppf"
    community.write_bytes(b"PPF30" + bytes(64))
    log = []
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class(log))
    _silence_translation(monkeypatch)
    data = p.fetch(season=2024, league_id=39)
    mapped = p.map_rosters(data, slot_mapping=[SlotMapping(slot_index=0, team_id=100)])
    rom = _valid_rom(tmp_path)

    result = p.patch(rom_path=rom, output_path=tmp_path / "out.bin", rosters=mapped, language="es")

    assert result.teams_patched == 1
    assert result.players_patched == 11
    assert status == [
        "Preparing ROM...",
        f"Spanish translation skipped: Not a PPF2 file: {community}",
        "Saving patched ROM...",
    ]
    assert log[-2:] == [("flush_tex_patches",), ("finalize",)]


def test_the_assets_directory_is_forwarded_to_the_translation(tmp_path, monkeypatch):
    seen = []
    p = WE2002Patcher(cache_dir=tmp_path / "cache", assets_dir=tmp_path / "assets")
    p.api = FakeApi()
    monkeypatch.setattr(patcher_module, "RomWriter", _fake_writer_class([]))
    _silence_translation(monkeypatch)

    def _record(cache_dir, lang="en", assets_dir=""):
        seen.append((cache_dir, lang, assets_dir))
        return "unused.ppf"

    monkeypatch.setattr(patcher_module, "ensure_ppf", _record)
    rom = _valid_rom(tmp_path)

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
    rom = _valid_rom(tmp_path)

    p.patch(
        rom_path=rom,
        output_path=tmp_path / "out.bin",
        rosters=MappedRosters(game_id="we2002"),
    )

    assert seen == [""]


@pytest.mark.real_rom
@pytest.mark.skipif(
    not os.environ.get("RETRO_ROSTER_TEST_ROMS"),
    reason="set RETRO_ROSTER_TEST_ROMS to a directory holding we2002.bin",
)
def test_patching_a_real_rom_produces_a_readable_output(patcher, tmp_path):
    # Fail rather than skip when the variable is set and the file is missing.
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
