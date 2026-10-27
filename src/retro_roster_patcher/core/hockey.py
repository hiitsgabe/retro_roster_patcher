"""Where a hockey player sits in his team's lineup, from his season line.

A line is the current season's once it is `MIN_GAMES` old and the previous
season's before that (`sports.espn`), so totals are not comparable between two
players: twelve games of a star score fewer points than a full season of a
depth forward. Rates are. Ice time per game is how a coach deploys a player,
so it orders the lines and pairs; a goalie's share of his team's starts finds
the starter. Before either, whether he dressed for the club's last games.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Seconds of ice time a game dressed is worth.
DRESSED_BONUS = 100


def _num(line: Mapping[str, object], name: str) -> float:
    value = line.get(name)
    return float(value) if isinstance(value, int | float) else 0.0


def deployment(line: Mapping[str, object], goalie: bool) -> float:
    """Higher plays more: in the lineup lately (a goalie: how often), then for a goalie his
    share of the team's starts (ESPN), else his games, wins or save %; for a
    skater seconds on ice per game (ESPN), else points per game."""
    # Dressed lately (ESPN's game rosters) comes first: an injured, scratched
    # or sent-down player keeps his season line but is out of the lineup. For
    # a goalie how often (the one recalled for the last games is the backup
    # now); for a skater whether, plus `DRESSED_BONUS` per game: one missed
    # game must not drop a star a line, but it separates the fourth-liners.
    dressed = _num(line, "dressed")
    if goalie:
        return dressed * 10_000 + _rate(line, goalie)
    return (dressed > 0) * 10_000 + dressed * DRESSED_BONUS + _rate(line, goalie)


def _rate(line: Mapping[str, object], goalie: bool) -> float:
    games = _num(line, "GP") or _num(line, "games")
    if goalie:
        team_games = _num(line, "teamGamesPlayed")
        started = _num(line, "gameStarted")
        if team_games and started:
            return started / team_games
        # Without starts, games then wins; a line with neither has only its
        # save % (below 1, so any goalie with games outranks it).
        return games or _num(line, "W") or _num(line, "SV%") / 100
    toi = _num(line, "timeOnIcePerGame")
    return toi or _num(line, "PTS") / max(games, 1.0)


if __name__ == "__main__":
    star = {"GP": 12, "PTS": 34, "timeOnIcePerGame": 1432.8}
    depth = {"GP": 72, "PTS": 56, "timeOnIcePerGame": 1143.8}
    assert deployment(star, False) > deployment(depth, False)
    assert deployment({"GP": 12, "PTS": 34}, False) > deployment({"GP": 72, "PTS": 56}, False)
    starter = {"GP": 10, "gameStarted": 10, "teamGamesPlayed": 12}
    backup = {"GP": 30, "gameStarted": 25, "teamGamesPlayed": 82}
    assert deployment(starter, True) > deployment(backup, True)
    assert deployment({**backup, "dressed": 3}, True) > deployment({**starter, "dressed": 1}, True)
    assert deployment({**star, "dressed": 2}, False) > deployment({**depth, "dressed": 3}, False)
