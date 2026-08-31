from retro_roster_patcher.sports import (
    League,
    LeagueData,
    Player,
    Team,
    TeamRoster,
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
