"""Patch real-world sports rosters into retro game ROMs.

Importing this package imports every game package, which is what populates the
registry. `games.we2002.tim_generator` is deliberately left out of that: it
imports the optional `images` extra.

`__all__` below is this library's whole root surface and
`tests/test_public_api.py` pins it exactly.
"""

# Keep these ahead of the game imports at the bottom of the block. A game module
# may import `retro_roster_patcher.core.*` directly but never the package root:
# while the game imports run, this module is still partially initialised.
from .core.assets import MissingAssetError
from .core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RetroRosterError,
    RomError,
    StorageError,
)
from .core.kits import PATTERNS, KitSpec, KitTemplate, default_kits, kit_from_image, render_kit
from .core.models import MappedRosters, PatchResult, RomInfo, RomSlot, SlotMapping
from .core.patcher import Patcher
from .core.registry import PatcherInfo, get_patcher, list_patchers, register
from .rom_finder import RomFinder, RomFinderConfig, RomFinderResult
from .sports import Transport
from .sports.models import League, LeagueData, Player, PlayerStats, Team, TeamRoster
from .sports.serde import league_data_from_dict, league_data_to_dict

# isort: split
#
# Imported for the side effect of running @register. Keep last, and keep the
# split marker: without it ruff's import sorter files `.games` between `.core`
# and `.sports`.
from .games import iss2000_n64 as _iss2000_n64  # noqa: E402,F401
from .games import iss_deluxe_snes as _iss_deluxe_snes  # noqa: E402,F401
from .games import iss_snes as _iss_snes  # noqa: E402,F401
from .games import kgj_mlb_snes as _kgj_mlb_snes  # noqa: E402,F401
from .games import mvp_psp as _mvp_psp  # noqa: E402,F401
from .games import nbalive95_genesis as _nbalive95_genesis  # noqa: E402,F401
from .games import nhl05_ps2 as _nhl05_ps2  # noqa: E402,F401
from .games import nhl07_psp as _nhl07_psp  # noqa: E402,F401
from .games import nhl94_genesis as _nhl94_genesis  # noqa: E402,F401
from .games import nhl94_snes as _nhl94_snes  # noqa: E402,F401
from .games import pes2013_ps2 as _pes2013_ps2  # noqa: E402,F401
from .games import we2002 as _we2002  # noqa: E402,F401

__version__ = "0.8.0"

__all__ = [
    "ApiError",
    "CapabilityError",
    "KitSpec",
    "KitTemplate",
    "League",
    "LeagueData",
    "MappedRosters",
    "MappingError",
    "MissingAssetError",
    "PATTERNS",
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
    "Transport",
    "__version__",
    "default_kits",
    "get_patcher",
    "kit_from_image",
    "league_data_from_dict",
    "league_data_to_dict",
    "list_patchers",
    "register",
    "render_kit",
]
