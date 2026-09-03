"""Winning Eleven 2002 (PlayStation) on the unified Patcher interface.

This module is the translation layer between the ported reader/writer/mapper and
the contracts in `core.patcher`. The port is a faithful copy of an upstream that
had no tests, and every offset, encoder, truncation rule and padding byte in it
stays that way — a differential audit found the two trees produce byte-identical
images. What has been allowed to diverge is narrow and is called out at each
site: a return value the upstream did not have, a correction to a comment the
upstream got wrong, and two defects the upstream also had. None of them moves a
byte.

The places the ported code breaks one of `core.patcher`'s contracts are worked
around here rather than fixed there, with one exception, noted below:

  * `RomWriter.write_team` writes no players at all unless it is handed a
    `players=` list, so `patch` always passes one. Counting players without
    passing them would report a patch that never happened.
  * `RomWriter.write_team` writes only as many players as the slot has room for
    — 14 places for slots 0-17, 15 for slots 18-31 — and drops the rest. That
    one is fixed in the writer rather than worked around here: it now returns
    the number it actually wrote, and `patch` accumulates that. The alternative
    was to re-derive the capacity rule here from a private helper, which would
    have put two copies of it in the tree.
  * `RomWriter.write_team` returns silently for any slot outside 0..31, so both
    `map_rosters` and `patch` bound their slots by `MAX_ML_SLOTS`.
  * `RomWriter.finalize` has `pass` for a body and returns `None`, so there is
    nothing to check in its return value. `patch` checks that the output file
    exists instead.
  * `RomWriter.write_team` only *queues* its 3D-jersey TEX patch, on instance
    state. `flush_tex_patches` is the one thing that applies them, so `patch`
    calls it; without that they go out of scope with the writer.

`RomWriter.verify_patches` is deliberately not called. It re-reads the original
ROM in full and returns a human-readable report string, and `PatchResult` has
nowhere to put one; upstream stored it on the patcher as a side effect. It stays
available for a caller that wants it.

WE2002 also carries its own `SlotMapping(real_team, slot_index, slot_name,
nat_index)` in `models.py`. Nothing here uses it: the public `SlotMapping` is
JSON-serialisable and the ROM-facing one is not, and the national slots that
`nat_index` addresses are out of scope for v0.1.
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
from ...sports.api_football import ApiFootballClient, DailyLimitError, RateLimitError
from ...sports.espn import EspnClient
from ...sports.models import LeagueData, Team, TeamRoster
from .models import WETeamRecord
from .ppf import PPFError, apply_ppf
from .rom_reader import RomReader
from .rom_writer import RomWriter
from .stat_mapper import StatMapper
from .translations.we2002 import LANGUAGE_CODES, LANGUAGES, ensure_ppf

# The ROM has two team tables: 32 Master League slots and 63 national slots
# (`_SQUADRE_ML` and `_SQUADRE_NAZ` in `rom_writer.py`). `slot_index` here means
# a Master League slot, because that is the table `RomWriter.write_team` writes
# and the only one `RomReader.read_team_slots` reports. The national table is
# reachable only through `write_nat_team`, which needs an index the public
# `SlotMapping` has no field for; it is out of scope for v0.1.
MAX_ML_SLOTS = 32


def _parse_hex_colour(value: str) -> tuple[int, int, int] | None:
    """Read a `RRGGBB` or `#RRGGBB` provider colour, or `None` if it is neither.

    Anything else — an empty string, a three-digit shorthand, a colour name —
    returns `None`, so the record keeps its own default rather than a kit built
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
    "we2002",
    platform="psx",
    sport="soccer",
    requires_slot_mapping=True,
    # False because the default provider needs no key. It is a claim about the
    # patcher — "this cannot work without a credential" — and with ESPN it can.
    # `check_api_key` below still refuses a keyless `api-football`, which is the
    # narrower claim `requires_api_key` has no way to express and which the
    # keyless-by-default NHL94 sibling has never needed.
    requires_api_key=False,
    providers=("espn", "api-football"),
)
class WE2002Patcher(Patcher):
    """Soccer ROMs have fixed, unnamed team slots.

    There is no code to match a real team against, so the caller must supply an
    explicit slot mapping. `default_slot_mapping` produces the sequential one the
    old UI used, as a starting point.

    Providers: `espn` needs no key and is the default; `api-football` needs one.
    Neither offers historical seasons here — ESPN's roster endpoint serves the
    current squad whatever season is asked for, and API-Football's free plan
    refuses old ones — so `fetch`'s `season` reaches the statistics documents and
    the cache keys rather than the squad.

    League ids are provider-scoped and always have been: `--league-id 39` is the
    Premier League to API-Football and `--league-id 2001` is the Premier League
    to ESPN. ESPN's own identifiers are the string codes `eng.1` and `esp.1`, but
    `ESPN_LEAGUES` has always carried an integer id per code and `EspnClient`
    translates internally, so `--league-id` stays `type=int` and no CLI surface
    moves. Passing one provider's id to the other yields
    `ApiError("League ... not found")`, which is the same answer an id neither
    knows has always given.
    """

    #: Language codes `patch` accepts in `options["language"]`, in menu order.
    #:
    #: Declared on the class so a caller can ask *before* calling. `patch`
    #: validates `options["language"]` itself and raises `CapabilityError` for a
    #: code outside this set, but that is too late for the two callers that
    #: matter: a UI has to offer the choice before it has anything to patch, and
    #: `cli.commands._patch_options` has to tell a `--language` on NHL94 apart
    #: from a `--language` here, which no amount of inspecting `patch` can do —
    #: its signature ends in `**options`, so it accepts every keyword and honours
    #: two.
    #:
    #: Not a `@register` capability. `PatcherInfo` is the registry's public
    #: description of a patcher and it crosses the IPC boundary in `list`'s
    #: payload, so a field there is a surface change. A plain class attribute is
    #: the same duck-typed boundary check `build_patcher` already applies to
    #: `--assets-dir`, and absent means "ships no translations". `PatcherInfo` is
    #: the right long-term home — that dataclass documents itself as driving
    #: "which arguments to prompt for" — and moving it there is a surface change
    #: to make deliberately, not as a side effect of adding a CLI flag.
    languages: tuple[str, ...] = tuple(LANGUAGE_CODES)

    def __init__(
        self,
        cache_dir: Path | str,
        *,
        api_key: str | None = None,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        assets_dir: Path | str | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(
            cache_dir,
            api_key=api_key,
            provider=provider,
            on_status=on_status,
            on_partial=on_partial,
        )
        # Read-only, user-supplied, optional. Holds the community translation
        # `w202-english.ppf`, which this project does not redistribute.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self.mapper = StatMapper()
        # `Any` for the same reason NHL94's is: `fetch` drives whichever of two
        # clients the provider names, and no single type describes both. Here the
        # four methods `fetch` calls do agree — `EspnClient`'s two soccer methods
        # are positional supersets of `ApiFootballClient`'s, which
        # `test_espn.py` holds them to — so the looseness costs less than it does
        # there, but a `Protocol` naming four methods for two concrete classes,
        # one of which round F deletes, would outlive its usefulness by a round.
        #
        # `ApiFootballClient` types `api_key` as `str`, but a patcher may be
        # built without one so `analyze_rom` can inspect a ROM: `check_api_key`
        # is what refuses a missing key, and it runs at the top of `fetch`,
        # before any client method is reached. Both clients
        # `os.makedirs(cache_dir, exist_ok=True)` from their own constructors, so
        # there is no directory to create here.
        if self.provider == "api-football":
            self.api: Any = ApiFootballClient(
                api_key=self.api_key or "",
                cache_dir=str(self.cache_dir),
                on_status=on_status,
                transport=transport,
            )
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def check_api_key(self) -> None:
        """Refuse a keyless `api-football`, and only that.

        The base implementation reads the class-level `requires_api_key`, which
        is now `False` because the default provider needs no credential. That is
        the right answer for the patcher as a whole and the wrong one for one of
        its two providers: `ApiFootballClient` sends `x-apisports-key: ""`, the
        API answers with an error envelope, `_request` returns `{}`, and `fetch`
        raises `ApiError("League 39 not found for season 2024")` — a message that
        blames the league for a missing key.

        Round F removes the provider and this override with it.
        """
        if self.provider == "api-football" and not self.api_key:
            raise CapabilityError(f"{self._subject()} requires an api_key for api-football")
        super().check_api_key()

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        # `RomReader.__init__` tolerates a missing file and reports size 0, so
        # this check is what turns "not there" into the `RomError` the interface
        # promises. A readable file that is not this game is not an error:
        # `validate_rom` rejects anything under 100 MB and `get_rom_info` then
        # reports no slots and `is_valid=False`.
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        # `validate_rom` is size-only, so any file of 100 MB or more reaches
        # `read_slot_palettes`, which opens it. Without this the `PermissionError`
        # from a ROM on a yanked mount or with the read bit off walked out of
        # `analyze_rom`, past `cmd_analyze`'s `except RomError` and past `main`,
        # while the NHL94 sibling answered the same file with a clean `RomError`.
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
                    # The group plus the slot's 1-based number, not the bare
                    # group. `RomSlot.display_name` must be distinct across a
                    # ROM's slots because it is what a slot-picking UI lists,
                    # and `league_group` is "Master League" for all 32 of them —
                    # forwarding it gave a consumer one string thirty-two times.
                    # This game is the one that *requires* a slot mapping, so it
                    # is precisely the one whose slots a UI has to render.
                    display_name=f"{slot.league_group} Slot {slot.index + 1}",
                )
                for slot in info.team_slots
            ],
            extra={"version": info.version},
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        self.check_api_key()
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
            # A league fetch is dozens of requests and one of them failing must
            # not cost the other dozens: the team keeps its place in the list,
            # carries the reason on `TeamRoster.error`, and the fetch goes on.
            # Everything below `map_rosters` tolerates a team with no players.
            #
            # The roster is built empty and filled in, rather than mutating the
            # skeleton published above: that skeleton is a snapshot a caller may
            # still be rendering, and writing through it would change what it
            # already handed over.
            roster = TeamRoster(team=team)
            try:
                # The squad first, because that is the order upstream used and
                # the order that matters under a rate limiter — whichever call
                # goes second is the one that gets throttled, and losing the
                # squad costs the whole team where losing the stats does not.
                # Neither provider's squad endpoint takes a season — both serve
                # the squad as it stands today — so `season` here reaches the
                # cache key and nothing else. Without it the key is the team id
                # alone, which never changes, so the first fetch of a team froze
                # its squad on disk and every later season replayed it and
                # reported success.
                #
                # Positional, and it stays that way for both providers only
                # because `EspnClient.get_squad`'s parameters are
                # `ApiFootballClient.get_squad`'s with `league_code` appended.
                # ESPN needs that code and is not given it here: it resolves the
                # code from the team list `get_teams` cached three lines up,
                # which is the arrangement `_find_league_code_for_team` exists
                # for and which keeps this one call site provider-agnostic.
                roster.players = self.api.get_squad(team.id, season)
                try:
                    # `get_player_stats` returns a list, re-keyed by player id
                    # because that is the shape `map_team_with_league_context`
                    # reads. Stats are optional: `map_player` falls back to
                    # position and age for a player who has none, so this
                    # failure costs ratings and not the team.
                    #
                    # ESPN answers this with one request per athlete — about 25 a
                    # team — where API-Football answers with one. That is why the
                    # squad still goes first: it is now the cheap call as well as
                    # the indispensable one.
                    stats = self.api.get_player_stats(team.id, season)
                    roster.player_stats = {ps.player_id: ps for ps in stats}
                except Exception:
                    self.status(
                        f"{team.name}: stats unavailable, ratings will use position defaults"
                    )
            # Both are API-Football's own; ESPN has no quota and raises neither,
            # so under the default provider these two arms are unreachable and
            # the broad one below is what catches a failed fetch. Round F removes
            # them with the client.
            except DailyLimitError:
                roster.error = "Daily API limit reached — upgrade your plan"
                self.status(f"{team.name}: {roster.error}")
            except RateLimitError:
                roster.error = "Rate limit reached — squad unavailable"
                self.status(f"{team.name}: {roster.error}")
            except Exception as exc:
                # As broad as upstream's, and deliberately so — a provider can
                # fail in ways this module has no list of. `TransportLeak` is a
                # `BaseException` precisely so the network guard still escapes
                # this, and `check_api_key` has already run, so a missing key
                # raises before the loop rather than becoming 20 team errors.
                roster.error = f"Failed to load squad: {exc}"
                self.status(f"{team.name}: {roster.error}")
            rosters.append(roster)

        if on_progress is not None:
            on_progress(1.0, "Complete")
        return LeagueData(league=league, teams=rosters)

    # -- map ----------------------------------------------------------------

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        # This class declares `requires_slot_mapping`, so `check_slot_mapping`
        # has already refused an absent or empty mapping. The rebinding is what
        # narrows `list | None` for the type checker, not a fallback.
        entries = slot_mapping or []

        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, WETeamRecord] = {}
        for entry in entries:
            # `RomWriter.write_team` returns without writing for a slot outside
            # this range, so accepting one here would report a patch that never
            # reached the ROM.
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
            # league-wide, and passing one roster would rate every player against
            # his own team-mates only.
            record = self.mapper.map_team_with_league_context(roster, data.teams)
            self._apply_kit_colours(record, roster.team)
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
        # First, ahead of every other guard: it is the one check that costs no
        # I/O, and the failure it prevents is the writer choking on another
        # game's record type with an exception outside this library's hierarchy.
        rosters.require_game(self.game_id)
        language = options.get("language", "en")
        if language not in LANGUAGES:
            raise CapabilityError(
                f"Unknown language {language!r}. Supported: {', '.join(LANGUAGES)}"
            )
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        # `analyze_rom` has always published this predicate; `patch` did not
        # apply it. Every write below is an absolute seek into a 700 MB image,
        # and seeking past the end of a short file extends it, so a 4 KB input
        # came back as a 12 MB "patched ISO" holding nothing but the patch. This
        # is stricter than upstream, which validated nothing here — it is not a
        # restored guard, it is a new one.
        if not RomReader(str(rom_path)).validate_rom():
            raise RomError(f"Too small to be a WE2002 ROM, or not a WE2002 ROM: {rom_path}")

        # Everything from here down reads the input ROM or writes the output
        # one, and `Patcher.patch` promises `RomError` on any write failure.
        # `RomWriter.__init__` `shutil.copy2`s the input, and `validate_rom`
        # above is size-only, so an unreadable 700 MB image passed every guard
        # and then raised `PermissionError` out of the whole CLI, ending the
        # NDJSON stream after three `status` events with no terminal one.
        # Only `OSError` is converted: `_apply_translation` already catches its
        # own, and anything else from the writer is a bug in the writer.
        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # The constructor copies the ROM to `output_path`, so the file the
            # translation patches below exists by the time it runs.
            writer = RomWriter(str(rom_path), str(output_path))
            self._apply_translation(output_path, language, on_progress)

            # The range is re-checked here even though `map_rosters` refuses an
            # out-of-range slot: `teams` is a plain dict, and a caller may hand
            # `patch` a `MappedRosters` it built itself. `RomWriter.write_team`
            # returns silently for a slot outside 0..31, so an unchecked one would
            # be counted as patched without reaching the ROM. Sorted so the writes
            # go out in slot order regardless of the mapping's insertion order.
            slots = sorted(slot for slot in rosters.teams if 0 <= slot < MAX_ML_SLOTS)

            teams_patched = 0
            players_patched = 0
            for i, slot in enumerate(slots):
                record = rosters.teams[slot]
                if on_progress is not None:
                    on_progress(0.05 + 0.9 * (i / len(slots)), f"Writing slot {slot}...")
                # `players=` is not optional in practice: without it `write_team`
                # writes names, kits and the flag, and no players at all.
                #
                # The whole list goes over, and the count comes back: the writer's
                # loop is bounded by the slot's ROM capacity (14 or 15 places), so a
                # 22-man squad in slot 0 leaves eight records on the floor.
                # `len(record.players)` would report all 22 as patched.
                written = writer.write_team(slot, record, players=record.players, include_flag=True)
                # Unconditional, unlike NHL94, and `PatchResult` documents why:
                # `write_team` writes the names, abbreviations, force bars, kit
                # colours and flag before it looks at `players`, so an in-range
                # slot has changed the ROM even when the squad is empty. A slot
                # out of range never gets here — it was filtered above.
                teams_patched += 1
                players_patched += written

            # Every `write_team` above queued a 3D-jersey TEX patch. Without this
            # they are all discarded when the writer goes out of scope.
            writer.flush_tex_patches()

            if on_progress is not None:
                on_progress(1.0, "Saving patched ROM...")
            self.status("Saving patched ROM...")
            writer.finalize()
            # `finalize` returns `None`, so the output file itself is the only
            # evidence available that anything was written.
            if not Path(output_path).exists():
                raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    # -- extras -------------------------------------------------------------

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Teams beyond the Master League slot count are dropped. Upstream gave them
        a sentinel slot index of 32, which `RomWriter.write_team` then discarded
        without writing anything; there are 32 slots either way, and a mapping
        that stops at 32 says so where a sentinel did not.
        """
        return [
            SlotMapping(slot_index=i, team_id=roster.team.id, team_name=roster.team.name)
            for i, roster in enumerate(data.teams)
            if i < MAX_ML_SLOTS
        ]

    @staticmethod
    def _apply_kit_colours(record: WETeamRecord, team: Team) -> None:
        """Copy the provider's team colours onto the ROM record.

        `kit_home` and `kit_away` are what reach the image: they fill the maglia
        palette that drives the 2D menu preview and the 3D shorts, the flag
        palette, and — `kit_home` alone — the 3D jersey TEX patch. No writer path
        reads `kit_third` today.

        A colour the provider did not supply leaves the record's own default in
        place rather than overwriting it with black. `kit_third` is the one
        exception, because it is not a provider value at all: upstream assigned
        it from `kit_home` unconditionally and so does this, so the accent
        matches the shirt whether or not a provider colour arrived. Mirroring it
        only inside the `home is not None` branch left the two disagreeing —
        white shirt, black accent — in exactly the case where nothing had chosen
        either.
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

        A failed translation is cosmetic; the roster patch under it is the point.
        The original code swallowed every exception here; this narrows that to
        the four this call can actually raise, and reports rather than hides
        them.

        `MissingAssetError` is what `ensure_ppf` raises when the packaged
        English PPF is not in the installation — it is a `RetroRosterError`, not
        an `OSError`, so it needs naming separately. `PPFError` covers a patch
        file this applier cannot read, and `OSError` covers one it cannot open.

        `ValueError` comes from further in. If `assets_dir` holds a file named
        `w202-english.ppf` that is not PPF2 — a PPF1 or PPF3 community patch, or
        a truncated download — then `translations.we2002.menu_records`'s
        `_parse_ppf2` raises a bare `ValueError("Not a PPF2 file: ...")` while
        `ensure_ppf` is still building the merged patch. Unnamed here, a wrong
        file in a directory this code only reads aborted `patch` before a single
        roster byte was written.
        """
        name = LANGUAGES[language]
        if on_progress is not None:
            on_progress(0.02, f"Applying {name} translation...")
        try:
            ppf_path = ensure_ppf(
                str(self.cache_dir / "translations"),
                language,
                assets_dir=str(self.assets_dir) if self.assets_dir is not None else "",
            )
            apply_ppf(str(output_path), ppf_path, skip_validation=True)
        except (MissingAssetError, PPFError, OSError, ValueError) as exc:
            self.status(f"{name} translation skipped: {exc}")
            if on_progress is not None:
                on_progress(0.05, f"{name} translation skipped")
            return
        if on_progress is not None:
            on_progress(0.05, f"{name} translation applied")
