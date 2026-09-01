"""NHL 94 (Sega Genesis) on the unified Patcher interface.

This module is the translation layer between the ported reader/writer/mapper —
which are a faithful copy of an untested upstream and stay that way — and the
contracts in `core.patcher`. Every place the ported code breaks one of those
contracts is worked around here rather than fixed there:

  * `NHL94GenesisRomReader.validate` dereferences only pointer 0 while
    `get_info` walks all 26, so a readable file can validate and then raise
    `IndexError`. `analyze_rom` catches it and answers `is_valid=False`, because
    `retro-roster analyze` probes every registered patcher against one ROM and a
    file that is not this game must not raise.
  * `NHL94GenesisRomWriter.write_team_roster` documents a `-1` error return but
    raises `IndexError` on a malformed image by the two routes pinned in
    `test_rom_writer.py` (`:918`, the zero fill past the end of a region the
    reader over-measured, and `:953`, the name write that follows a stats write
    the writer's own bounds guard refused). `patch` catches those and raises
    `RomError`, which is what its contract promises.

The original orchestrator returned `dict[str, list[Player]]` from its fetch step
and left team leader stats on `self.team_stats` as a side effect. Here `fetch`
returns a `LeagueData` with the leaders in `TeamRoster.extra["leaders"]`, so the
whole result is JSON-serialisable and `fetch` and `map_rosters` can run in
separate processes with a file between them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.errors import ApiError, RomError
from ...core.models import (
    MappedRosters,
    PartialFn,
    PatchResult,
    ProgressFn,
    RomInfo,
    RomSlot,
    SlotMapping,
    StatusFn,
)
from ...core.patcher import Patcher
from ...core.registry import register
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, TeamRoster
from ...sports.nhl import NhlApiClient
from .models import NHL94_GEN_TEAM_ORDER, TEAM_COUNT, NHL94GenPlayerRecord
from .rom_reader import NHL94GenesisRomReader
from .rom_writer import NHL94GenesisRomWriter
from .stat_mapper import NHL94GenStatMapper

# How many players `select_roster` is asked for: two goalies, four forward lines
# plus two spares, and seven defencemen. It is a selection cap, not a capacity —
# how many of them survive is decided by the ROM. `write_team_roster` patches
# in place inside whatever the existing record chain occupies and spends
# `2 + len(name) + 8` bytes per player, so a 452-byte region holds 23 players
# only if their names average nine characters. Real names arrive as
# "First Last" and `map_player` keeps 14 of them, which fits 19. The overflow is
# dropped silently by the writer and the last surviving name is truncated;
# `write_team_header` is then told the real count so the lines table only ever
# indexes players that were actually written.
MAX_PLAYERS_PER_SLOT = 23


@register(
    "nhl94-genesis",
    platform="genesis",
    sport="hockey",
    requires_slot_mapping=False,
    requires_api_key=False,
    providers=("espn", "nhl"),
)
class NHL94GenesisPatcher(Patcher):
    """Teams map to ROM slots by three-letter code, so no manual mapping step.

    Providers: `espn` for the current season, `nhl` for seasons back to 1993.
    Only the `nhl` provider honours `fetch`'s `season`; ESPN's roster endpoint
    serves the current squad and nothing else.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        api_key: str | None = None,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(
            cache_dir,
            api_key=api_key,
            provider=provider,
            on_status=on_status,
            on_partial=on_partial,
        )
        self.mapper = NHL94GenStatMapper()
        # Both clients `os.makedirs(cache_dir, exist_ok=True)` in their own
        # constructors, so there is nothing to do here. Constructing the client
        # eagerly is what keeps `analyze_rom` free of a lazily-built API object.
        #
        # `Any` rather than a union or a Protocol, and it is the one loose
        # annotation in this tree: the two clients disagree on the signature of
        # every method `fetch` calls — `get_hockey_squad(team_id)` against
        # `get_hockey_squad(code, season)` — so no single type describes both.
        # The cost is real: calling the wrong client's method is a runtime bug
        # here rather than a mypy error, which is why `fetch` branches on
        # `self.provider` and both branches are pinned by tests.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NHL94GenesisRomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        size = len(reader.data or b"")
        try:
            info = reader.get_info()
        except IndexError:
            # `validate` bounds-checks pointer 0 and nothing else, so *any* of
            # the 26 pointers — pointer 0 included — landing in the last five
            # bytes of the file reaches here: `_read_team_slots` dereferences
            # every one of them through `_read_team_city`, which reads a 16-bit
            # word at `team_base + 4`. That is a file which is not this game, not
            # an unreadable one, so it is reported rather than raised.
            return RomInfo(
                path=str(rom_path),
                size=size,
                game_id=self.game_id,
                is_valid=False,
            )
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=slot.current_name,
                    display_name=slot.display_name,
                )
                for slot in info.team_slots
            ],
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        # A no-op for this game, which needs no key. Called anyway so every
        # `fetch` in the codebase opens the same way and a later capability
        # change is a decorator edit rather than a code edit.
        self.check_api_key()
        self.status("Fetching NHL teams...")
        teams = self.api.get_nhl_teams()
        if not teams:
            raise ApiError("The provider returned no NHL teams")

        # Only teams that exist as a slot in the 1994 ROM are worth fetching:
        # the expansion teams cost a network round trip and are then discarded
        # by `map_rosters`.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NHL94 Genesis ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            if self.provider == "nhl":
                players = self.api.get_hockey_squad(team.code, season)
                leaders = self.api.get_hockey_team_leaders(team.code, season)
            else:
                players = self.api.get_hockey_squad(team.id)
                leaders = self.api.get_hockey_team_leaders(team.id)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    extra={"leaders": leaders or {}},
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        return LeagueData(
            league=League(id=0, name="NHL", country="US", season=season),
            teams=rosters,
        )

    # -- map ----------------------------------------------------------------

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, list[NHL94GenPlayerRecord]] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            selected = self.mapper.select_roster(
                roster.players, leaders, max_players=MAX_PLAYERS_PER_SLOT
            )
            teams[slot] = [
                self.mapper.map_player(player, roster.team.code, leaders.get(str(player.id), {}))
                for player in selected
            ]
        return MappedRosters(game_id=self.game_id, teams=teams)

    # -- patch --------------------------------------------------------------

    def patch(
        self,
        *,
        rom_path: Path,
        output_path: Path,
        rosters: MappedRosters,
        on_progress: ProgressFn | None = None,
        **options: Any,
    ) -> PatchResult:
        self.status("Validating ROM...")
        reader = NHL94GenesisRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid NHL94 Genesis ROM: {rom_path}")

        self.status("Initializing ROM writer...")
        # The image is read from disk twice — once above, once by the writer's
        # own internal reader — which is ~2 MB of redundant I/O per patch. Kept
        # deliberately: it is what lets "not this game" fail before any writer
        # state exists, and the writer owns its reader for its whole lifetime.
        writer = NHL94GenesisRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # Without this the game refuses to boot an edited cartridge.
        writer.disable_checksum()

        # `filled_slots()` is the model's own definition of "slots that received
        # players", and the truthiness matters on its own: an empty list reaching
        # `write_team_roster` zero-fills the whole region it was going to patch,
        # erasing a team's roster while this method still reports success.
        #
        # The range is then re-checked, because those keys come from a plain dict
        # that may have crossed a JSON boundary since `map_rosters` built it. The
        # reader bounds-checks only `team_index >= TEAM_COUNT`, so a negative key
        # reads the four bytes *preceding* the pointer table and treats whatever
        # is there as a team pointer: the stray write lands wherever that word
        # points, which is anywhere in the image, not near offset 0.
        targets = [slot for slot in rosters.filled_slots() if 0 <= slot < TEAM_COUNT]

        teams_patched = 0
        players_patched = 0
        for i, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(i / len(targets), f"Writing {NHL94_GEN_TEAM_ORDER[slot]}...")
            players: list[NHL94GenPlayerRecord] = rosters.teams[slot]
            try:
                written = writer.write_team_roster(slot, players)
                if written <= 0:
                    # -1 is the writer's documented error return; 0 means the
                    # region it found was too small for even one record. Either
                    # way nothing reached the image, so nothing is counted and
                    # the header — which would index a lines table into players
                    # that do not exist — is not written either.
                    continue
                writer.write_team_header(slot, players, actual_count=written)
            except IndexError as exc:
                # `write_team_roster` promises -1 on error and delivers an
                # IndexError instead whenever the record chain it is patching
                # runs past the end of the image. Abort rather than skip the
                # slot: the partial write is already in the writer's buffer, so
                # carrying on would finalize a damaged ROM under a success
                # return. Nothing has been written to disk at this point.
                raise RomError(
                    f"Corrupt team block at slot {slot} in {rom_path}: "
                    f"the roster region runs past the end of the image"
                ) from exc
            teams_patched += 1
            players_patched += written

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        writer.update_header_checksum()
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
