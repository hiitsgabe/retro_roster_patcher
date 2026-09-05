import json
from dataclasses import replace

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
                # The real shape: both producers key this blob by `str` player id
                # and the consumer reads `leaders.get(str(player.id), {})`.
                extra={"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}},
            ),
            # `loading` and `error` sit at their dataclass defaults on the roster
            # above; this one is in a non-default state for both.
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
    # `[18.0] == [18]` is True and the hashes match, so neither the value comparison
    # above nor a whole-object equality can tell an `int` key from a `float` one.
    # `TeamRoster.player_stats` is declared `dict[int, PlayerStats]`.
    assert type(list(restored.teams[0].player_stats)[0]) is int
    assert type(restored.teams[0].player_stats[18]) is PlayerStats
    # And it lands in the file itself: `str(18)` is `"18"` but `str(18.0)` is
    # `"18.0"`, so a key of the wrong numeric type rewrites the rosters file's key
    # text between `fetch` and `patch`.
    assert '"player_stats": {"18": ' in json.dumps(league_data_to_dict(restored))


def _with_absences(*names: str) -> LeagueData:
    return LeagueData(
        league=League(id=39, name="Premier League"),
        teams=[
            TeamRoster(
                team=Team(id=33, name="Manchester United"),
                player_stats={18: replace(stats(18), unsupplied=names)},
            )
        ],
    )


def test_which_stats_a_provider_never_measured_survives_the_json_round_trip():
    # `fetch` and `patch` are separate processes with this file between them, so a
    # provider's absences have to reach the mapper through JSON. The round trip is
    # through real `json.dumps`, which is also the proof the field is not a `set`.
    original = _with_absences("duels_total", "duels_won")
    restored = round_trip(original)
    assert restored.teams[0].player_stats[18].unsupplied == ("duels_total", "duels_won")
    assert restored == original


def test_the_absences_come_back_as_a_tuple_and_not_the_json_array():
    # `("duels_total",) == ["duels_total"]` is False, but every consumer asks
    # `name in unsupplied`, which both shapes answer identically. The declared type
    # is what has to be asserted.
    restored = round_trip(_with_absences("duels_total"))
    assert type(restored.teams[0].player_stats[18].unsupplied) is tuple


def test_a_file_written_before_the_field_existed_reads_as_fully_measured():
    # Every rosters file written before this field existed came from a provider that
    # measured all twenty stats. Absent has to mean "nothing is missing".
    raw = league_data_to_dict(sample())
    del raw["teams"][0]["player_stats"][18]["unsupplied"]
    restored = league_data_from_dict(raw)
    assert restored.teams[0].player_stats[18].unsupplied == ()


def test_the_extra_blob_passes_through_untouched():
    restored = round_trip(sample())
    assert restored.teams[0].extra == {"leaders": {"8471675": {"G": 42, "A": 54, "PTS": 96}}}


def test_extra_keys_are_passed_through_rather_than_re_keyed():
    # No `json.dumps` hop: `league_data_to_dict` output goes straight back in, so a
    # non-`str` key reaches the reader and the claim is that it survives unchanged.
    # Deliberately not the production path -- in production the reader is only
    # reached through `to_dict` -> `dumps` -> `loads`, after which every key is a
    # `str`, which is why `player_stats` needs its `int(pid)` conversion and `extra`
    # must not have one.
    data = LeagueData(
        league=League(id=1, name="N"),
        teams=[TeamRoster(team=Team(id=1, name="X"), extra={7: "seven"})],
    )
    restored = league_data_from_dict(league_data_to_dict(data))
    assert list(restored.teams[0].extra) == [7]
    assert type(list(restored.teams[0].extra)[0]) is int


def test_the_extra_blob_is_shallow_copied_rather_than_aliased():
    # `extra` is the only field handed over whole instead of rebuilt from its parts,
    # so without the `dict(...)` a write through the roster reaches back into the
    # caller's parsed JSON.
    #
    # `dict(...)` copies one level and that is the whole guarantee, so the second
    # half of this test pins the sharp edge: the nested dicts are the *same objects*
    # on both sides. Do not deepen the copy -- `extra` is a provider-defined escape
    # hatch of arbitrary size and nothing under `src/` mutates one in place.
    source = {"leaders": {"8471675": {"G": 42}}}
    raw = {
        "league": {"id": 1, "name": "N"},
        "teams": [{"team": {"id": 1, "name": "X"}, "extra": source}],
    }
    restored = league_data_from_dict(raw)
    restored.teams[0].extra["POISON"] = 1
    assert source == {"leaders": {"8471675": {"G": 42}}}
    # And the level below, which the copy does not reach.
    assert restored.teams[0].extra["leaders"] is source["leaders"]
    restored.teams[0].extra["leaders"]["POISON"] = 1
    assert source == {"leaders": {"8471675": {"G": 42}, "POISON": 1}}


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
    # roster is missing at once. Absent is only one of the two shapes the `or []` /
    # `or {}` guards absorb; a present `null` is the other, and it is what separates
    # `X or D` from `raw.get(k, D)`.
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


