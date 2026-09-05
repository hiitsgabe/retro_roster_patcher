"""Winning Eleven 2002 (PlayStation) on the unified Patcher interface.

`RomWriter` behaviours this module has to work around:

  * `write_team` writes no players at all unless handed a `players=` list.
  * `write_team` writes only as many players as the slot has room for — 14
    places for slots 0-17, 15 for slots 18-31 — and returns the count it
    actually wrote, so never count `len(record.players)` as patched.
  * `write_team` returns silently for a slot outside 0..31; bound every slot by
    `MAX_ML_SLOTS` before handing it over.
  * `write_team` only queues its 3D-jersey TEX patch; `flush_tex_patches` is
    what applies them, and must be called before the writer goes out of scope.
  * `finalize` returns `None`, so the output file is the only evidence of a
    write.

`verify_patches` is deliberately not called: it returns a human-readable report
and `PatchResult` has nowhere to put one.

The `SlotMapping` in `models.py` is the ROM-facing one and is unused here; only
the JSON-serialisable `core.models` one crosses this interface.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.assets import MissingAssetError
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
from .models import WETeamRecord
from .ppf import PPFError, apply_ppf
from .rom_reader import RomReader
from .rom_writer import RomWriter
from .stat_mapper import StatMapper
from .translations.we2002 import LANGUAGE_CODES, LANGUAGES, ensure_ppf

# The ROM has two team tables: 32 Master League slots and 63 national slots.
# `slot_index` always means a Master League slot; the national table is reachable
# only through `write_nat_team`, which the public `SlotMapping` cannot address.
MAX_ML_SLOTS = 32


def _parse_hex_colour(value: str) -> tuple[int, int, int] | None:
    """Read a `RRGGBB` or `#RRGGBB` provider colour, or `None` if it is neither.

    Three-digit shorthand and colour names are `None` too, so the record keeps
    its own default rather than a kit built from half a value.
    """
    text = value.lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


@register(
    "we2002",
    platform="psx",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class WE2002Patcher(Patcher):
    """Soccer ROMs have fixed, unnamed team slots.

    There is no code to match a real team against, so the caller must supply an
    explicit slot mapping; `default_slot_mapping` is a sequential starting point.

    ESPN's roster endpoint serves the current squad whatever season is asked
    for, so `fetch`'s `season` reaches the statistics documents and the cache
    keys, not the squad.

    League ids are ESPN's: `--league-id 2001` is the Premier League. ESPN's own
    identifiers are string codes (`eng.1`, `esp.1`); `ESPN_LEAGUES` carries an
    integer per code and `EspnClient` translates, so `--league-id` stays an int.
    """

    #: Language codes `patch` accepts in `options["language"]`, in menu order.
    #:
    #: Keep it a plain class attribute, not a `@register` capability: callers
    #: must be able to ask before calling `patch`, and absent means "ships no
    #: translations".
    languages: tuple[str, ...] = tuple(LANGUAGE_CODES)

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        assets_dir: Path | str | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(
            cache_dir,
            provider=provider,
            on_status=on_status,
            on_partial=on_partial,
        )
        # User-supplied, read-only: holds the community `w202-english.ppf`.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self.mapper = StatMapper()
        # Keep it `Any`: tests substitute a stand-in implementing only the
        # methods `fetch` calls.
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        # `RomReader.__init__` tolerates a missing file and reports size 0, so
        # this check is what raises the `RomError` the interface promises.
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        # `validate_rom` is size-only, so any file of 100 MB or more gets opened
        # here; keep the wrapper so an unreadable one still leaves as `RomError`.
        with as_rom_error(rom_path):
            info = RomReader(str(rom_path)).get_rom_info()
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=slot.current_name,
                    # Must stay distinct per slot: `league_group` is "Master
                    # League" for all 32, so the number has to be in the name.
                    display_name=f"{slot.league_group} Slot {slot.index + 1}",
                )
                for slot in info.team_slots
            ],
            extra={"version": info.version},
        )

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        if league_id is None:
            raise CapabilityError("we2002 requires a league_id; there is no default league")

        if on_progress is not None:
            on_progress(0.05, "Fetching league info...")
        leagues = self.api.get_leagues(id=league_id, season=season)
        league = next(iter(leagues), None)
        if league is None:
            raise ApiError(f"League {league_id} not found for season {season}")

        if on_progress is not None:
            on_progress(0.1, f"Fetching teams for {league.name}...")
        teams = self.api.get_teams(league_id, season)
        if not teams:
            raise ApiError(f"League {league_id} has no teams for season {season}")

        # Publish the team list immediately so a UI can render it while squads load.
        skeleton = LeagueData(
            league=league,
            teams=[TeamRoster(team=t, loading=True) for t in teams],
        )
        self.partial(skeleton)

        rosters: list[TeamRoster] = []
        for i, team in enumerate(teams):
            if on_progress is not None:
                on_progress(0.1 + 0.8 * (i / len(teams)), f"Fetching {team.name}...")
            # One failed team must not cost the rest of the league: it keeps its
            # place and carries the reason on `TeamRoster.error`. Build a fresh
            # roster rather than mutating the published skeleton, which a caller
            # may still be rendering.
            roster = TeamRoster(team=team)
            try:
                # Squad first: it is one request, stats are one per athlete
                # (~25 a team), and under a rate limiter the second call is the
                # one that gets throttled. ESPN's squad endpoint ignores
                # `season`, which here only varies the cache key — drop it and
                # the first fetch of a team freezes its squad on disk forever.
                roster.players = self.api.get_squad(team.id, season)
                try:
                    # Stats are optional: `map_player` falls back to position and
                    # age, so this failure costs ratings and not the team.
                    stats = self.api.get_player_stats(team.id, season)
                    roster.player_stats = {ps.player_id: ps for ps in stats}
                except Exception:
                    self.status(
                        f"{team.name}: stats unavailable, ratings will use position defaults"
                    )
            except Exception as exc:
                # Deliberately broad: a provider can fail in ways this module has
                # no list of. `TransportLeak` is a `BaseException` so the network
                # guard still escapes this.
                roster.error = f"Failed to load squad: {exc}"
                self.status(f"{team.name}: {roster.error}")
            rosters.append(roster)

        if on_progress is not None:
            on_progress(1.0, "Complete")
        return LeagueData(league=league, teams=rosters)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        # Narrows `list | None` for the type checker; `check_slot_mapping` has
        # already refused an absent or empty mapping.
        entries = slot_mapping or []

        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, WETeamRecord] = {}
        for entry in entries:
            # `write_team` returns without writing for a slot outside this
            # range, so accepting one would report a patch that never happened.
            if not 0 <= entry.slot_index < MAX_ML_SLOTS:
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the WE2002 range 0..{MAX_ML_SLOTS - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            # The whole league, not just this team: percentiles are normalised
            # league-wide.
            record = self.mapper.map_team_with_league_context(roster, data.teams)
            self._apply_kit_colours(record, roster.team)
            teams[entry.slot_index] = record
        return MappedRosters(game_id=self.game_id, teams=teams)

    def patch(
        self,
        *,
        rom_path: Path,
        output_path: Path,
        rosters: MappedRosters,
        on_progress: ProgressFn | None = None,
        **options: Any,
    ) -> PatchResult:
        # Must come before every other guard: it is the only check that costs no
        # I/O, and it stops the writer choking on another game's record type.
        rosters.require_game(self.game_id)
        language = options.get("language", "en")
        if language not in LANGUAGES:
            raise CapabilityError(
                f"Unknown language {language!r}. Supported: {', '.join(LANGUAGES)}"
            )
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        # Every write below is an absolute seek into a 700 MB image, and seeking
        # past the end of a short file extends it: without this a 4 KB input
        # comes back as a 12 MB "patched ISO" holding nothing but the patch.
        if not RomReader(str(rom_path)).validate_rom():
            raise RomError(f"Too small to be a WE2002 ROM, or not a WE2002 ROM: {rom_path}")

        # `Patcher.patch` promises `RomError` on any write failure, and
        # `validate_rom` above is size-only, so an unreadable image reaches the
        # writer's `shutil.copy2`. Only `OSError` is converted; anything else
        # from the writer is a bug in the writer.
        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # The constructor copies the ROM to `output_path`, so the file the
            # translation patches below exists by the time it runs.
            writer = RomWriter(str(rom_path), str(output_path))
            self._apply_translation(output_path, language, on_progress)

            # Re-check the range even though `map_rosters` does: a caller may
            # hand `patch` a `MappedRosters` it built itself. Sorted so writes go
            # out in slot order regardless of insertion order.
            slots = sorted(slot for slot in rosters.teams if 0 <= slot < MAX_ML_SLOTS)

            teams_patched = 0
            players_patched = 0
            for i, slot in enumerate(slots):
                record = rosters.teams[slot]
                if on_progress is not None:
                    on_progress(0.05 + 0.9 * (i / len(slots)), f"Writing slot {slot}...")
                # Pass the whole list and count what comes back: the writer's
                # loop is bounded by the slot's ROM capacity (14 or 15 places),
                # so a 22-man squad leaves records on the floor.
                written = writer.write_team(slot, record, players=record.players, include_flag=True)
                # Unconditional: `write_team` writes names, abbreviations, force
                # bars, kit colours and flag before it looks at `players`, so an
                # in-range slot has changed the ROM even with an empty squad.
                teams_patched += 1
                players_patched += written

            # Every `write_team` above queued a 3D-jersey TEX patch; without this
            # they are discarded when the writer goes out of scope.
            writer.flush_tex_patches()

            if on_progress is not None:
                on_progress(1.0, "Saving patched ROM...")
            self.status("Saving patched ROM...")
            writer.finalize()
            # `finalize` returns `None`, so the output file is the only evidence
            # that anything was written.
            if not Path(output_path).exists():
                raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Teams beyond the 32 Master League slots are dropped.
        """
        return [
            SlotMapping(slot_index=i, team_id=roster.team.id, team_name=roster.team.name)
            for i, roster in enumerate(data.teams)
            if i < MAX_ML_SLOTS
        ]

    @staticmethod
    def _apply_kit_colours(record: WETeamRecord, team: Team) -> None:
        """Copy the provider's team colours onto the ROM record.

        `kit_home` and `kit_away` fill the maglia palette (2D menu preview and
        3D shorts) and the flag palette; `kit_home` alone drives the 3D jersey
        TEX patch. A colour the provider did not supply must leave the record's
        default in place. `kit_third` is never a provider value: mirror it from
        `kit_home` unconditionally, or a defaulted record gets a white shirt
        with a black accent.
        """
        home = _parse_hex_colour(team.color)
        if home is not None:
            record.kit_home = home
        away = _parse_hex_colour(team.alternate_color)
        if away is not None:
            record.kit_away = away
        record.kit_third = record.kit_home

    def _apply_translation(
        self,
        output_path: Path,
        language: str,
        on_progress: ProgressFn | None,
    ) -> None:
        """Apply the translation PPF, degrading to Japanese menus on failure.

        Two patches can arrive here and must be applied differently:

          * the community full translation `w202-english.ppf`, applied as it
            stands with validation skipped. It is a PPF2 or PPF3 built against
            one specific dump, so its stored size and its 1024-byte block at
            0x9320 will not match every good image;
          * otherwise the generated patch — the packaged English PPF1, or a
            language generated into `cache_dir` — applied with validation.

        A failed translation is cosmetic; the roster patch under it is the point,
        so failures are reported and swallowed. `MissingAssetError` covers a
        missing packaged PPF, `PPFError` a patch file that cannot be read,
        `OSError` one that cannot be opened, and `ValueError` the bare raise from
        `menu_records._parse_ppf2` when a non-English language merges menu
        records out of a community file that is not PPF2.
        """
        name = LANGUAGES[language]
        if on_progress is not None:
            on_progress(0.02, f"Applying {name} translation...")
        try:
            community = self._community_ppf(language)
            if community is not None:
                apply_ppf(str(output_path), str(community), skip_validation=True)
            else:
                ppf_path = ensure_ppf(
                    str(self.cache_dir / "translations"),
                    language,
                    assets_dir=str(self.assets_dir) if self.assets_dir is not None else "",
                )
                apply_ppf(str(output_path), ppf_path)
        except (MissingAssetError, PPFError, OSError, ValueError) as exc:
            self.status(f"{name} translation skipped: {exc}")
            if on_progress is not None:
                on_progress(0.05, f"{name} translation skipped")
            return
        if on_progress is not None:
            on_progress(0.05, f"{name} translation applied")

    def _community_ppf(self, language: str) -> Path | None:
        """The operator's own `w202-english.ppf`, if there is one to apply here.

        English only: the file is a full English translation, and for the other
        languages the generator reads only its menu records. Applying it whole
        would give English menus under a Spanish request.

        `is_file` rather than `exists`, so a directory of that name is "no
        community patch" instead of an `IsADirectoryError` inside `apply_ppf`.
        """
        if language != "en" or self.assets_dir is None:
            return None
        candidate = self.assets_dir / "w202-english.ppf"
        return candidate if candidate.is_file() else None
