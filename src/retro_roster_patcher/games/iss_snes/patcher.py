"""International Superstar Soccer (SNES) on the unified Patcher interface.

An explicit slot mapping is required and must stay required: the 27 slots are
national teams -- Germany, Italy, Holland -- and the data source is a club
league, so assigning league team *i* to ROM slot *i* is arbitrary.
`default_slot_mapping` offers that sequential assignment as a starting point
only. There is no `api_key`; do not add one back.

ESPN's squad is today's, its statistics are the season's: `get_squad` has no
season in its URL while `get_player_stats` honours the season in its path, so a
2019 patch gets 2025's names carrying 2019's numbers. The season still reaches
`get_squad` because it is part of the cache key.

The writer is a ROM hack and `rom_writer.py`'s docstring is the map. Two
consequences reach this module: the order of the write calls in `patch` is load
bearing, and there is no ROM version check anywhere, so a revision other than
the one the ten 65816 patch addresses belong to is patched just as willingly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ...core.errors import ApiError, CapabilityError, MappingError, RomError, as_rom_error
from ...core.kits import KitSpec, KitTemplate, default_kits
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
from . import kits, screens
from .models import (
    PLAYERS_PER_TEAM,
    TEAM_ENUM_ORDER,
    TOTAL_TEAMS,
    Flag,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from .rom_reader import ISSRomReader
from .rom_writer import (
    BLANK_DESIGN,
    MIN_PATCHABLE_SIZE,
    ISSRomWriter,
    Pins,
    Shirts,
    design_pointers,
    two_band_flag,
)
from .stat_mapper import ISSStatMapper

#: The goalkeeper kit every patched team gets: green shirt, black shorts. A
#: constant, because no provider publishes a goalkeeper kit.
GK_KIT = ((0, 128, 0), (0, 0, 0))

#: What `patch(blank_unused=True)` writes over every player of an unmapped slot.
_BLANK_SQUAD = [ISSPlayerRecord(name="")] * PLAYERS_PER_TEAM


def _parse_hex_colour(value: str) -> tuple[int, int, int] | None:
    """Read a `RRGGBB` or `#RRGGBB` provider colour, or `None` if it is neither."""
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
    the ROM to match a provider team against, so the caller says which club goes
    in which slot.

    The only provider is `espn`, which needs no credential. League ids are
    ESPN's: `--league-id 2001` is the Premier League.
    """

    #: `patch` draws each team's crest as its flag unless `logos=False`.
    supports_logos: bool = True
    #: `patch(blank_unused=True)` empties every slot the mapping leaves out: no
    #: team name on the selection screen, in its description or on the in-game
    #: tiles, a transparent flag and blank player names. Its kit, chest design
    #: and abilities stay, and it can still be picked and played.
    supports_blank_unused: bool = True
    #: `patch(kits=...)` as `Patcher` describes it. A kit is colours: shirt,
    #: shorts and socks in shades. The pattern of the home kit borrows one of
    #: the cartridge's chest designs (`kits.PATTERN_DESIGNS`), drawn in the
    #: crest's colours; `{"template": n}` copies one of the 26 cartridge kits
    #: `kit_templates` lists, its chest design included.
    supports_kits: bool = True

    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the cartridge's current art for each. See `screens.SLOTS`.
    image_slots: tuple[str, ...] = screens.IMAGE_SLOT_NAMES

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
        # User-supplied, read-only: `logos/<team_id>.png` crests drawn instead
        # of the provider's logo.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._transport = transport
        self.mapper = ISSStatMapper()
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = ISSRomReader(str(rom_path))
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
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ],
            # Which end of the file every offset is measured from, and not
            # reachable any other way once the reader is gone.
            extra={"has_header": info.has_header},
        )

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
            # Must stay inside this library's hierarchy: a bare `ValueError`
            # escapes a consumer catching `RetroRosterError`.
            raise ApiError(f"League {league_id} not found for season {season}")

        if on_progress is not None:
            on_progress(0.1, f"Fetching teams for {league.name}...")
        teams = self.api.get_teams(league_id, season)
        if not teams:
            raise ApiError(f"League {league_id} has no teams for season {season}")

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
            # Built fresh, not mutated in place: the skeleton published above may
            # still be rendering in a caller.
            roster = TeamRoster(team=team)
            try:
                # `season` reaches the cache key and nothing else -- ESPN's squad
                # endpoint serves the squad as it stands today. Keep passing it,
                # or every season replays the first one ever fetched.
                roster.players = self.api.get_squad(team.id, season)
                try:
                    stats = self.api.get_player_stats(team.id, season)
                    roster.player_stats = {ps.player_id: ps for ps in stats}
                except Exception:
                    # Stats are optional: `map_player` falls back to position and
                    # age, so this costs ratings rather than the team.
                    self.status(
                        f"{team.name}: stats unavailable, ratings will use position defaults"
                    )
                # The eleven who started most of the team's last few matches pick
                # the starting places (`_select_best_15`); the season's starts
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
                # Deliberately broad, and `TransportLeak` is a `BaseException`
                # precisely so the network guard still escapes this.
                roster.error = f"Failed: {exc}"
                self.status(f"{team.name}: {roster.error}")
            rosters.append(roster)

        if on_progress is not None:
            on_progress(1.0, "Done!")
        return LeagueData(league=league, teams=rosters)

    def suggest_squad_order(self, team_roster):
        ordered = self.mapper._select_best_15(
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
        """Reduce league data to one `ISSTeamRecord` per mapped ROM slot.

        Sparse: a key exists only for a slot the caller named. Nothing reads a
        missing one, and an unmapped slot keeps its 1994 squad.
        """
        self.check_slot_mapping(slot_mapping)
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
            if entry.slot_index in teams:
                raise MappingError(
                    f"Slot {entry.slot_index} ({TEAM_ENUM_ORDER[entry.slot_index]}) "
                    f"is mapped more than once"
                )
            # The whole league, not just this team: percentiles are normalised
            # league-wide.
            record = self.mapper.map_team_with_league_context(
                roster, data.teams, order_as_given=order_as_given
            )
            self._apply_colours(record, roster.team)
            record.team_id = roster.team.id
            record.logo_url = roster.team.logo_url
            teams[entry.slot_index] = record
        return MappedRosters(game_id=self.game_id, teams=teams)

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        with as_rom_error(rom_path):
            return screens.export(Path(rom_path), Path(out_dir))

    def patch(
        self,
        *,
        rom_path: Path,
        output_path: Path,
        rosters: MappedRosters,
        on_progress: ProgressFn | None = None,
        **options: Any,
    ) -> PatchResult:
        rosters.require_game(self.game_id)
        # Checked before the ROM is touched: an unknown slot or an unreadable
        # picture never leaves a half-written output behind.
        pictures = screens.load_images(options.get("images"), self.status)
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")

        reader = ISSRomReader(str(rom_path))
        with as_rom_error(rom_path):
            # Called for the side effect: it sets `header_offset`, which every
            # offset the writer uses is measured from. Its 1 MB return value is a
            # heuristic and must not be enforced here -- only `data_fits` is
            # arithmetic, so only it may refuse a patch.
            reader.validate_rom()
            fits = reader.data_fits()
            image = Path(rom_path).read_bytes()[reader.header_offset :] if fits else b""
        if not fits:
            raise RomError(
                f"Too small to be an ISS ROM: {rom_path} holds "
                f"{Path(rom_path).stat().st_size} bytes and this patcher writes as far as "
                f"{MIN_PATCHABLE_SIZE} past any copier header"
            )

        # Re-checked: the keys may have crossed a JSON boundary since
        # `map_rosters` built them. Sorted, because `write_team_name_texts`
        # breaks a tie between two equally long names by whichever it met first.
        slots = sorted(slot for slot in rosters.teams if 0 <= slot < TOTAL_TEAMS)
        # Read and checked before the copy, so a bad kit picture is refused up
        # front; templates are read off the input, before any slot changes.
        given = options.get("kits", True)
        plans = None if given is False else self._kit_plans(image, rosters, slots, given)
        blank = bool(options.get("blank_unused", False))
        unused = [s for s in range(TOTAL_TEAMS) if s not in slots] if blank else []
        flags, fallbacks = self._flags(rosters, slots, logos=options.get("logos", True))

        patched_names: dict[int, str] = {}
        patched_tile_names: dict[int, str] = {}
        shirts: dict[int, Shirts] = {}
        pins: dict[int, Pins] = {}
        teams_patched = 0
        players_patched = 0

        with as_rom_error(rom_path):
            self.status("Preparing ROM...")
            # The constructor copies the input over the output and holds a
            # handle; `with` releases it when `write_name_tiles` raises.
            with ISSRomWriter(str(rom_path), str(output_path), reader.header_offset) as writer:
                for i, slot in enumerate(slots):
                    record: ISSTeamRecord = rosters.teams[slot]
                    if on_progress is not None:
                        on_progress(i / len(slots), f"Patching {record.name}...")

                    # Write order is load bearing: the name tiles leave bank $89
                    # before any flag is written there -- see
                    # `ISSRomWriter.write_name_tiles`.
                    writer.write_player_names(slot, record.players)
                    written = writer.write_player_data(slot, record.players)
                    # The shirt numbers (flag entry 12) are set against the
                    # shirts the team now wears; `None`: the cartridge's kit.
                    shirts[slot] = (None, None)
                    if plans is not None:
                        home, away = plans[slot]
                        for kit, plan in enumerate((home, away)):
                            if plan is not None:
                                writer.write_kit_block(slot, kit, plan.block)
                        shirts[slot] = (
                            home.shades if home is not None else None,
                            away.shades if away is not None else None,
                        )
                        if record.kit_gk:
                            writer.write_gk_kit(slot, record.kit_gk)
                        # The chest design and the clash byte belong to the
                        # team, so they follow the home kit; without one the
                        # chest is blank, never the old nation's design.
                        if home is None:
                            writer.write_chest(slot, design_pointers(BLANK_DESIGN))
                        else:
                            writer.write_chest(slot, home.chest)
                            writer.write_clash(slot, home.clash)
                            pins[slot] = home.pins

                    patched_names[slot] = record.name
                    patched_tile_names[slot] = record.short_name
                    # Unconditional: the name, the kit and the description land
                    # whether or not a squad was supplied.
                    teams_patched += 1
                    # `written`, not `len(record.players)`: the writer stops at
                    # 15 and a longer squad leaves the rest on the floor.
                    players_patched += written

                for slot in unused:
                    writer.write_player_names(slot, _BLANK_SQUAD)
                    patched_names[slot] = patched_tile_names[slot] = ""
                if unused:
                    self.status(f"Unused slots blanked: {len(unused)}")

                if on_progress is not None:
                    on_progress(0.80, "Writing team names...")
                writer.write_team_name_texts(patched_names)
                writer.write_team_descriptions(patched_names)

                if on_progress is not None:
                    on_progress(0.85, "Writing in-game name tiles...")
                writer.write_name_tiles(patched_tile_names)

                if on_progress is not None:
                    on_progress(0.90, "Writing flags...")
                notes = writer.write_flags(flags, fallbacks, shirts, unused, pins)
                for slot, note in notes.items():
                    self.status(f"{rosters.teams[slot].name}: crest {note}")
                drawn = sum("did not fit" not in notes.get(slot, "") for slot in fallbacks)
                if drawn:
                    self.status(f"Team crests drawn: {drawn}")

                if on_progress is not None:
                    on_progress(0.95, "Finalizing...")
                self.status("Saving patched ROM...")
                writer.finalize()

            if not Path(output_path).exists():
                raise RomError(f"Failed to write patched ROM to {output_path}")
            if pictures:
                self.status("Drawing screen art...")
            # Also corrects the header checksum, pictures or not.
            screens.write_file(Path(output_path), pictures, self.status)

        if on_progress is not None:
            on_progress(1.0, f"Done! Saved to {output_path}")
        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    @staticmethod
    def _kit_plans(
        image: bytes, rosters: MappedRosters, slots: list[int], given: Any
    ) -> dict[int, tuple[kits.Kit | None, kits.Kit | None]]:
        """Every mapped slot's home and away kits, read and checked.

        `given` is the `kits` option: anything but a dict draws each record's
        default (`_default_kit`); a dict `{slot: {"home": KIT, "away": KIT}}`
        overrides a side, KIT as `kits.parse` reads it. `None` for a side with
        neither keeps the cartridge's kit. A choice that cannot be read is a
        `CapabilityError` naming the slot.
        """
        overrides = given if isinstance(given, dict) else {}
        plans: dict[int, tuple[kits.Kit | None, kits.Kit | None]] = {}
        for slot in slots:
            record: ISSTeamRecord = rosters.teams[slot]
            chosen = overrides.get(slot) or overrides.get(str(slot)) or {}
            sides: list[kits.Kit | None] = []
            for kit, side in enumerate(("home", "away")):
                try:
                    value = chosen.get(side)
                    choice = kits.parse(value) if value is not None else _default_kit(record, side)
                    sides.append(None if choice is None else kits.resolve(image, choice, kit))
                except (
                    CapabilityError,
                    ValueError,
                    TypeError,
                    KeyError,
                    OSError,
                    ImportError,
                ) as exc:
                    raise CapabilityError(f"slot {slot} {side} kit: {exc}") from exc
            plans[slot] = (sides[0], sides[1])
        return plans

    @staticmethod
    def _cartridge(rom_path: Path) -> bytes:
        """The whole cartridge image at `rom_path`, past any copier header."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = ISSRomReader(str(rom_path))
        with as_rom_error(rom_path):
            if not reader.validate_rom():
                raise RomError(f"Not an 8 Mbit ISS cartridge image: {rom_path}")
            return Path(rom_path).read_bytes()[reader.header_offset :]

    def kit_templates(self, rom_path: Path) -> list[KitTemplate]:
        """The 26 national teams' kits on the cartridge, numbered by slot
        (`TEAM_ENUM_ORDER`), each with a preview of its home kit
        (`kit_preview_png`). `{"template": n}` copies kit n's colours and its
        chest design, the retail cartridge's even on an image patched before.
        Needs Pillow; `CapabilityError` without it."""
        image = kits.retail(self._cartridge(rom_path))
        try:
            return [
                KitTemplate(n, TEAM_ENUM_ORDER[n], kits.preview_png(image, n, 0))
                for n in range(kits.TEMPLATES)
            ]
        except ImportError as exc:
            raise CapabilityError("Kit templates need Pillow (the images extra)") from exc

    def kit_preview_png(
        self,
        rom_path: Path,
        slot: int,
        which: str = "home",
        choice: Any = None,
        home: Any = None,
    ) -> bytes:
        """A 96x144 RGBA PNG of the Team Colors player in a slot's kit, drawn
        from the cartridge's own tiles (checked against Mesen 2 pixel for
        pixel). `slot` is the slot itself, 0-26; `which` "home" or "away";
        `choice` one KIT of the `kits` option, None for the kit as the image
        holds it. The chest design belongs to the team and follows the home
        kit, so an away preview draws `home`'s (the team's home KIT), or the
        one the image holds without it. A choice's chest design is drawn in
        the colours the patch asks of the crest; in the game it takes the
        nearest the crest has. Nothing is written. Needs Pillow;
        `CapabilityError` without it."""
        if not 0 <= slot < TOTAL_TEAMS:
            raise CapabilityError(f"Unknown kit slot {slot}; iss-snes has 0..{TOTAL_TEAMS - 1}")
        if which not in ("home", "away"):
            raise CapabilityError(f"Unknown kit {which!r}; one of home, away")
        image = self._cartridge(rom_path)
        kit = ("home", "away").index(which)
        try:
            plan = None if choice is None else kits.resolve(image, kits.parse(choice), kit)
            design = None if home is None else kits.resolve(image, kits.parse(home), 0)
            return kits.preview_png(image, slot, kit, plan, design)
        except ImportError as exc:
            raise CapabilityError("Kit previews need Pillow (the images extra)") from exc
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise CapabilityError(f"slot {slot} {which} kit: {exc}") from exc

    def _flags(
        self, rosters: MappedRosters, slots: list[int], *, logos: bool
    ) -> tuple[dict[int, Flag], dict[int, Flag | None]]:
        """Each slot's flag, and for a crest what it falls back to.

        A crest comes from `assets_dir/logos/<team_id>.png`, else the provider's
        logo (downloaded once into `cache_dir/logos`). A team without one, or
        with one that will not load, gets the two-band flag in its colours; a
        team with neither keeps the cartridge's flag. Without Pillow no crest
        is drawn, with one status line.
        """
        flags: dict[int, Flag] = {}
        fallbacks: dict[int, Flag | None] = {}
        for slot in slots:
            record: ISSTeamRecord = rosters.teams[slot]
            band = two_band_flag(*record.flag_colors[:2]) if record.flag_colors else None
            crest = None
            if logos:
                try:
                    crest = self._crest(record)
                except ImportError:
                    self.status("Pillow is not installed; team crests skipped")
                    logos = False
            if crest is not None:
                flags[slot] = crest
                fallbacks[slot] = band
            elif band is not None:
                flags[slot] = band
        return flags, fallbacks

    def _crest(self, record: ISSTeamRecord) -> Flag | None:
        """`record`'s crest as a flag, or `None` (with a status line if it failed).

        Raises `ImportError` without Pillow.
        """
        override = self._logo_override(record)
        if override is None and not record.logo_url:
            return None
        from .logo import logo_flag

        try:
            png = override.read_bytes() if override else self._load_logo(record.logo_url)
            return logo_flag(png)
        except ImportError:
            raise
        except Exception as exc:  # a bad download or an unreadable image
            self.status(f"{record.name}: crest skipped: {exc}")
            return None

    def _logo_override(self, record: ISSTeamRecord) -> Path | None:
        if self.assets_dir is None or record.team_id is None:
            return None
        candidate = self.assets_dir / "logos" / f"{record.team_id}.png"
        return candidate if candidate.is_file() else None

    def _load_logo(self, url: str) -> bytes:
        """The logo bytes, from `cache_dir/logos` or downloaded once."""
        cache = self.cache_dir / "logos" / f"{hashlib.sha1(url.encode()).hexdigest()}.png"
        if cache.is_file():
            return cache.read_bytes()
        tx = self._transport or _http.default_transport
        try:
            content = tx(url, {}, 15.0)
        except Exception as exc:
            raise ApiError(f"GET {url} failed: {exc}") from exc
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
        return content

    def default_slot_mapping(self, data: LeagueData) -> list[SlotMapping]:
        """Sequential mapping: team 0 to slot 0, team 1 to slot 1, and so on.

        Teams beyond the 27th are dropped. This is a starting point a caller
        edits, never a substitute for an explicit mapping.
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
        """Put the team's default kits and the provider's colours on the record.

        They must live on the record, not beside it: `MappedRosters` is what
        crosses from `map_rosters` to `patch`, and a `Team` does not travel with
        it. The kits are `default_kits`' (the kit catalogue's for a club it
        knows, else plain kits in the provider's two colours); the two-band
        flag is drawn in the provider's colours.
        """
        record.kit_home_spec, record.kit_away_spec = default_kits(team)
        for side, spec in (("home", record.kit_home_spec), ("away", record.kit_away_spec)):
            if spec is not None:
                setattr(record, f"kit_{side}", (spec.shirt, spec.shorts, spec.socks))
        record.kit_gk = GK_KIT
        primary = _parse_hex_colour(team.color)
        alternate = _parse_hex_colour(team.alternate_color)
        if primary is not None:
            record.flag_colors = [primary, alternate if alternate is not None else primary]


def _default_kit(record: ISSTeamRecord, side: str) -> KitSpec | None:
    """`record`'s kit for `side` when the `kits` option names none: its spec,
    else a plain kit in its colours (shirt, shorts, socks), else None."""
    spec: KitSpec | None = getattr(record, f"kit_{side}_spec")
    colours: tuple[tuple[int, int, int], ...] = getattr(record, f"kit_{side}")
    if spec is None and colours:
        shirt = colours[0]
        shorts = colours[1] if len(colours) > 1 else shirt
        socks = colours[2] if len(colours) > 2 else shirt
        spec = KitSpec(shirt=shirt, secondary=shirt, pattern="plain", shorts=shorts, socks=socks)
    return spec
