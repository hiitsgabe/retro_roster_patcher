"""International Superstar Soccer (SNES) on the unified Patcher interface.

The translation layer between the ported reader/writer/mapper -- a faithful copy
of an untested upstream, and kept that way -- and the contracts in
`core.patcher`. Six things about this game are worth knowing before reading the
code.

**It requires an explicit slot mapping, and that is a deliberate divergence.**
Upstream's `create_slot_mapping` assigned league team *i* to ROM slot *i*, in
whatever order the provider listed them, truncating at 27, reading nothing from
the image, with no way for a user to change it. ISS's 27 slots are *national
teams* -- Germany, Italy, Holland -- and the data source is a *club league*, so
that assignment is arbitrary: it puts whoever ESPN lists first into Germany.
WE2002 has the identical structural problem and answers it the same way.
`default_slot_mapping` still produces upstream's sequential mapping for a caller
that wants somewhere to start. **This must not be "restored" by a future port
audit.**

**There is no `api_key`.** Upstream's constructor took one, positionally and
required, and on the injected-client branch never read it. The provider it
belonged to is gone from this library. Per `core/patcher.py` it raises
`TypeError` at the call site rather than being accepted and ignored.

**The provider deltas that came with that.** Upstream's per-team `except` block
began `from services.sports_api.api_football import RateLimitError,
DailyLimitError` -- an import, inside the handler, of a module this library no
longer has, so today it raised `ModuleNotFoundError` from inside the very code
meant to contain the failure and cost the whole fetch. It collapses to the one
`except Exception` that sets `TeamRoster.error`. And `get_squad` was called with
no season at all, so the first season fetched was cached under a key that never
changed and served forever after.

**ESPN's squad is today's, its statistics are the season's.** `get_squad` has no
season in its URL -- the endpoint serves the current squad whatever is asked --
while `get_player_stats` honours the season in its path. So a 2019 patch gets
2025's names carrying 2019's numbers. That is ESPN's shape and not something
this patcher can correct; the season still reaches `get_squad` because it is
part of the cache key.

**Two of the four attributes are the same number.** `speed` and `stamina` come
out of one formula; `games/iss_snes/stat_mapper.py` says so at length.

**The writer is a ROM hack.** It patches ten bytes of 65816 machine code at
fixed addresses to redirect a bank, compresses in the game's own format,
rewrites three pointer tables in three encodings and renders a bitmap font.
`games/iss_snes/rom_writer.py`'s docstring is the map. Two consequences reach
this module: the order of the write calls in `patch` is upstream's and is load
bearing, and there is no ROM version check anywhere, so a revision other than
the one those ten addresses belong to is patched just as willingly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.errors import ApiError, CapabilityError, MappingError, RomError, as_rom_error
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
from ...sports.models import LeagueData, Team, TeamRoster
from .models import TEAM_ENUM_ORDER, TOTAL_TEAMS, ISSTeamRecord
from .rom_reader import ISSRomReader
from .rom_writer import MIN_PATCHABLE_SIZE, ISSRomWriter
from .stat_mapper import ISSStatMapper

#: The goalkeeper kit every patched team gets: green shirt, black shorts.
#:
#: A constant and not a provider value. No provider publishes a goalkeeper kit,
#: and upstream assigned this pair unconditionally inside its patch loop, so
#: every patched side's keeper wears the same thing. Hoisted out of the loop
#: because it is a decision, not a computation.
GK_KIT = ((0, 128, 0), (0, 0, 0))

#: The shorts colour in both outfield kits. Also a constant, also upstream's:
#: `kit_home` is `(primary, white, primary)` and `kit_away` is
#: `(alternate, white, alternate)`, so a team whose provider colour is white
#: gets a kit that is white throughout.
KIT_SHORTS = (255, 255, 255)


def _parse_hex_colour(value: str) -> tuple[int, int, int] | None:
    """Read a `RRGGBB` or `#RRGGBB` provider colour, or `None` if it is neither.

    Anything else -- an empty string, a three-digit shorthand, a colour name --
    returns `None`, so the ROM keeps its own kit rather than getting one built
    from half a value.
    """
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


@register(
    "iss-snes",
    platform="snes",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class ISSPatcher(Patcher):
    """27 national-team slots that no club competition maps onto by itself.

    `requires_slot_mapping=True`: there is no team code, abbreviation or name in
    the ROM to match a provider team against -- the slots are Germany through
    Super Star and the data is a club league -- so the caller says which club
    goes in which slot. See this module's docstring for why that is a divergence
    from upstream and not a restoration of something it did.

    The only provider is `espn`, which needs no credential. League ids are
    ESPN's: `--league-id 2001` is the Premier League.
    """

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(
            cache_dir,
            provider=provider,
            on_status=on_status,
            on_partial=on_partial,
        )
        self.mapper = ISSStatMapper()
        # Built eagerly, and `EspnClient.__init__` creates its cache directory,
        # so constructing this patcher can raise `StorageError`. Nothing here
        # reaches the network. Typed `Any` because the tests substitute a
        # stand-in implementing the four methods `fetch` calls and nothing else.
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = ISSRomReader(str(rom_path))
        # `get_rom_info` opens the file three times and none of the three
        # catches anything but its own `OSError`, so a revoked read bit or a
        # yanked mount arrives here as the `OSError` this converts. A readable
        # file that is not this game is not an error: it comes back with
        # `is_valid=False` and no slots.
        with as_rom_error(rom_path):
            info = reader.get_rom_info()
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # A player, and labelled as one. This reader parses no team
                    # name -- see `ISSRomReader.read_team_slots`.
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    # The national side whose data occupies this slot, which is
                    # distinct across all 27 and is what a slot-picking UI lists.
                    # This game is the one that *requires* a mapping, so it is
                    # precisely the one whose slots have to be renderable.
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ],
            # ROM-derived and not reachable any other way once the reader is
            # gone: it decides which end of the file every offset is measured
            # from. JSON-serialisable, per `core/models.py`.
            extra={"has_header": info.has_header},
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        if league_id is None:
            raise CapabilityError("iss-snes requires a league_id; there is no default league")

        if on_progress is not None:
            on_progress(0.05, "Fetching league info...")
        leagues = self.api.get_leagues(id=league_id, season=season)
        league = next(iter(leagues), None)
        if league is None:
            # Upstream raised a bare `ValueError` here, which is outside this
            # library's exception hierarchy, so a consumer catching
            # `RetroRosterError` did not catch it.
            raise ApiError(f"League {league_id} not found for season {season}")

        if on_progress is not None:
            on_progress(0.1, f"Fetching teams for {league.name}...")
        teams = self.api.get_teams(league_id, season)
        if not teams:
            raise ApiError(f"League {league_id} has no teams for season {season}")

        # Publish the team list immediately so a UI can render it while squads load.
        self.partial(
            LeagueData(
                league=league,
                teams=[TeamRoster(team=t, loading=True) for t in teams],
            )
        )

        rosters: list[TeamRoster] = []
        for i, team in enumerate(teams):
            if on_progress is not None:
                on_progress(0.1 + 0.8 * (i / len(teams)), f"Fetching squad: {team.name}...")
            # A league fetch is dozens of requests and one failing must not cost
            # the other dozens: the team keeps its place, carries the reason on
            # `TeamRoster.error`, and the fetch goes on. Built fresh rather than
            # mutating the skeleton published above, which a caller may still be
            # rendering.
            roster = TeamRoster(team=team)
            try:
                # `season` reaches the cache key and nothing else -- ESPN's
                # squad endpoint serves the squad as it stands today. Upstream
                # omitted it, so the key was the team id alone and the first
                # season ever fetched was replayed for every later one.
                roster.players = self.api.get_squad(team.id, season)
                try:
                    stats = self.api.get_player_stats(team.id, season)
                    roster.player_stats = {ps.player_id: ps for ps in stats}
                except Exception:
                    # Stats are optional: `map_player` falls back to position and
                    # age, so this costs ratings rather than the team. Upstream
                    # wrote `pass` here and said nothing.
                    self.status(
                        f"{team.name}: stats unavailable, ratings will use position defaults"
                    )
            except Exception as exc:
                # As broad as upstream's. `TransportLeak` is a `BaseException`
                # precisely so the test suite's network guard still escapes this.
                # The two arms above this one named API-Football's rate-limit and
                # quota errors and imported them *here*, inside the handler; with
                # that provider gone the import itself raised
                # `ModuleNotFoundError` and this is the only arm left.
                roster.error = f"Failed: {exc}"
                self.status(f"{team.name}: {roster.error}")
            rosters.append(roster)

        if on_progress is not None:
            on_progress(1.0, "Done!")
        return LeagueData(league=league, teams=rosters)

    # -- map ----------------------------------------------------------------

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        """Reduce league data to one `ISSTeamRecord` per mapped ROM slot.

        Sparse: a key exists only for a slot the caller named. Nothing reads a
        missing one, and an unmapped slot keeps its 1994 squad.
        """
        self.check_slot_mapping(slot_mapping)
        # `check_slot_mapping` has already refused an absent or empty mapping;
        # the rebinding narrows `list | None` for the type checker.
        entries = slot_mapping or []

        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, ISSTeamRecord] = {}
        for entry in entries:
            if not 0 <= entry.slot_index < TOTAL_TEAMS:
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the ISS range 0..{TOTAL_TEAMS - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            # A slot named twice is a mapping the caller has to fix: unlike the
            # abbreviation-matched games, where two provider aliases can
            # legitimately land on one slot, every entry here is something the
            # caller typed. Silently keeping the last would patch a slot the
            # caller did not think they had assigned.
            if entry.slot_index in teams:
                raise MappingError(
                    f"Slot {entry.slot_index} ({TEAM_ENUM_ORDER[entry.slot_index]}) "
                    f"is mapped more than once"
                )
            # The whole league, not just this team: percentiles are normalised
            # league-wide, and passing one roster would rate every player against
            # his own team-mates only.
            record = self.mapper.map_team_with_league_context(roster, data.teams)
            self._apply_colours(record, roster.team)
            teams[entry.slot_index] = record
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
        # First, ahead of every other guard and ahead of the first status
        # message: it is the one check that costs no I/O, and the failure it
        # prevents is the writer choking on another game's record type with an
        # exception outside this library's hierarchy.
        rosters.require_game(self.game_id)
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")

        reader = ISSRomReader(str(rom_path))
        with as_rom_error(rom_path):
            # Called for the side effect: it is what sets `header_offset`, which
            # every offset the writer uses is measured from. Its return value --
            # a 1 MB size floor -- is deliberately NOT enforced here. That floor
            # is a heuristic about which cartridge this is, and a heuristic
            # guards `analyze_rom` only: a false negative there costs
            # auto-detection, where refusing `patch --game iss-snes` on a
            # correct-but-unexpected image costs the user the patch. See
            # `ISSRomReader.validate_rom`.
            reader.validate_rom()
            fits = reader.data_fits()
        if not fits:
            # Arithmetic, not a guess, so it guards `patch` too. Every write in
            # the writer is an absolute seek into a file opened `r+b`, and
            # seeking past the end and writing extends it -- so without this a
            # short file came back as a 297 KB "patched ROM" made of one hole
            # and two flag tiles, reported as a success.
            raise RomError(
                f"Too small to be an ISS ROM: {rom_path} holds "
                f"{Path(rom_path).stat().st_size} bytes and this patcher writes as far as "
                f"{MIN_PATCHABLE_SIZE} past any copier header"
            )

        # The keys come from a plain dict that may have crossed a JSON boundary
        # since `map_rosters` built it, so the range is re-checked. Sorted, which
        # both makes the writes deterministic and reproduces the insertion order
        # upstream's sequential mapping produced -- `write_team_name_texts`
        # breaks a tie between two equally long names by whichever it met first.
        slots = sorted(slot for slot in rosters.teams if 0 <= slot < TOTAL_TEAMS)

        patched_names: dict[int, str] = {}
        patched_tile_names: dict[int, str] = {}
        patched_flag_colors: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
        teams_patched = 0
        players_patched = 0

        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # The constructor copies the input over the output and holds a
            # handle. `with` is what releases it if anything below raises;
            # `write_name_tiles` raises `RomError` by design.
            with ISSRomWriter(str(rom_path), str(output_path), reader.header_offset) as writer:
                for i, slot in enumerate(slots):
                    record: ISSTeamRecord = rosters.teams[slot]
                    if on_progress is not None:
                        on_progress(i / len(slots), f"Patching {record.name}...")

                    # This order is upstream's and is preserved. The four calls
                    # touch disjoint regions, but the three below the loop do not
                    # all: see `ISSRomWriter.write_name_tiles`.
                    writer.write_player_names(slot, record.players)
                    written = writer.write_player_data(slot, record.players)
                    writer.write_kit_colors(slot, record)
                    if record.flag_colors:
                        writer.write_predominant_color(slot, record.flag_colors[0])
                        patched_flag_colors[slot] = (record.flag_colors[0], record.flag_colors[1])

                    patched_names[slot] = record.name
                    patched_tile_names[slot] = record.short_name
                    # Unconditional, as in WE2002 and for the same reason
                    # `PatchResult` gives: the name, the kit and the description
                    # land whether or not a squad was supplied, so an in-range
                    # slot has changed the ROM even with an empty player list.
                    teams_patched += 1
                    # `written`, not `len(record.players)`: the writer stops at
                    # 15 and a longer squad leaves the rest on the floor.
                    players_patched += written

                if on_progress is not None:
                    on_progress(0.80, "Writing flags...")
                writer.write_flag_tiles_and_colors(patched_flag_colors)

                if on_progress is not None:
                    on_progress(0.85, "Writing team names...")
                writer.write_team_name_texts(patched_names)
                writer.write_team_descriptions(patched_names)

                if on_progress is not None:
                    on_progress(0.90, "Writing in-game name tiles...")
                writer.write_name_tiles(patched_tile_names)

                if on_progress is not None:
                    on_progress(0.95, "Finalizing...")
                self.status("Saving patched ROM...")
                writer.finalize()

            # `finalize` returns `None`, so the output file itself is the only
            # evidence available that anything was written.
            if not Path(output_path).exists():
                raise RomError(f"Failed to write patched ROM to {output_path}")

        if on_progress is not None:
            on_progress(1.0, f"Done! Saved to {output_path}")
        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    # -- extras -------------------------------------------------------------

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Upstream's `create_slot_mapping`, and the whole of what it offered.
        Teams beyond the 27th are dropped, as they were there.

        This is a *starting point a caller may edit*, which is the only reason
        it survives: as the sole mapping it put whoever the provider happened to
        list first into Germany. See this module's docstring.
        """
        return [
            SlotMapping(
                slot_index=i,
                team_id=roster.team.id,
                team_name=roster.team.name,
            )
            for i, roster in enumerate(data.teams)
            if i < TOTAL_TEAMS
        ]

    @staticmethod
    def _apply_colours(record: ISSTeamRecord, team: Team) -> None:
        """Copy the provider's team colours onto the ROM record.

        Four things read them: both outfield kits, the flag palette, the flag
        tiles and the predominant-colour byte. Upstream computed all four inside
        its patch loop from a `Team` it had beside the record; here they are on
        the record, because `MappedRosters` is what crosses from `map_rosters` to
        `patch` and a `Team` does not travel with it.

        `flag_colors` is empty when the provider gave no primary colour, which
        is exactly when upstream wrote neither the flag nor the predominant
        byte; and it holds the primary twice when there is no alternate, which
        is what upstream's `elif primary` branch did. The goalkeeper kit is
        assigned whatever the provider said, as it was there.
        """
        primary = _parse_hex_colour(team.color)
        alternate = _parse_hex_colour(team.alternate_color)
        if primary is not None:
            record.kit_home = (primary, KIT_SHORTS, primary)
        if alternate is not None:
            record.kit_away = (alternate, KIT_SHORTS, alternate)
        record.kit_gk = GK_KIT
        if primary is not None:
            record.flag_colors = [primary, alternate if alternate is not None else primary]
