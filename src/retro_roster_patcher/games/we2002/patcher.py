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

import hashlib
import shutil
import struct
from pathlib import Path
from typing import Any

from ...core.assets import MissingAssetError
from ...core.errors import ApiError, CapabilityError, MappingError, RomError, as_rom_error
from ...core.kits import KitSpec, KitTemplate, default_kits, kit_from_image
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
from .flag_atlas import VALID_STYLES, LogoImages, assign_styles
from .jersey_tex import JERSEY_TEMPLATES, apply_choice, is_page_png, page_png, template_preview
from .logo_render import render_logos
from .models import WETeamRecord
from .ppf import PPFError, apply_ppf
from .rom_reader import RomReader
from .rom_writer import (
    _ML_COLOR_OFFSETS,
    _OFS_BANDIERE_FORMA1,
    _TEX_JERSEY_COLORS,
    RomWriter,
    read_tex_files,
)
from .screen_art import SLOT_NAMES, SLOTS, apply_images, export_slot
from .stat_mapper import StatMapper
from .translations.we2002 import LANGUAGE_CODES, LANGUAGES, ensure_ppf

# The ROM has two team tables: 32 Master League slots and 63 national slots.
# A mapped `slot_index` i fills both tables at once, as the original tool did:
# Master League slot i when i < 32, and national slot i when i < 63. So slots
# 32..62 are national-only, and a league of up to 63 teams fits.
MAX_ML_SLOTS = 32
MAX_NAT_SLOTS = 63


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
    #: `patch` draws each team's logo into the flag slots unless `logos=False`.
    supports_logos: bool = True
    #: `patch(blank_unused=True)` empties the name and flag of every slot the
    #: mapping leaves out, in both tables.
    supports_blank_unused: bool = True
    #: `patch(kits=...)` as `Patcher` describes it: a spec or a picture is
    #: painted onto the 3D kit texture, `{"template": n}` copies one of the 95
    #: designs `kit_templates` lists, and the 2D preview follows.
    supports_kits: bool = True
    #: Slots 0-31 fill a Master League slot and the national slot of the same
    #: number; 32-62 a national slot only.
    slot_groups: tuple[tuple[str, int, int], ...] = (
        ("Master League", 0, MAX_ML_SLOTS),
        ("National only", MAX_ML_SLOTS, MAX_NAT_SLOTS - MAX_ML_SLOTS),
    )
    #: The groups describe the slots, not leagues: one league fills them.
    max_leagues: int = 1
    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the current art of each as a PNG to edit.
    image_slots: tuple[str, ...] = SLOT_NAMES

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
        # User-supplied, read-only: holds the community `w202-english.ppf` and
        # any `logos/<team_id>.png` overrides.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._transport = transport
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
                # The eleven who started most of the team's last few matches pick
                # the starting places (`_select_best_23`); the season's starts
                # only fill what the lineup leaves open. Optional, and plain data
                # for the JSON boundary.
                if hasattr(self.api, "get_lineup"):
                    try:
                        lineup = self.api.get_lineup(team.id, league_id)
                    except Exception:
                        lineup = None
                    if lineup:
                        roster.extra["lineup"] = lineup
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

    def suggest_squad_order(self, team_roster):
        ordered = self.mapper._select_best_23(
            team_roster.players, team_roster.player_stats, team_roster.extra.get("lineup")
        )
        return self._append_unused(ordered, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        # Narrows `list | None` for the type checker; `check_slot_mapping` has
        # already refused an absent or empty mapping.
        entries = slot_mapping or []

        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, WETeamRecord] = {}
        for entry in entries:
            # The writers return without writing for a slot outside this range,
            # so accepting one would report a patch that never happened.
            if not 0 <= entry.slot_index < MAX_NAT_SLOTS:
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the WE2002 range 0..{MAX_NAT_SLOTS - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            # The whole league, not just this team: percentiles are normalised
            # league-wide.
            record = self.mapper.map_team_with_league_context(
                roster, data.teams, order_as_given=order_as_given
            )
            self._apply_kit_colours(record, roster.team)
            record.kit_home_spec, record.kit_away_spec = default_kits(roster.team)
            # The specs are what the kits are drawn from; the RGB fields, which
            # the flag palette and the 2D preview read, follow their shirts (the
            # away shirt is the contrast of the home one when the provider gave
            # only one colour).
            if record.kit_home_spec is not None:
                record.kit_home = record.kit_home_spec.shirt
            if record.kit_away_spec is not None:
                record.kit_away = record.kit_away_spec.shirt
            record.kit_third = record.kit_home
            record.team_id = roster.team.id
            record.logo_url = roster.team.logo_url
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
        reader = RomReader(str(rom_path))
        if not reader.validate_rom():
            raise RomError(f"Too small to be a WE2002 ROM, or not a WE2002 ROM: {rom_path}")

        # Re-check the range even though `map_rosters` does: a caller may
        # hand `patch` a `MappedRosters` it built itself. Sorted so writes go
        # out in slot order regardless of insertion order.
        slots = sorted(slot for slot in rosters.teams if 0 <= slot < MAX_NAT_SLOTS)

        # Kits: False leaves the disc's 2D previews and 3D textures alone; a
        # dict overrides the records' kits. Every choice is read and checked
        # here, before the 700 MB copy below, so a bad picture or spec is a
        # `CapabilityError` naming the slot rather than a failed patch.
        kits = options.get("kits", True)
        if isinstance(kits, dict):
            self._apply_kit_choices(rosters, slots, kits)

        # `Patcher.patch` promises `RomError` on any write failure, and
        # `validate_rom` above is size-only, so an unreadable image reaches the
        # writer's `shutil.copy2`. Only `OSError` is converted; anything else
        # from the writer is a bug in the writer.
        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # Every offset below is into raw 2352-byte sectors. A cooked
            # 2048-byte .iso of the same game is big enough to pass the size
            # check and would be patched into garbage. Inside `as_rom_error`:
            # this is the first read of the file, so an unreadable one fails here.
            if not reader.is_raw_image():
                raise RomError(
                    f"Not a raw CD image (.bin with 2352-byte sectors); a cooked .iso "
                    f"can't be patched: {rom_path}"
                )
            # The constructor copies the ROM to `output_path`, so the file the
            # translation patches below exists by the time it runs.
            writer = RomWriter(str(rom_path), str(output_path))
            self._apply_translation(rom_path, output_path, language, on_progress)

            if kits is False:
                writer.write_kits = False

            # Logos go first: they decide each team's flag style and palette,
            # which the per-team writes below put into the ROM.
            blank_unused = bool(options.get("blank_unused", False))
            logos_queued = 0
            used_styles: set[int] = set()
            if options.get("logos", True):
                logos_queued, used_styles = self._prepare_logos(
                    rom_path, rosters, slots, writer, blank_unused
                )

            teams_patched = 0
            players_patched = 0
            for i, slot in enumerate(slots):
                record = rosters.teams[slot]
                if on_progress is not None:
                    on_progress(0.05 + 0.9 * (i / len(slots)), f"Writing slot {slot}...")
                # Both tables, Master League first, as the original tool did.
                # The national squad (23 places) holds what the Master League
                # one (14 or 15) does and more, so its count is the one reported.
                if slot < MAX_ML_SLOTS:
                    writer.write_team(slot, record, players=record.players, include_flag=True)
                written = writer.write_nat_team(
                    slot, record, players=record.players, include_flag=True
                )
                # Unconditional: the writers write names, abbreviations, force
                # bars, kit colours and flag before they look at `players`, so an
                # in-range slot has changed the ROM even with an empty squad.
                teams_patched += 1
                players_patched += written

            if blank_unused:
                logos_queued += self._blank_unused(rosters, writer, used_styles)
            images = self._collect_images(options.get("images"))

            # Every `write_team` above queued a 3D-jersey TEX patch; without this
            # they are discarded when the writer goes out of scope.
            writer.flush_tex_patches()
            for line in getattr(writer, "kit_problems", ()):
                self.status(f"Kits: {line}")
            if logos_queued:
                self._flush_logos(writer)
            if images:
                self._apply_images(output_path, images)

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

    def _prepare_logos(
        self,
        rom_path: Path,
        rosters: MappedRosters,
        slots: list[int],
        writer: Any,
        blank_unused: bool = False,
    ) -> tuple[int, set[int]]:
        """Give every mapped team with a logo a flag style of its own.

        Downloads (or reads from `assets_dir/logos/<team_id>.png`) each team's
        logo, renders it into the style's three bitmaps and queues them on the
        writer; the record's style byte and palette are set so the per-team
        writes carry them. Without Pillow every logo is skipped, with a single
        status line. One team's bad logo costs that team its logo and nothing
        else. Master League slot 22 draws through slot 10's palette, so a logo
        there would take slot 10's colours: that slot keeps the striped flag.
        Returns how many logos were queued.
        """
        wanted = []
        for slot in slots:
            record = rosters.teams[slot]
            if not (record.logo_url or self._logo_override(record)):
                continue
            if slot < MAX_ML_SLOTS and slot not in _ML_COLOR_OFFSETS:
                self.status(
                    f"{record.name}: logo skipped, Master League slot {slot} has no "
                    f"palette of its own"
                )
                continue
            wanted.append(slot)
        if not wanted:
            return 0, set()
        with open(rom_path, "rb") as f:
            f.seek(_OFS_BANDIERE_FORMA1)
            forma = f.read(95)
        if blank_unused:
            patched = set(range(MAX_NAT_SLOTS + MAX_ML_SLOTS))
        else:
            patched = set(slots) | {MAX_NAT_SLOTS + s for s in slots if s < MAX_ML_SLOTS}
        styles = assign_styles(forma, patched, wanted)
        queued = 0
        used: set[int] = set()
        for slot in wanted:
            record = rosters.teams[slot]
            if slot not in styles:
                self.status(f"{record.name}: logo skipped, no flag styles left")
                continue
            try:
                png = self._load_logo(record)
                images, stadium = render_logos(png)
            except ImportError:
                self.status("Pillow is not installed; team logos skipped")
                return queued, used
            except Exception as exc:  # a bad download or an unreadable image
                self.status(f"{record.name}: logo skipped: {exc}")
                continue
            record.logo_png = png
            record.flag_style = styles[slot]
            record.flag_palette = list(images.palette) + [(0, 0, 0)]
            writer.queue_logo(styles[slot], images, stadium)
            queued += 1
            used.add(styles[slot])
        return queued, used

    def _apply_kit_choices(
        self, rosters: MappedRosters, slots: list[int], given: dict[Any, Any]
    ) -> None:
        """Put `patch(kits={slot: {"home": KIT, "away": KIT}})` onto the records.

        KIT is a `KitSpec`, its dict, `{"image": png bytes or path}` (a
        picture of the kit, or the 128x256 texture page itself from
        `kit_page_png`, painted over), `{"template": n}` (a TEX index
        `kit_templates` lists) or None for the record's default. The record's
        RGB kit colours, which the flag palette and the 2D preview read, follow
        the choice: the spec's shirt, a kit picture's (read with
        `kit_from_image`, left alone when it cannot be; a page is left alone),
        or the template's known shirt colour.
        """
        for slot in slots:
            chosen = given.get(slot) or given.get(str(slot)) or {}
            record = rosters.teams[slot]
            for which in ("home", "away"):
                if chosen.get(which) is None:
                    continue
                try:
                    spec, image, template = self._kit_choice(chosen[which])
                except CapabilityError as exc:
                    raise CapabilityError(f"slot {slot} {which} kit: {exc}") from exc
                except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
                    raise CapabilityError(f"slot {slot} {which} kit: {exc}") from exc
                setattr(record, f"kit_{which}_spec", spec)
                setattr(record, f"kit_{which}_image", image)
                setattr(record, f"kit_{which}_template", template)
                colour = None
                if spec is not None:
                    colour = spec.shirt
                elif template is not None:
                    colour = _TEX_JERSEY_COLORS[template]
                elif image is not None and not is_page_png(image):
                    try:
                        colour = kit_from_image(image).shirt
                    except (ImportError, ValueError, OSError):
                        pass
                if colour is not None:
                    setattr(record, f"kit_{which}", colour)
            record.kit_third = record.kit_home

    @staticmethod
    def _kit_choice(value: Any) -> tuple[KitSpec | None, bytes | None, int | None]:
        """One KIT of the option as (spec, image, template), exactly one set,
        checked: the template in range, the picture read and decodable by
        Pillow, the spec well formed. Raises `CapabilityError` for a bad
        template, else whatever reading or parsing raises."""
        if isinstance(value, KitSpec):
            return value, None, None
        value = dict(value)
        if "template" in value:
            template = int(value["template"])
            if template not in JERSEY_TEMPLATES:
                raise CapabilityError(
                    f"Unknown kit template {template}; we2002 has 0..{len(JERSEY_TEMPLATES) - 1}"
                )
            return None, None, template
        if "image" in value:
            png = value["image"]
            data = Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)
            import io

            try:
                from PIL import Image
            except ImportError as exc:
                raise CapabilityError("kit pictures need Pillow (the images extra)") from exc
            with Image.open(io.BytesIO(data)) as picture:  # raises OSError when not an image
                picture.load()
            return None, data, None
        return KitSpec.from_dict(value), None, None

    @staticmethod
    def _tex_files(rom_path: Path) -> dict[int, bytes]:
        """The disc's TEX files, after the same checks `patch` makes: the file
        exists, is big enough to be WE2002 and is a raw 2352-byte image."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = RomReader(str(rom_path))
        with as_rom_error(rom_path):
            if not reader.validate_rom() or not reader.is_raw_image():
                raise RomError(f"Not a raw WE2002 image (.bin with 2352-byte sectors): {rom_path}")
            return read_tex_files(str(rom_path))

    def kit_templates(self, rom_path: Path) -> list[KitTemplate]:
        """The 95 jersey designs on the disc, one per TEX file: 0-62 the
        national and all-star kits, 63-94 the Master League clubs, each with a
        96x128 PNG of its home kit. Needs Pillow; `CapabilityError` without it."""
        files = self._tex_files(rom_path)
        try:
            return [
                KitTemplate(index, name, template_preview(files[index]))
                for index, name in JERSEY_TEMPLATES.items()
                if index in files
            ]
        except ImportError as exc:
            raise CapabilityError("Kit templates need Pillow (the images extra)") from exc

    def kit_page_png(
        self,
        rom_path: Path,
        slot: int,
        which: str = "home",
        *,
        template: int | None = None,
        guides: bool = True,
    ) -> bytes:
        """The 3D kit texture page of a slot's current kit as a 128x256 RGBA
        PNG to paint over and hand back as `patch(kits={slot: {which: {"image":
        png}}})`. `slot` is a TEX index: 0-62 the national teams, 63-94 the
        Master League (a mapped slot s below 32 owns both TEX s and 63 + s);
        `template=n` reads design n instead, the numbering `kit_templates`
        uses. `which` is "home" or "away". With `guides`, every region the
        outfield model draws is outlined in red and labelled. Needs Pillow;
        `CapabilityError` without it."""
        index = slot if template is None else template
        if index not in JERSEY_TEMPLATES:
            raise CapabilityError(
                f"Unknown kit slot {index}; we2002 has 0..{len(JERSEY_TEMPLATES) - 1}"
            )
        if which not in ("home", "away"):
            raise CapabilityError(f"Unknown kit {which!r}; one of home, away")
        files = self._tex_files(rom_path)
        if index not in files:
            raise RomError(f"TEX_{index:02d}.BIN is not on the disc")
        try:
            return page_png(files[index], which, guides)
        except ImportError as exc:
            raise CapabilityError("Kit pages need Pillow (the images extra)") from exc

    def kit_preview_png(
        self,
        rom_path: Path,
        slot: int,
        which: str = "home",
        choice: Any = None,
        *,
        template: int | None = None,
    ) -> bytes:
        """A 96x128 RGBA "photo" of a slot's kit, as `patch` would leave it:
        `choice` is one KIT of the `kits` option (a `KitSpec` or its dict, a
        `{"image": png bytes or path}`, a page or a photo, or `{"template": n}`),
        applied in memory to the slot's current texture, None for the kit as it
        is; `template=n` is the shading base for a spec or picture, as the
        writer uses it. `slot` and `which` as in `kit_page_png`. Nothing is
        written. Needs Pillow; `CapabilityError` without it."""
        if slot not in JERSEY_TEMPLATES:
            raise CapabilityError(
                f"Unknown kit slot {slot}; we2002 has 0..{len(JERSEY_TEMPLATES) - 1}"
            )
        if which not in ("home", "away"):
            raise CapabilityError(f"Unknown kit {which!r}; one of home, away")
        files = self._tex_files(rom_path)
        if slot not in files:
            raise RomError(f"TEX_{slot:02d}.BIN is not on the disc")
        tex = files[slot]
        try:
            if choice is not None:
                spec, image, chosen = self._kit_choice(choice)
                base = chosen if chosen is not None else template
                tex = apply_choice(tex, which, spec, image, base, files)
            return template_preview(tex, which)
        except ImportError as exc:
            raise CapabilityError("Kit previews need Pillow (the images extra)") from exc

    def _collect_images(self, given: Any) -> dict[str, bytes]:
        """Screen art to write: `patch(images=...)` first, then `<assets_dir>/images/<slot>.png`."""
        images: dict[str, bytes] = {}
        if self.assets_dir is not None:
            for name in SLOTS:
                candidate = self.assets_dir / "images" / f"{name}.png"
                if candidate.is_file():
                    images[name] = candidate.read_bytes()
        for name, png in dict(given or {}).items():
            if name not in SLOTS:
                raise CapabilityError(f"Unknown image slot {name!r}. Slots: {', '.join(SLOTS)}")
            images[name] = Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)
        return images

    def _apply_images(self, output_path: Path, images: dict[str, bytes]) -> None:
        """Write the screen art; like the logos, a failure costs the art only."""
        try:
            report = apply_images(str(output_path), images)
        except ImportError:
            self.status("Pillow is not installed; screen images skipped")
            return
        except (RomError, ValueError, OSError) as exc:
            self.status(f"Screen images skipped: {exc}")
            return
        for line in report.skipped:
            self.status(f"Screen images: {line}")
        self.status(f"Screen images written: {', '.join(sorted(images))}")

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back.

        Reads the image at `rom_path` only. Needs Pillow; raises `ImportError`
        without it, `RomError` for an image without the data files.
        """
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        with as_rom_error(rom_path):
            for name, slot in SLOTS.items():
                png = export_slot(str(rom_path), slot)
                target = out / f"{name}.png"
                target.write_bytes(png)
                written.append(target)
        return written

    def _blank_unused(self, rosters: MappedRosters, writer: Any, used_styles: set[int]) -> int:
        """Empty every slot the mapping left out, in both tables.

        An empty name, a black kit and a flag style whose three bitmaps are
        fully transparent; the style is one no mapped team draws, and never 1,
        the striped default a team without a logo keeps. Returns 1 when a
        blank style was queued on the writer, so the caller flushes it.
        """
        style = next(s for s in VALID_STYLES if s not in used_styles and s != 1)
        blank = WETeamRecord(
            name="",
            short_name="",
            kit_home=(0, 0, 0),
            kit_away=(0, 0, 0),
            kit_third=(0, 0, 0),
            flag_style=style,
            flag_palette=[(0, 0, 0)] * 16,
            logo_png=b"",
        )
        writer.queue_logo(
            style,
            LogoImages(
                palette=[(0, 0, 0)] * 15,
                big=[[0] * 64 for _ in range(28)],
                medium=[[0] * 24 for _ in range(16)],
                mini=[[0] * 24 for _ in range(8)],
            ),
        )
        for slot in range(MAX_NAT_SLOTS):
            if slot in rosters.teams:
                continue
            if slot < MAX_ML_SLOTS:
                writer.write_team(slot, blank, include_flag=True)
            writer.write_nat_team(slot, blank, include_flag=True)
        return 1

    def _logo_override(self, record: WETeamRecord) -> Path | None:
        if self.assets_dir is None or record.team_id is None:
            return None
        candidate = self.assets_dir / "logos" / f"{record.team_id}.png"
        return candidate if candidate.is_file() else None

    def _load_logo(self, record: WETeamRecord) -> bytes:
        """The logo bytes: the user's override, the cache, or a download."""
        override = self._logo_override(record)
        if override is not None:
            return override.read_bytes()
        digest = hashlib.sha1(record.logo_url.encode()).hexdigest()
        cache = self.cache_dir / "logos" / f"{digest}.png"
        if cache.is_file():
            return cache.read_bytes()
        tx = self._transport or _http.default_transport
        try:
            content = tx(record.logo_url, {}, 15.0)
        except Exception as exc:
            raise ApiError(f"GET {record.logo_url} failed: {exc}") from exc
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
        return content

    def _flush_logos(self, writer: Any) -> None:
        """Write the queued logos into the atlas; a failure costs the logos only."""
        try:
            report = writer.flush_logo_patches()
        except (RomError, ValueError, OSError) as exc:
            self.status(f"Team logos skipped: {exc}")
            return
        for line in report.skipped:
            self.status(f"Logo atlas: {line}")
        if report.files:
            self.status(f"Team logos written to {len(report.files)} data files")

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Teams 32..62 land in the national table only; beyond 63 they are dropped.
        """
        return [
            SlotMapping(slot_index=i, team_id=roster.team.id, team_name=roster.team.name)
            for i, roster in enumerate(data.teams)
            if i < MAX_NAT_SLOTS
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
        rom_path: Path,
        output_path: Path,
        language: str,
        on_progress: ProgressFn | None,
    ) -> None:
        """Apply the translation PPF, degrading to Japanese menus on failure.

        The community patch writes absolute offsets with validation skipped, so
        on a disc that is not the dump it was made for (another release, or a
        disc patched before: its 2D files were rebuilt) it corrupts the menu
        art. After it is applied the screen files are parsed; if one no longer
        reads, the output is copied again from `rom_path` and the translation
        reported as skipped.

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
                broken = _unreadable_screen_files(str(output_path))
                if broken:
                    shutil.copy2(rom_path, output_path)
                    raise PPFError(
                        f"{', '.join(broken)} no longer read after the patch: this disc is not "
                        "the dump the translation was made for (patch an original disc)"
                    )
            else:
                ppf_path = ensure_ppf(
                    str(self.cache_dir / "translations"),
                    language,
                    assets_dir=str(self.assets_dir) if self.assets_dir is not None else "",
                )
                apply_ppf(str(output_path), ppf_path)
        # `struct.error`/`IndexError`: a truncated PPF fails mid-parse. Like any
        # other bad translation file, skip it rather than fail the roster patch.
        except (MissingAssetError, PPFError, OSError, ValueError, struct.error, IndexError) as exc:
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


#: Screen files the community translation touches; each must still parse after it.
_SCREEN_FILES = ("DATSEL.BIN", "DAT2D.BIN", "TITLE.BIN", "LOGO.BIN")


def _unreadable_screen_files(image_path: str) -> list[str]:
    """The screen files on the disc that no longer parse as 2D data files."""
    from .flag_atlas import _disc_files, read_data_file

    try:
        with open(image_path, "rb") as f:
            present = [name for name in _SCREEN_FILES if name in _disc_files(f)]
    except RomError:
        return []  # not a disc the screen files can be read from: nothing to check
    broken = []
    for name in present:
        try:
            read_data_file(image_path, name)
        except RomError:
            broken.append(name)
    return broken
