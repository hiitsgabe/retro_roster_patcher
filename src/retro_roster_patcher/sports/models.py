"""Shared data models for sports API clients (ESPN, the NHL API)."""

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

    Every count is `int` or `float` and never `None`, so `unsupplied` is what
    separates a filler `0` from a measured zero.
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
    # Names of the fields above whose value is filler rather than a measurement.
    # A tuple, not a set: `json.dumps` raises on a set.
    unsupplied: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Coerce back to a tuple, or a JSON round-trip is silently unequal:
        # `("duels_total",) != ["duels_total"]`.
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
    error: str = ""  # Non-empty if this team's squad fetch failed
    # Provider-shaped data that the flat per-player PlayerStats cannot hold.
    # Only the patcher that put a key here reads it back.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class LeagueData:
    league: League
    teams: list[TeamRoster] = field(default_factory=list)
