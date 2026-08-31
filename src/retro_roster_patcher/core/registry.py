"""In-tree registry of game patchers.

A plain dict rather than `importlib.metadata` entry points: with ten first-party
games in this repository, plugin discovery would add versioning and debugging cost
for no benefit. Games register themselves at import time via `@register`, and
`retro_roster_patcher/__init__.py` imports every game package so the dict is
populated by the time anyone calls `get_patcher`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T", bound=type)

_REGISTRY: dict[str, type] = {}


@dataclass(frozen=True)
class PatcherInfo:
    """What a patcher can do, without instantiating it.

    Drives `retro-roster list` and any future UI: which arguments to prompt for,
    whether a slot-mapping step is needed, which providers to offer.
    """

    game_id: str
    platform: str
    sport: str
    requires_slot_mapping: bool
    requires_api_key: bool
    providers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "platform": self.platform,
            "sport": self.sport,
            "requires_slot_mapping": self.requires_slot_mapping,
            "requires_api_key": self.requires_api_key,
            "providers": list(self.providers),
        }


def register(
    game_id: str,
    *,
    platform: str,
    sport: str,
    requires_slot_mapping: bool = False,
    requires_api_key: bool = False,
    providers: tuple[str, ...] = (),
) -> Callable[[T], T]:
    """Class decorator that records a patcher and stamps its capabilities."""

    def decorator(cls: T) -> T:
        if game_id in _REGISTRY:
            raise ValueError(
                f"Patcher id {game_id!r} is already registered by {_REGISTRY[game_id].__name__}"
            )
        cls.game_id = game_id  # type: ignore[attr-defined]
        cls.platform = platform  # type: ignore[attr-defined]
        cls.sport = sport  # type: ignore[attr-defined]
        cls.requires_slot_mapping = requires_slot_mapping  # type: ignore[attr-defined]
        cls.requires_api_key = requires_api_key  # type: ignore[attr-defined]
        cls.providers = providers  # type: ignore[attr-defined]
        _REGISTRY[game_id] = cls
        return cls

    return decorator


def get_patcher(game_id: str) -> type:
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
            game_id=cls.game_id,  # type: ignore[attr-defined]
            platform=cls.platform,  # type: ignore[attr-defined]
            sport=cls.sport,  # type: ignore[attr-defined]
            requires_slot_mapping=cls.requires_slot_mapping,  # type: ignore[attr-defined]
            requires_api_key=cls.requires_api_key,  # type: ignore[attr-defined]
            providers=cls.providers,  # type: ignore[attr-defined]
        )
        for _, cls in sorted(_REGISTRY.items())
    ]
