import json

import pytest

from retro_roster_patcher.sports.models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)
from retro_roster_patcher.sports.serde import league_data_from_dict, league_data_to_dict


def stats(player_id: int) -> PlayerStats:
    """`PlayerStats` declares every field but `lineups` as required, so spell them all out."""
    return PlayerStats(
        player_id=player_id,
        appearances=30,
        minutes=2450,
        goals=4,
        assists=3,
        shots_total=21,
        shots_on=9,
        passes_total=1580,
        passes_accuracy=88.4,
        tackles_total=64,
        interceptions=31,
        blocks=12,
        duels_total=310,
        duels_won=171,
        dribbles_attempts=22,
        dribbles_success=11,
        fouls_committed=41,
        fouls_drawn=28,
        cards_yellow=7,
        cards_red=1,
        rating=7.1,
        lineups=28,
    )


def sample() -> LeagueData:
    return LeagueData(
        league=League(id=39, name="Premier League", country="England", season=2024),
        teams=[
            TeamRoster(
                team=Team(id=33, name="Manchester United", code="MUN", color="DA291C"),
                players=[
                    Player(id=18, name="Casemiro", position="Midfielder", number=18),
                    Player(id=19, name="Onana", position="Goalkeeper", number=24),
                ],
                player_stats={18: stats(18)},
                # The real shape. Both producers of this blob key it by `str`
                # player id -- `espn.py`'s `_extract_pid` returns `str | None`,
                # `nhl.py` does `str(sk.get("playerId", ""))` -- and the consumer
                # reads it back as `leaders.get(str(player.id), {})`. A blob keyed
                # by `int` would not survive JSON, so using the real shape here is
                # what makes the pass-through claim meaningful.
                extra={"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}},
            ),
            # `loading` and `error` sit at their dataclass defaults on the roster
            # above, so nothing there can tell whether the reader sets them at
            # all. This one is in a non-default state for both, and gives the
            # team list a length and an order worth asserting.
            TeamRoster(
                team=Team(id=40, name="Liverpool", code="LIV"),
                loading=True,
                error="rate limited",
            ),
        ],
    )


def round_trip(data: LeagueData) -> LeagueData:
    """Through real JSON, which is the only trip that matters."""
    return league_data_from_dict(json.loads(json.dumps(league_data_to_dict(data))))


def test_a_league_survives_a_full_json_round_trip():
    original = sample()
    assert round_trip(original) == original


def test_player_stats_keys_come_back_as_ints():
    restored = round_trip(sample())
    assert list(restored.teams[0].player_stats) == [18]
    assert type(restored.teams[0].player_stats[18]) is PlayerStats


def test_the_extra_blob_passes_through_untouched():
    restored = round_trip(sample())
    assert restored.teams[0].extra == {"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}}


def test_a_loading_roster_keeps_its_state_and_its_place_in_the_list():
    restored = round_trip(sample())
    assert [t.team.code for t in restored.teams] == ["MUN", "LIV"]
    assert restored.teams[1].loading is True
    assert restored.teams[1].error == "rate limited"
    assert restored.teams[0].loading is False
    assert restored.teams[0].error == ""


def test_unknown_keys_are_ignored_so_newer_files_still_load():
    raw = league_data_to_dict(sample())
    raw["league"]["some_future_field"] = "whatever"
    raw["teams"][0]["team"]["stadium"] = "Old Trafford"
    # All four nested types filter independently, so plant a future key in each.
    # `player_stats` is still keyed by `int` here: this is `league_data_to_dict`
    # output that has not been through `json.dumps`.
    raw["teams"][0]["players"][0]["preferred_foot"] = "right"
    raw["teams"][0]["player_stats"][18]["expected_goals"] = 3.7
    restored = league_data_from_dict(raw)
    assert restored.league.name == "Premier League"
    assert restored.teams[0].team.name == "Manchester United"
    assert restored.teams[0].players[0].name == "Casemiro"
    assert restored.teams[0].player_stats[18] == stats(18)


def test_optional_keys_may_be_absent_entirely():
    # A hand-written file, not one this module produced: every optional key of a
    # roster is missing. This is the only test that reaches the `or []` / `or {}`
    # guards on `players`, `player_stats` and `extra`; the three remaining guards
    # are covered by the test below it.
    raw = {
        "league": {"id": 39, "name": "Premier League"},
        "teams": [{"team": {"id": 1, "name": "X"}}],
    }
    restored = league_data_from_dict(raw)
    assert restored.league.country == ""
    assert restored.league.season == 0
    assert restored.teams[0].players == []
    assert restored.teams[0].player_stats == {}
    assert restored.teams[0].extra == {}
    assert restored.teams[0].loading is False
    assert restored.teams[0].error == ""


def test_absent_containers_are_read_as_empty_rather_than_as_none():
    # The other three guards: `league`, `teams`, and a roster's own `team`. Absent
    # is not the same as empty here -- `raw.get` yields `None`, and unguarded that
    # reaches `_only_declared` as an `AttributeError` about `NoneType` instead of a
    # `TypeError` naming the fields the file actually lacks.
    assert league_data_from_dict({"league": {"id": 1, "name": "N"}}).teams == []
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"teams": []})
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 1, "name": "N"}, "teams": [{}]})


def test_a_payload_missing_a_required_field_raises():
    # Leniency stops at fields with defaults. `id` and `name` have none, so a
    # truncated file is an error rather than a silently half-built league.
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 39}, "teams": []})


def test_an_empty_roster_round_trips():
    empty = LeagueData(league=League(id=1, name="NHL"), teams=[])
    assert round_trip(empty) == empty
