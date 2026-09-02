"""Sports data sources, shared across game patchers.

The three API-Football exceptions are re-exported here because the clients are a
deliverable of this extraction in their own right: a consumer calling
`api_football.ApiFootballClient` directly needs to be able to name the free-plan
and quota failures without importing from the implementation module. All three
are `ApiError` subclasses, so a consumer that only wants "the provider failed"
can keep catching `ApiError` from `retro_roster_patcher.core.errors`.
"""

from .api_football import DailyLimitError, RateLimitError, SeasonNotAvailableError
from .models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)

__all__ = [
    "DailyLimitError",
    "League",
    "LeagueData",
    "Player",
    "PlayerStats",
    "RateLimitError",
    "SeasonNotAvailableError",
    "Team",
    "TeamRoster",
]
