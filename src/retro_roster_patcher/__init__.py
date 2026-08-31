"""Patch real-world sports rosters into retro game ROMs.

Importing this package imports every game package, which is what populates the
registry. Import cost is trivial (pure Python, no I/O at import time) and the
alternative — lazy discovery — makes `list_patchers()` order-dependent.
"""

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
