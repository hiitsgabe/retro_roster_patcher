"""Sports data sources, shared across game patchers.

The three API-Football exceptions are re-exported here because the clients are a
deliverable of this extraction in their own right: a consumer calling
`api_football.ApiFootballClient` directly needs to be able to name the free-plan
and quota failures without importing from the implementation module. All three
are `ApiError` subclasses, so a consumer that only wants "the provider failed"
can keep catching `ApiError` from `retro_roster_patcher.core.errors`.

`team_colors` is re-exported as a module, not unpacked into this namespace: it
is a bag of cache functions over one JSON file, and `load_color_cache` beside
`League` would read as one namespace where there are two. Exported at all
because API-Football ships no team colours, so a UI has to offer the user a
palette and remember the choice, and until now nothing in any `__all__` said
the library already does that.
"""

from . import team_colors
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
    "team_colors",
]
