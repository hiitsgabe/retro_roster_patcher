"""Types crossing the public boundary.

Game-specific fields live in `RomInfo.extra` rather than in a subclass, so the
CLI can render any ROM without knowing which game produced it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import MappingError

# Reported as a fraction 0.0-1.0 plus a human-readable message.
ProgressFn = Callable[[float, str], None]

# Reported as a human-readable message with no completion estimate.
StatusFn = Callable[[str], None]

# Fired when a partial result is worth showing before the whole operation finishes.
PartialFn = Callable[[Any], None]


@dataclass
class RomSlot:
    """One team-shaped hole in a ROM.

    `current_name` is what the ROM says today, or a generic positional label
    where the game's name table cannot be read.

    `display_name` is the canonical name for this position from the game's own
    team order. A producer must make it distinct across one ROM's slots: it is
    what a slot-picking UI lists, so a repeat leaves two rows indistinguishable.
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
    # verbatim.
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

    `teams` maps ROM slot index to that game's own player-record type, opaque to
    everything but the owning patcher. `game_id` records which patcher's
    `map_rosters` produced the values; every `patch` calls `require_game` first.
    """

    game_id: str
    teams: dict[int, Any] = field(default_factory=dict)

    def require_game(self, game_id: str) -> None:
        """Raise `MappingError` unless these rosters were mapped for `game_id`.

        Without this the wrong game's rosters fail deep inside the writer with an
        `AttributeError` outside this library's exception hierarchy.
        """
        if self.game_id != game_id:
            raise MappingError(
                f"These rosters were mapped for {self.game_id!r}, "
                f"not for {game_id!r}; re-run map_rosters on the {game_id!r} patcher"
            )

    def filled_slots(self) -> list[int]:
        """Slot indices whose mapped value is truthy, in ascending order.

        Only meaningful for a game whose per-slot value is a list of players. A
        game storing one always-truthy record object per slot gets every key back
        and should iterate `teams` directly.
        """
        return sorted(i for i, players in self.teams.items() if players)


@dataclass
class PatchResult:
    """A successful patch. Failures raise instead of returning this.

    `teams_patched` counts slots something reached the ROM for — not "slots that
    got players", since a game may write a name and kit for a slot with no
    squad. Read `players_patched` too for that.
    """

    output_path: str
    teams_patched: int = 0
    players_patched: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": self.output_path,
            "teams_patched": self.teams_patched,
            "players_patched": self.players_patched,
        }
