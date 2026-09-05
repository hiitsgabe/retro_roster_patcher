"""Sports data sources, shared across game patchers.

Neither client takes a credential: every endpoint this package reads is keyless.
A consumer that wants "the provider failed" catches `ApiError` from
`retro_roster_patcher.core.errors`.

`Transport` is re-exported from the private `_http` because it types a public
keyword parameter on the clients and the patchers; it is the same object, not an
alias. `team_colors` is re-exported as a module rather than unpacked, because it
is a bag of cache functions over one JSON file.
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
