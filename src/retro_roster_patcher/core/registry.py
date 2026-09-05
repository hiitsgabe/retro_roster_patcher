"""In-tree registry of game patchers.

Games register themselves at import time via `@register`, and
`retro_roster_patcher/__init__.py` imports every game package so the dict is
populated by the time anyone calls `get_patcher`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from .patcher import Patcher

# The bound is a static contract only: the decorator runs no `issubclass` check
# and stamps whatever it is handed.
T = TypeVar("T", bound=Patcher)

_REGISTRY: dict[str, type[Patcher]] = {}


@dataclass(frozen=True)
class PatcherInfo:
    """What a patcher can do, without instantiating it.

    Crosses the IPC boundary in `list`'s JSON payload, so a field here is public
    surface.
    """

    game_id: str
    platform: str
    sport: str
    requires_slot_mapping: bool
    providers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "platform": self.platform,
            "sport": self.sport,
            "requires_slot_mapping": self.requires_slot_mapping,
            "providers": list(self.providers),
        }


def register(
    game_id: str,
    *,
    platform: str,
    sport: str,
    requires_slot_mapping: bool = False,
    providers: tuple[str, ...] = (),
) -> Callable[[type[T]], type[T]]:
    """Class decorator that records a patcher and stamps its capabilities."""

    def decorator(cls: type[T]) -> type[T]:
        if game_id in _REGISTRY:
            raise ValueError(
                f"Patcher id {game_id!r} is already registered by {_REGISTRY[game_id].__name__}"
            )
        cls.game_id = game_id
        cls.platform = platform
        cls.sport = sport
        cls.requires_slot_mapping = requires_slot_mapping
        cls.providers = providers
        _REGISTRY[game_id] = cls
        return cls

    return decorator


def get_patcher(game_id: str) -> type[Patcher]:
    """Look up a patcher class by id."""
    try:
        return _REGISTRY[game_id]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"Unknown game id {game_id!r}. Known ids: {known}") from None


def list_patchers() -> list[PatcherInfo]:
    """Describe every registered patcher, sorted by game id."""
    return [
        PatcherInfo(
            game_id=cls.game_id,
            platform=cls.platform,
            sport=cls.sport,
            requires_slot_mapping=cls.requires_slot_mapping,
            providers=cls.providers,
        )
        for _, cls in sorted(_REGISTRY.items())
    ]
