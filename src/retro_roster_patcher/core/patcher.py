"""The one interface every game patcher implements.

Four methods, in the order a wizard calls them:

    analyze_rom  -> is this the right ROM, and what slots does it have?
    fetch        -> pull real-world roster data from a provider
    map_rosters  -> reduce that data to what this game's writer consumes
    patch        -> write the output ROM

`fetch` and `patch` are separate so a caller can preview between them without
re-running the network step.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..sports.models import LeagueData, Player, TeamRoster
from .errors import CapabilityError
from .models import (
    MappedRosters,
    PartialFn,
    PatchResult,
    ProgressFn,
    RomInfo,
    SlotMapping,
    StatusFn,
)


class Patcher(ABC):
    """Base class for game patchers.

    The five capability attributes below are placeholders overwritten by
    `@register`. They are declared here so type checkers and IDEs see them.
    """

    game_id: str = ""
    platform: str = ""
    sport: str = ""
    requires_slot_mapping: bool = False
    providers: tuple[str, ...] = ()

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
    ) -> None:
        """Build a patcher.

        `cache_dir` accepts a string as well as a `Path`: callers across the JSON
        boundary can only hand over strings.

        Keep construction free of network I/O, so `retro-roster analyze` can
        instantiate every registered patcher purely to inspect a ROM. It is not
        free of side effects — a sports client creates its cache directory from
        its constructor, so this can raise `StorageError`.
        """
        if provider is not None and provider not in self.providers:
            supported = ", ".join(self.providers) or "none"
            raise CapabilityError(
                f"{self._subject()} does not support provider {provider!r}. Supported: {supported}"
            )
        self.cache_dir = Path(cache_dir)
        self.provider = provider or (self.providers[0] if self.providers else None)
        self.on_status = on_status
        self.on_partial = on_partial

    def _subject(self) -> str:
        """Name this patcher in an error message.

        `game_id` is empty until `@register` stamps it, hence the fallback.
        """
        return self.game_id or type(self).__name__

    def status(self, message: str) -> None:
        """Report a human-readable status message, if anyone is listening."""
        if self.on_status is not None:
            self.on_status(message)

    def partial(self, data: Any) -> None:
        """Publish an intermediate result worth showing before the call returns."""
        if self.on_partial is not None:
            self.on_partial(data)

    def check_slot_mapping(self, slot_mapping: list[SlotMapping] | None) -> None:
        """Enforce the declared slot-mapping capability.

        Call this at the top of `map_rosters`. It checks presence against the
        declared capability and nothing else; validating slot indices and
        checking them against the ROM remain the subclass's job.
        """
        if self.requires_slot_mapping:
            if not slot_mapping:
                raise CapabilityError(f"{self._subject()} requires a slot mapping")
        elif slot_mapping:
            raise CapabilityError(
                f"{self._subject()} does not use slot mappings; it maps teams automatically"
            )

    def suggest_squad_order(self, team_roster: TeamRoster) -> list[Player]:
        """Return this team's players in the order this game would field them.

        A UI editor shows the squad in this order — starters first (by the
        game's own position template), then bench, then any extras the ROM will
        not store. It reuses the same selection each game already applies in
        `map_rosters`, then appends whatever that selection dropped, so the list
        is a reordering of the full squad and loses no player.

        The default returns the players untouched; games with a roster model
        override it. Purely advisory: `map_rosters` still runs the authoritative
        selection at patch time.
        """
        return list(team_roster.players)

    @staticmethod
    def _append_unused(ordered: list[Player], everyone: list[Player]) -> list[Player]:
        """`ordered` followed by every player it left out, original order kept."""
        used = {id(p) for p in ordered}
        return ordered + [p for p in everyone if id(p) not in used]

    @abstractmethod
    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect a ROM.

        Raise `RomError` only when the file is missing or unreadable, the
        filesystem's `OSError`s included. A readable file that is not this game
        must return `RomInfo(is_valid=False)` rather than raise: `retro-roster
        analyze` probes every registered patcher against one ROM.
        """

    @abstractmethod
    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        """Pull roster data for one season.

        Raise `ApiError` on upstream failure. A failure that costs part of a
        fetch rather than all of it is not an exception: record it on the
        affected `TeamRoster.error` and carry on.
        """

    @abstractmethod
    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to this game's own record types.

        Call `self.check_slot_mapping(slot_mapping)` first, then validate the
        mapping's contents yourself and raise `MappingError`.
        """

    @abstractmethod
    def patch(
        self,
        *,
        rom_path: Path,
        output_path: Path,
        rosters: MappedRosters,
        on_progress: ProgressFn | None = None,
        **options: Any,
    ) -> PatchResult:
        """Write the patched ROM.

        Raise `RomError` on any write failure, the filesystem's `OSError`s
        included. An unwritable `--out` *directory* is not this — the CLI creates
        it before calling, and reports `StorageError`.
        """
