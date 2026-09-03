"""Patch real-world sports rosters into retro game ROMs.

Importing this package imports every game package, which is what populates the
registry. Import cost is trivial (pure Python, no I/O at import time) and the
alternative — lazy discovery — makes `list_patchers()` order-dependent. The one
module deliberately left out of that is `games.we2002.tim_generator`, which
imports the optional `images` extra; `games/we2002/__init__.py` says why.

`__all__` below is this library's whole root surface and
`tests/test_public_api.py` pins it exactly. `RomFinder` is here rather than
behind its dotted path because the two consumers this library was extracted for
— a pygame launcher and a Flutter app over embedded CPython — both have to
locate a ROM before they can patch one, and nothing in any `__all__` said the
library already does that. The rest of the extracted services are exported from
the package they belong to: `sports.team_colors`, `games.we2002.AfsHandler`,
`games.we2002.CsvHandler`, `games.we2002.TimGenerator`.
"""

# These core and sports imports must stay ahead of the game imports at the bottom of
# the block. A game module is only ever safe to import `retro_roster_patcher.core.*`
# directly, never the package root: while the game imports below are running, this
# module is still partially initialised, so `from retro_roster_patcher import Patcher`
# would resolve only by accident of ordering and break the moment the block is reordered.
from .core.assets import MissingAssetError
from .core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RetroRosterError,
    RomError,
    StorageError,
)
from .core.models import MappedRosters, PatchResult, RomInfo, RomSlot, SlotMapping
from .core.patcher import Patcher
from .core.registry import PatcherInfo, get_patcher, list_patchers, register
from .rom_finder import RomFinder, RomFinderConfig, RomFinderResult
from .sports import Transport
from .sports.models import League, LeagueData, Player, PlayerStats, Team, TeamRoster
from .sports.serde import league_data_from_dict, league_data_to_dict

# isort: split
#
# Imported for the side effect of running @register. Keep last so the core and
# sports modules above are fully initialised first. The split marker is what
# holds it there: left in one block, ruff's import sorter files `.games`
# alphabetically between `.core` and `.sports` and the ordering this comment
# describes is silently undone.
from .games import nbalive95_genesis as _nbalive95_genesis  # noqa: E402,F401
from .games import nhl94_genesis as _nhl94_genesis  # noqa: E402,F401
from .games import nhl94_snes as _nhl94_snes  # noqa: E402,F401
from .games import we2002 as _we2002  # noqa: E402,F401

__version__ = "0.1.0.dev0"

__all__ = [
    "ApiError",
    "CapabilityError",
    "League",
    "LeagueData",
    "MappedRosters",
    "MappingError",
    "MissingAssetError",
    "PatchResult",
    "Patcher",
    "PatcherInfo",
    "Player",
    "PlayerStats",
    "RetroRosterError",
    "RomError",
    "RomFinder",
    "RomFinderConfig",
    "RomFinderResult",
    "RomInfo",
    "RomSlot",
    "SlotMapping",
    "StorageError",
    "Team",
    "TeamRoster",
    # The type of the `transport=` keyword both patcher constructors accept.
    # Reachable from the root because the patchers are: `get_patcher` hands back
    # a class a consumer then constructs, and it should not have to reach into
    # `sports` — or worse, `sports._http` — to annotate what it passes.
    "Transport",
    "__version__",
    "get_patcher",
    "league_data_from_dict",
    "league_data_to_dict",
    "list_patchers",
    "register",
]
