"""Shared data models for sports API clients (API-Football, ESPN, etc.)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class League:
    id: int
    name: str
    country: str = ""
    country_code: str = ""
    logo_url: str = ""
    season: int = 0
    teams_count: int = 0


@dataclass
class Player:
    id: int
    name: str
    first_name: str = ""
    last_name: str = ""
    age: int = 0
    nationality: str = ""
    position: str = ""  # Soccer: "Goalkeeper"/"Defender"/etc. Hockey: "C"/"LW"/"RW"/"D"/"G"
    number: int | None = None
    photo_url: str = ""
    # Optional hockey fields
    weight: float = 0.0  # lbs
    handedness: str = ""  # "L" or "R" (throw hand)
    bats: str = ""  # "L", "R", or "B" (bat hand, baseball only)


@dataclass
class PlayerStats:
    """Detailed per-season stats for one player, from whichever provider had them.

    Every count below is declared `int` or `float` and is never `None`, so a
    consumer can do arithmetic on it without a guard. That leaves nowhere to say
    "this provider does not measure this", and a provider that does not measure
    something has to write `0` -- which reads as a *measurement* of zero and
    ranks the player at the bottom of the league for it. The two are opposite
    facts: a player who genuinely won no duels should rate low, and a player
    whose provider never reported duels should not be rated on duels at all.

    `unsupplied` is where that distinction lives. It names the fields on this
    object that carry a filler value rather than a measurement, so a consumer can
    tell the difference without knowing which provider it is reading.
    """

    player_id: int
    appearances: int
    minutes: int
    goals: int
    assists: int
    shots_total: int
    shots_on: int
    passes_total: int
    passes_accuracy: float  # percentage
    tackles_total: int
    interceptions: int
    blocks: int
    duels_total: int
    duels_won: int
    dribbles_attempts: int
    dribbles_success: int
    fouls_committed: int
    fouls_drawn: int
    cards_yellow: int
    cards_red: int
    rating: float | None  # Provider's own average match rating, if it publishes one
    lineups: int = 0  # Times in starting XI
    # Names of the fields above whose value is filler, because the provider that
    # built this object does not report that statistic at all.
    #
    # Empty by default, which is the only default that can be right: it means
    # "everything here was measured", and every producer and every rosters file
    # written before this field existed meant exactly that. Naming the *absences*
    # rather than the presences is what keeps that true -- a `supplied` set would
    # need a default enumerating all twenty fields, and an older file, which
    # names none of them, would deserialise as a player about whom nothing is
    # known.
    #
    # A `tuple` and not a `set` or `frozenset` because this crosses `serde`:
    # `json.dumps` has no representation for a set and raises on one, where a
    # tuple round-trips through a JSON array. `__post_init__` converts the list
    # that comes back, so a round-tripped `PlayerStats` compares equal to the one
    # that was written.
    unsupplied: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # `serde` rebuilds this from JSON, where the tuple has become a list.
        # Without this the round-trip is unequal in a way that no field-by-field
        # reader would notice: `("duels_total",) != ["duels_total"]`, but both
        # answer the `in` test every consumer actually runs.
        self.unsupplied = tuple(self.unsupplied)


@dataclass
class Team:
    id: int
    name: str
    short_name: str = ""
    code: str = ""  # 3-letter abbreviation
    logo_url: str = ""
    country: str = ""
    color: str = ""  # Primary hex color (e.g. "C60000")
    alternate_color: str = ""  # Secondary hex color


@dataclass
class TeamRoster:
    team: Team
    players: list[Player] = field(default_factory=list)
    player_stats: dict[int, PlayerStats] = field(default_factory=dict)
    loading: bool = False  # True while squad is still being fetched
    error: str = ""  # Non-empty if squad fetch failed (e.g. rate limit)
    # Provider-shaped data that PlayerStats cannot hold. ESPN and the NHL API
    # return "team leaders" as a nested dict keyed by player id; PlayerStats is
    # an API-Football shape and would lose it. Only the patcher that put a key
    # here reads it back.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LeagueData:
    league: League
    teams: list[TeamRoster] = field(default_factory=list)
