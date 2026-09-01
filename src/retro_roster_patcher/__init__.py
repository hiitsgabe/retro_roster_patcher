"""Patch real-world sports rosters into retro game ROMs.

Importing this package imports every game package, which is what populates the
registry. Import cost is trivial (pure Python, no I/O at import time) and the
alternative — lazy discovery — makes `list_patchers()` order-dependent.
"""

# These core and sports imports must stay ahead of the game imports at the bottom of
# the block. A game module is only ever safe to import `retro_roster_patcher.core.*`
# directly, never the package root: while the game imports below are running, this
# module is still partially initialised, so `from retro_roster_patcher import Patcher`
# would resolve only by accident of ordering and break the moment the block is reordered.
from .core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RetroRosterError,
    RomError,
)
from .core.models import MappedRosters, PatchResult, RomInfo, RomSlot, SlotMapping
from .core.patcher import Patcher
from .core.registry import PatcherInfo, get_patcher, list_patchers, register
from .sports.models import League, LeagueData, Player, PlayerStats, Team, TeamRoster

# isort: split
#
# Imported for the side effect of running @register. Keep last so the core and
# sports modules above are fully initialised first. The split marker is what
# holds it there: left in one block, ruff's import sorter files `.games`
# alphabetically between `.core` and `.sports` and the ordering this comment
# describes is silently undone.
from .games import nhl94_genesis as _nhl94_genesis  # noqa: E402,F401

__version__ = "0.1.0.dev0"

__all__ = [
    "ApiError",
    "CapabilityError",
    "League",
    "LeagueData",
    "MappedRosters",
    "MappingError",
    "PatchResult",
    "Patcher",
    "PatcherInfo",
    "Player",
    "PlayerStats",
    "RetroRosterError",
    "RomError",
    "RomInfo",
    "RomSlot",
    "SlotMapping",
    "Team",
    "TeamRoster",
    "__version__",
    "get_patcher",
    "list_patchers",
    "register",
]
