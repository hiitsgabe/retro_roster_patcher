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

from ..sports.models import LeagueData
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

        `cache_dir` accepts a string as well as a `Path` and is normalised to a
        `Path`, because callers across the JSON boundary — the NDJSON IPC surface
        and the CLI — can only hand over strings.

        There is no `api_key` parameter. There was one while a provider still
        took a credential; it is removed rather
        than accepted and ignored, because a parameter that silently does
        nothing lets a caller believe a credential is in use. Passing one now
        raises `TypeError` at the call site, which is where the mistaken belief
        is.

        Construction stays free of network I/O so `retro-roster analyze` can
        instantiate every registered patcher purely to inspect a ROM — an
        operation that never reaches a provider. It is not free of side effects:
        every sports client creates its cache directory from its own constructor
        through `errors.ensure_cache_dir`, and the patchers build their client
        eagerly. So constructing a patcher can raise `StorageError` — on a
        read-only `$HOME`, which is the Batocera and Android default — and that
        is the one failure that happens before any method of this interface is
        called.
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

    # -- helpers available to subclasses ------------------------------------

    def _subject(self) -> str:
        """Name this patcher in an error message.

        `game_id` is empty until `@register` stamps it, so a subclass that is
        written but not yet decorated would otherwise raise errors with no
        subject at all. Fall back to the class name.
        """
        return self.game_id or type(self).__name__

    def status(self, message: str) -> None:
        """Report a human-readable status message, if anyone is listening."""
        if self.on_status is not None:
            self.on_status(message)

    def partial(self, data: Any) -> None:
        """Publish an intermediate result worth showing before the call returns.

        WE2002 fires this once the team list is known, so a UI can render teams
        while their squads are still loading. It is a constructor callback rather
        than a `fetch` argument because it is a channel, like `on_status`, not a
        per-call option.
        """
        if self.on_partial is not None:
            self.on_partial(data)

    def check_slot_mapping(self, slot_mapping: list[SlotMapping] | None) -> None:
        """Enforce the declared slot-mapping capability.

        Subclasses call this at the top of `map_rosters`. Without it, a caller
        that passes a mapping to an auto-mapping patcher gets silence; with it,
        they get an error naming the mismatch.

        This checks presence against the declared capability and nothing else.
        Validating slot indices, rejecting duplicates, and checking the mapping
        against the ROM remain the subclass's job and raise `MappingError`.
        """
        if self.requires_slot_mapping:
            if not slot_mapping:
                raise CapabilityError(f"{self._subject()} requires a slot mapping")
        elif slot_mapping:
            raise CapabilityError(
                f"{self._subject()} does not use slot mappings; it maps teams automatically"
            )

    # -- the interface ------------------------------------------------------

    @abstractmethod
    def analyze_rom(self, rom_path: Path) -> RomInfo:
        """Inspect a ROM.

        Raises `RomError` only when the file is missing or unreadable. A readable
        file that is not this game is not an error: return
        `RomInfo(is_valid=False)` instead of raising, because `retro-roster
        analyze` probes every registered patcher against one ROM to discover
        which of them recognises it.

        "Unreadable" includes the `OSError` the filesystem raises — a revoked
        read bit, a yanked USB or SMB mount, an EIO. That is not automatic, and
        the two implementations get there differently: NHL94's reader answers
        `load() -> False` because it catches its own, and WE2002's does not, so
        its patcher wraps the call in `errors.as_rom_error`. Either way the
        caller sees `RomError`, which is the only thing this sentence promises.
        `cmd_analyze` catches `RomError` per patcher and `continue`s, so
        anything else aborts the whole sweep.
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

        Raises `ApiError` on upstream failure. No provider defines a subclass of
        it: three lived in `sports/api_football.py` and went with that module, so
        `except ApiError` is both the general answer and the only one.

        A failure that costs part of a fetch rather than all of it is not an
        exception. WE2002 loses one team's squad to a per-team `TeamRoster.error`
        and carries on, because a league is dozens of requests and one of them
        failing must not cost the other dozens.
        """

    @abstractmethod
    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to this game's own record types.

        Implementations call `self.check_slot_mapping(slot_mapping)` first. That
        guard only checks the mapping's presence against the declared capability;
        validating its contents raises `MappingError`.
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

        Raises `RomError` on any write failure, the filesystem's `OSError`s
        included. As in `analyze_rom` the two implementations reach that
        differently: NHL94's writer returns `False` from `finalize`, and
        WE2002's raises, so its patcher wraps the whole write in
        `errors.as_rom_error`. An unwritable `--out` *directory* is not this —
        the CLI creates it before calling, and reports `StorageError`.
        """