def test_absent_containers_are_read_as_empty():
    # The three guards the test above does not reach: `league`, `teams`, and a
    # roster's own `team`. `league` and `team` are handed to `_only_declared`, so
    # unguarded they raise `AttributeError: 'NoneType' object has no attribute
    # 'items'` instead of the `TypeError` naming the `id` and `name` the file lacks.
    # `teams` never reaches `_only_declared` -- it is the iterable of a list
    # comprehension -- so there is no wrong-exception contrast to draw and the
    # guarded empty list is the whole claim.
    assert league_data_from_dict({"league": {"id": 1, "name": "N"}}).teams == []
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"teams": []})
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 1, "name": "N"}, "teams": [{}]})


def test_a_present_json_null_is_absorbed_like_an_absent_key():
    # `null` is legal JSON and a writer that emits every key sends it where this one
    # omits the key. That producer is why the six container guards -- `league`,
    # `teams`, a roster's own `team`, `players`, `player_stats` and `extra` -- are
    # `raw.get(k) or DEFAULT` rather than `raw.get(k, DEFAULT)`: the two forms agree
    # on an absent key and differ only on a present `null`.
    #
    # `error` is the seventh `or`, and being a scalar what a `get` default lets
    # through is worse: the `str()` around it would render a `null` as the *string*
    # `"None"`, non-empty and so an error to any consumer that tests for one.
    # `loading` is written `raw.get("loading", False)`, correctly: `bool(None)` is
    # already `False`.
    roster = json.loads(
        '{"league": {"id": 1, "name": "N"},'
        ' "teams": [{"team": {"id": 1, "name": "X"},'
        ' "players": null, "player_stats": null, "extra": null,'
        ' "loading": null, "error": null}]}'
    )
    restored = league_data_from_dict(roster)
    assert restored.teams[0].players == []
    assert restored.teams[0].player_stats == {}
    assert restored.teams[0].extra == {}
    assert restored.teams[0].loading is False
    assert restored.teams[0].error == ""
    # `teams` is the iterable of a comprehension: a `null` reaching it is a
    # `TypeError` at the comprehension, not a field holding `None`, so the empty
    # list is again the whole claim.
    nulled_teams = json.loads('{"league": {"id": 1, "name": "N"}, "teams": null}')
    assert league_data_from_dict(nulled_teams).teams == []
    # `league` and a roster's own `team` go to `_only_declared`, which calls
    # `.items()`. Absorbing the `null` into `{}` is what turns an
    # `AttributeError: 'NoneType' object has no attribute 'items'` into the
    # `TypeError` that names the `id` and `name` the payload actually lacks.
    nulled_league = json.loads('{"league": null, "teams": []}')
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict(nulled_league)
    nulled_team = json.loads('{"league": {"id": 1, "name": "N"}, "teams": [{"team": null}]}')
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict(nulled_team)


def test_json_legal_values_of_the_wrong_type_are_coerced_to_the_declared_one():
    # Leniency about optional fields extends to their type. A hand-edited or
    # older-schema file can legally carry `1` for `loading` and a bare number for
    # `error` -- both are valid JSON, neither is the declared `bool` / `str`. The
    # `bool()` and `str()` calls on the read side are the only thing standing
    # between those and a typed field holding something it does not declare.
    #
    # For a value that survives `error`'s `or ""` -- anything truthy -- coercion
    # is also the *whole* of what the read side does. The second roster's `error`
    # is already a `str`, and a number cannot carry the thing that shows it is
    # passed through verbatim, so it carries the surrounding whitespace a provider
    # message plausibly arrives with: it comes back as written rather than tidied.
    #
    # The third and fourth rosters are the other side of that `or`, and the price
    # of it: a falsy `error` is read as "no error" rather than coerced, so `0`
    # does not become `"0"` nor `false` `"False"`. Both of those strings are
    # non-empty, which is what a consumer reads as a failure, so on this field
    # dropping the value is the coercion that preserves the meaning. `loading`
    # has no such rule -- it is `bool()` of whatever is there, and `0` is `False`
    # because `bool(0)` is, not because anything absorbed it.
    raw = {
        "league": {"id": 1, "name": "N"},
        "teams": [
            {"team": {"id": 1, "name": "X"}, "loading": 1, "error": 404},
            {"team": {"id": 2, "name": "Y"}, "error": "  rate limited\n"},
            {"team": {"id": 3, "name": "Z"}, "loading": 0, "error": 0},
            {"team": {"id": 4, "name": "W"}, "error": False},
        ],
    }
    restored = league_data_from_dict(raw)
    assert restored.teams[0].loading is True
    assert restored.teams[0].error == "404"
    assert restored.teams[1].error == "  rate limited\n"
    assert restored.teams[2].error == ""
    assert restored.teams[2].loading is False
    assert restored.teams[3].error == ""


def test_a_payload_missing_a_required_field_raises():
    # Leniency stops at fields with defaults. `id` and `name` have none, so a
    # truncated file is an error rather than a silently half-built league.
    with pytest.raises(TypeError, match="name"):
        league_data_from_dict({"league": {"id": 39}, "teams": []})


def test_an_empty_roster_round_trips():
    empty = LeagueData(league=League(id=1, name="NHL"), teams=[])
    assert round_trip(empty) == empty
