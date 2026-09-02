"""Sports data sources, shared across game patchers.

The three clients are a deliverable of this extraction in their own right, so
they are exported beside the models they return. The three API-Football
exceptions are here for the same reason: a consumer calling
`ApiFootballClient` directly needs to be able to name the free-plan and quota
failures without importing from the implementation module. All three are
`ApiError` subclasses, so a consumer that only wants "the provider failed" can
keep catching `ApiError` from `retro_roster_patcher.core.errors`.

`Transport` is re-exported from the private `_http` because it is the type of a
public keyword parameter on seven callables — the three clients, the two
patchers, `TimGenerator` and `_http.get_json` — and supplying one is the stated
reason `_http` exists. A consumer should not have to import from an underscore
module to annotate an argument the library asks it for. The definition stays in
`_http`: this is the same object, not a second alias, which
`tests/test_public_api.py` asserts by identity.

`team_colors` is re-exported as a module, not unpacked into this namespace: it
is a bag of cache functions over one JSON file, and `load_color_cache` beside
`League` would read as one namespace where there are two. Exported at all
because API-Football ships no team colours, so a UI has to offer the user a
palette and remember the choice, and until now nothing in any `__all__` said
the library already does that.
"""

from . import team_colors
from ._http import Transport
from .api_football import (
    ApiFootballClient,
    DailyLimitError,
    RateLimitError,
    SeasonNotAvailableError,
)
from .espn import EspnClient
from .models import (
    League,
    LeagueData,
    Player,
    PlayerStats,
    Team,
    TeamRoster,
)
from .nhl import NhlApiClient

__all__ = [
    "ApiFootballClient",
    "DailyLimitError",
    "EspnClient",
    "League",
    "LeagueData",
    "NhlApiClient",
    "Player",
    "PlayerStats",
    "RateLimitError",
    "SeasonNotAvailableError",
    "Team",
    "TeamRoster",
    "Transport",
    "team_colors",
]
