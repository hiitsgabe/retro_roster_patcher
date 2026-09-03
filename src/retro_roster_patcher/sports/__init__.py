"""Sports data sources, shared across game patchers.

The two clients are a deliverable of this extraction in their own right, so they
are exported beside the models they return. Neither takes a credential: every
endpoint this package reads is keyless.

This package used to export three `ApiError` subclasses — `RateLimitError`,
`DailyLimitError` and `SeasonNotAvailableError` — so that a consumer could name
API-Football's free-plan and quota failures. That client is gone and nothing
else ever raised them, so they are gone too rather than left as an `except`
clause that can never fire. A consumer that wants "the provider failed" catches
`ApiError` from `retro_roster_patcher.core.errors`, which is what it always was.

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
because no provider here ships team colours, so a UI has to offer the user a
palette and remember the choice, and until now nothing in any `__all__` said
the library already does that.
"""

from . import team_colors
from ._http import Transport
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
    "EspnClient",
    "League",
    "LeagueData",
    "NhlApiClient",
    "Player",
    "PlayerStats",
    "Team",
    "TeamRoster",
    "Transport",
    "team_colors",
]
