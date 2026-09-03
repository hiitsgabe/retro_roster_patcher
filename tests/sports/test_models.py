from dataclasses import replace

from retro_roster_patcher.sports import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)

# Every field but `lineups` and `unsupplied` is required, so spell them all out
# once and vary one at a time with `dataclasses.replace`.
_BASE_STATS = PlayerStats(
    player_id=1,
    appearances=1,
    minutes=90,
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
)


def test_a_league_data_can_be_built_end_to_end():
    league = League(id=39, name="Premier League", country="England")
    team = Team(id=33, name="Manchester United")
    player = Player(id=874, name="Cristiano Ronaldo")
    roster = TeamRoster(team=team, players=[player])
    data = LeagueData(league=league, teams=[roster])

    assert data.league.name == "Premier League"
    assert data.teams[0].players[0].name == "Cristiano Ronaldo"


def test_team_roster_defaults_are_empty_and_not_loading():
    roster = TeamRoster(team=Team(id=1, name="X"))
    assert roster.players == []
    assert roster.player_stats == {}
    assert roster.loading is False
    assert roster.error == ""


def test_provider_specific_data_survives_on_the_roster_extra():
    roster = TeamRoster(
        team=Team(id=1, name="X"),
        players=[Player(id=7, name="Y")],
        extra={"leaders": {"7": {"goals": 40}}},
    )
    assert roster.extra["leaders"]["7"]["goals"] == 40
    assert TeamRoster(team=Team(id=1, name="X")).extra == {}


def test_a_player_stats_built_without_the_field_claims_every_stat_was_measured():
    # The default is what keeps `api_football` -- which names no absences and
    # measures all twenty -- producing exactly what it produced before this field
    # existed, and what makes a rosters file written before it deserialise as the
    # fully-measured record it is.
    assert _BASE_STATS.unsupplied == ()


def test_a_named_absence_is_kept_verbatim():
    stats = replace(_BASE_STATS, unsupplied=("duels_total", "duels_won"))
    assert stats.unsupplied == ("duels_total", "duels_won")


def test_a_list_of_absences_is_normalised_to_a_tuple():
    # `serde` hands this back as the JSON array it became on the way out. Without
    # the conversion a round-tripped record is unequal to the one written, in a
    # way the `in` test every consumer runs cannot see.
    stats = replace(_BASE_STATS, unsupplied=["dribbles_success"])
    assert type(stats.unsupplied) is tuple
    assert stats.unsupplied == ("dribbles_success",)
