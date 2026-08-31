"""Types crossing the public boundary.

These replace the per-game `ISSRomInfo` / `NHL94GenRomInfo` / `MVPRomInfo` family.
Game-specific fields live in `RomInfo.extra` rather than in a subclass, so the CLI
and any future UI can render any ROM without knowing which game produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# Reported as a fraction 0.0-1.0 plus a human-readable message.
ProgressFn = Callable[[float, str], None]

# Reported as a human-readable message with no completion estimate.
StatusFn = Callable[[str], None]

# Fired when a partial result is worth showing before the whole operation finishes.
PartialFn = Callable[[Any], None]


@dataclass
class RomSlot:
    """One team-shaped hole in a ROM.

    `current_name` is what the ROM says today; `display_name` is the canonical
    name for this position from the game's own team order.
    """

    index: int
    current_name: str = ""
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "current_name": self.current_name,
            "display_name": self.display_name,
        }


@dataclass
class RomInfo:
    """The result of inspecting a ROM on disk."""

    path: str
    size: int
    game_id: str
    is_valid: bool = True
    slots: list[RomSlot] = field(default_factory=list)
    # Values must be JSON-serialisable: this dict crosses the NDJSON boundary
    # verbatim, and a non-primitive value raises TypeError inside the renderer,
    # far from the patcher that put it there.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "size": self.size,
            "game_id": self.game_id,
            "is_valid": self.is_valid,
            "slots": [s.to_dict() for s in self.slots],
            "extra": dict(self.extra),
        }


@dataclass(frozen=True)
class SlotMapping:
    """Binds one ROM slot to one real-world team.

    Only patchers whose `requires_slot_mapping` is True accept these.
    """

    slot_index: int
    team_id: int
    team_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_index": self.slot_index,
            "team_id": self.team_id,
            "team_name": self.team_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotMapping:
        return cls(
            slot_index=int(data["slot_index"]),
            team_id=int(data["team_id"]),
            team_name=str(data.get("team_name", "")),
        )


@dataclass
class MappedRosters:
    """Roster data reduced to the shape one specific game's writer consumes.

    `teams` maps ROM slot index to that game's own player-record type. The
    library treats the values as opaque; only the owning patcher interprets them.
    """

    game_id: str
    teams: dict[int, Any] = field(default_factory=dict)

    def filled_slots(self) -> list[int]:
        """Slot indices that actually received players, in ascending order."""
        return sorted(i for i, players in self.teams.items() if players)


@dataclass
class PatchResult:
    """A successful patch. Failures raise instead of returning this."""

    output_path: str
    teams_patched: int = 0
    players_patched: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "teams_patched": self.teams_patched,
            "players_patched": self.players_patched,
        }
