"""Types crossing the public boundary.

These replace the per-game `ISSRomInfo` / `NHL94GenRomInfo` / `MVPRomInfo` family.
Game-specific fields live in `RomInfo.extra` rather than in a subclass, so the CLI
and any future UI can render any ROM without knowing which game produced it.
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
    where the game's name table cannot yet be read: WE2002's reader does not
    parse the variable-length name strings and answers `"ML Slot 6"`.

    `display_name` is the canonical name for this position from the game's own
    team order, and a producer must make it distinct across one ROM's slots. It
    is the field a slot-picking UI lists, so a repeated value is not a cosmetic
    flaw — it leaves the user unable to tell two rows apart. WE2002 filled it
    from the slot's league group, one string for all 32 slots, until it was
    changed to name the position: `"Master League Slot 6"`.
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

    `game_id` records which patcher's `map_rosters` produced the values, and is
    what makes handing them to a different patcher a reported error rather than
    a crash: every `patch` implementation calls `require_game` first.
    """

    game_id: str
    teams: dict[int, Any] = field(default_factory=dict)

    def require_game(self, game_id: str) -> None:
        """Raise `MappingError` unless these rosters were mapped for `game_id`.

        The values in `teams` are one game's private record type, so the wrong
        game's rosters do not fail at the boundary — they fail inside the writer
        on an attribute or an iteration the value does not support, with an
        `AttributeError` or `TypeError` that is outside this library's exception
        hierarchy. A consumer holding rosters for several games (both target
        applications do) would not catch that by catching `RetroRosterError`.
        """
        if self.game_id != game_id:
            raise MappingError(
                f"These rosters were mapped for {self.game_id!r}, "
                f"not for {game_id!r}; re-run map_rosters on the {game_id!r} patcher"
            )

    def filled_slots(self) -> list[int]:
        """Slot indices whose mapped value is truthy, in ascending order.

        For a game that stores a list of player records per slot — NHL94 — that
        is exactly the slots which received players, and its patcher depends on
        the distinction: an empty list reaching the writer erases the slot it was
        going to patch. For a game that stores one record object per slot —
        WE2002's `WETeamRecord` — every value is truthy however empty, so this
        returns every key and that patcher iterates `teams` directly instead.
        """
        return sorted(i for i, players in self.teams.items() if players)


@dataclass
class PatchResult:
    """A successful patch. Failures raise instead of returning this.

    `teams_patched` counts the slots something reached the ROM for, and
    `players_patched` counts the player records that reached it. The first is
    not "slots that got players", and the two games make the difference
    visible, so a UI that renders `teams_patched` alone gets a number whose
    meaning depends on the game:

    - NHL94 writes nothing per slot but player records, so a slot whose writer
      wrote none is not counted. Two slots holding empty rosters give `(0, 0)`.
    - WE2002 also writes the team name, abbreviations, kit colours and flag,
      all of which land whether or not a squad was supplied, so an in-range
      slot always counts. Two slots holding a record with no players give
      `(2, 0)`.

    One convention, two units of work — not two conventions. Unifying the
    numbers would mean either dropping WE2002 slots whose name and kit really
    did change, or counting NHL94 slots where nothing did. A consumer that
    wants "slots that received a squad" must read `players_patched` too.
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
