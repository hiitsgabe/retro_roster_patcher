"""International Superstar Soccer 2000 (N64, USA) on the Patcher interface.

The 100 national-team slots take club teams, so the slot mapping is explicit,
as in `iss-snes`. A mapped slot gets the club's 22 (its starting XI in the
cartridge's formation nearest its own, the lineup's folded shape unless edited,
then the bench), each player's name, number, position, foot, height, build and
ratings (`mapper`), his look from his head shot (`looks`), the club's name on
every menu (`names`) and its crest as the flag (`flags`), which the team select,
the lineup screen, the VS banner, the flags in the stands and the match
scoreboard all draw.

With the nation fill (`fill_slots`), today's national sides take the slots a
league leaves. A nation the cartridge has wears the cartridge's flag and kits
(`retail`, packaged, so an earlier patch's image gives them back too) and, on
its own slot, the cartridge's name; one written elsewhere gets its own name, and
one the cartridge lacks ESPN's flag and colours. The scenarios follow their
nations, or play patched teams once a nation is gone (`scenarios`).
"""

from __future__ import annotations

import array
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from ...core.errors import CapabilityError, MappingError, RomError, as_rom_error
from ...core.headshots import measure_faces
from ...core.kits import KitSpec, default_kits, kit_from_image
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
from ...sports.models import LeagueData
from ...sports.nationals import cartridge_slot, cartridge_slot_map
from ..iss_snes.patcher import ISSPatcher
from ..iss_snes.scenarios import repoint
from . import flags, kits, looks, names, retail, scenarios, screens
from . import rom as R
from . import writer as W
from .mapper import FORMATION_DONORS, N64StatMapper, N64Team

#: Team-id order, as the menus spell the names.
TEAM_NAMES = (
    "Germany|France|Italy|Spain|Holland|Switzerland|Austria|Denmark|Belgium|Portugal|"
    "Luxembourg|San Marino|Andorra|Liechtenstein|England|Scotland|Wales|N.Ireland|Ireland|"
    "Sweden|Norway|Finland|Iceland|Faroe Islands|Yugoslavia|Croatia|Romania|Bulgaria|Greece|"
    "Poland|Czech Rep.|Slovakia|Hungary|Slovenia|Bosnia|Albania|Macedonia|Russia|Turkey|"
    "Estonia|Latvia|Lithuania|Belarus|Ukraine|Moldova|Georgia|Armenia|Azerbaijan|Malta|Cyprus|"
    "Israel|Cameroon|Nigeria|S.Africa|Tunisia|Morocco|Egypt|Liberia|Algeria|Mali|Ghana|U.S.A.|"
    "Canada|Mexico|Jamaica|Cuba|Honduras|Costa Rica|Trinidad&Tob.|Brazil|Argentina|Colombia|"
    "Uruguay|Paraguay|Bolivia|Chile|Peru|Venezuela|Ecuador|Japan|S.Korea|N.Korea|China|"
    "Hong Kong|Thailand|Malaysia|Saudi Arabia|U.A.E.|Iran|Uzbekistan|Kazakhstan|Nepal|India|"
    "Iraq|Kuwait|Qatar|Australia|New Zealand|Papua NG|Fiji"
).split("|")


#: The cartridge's spelling -> ESPN's (league 2021, international friendlies).
#: ESPN's international friendlies, the league national squads come from.
NATIONAL_LEAGUE = 2021

#: A defunct or renamed cartridge nation is no nation's own slot, so it has no alias:
#: "Yugoslavia" is not Serbia (another state, another flag) and "Macedonia" is now North
#: Macedonia. Their successors are placed as moved nations, with their own name and flag.
#: Czechia and Turkiye keep their slots: the same state and flag, a shorter name.
NATIONAL_ALIASES = {
    "Holland": "Netherlands",
    "N.Ireland": "Northern Ireland",
    "Ireland": "Republic of Ireland",
    "Czech Rep.": "Czechia",
    "Bosnia": "Bosnia-Herzegovina",
    "Turkey": "Turkiye",
    "S.Africa": "South Africa",
    "U.S.A.": "United States",
    "Trinidad&Tob.": "Trinidad and Tobago",
    "S.Korea": "South Korea",
    "N.Korea": "North Korea",
    "U.A.E.": "United Arab Emirates",
    "Papua NG": "Papua New Guinea",
}


#: The confederation of each region of team select (`ISS2000Patcher.slot_groups`).
REGION_CONFEDERATIONS = {
    "Europe": "UEFA",
    "Africa": "CAF",
    "CONCACAF": "CONCACAF",
    "South America": "CONMEBOL",
    "Asia": "AFC",
    "Oceania": "OFC",
}


@register(
    "iss2000-n64",
    platform="n64",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class ISS2000Patcher(Patcher):
    """100 national-team slots of 22 players."""

    supports_logos: bool = True
    team_slots: int | None = R.NATIONAL_TEAMS
    supports_blank_unused: bool = False
    supports_looks: bool = True
    #: `patch(kits=...)` as `Patcher` describes it, colours only: a kit's
    #: shirt, shorts and socks recolour the slot's own kit (`kits`); its
    #: pattern stays the slot's design.
    supports_kits: bool = True
    #: The national slots are the 100 slots themselves; the app fills those
    #: no club takes with today's national squads (`national_slot_map`).
    supports_nationals: bool = True
    national_slot_base: int | None = 0
    #: `fill_slots`: the 100 nations; the all-star sides 100-105 are not on team select.
    supports_nation_fill: bool = True
    #: Each team's formation row is copied from a cartridge team of its shape: one
    #: donor per key of `mapper.FORMATION_DONORS`.
    writes_formation: bool = True
    formations: tuple[str, ...] = tuple(f"{d}-{m}-{f}" for d, m, f in FORMATION_DONORS)
    slot_groups = (
        ("Europe", 0, 51),
        ("Africa", 51, 10),
        ("CONCACAF", 61, 8),
        ("South America", 69, 10),
        ("Asia", 79, 17),
        ("Oceania", 96, 4),
    )

    #: `patch(images={slot: png})`: "menu", the wallpaper behind every menu, and
    #: "title", the PRESS START screen (`screens`); `export_images` writes the
    #: cartridge's.
    image_slots: tuple[str, ...] = screens.SLOTS

    fetch = ISSPatcher.fetch

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
        super().__init__(cache_dir, provider=provider, on_status=on_status, on_partial=on_partial)
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._transport = transport
        self.mapper = N64StatMapper()
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # --- ROM -------------------------------------------------------------------

    @staticmethod
    def _image(rom_path: Path) -> bytes:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        data = Path(rom_path).read_bytes()
        if data[:4] == b"\x37\x80\x40\x12":  # .v64: each 16-bit word swapped
            data = bytes(b for pair in zip(data[1::2], data[0::2], strict=True) for b in pair)
        elif data[:4] == b"\x40\x12\x37\x80":  # .n64: each 32-bit word reversed
            words = array.array("I", data)
            words.byteswap()
            data = words.tobytes()
        if not R.is_iss2000(data):
            raise RomError(f"Not an ISS 2000 (USA) cartridge image: {rom_path}")
        return data

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        with as_rom_error(rom_path):
            try:
                image = self._image(rom_path)
                valid = True
            except RomError:
                image, valid = b"", False
        slots = []
        if valid:
            for t, name in enumerate(TEAM_NAMES):
                first = R.decode_name(image[R.record_offset(t, 0) + R.NAME :][: R.NAME_LEN])
                slots.append(
                    RomSlot(index=t, current_name=f"First player: {first}", display_name=name)
                )
        return RomInfo(
            path=str(rom_path),
            size=Path(rom_path).stat().st_size if Path(rom_path).exists() else 0,
            game_id=self.game_id,
            is_valid=valid,
            slots=slots,
        )

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        with as_rom_error(rom_path):
            return screens.export(self._image(rom_path), Path(out_dir))

    def national_slot_map(self, rom_path: Path, data: LeagueData) -> list[SlotMapping]:
        """Pair the cartridge's 100 nations with `data`'s national teams by
        name (`NATIONAL_ALIASES` for the cartridge's spellings)."""
        return cartridge_slot_map(TEAM_NAMES, NATIONAL_ALIASES, data)

    def fill_slots(self, rom_path: Path) -> list[int]:
        return list(range(R.NATIONAL_TEAMS))

    def fill_regions(self, rom_path: Path) -> dict[int, str]:
        """Each slot's confederation, by its region (`slot_groups`): a moved nation goes
        under its own region's label first."""
        return {
            slot: REGION_CONFEDERATIONS[name]
            for name, first, count in self.slot_groups
            for slot in range(first, first + count)
        }

    # --- mapping ---------------------------------------------------------------

    def suggest_squad_order(self, team_roster):
        squad, _ = self.mapper.select_22(team_roster)
        return self._append_unused(squad, team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        by_id = {r.team.id: r for r in data.teams}
        pcts = self.mapper.league_percentiles(data.teams)
        teams: dict[int, N64Team] = {}
        for entry in slot_mapping or []:
            if not 0 <= entry.slot_index < R.NATIONAL_TEAMS:
                raise MappingError(f"Slot {entry.slot_index} is outside 0..{R.NATIONAL_TEAMS - 1}")
            if entry.slot_index in teams:
                raise MappingError(f"Slot {entry.slot_index} is mapped more than once")
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    "which is not in the fetched league data"
                )
            national = data.league.id == NATIONAL_LEAGUE
            if national and not roster.players:
                self.status(f"Skipped {roster.team.name}: ESPN has no squad for it")
                continue
            team = self.mapper.map_team(roster, pcts, order_as_given=order_as_given)
            team.national = national
            team.nation = (
                cartridge_slot(TEAM_NAMES, NATIONAL_ALIASES, roster.team) if national else None
            )
            team.league = "" if national else data.league.name
            team.kit_home, team.kit_away = default_kits(roster.team)
            teams[entry.slot_index] = team
        return MappedRosters(game_id=self.game_id, teams=teams)

    # --- patch -----------------------------------------------------------------

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
        given = options.get("kits", True)
        kit_plan = None if given is False else self._kit_plan(rosters, given)
        pictures = {}
        for slot, value in (options.get("images") or {}).items():
            if slot not in screens.SLOTS:
                raise CapabilityError(
                    f"iss2000-n64 has no screen art slot {slot!r}; it has {screens.SLOTS}"
                )
            pictures[slot] = value if isinstance(value, bytes) else Path(value).read_bytes()
        with as_rom_error(rom_path):
            image = bytearray(self._image(rom_path))
        slots = sorted(s for s in rosters.teams if 0 <= int(s) < R.NATIONAL_TEAMS)
        teams: dict[int, N64Team] = {int(s): rosters.teams[s] for s in slots}
        if options.get("looks", True):
            teams = self._dress(teams)

        # Read before anything is written: the slots that still show a nation's cartridge flag.
        flag_at = _flags_shown(image)
        present = _present(image)
        nations = {s: t.nation for s, t in teams.items() if t.nation is not None}
        own = {s for s, n in nations.items() if s == n}
        # A nation the cartridge has wears its cartridge kits and flag: the flag stays where
        # the slot shows it, else it is drawn back from the packaged one.
        kept = {s for s, n in nations.items() if flag_at.get(s) == n}
        records = {s: (retail.kit(n, 0), retail.kit(n, 1)) for s, n in nations.items()}
        if kit_plan is not None:
            kit_plan = self._kit_plan(rosters, given, set(records))

        space = R.AssetSpace(image)
        players = 0
        for i, (slot, team) in enumerate(sorted(teams.items())):
            if on_progress is not None:
                on_progress(0.1 + 0.5 * i / max(len(teams), 1), f"Patching {team.name}...")
            players += W.write_team(image, slot, team)

        self.status("Writing team names...")
        cells: dict[int, str | bytes] = {s: _menu_name(t) for s, t in teams.items()}
        cells.update({s: retail.name(s) for s in own})
        names.write(image, space, cells)

        names.write_regions(image, space, self._region_labels(teams))
        W.write_start_page(image, sorted(s for s, t in teams.items() if not t.national))

        # A nation the cartridge has always wears its cartridge flag; `logos` only decides
        # whether clubs and the nations it lacks get theirs drawn.
        logos = options.get("logos", True)
        self.status("Drawing crests..." if logos else "Drawing flags...")
        crests = {}
        # Clubs first, then the cartridge's flags, then ESPN's: a flag left without a texture
        # of its own is drawn in the generic stripes, which suit a country's flag better than
        # a crest.
        for slot, team in sorted(
            teams.items(), key=lambda st: (st[1].national, st[1].nation is None)
        ):
            if slot in kept:
                continue
            if slot in nations:
                crests[slot] = retail.flag(nations[slot])
            elif logos and (png := self._logo(team)) is not None:
                crests[slot] = flags.draw(png, (255, 255, 255))
        if crests:
            drawn = [s for s in crests if teams[s].national and s not in nations]
            # Without logos the clubs keep the flags they have, textures included.
            patched = set(teams) - kept if logos else set(crests)
            flags.write(image, space, crests, patched, kept, drawn)
        self.status(f"Team crests drawn: {len(crests)}")

        if kit_plan or records:
            self.status("Colouring kits...")
            kits.write(image, space, kit_plan or {}, records)

        self._scenarios(image, teams, present, own)

        if pictures:
            self.status("Drawing screen art...")
            screens.write(image, space, pictures)

        R.fix_crc(image)
        Path(output_path).write_bytes(bytes(image))
        if len(image) > R.ROM_SIZE:
            self.status(
                f"ROM grown to {len(image) // 0x100000} MiB: some emulators and flash carts "
                "read only 16 MiB"
            )
        if on_progress is not None:
            on_progress(1.0, f"Done! Saved to {output_path}")
        return PatchResult(str(output_path), teams_patched=len(teams), players_patched=players)

    @staticmethod
    def _kit_plan(
        rosters: MappedRosters, given: Any, looked: set[int] | frozenset[int] = frozenset()
    ) -> dict[int, tuple[KitSpec | None, KitSpec | None]]:
        """(first, second) kit per mapped slot: `given[slot]` sides when the caller
        sets them, else the team's default kits; a slot in `looked` (a nation in its
        cartridge kits) takes only the sides given."""
        overrides = given if isinstance(given, dict) else {}
        plan: dict[int, tuple[KitSpec | None, KitSpec | None]] = {}
        for slot, team in rosters.teams.items():
            slot = int(slot)
            chosen = overrides.get(slot) or overrides.get(str(slot)) or {}
            if slot in looked and not chosen:
                continue
            sides: list[KitSpec | None] = []
            for side, default in (("home", team.kit_home), ("away", team.kit_away)):
                value = chosen.get(side)
                try:
                    sides.append(
                        _kit_spec(value)
                        if value is not None
                        else None
                        if slot in looked
                        else default
                    )
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    raise CapabilityError(f"slot {slot} {side} kit: {exc}") from exc
            plan[slot] = (sides[0], sides[1])
        return plan

    def _region_labels(self, teams: dict[int, N64Team]) -> dict[str, str | None]:
        """Each region whose every slot is a mapped club is named after their league (the
        one most of them play in). Its label is one picture for all its pages, so any
        other region shows the cartridge's (None)."""
        labels: dict[str, str | None] = {}
        for region, first, count in self.slot_groups:
            here = [teams[s].league if s in teams else "" for s in range(first, first + count)]
            labels[region] = _label(Counter(here).most_common(1)[0][0]) if all(here) else None
        return labels

    def _scenarios(
        self, image: bytearray, teams: dict[int, N64Team], present: dict[int, int], own: set[int]
    ) -> None:
        """Point each scenario at where its nations live now: a nation written into a slot,
        else a slot this patch left that still shows its cartridge flag or kits (`present`).
        A scenario that lost a nation plays the patched teams `repoint` picks."""
        at = {t.nation: s for s, t in sorted(teams.items()) if t.nation is not None}
        for slot, nation in present.items():
            if slot not in teams:
                at.setdefault(nation, slot)
        kept = {s for s in at.values() if s not in teams}
        wanted = [(at.get(a, -1), at.get(b, -1)) for a, b in scenarios.RETAIL]
        # A lost nation is a "blank" slot: its scenario never keeps it, even once every pair
        # of patched teams is played.
        moved = repoint(wanted, set(teams), blank={-1}, kept=kept)
        pairs = [
            old if -1 in pair else pair for pair, old in zip(moved, scenarios.RETAIL, strict=True)
        ]
        label = {
            s: (TEAM_NAMES[s] if s in own else _menu_name(t), t.code or _menu_name(t)[:3].upper())
            for s, t in teams.items()
        }
        for s in kept:
            name = TEAM_NAMES[present[s]]
            label[s] = (name, name[:3].upper())
        scenarios.write(image, pairs, label)

    def _dress(self, teams: dict[int, N64Team]) -> dict[int, N64Team]:
        urls = {
            p.player_id: p.photo_url
            for t in teams.values()
            for p in t.players
            if p.player_id and p.photo_url
        }
        faces = measure_faces(self.cache_dir, urls, self._transport, self.status)
        dressed = deepcopy(teams)
        for team in dressed.values():
            looks.dress(team.players, faces)
        return dressed

    def _logo(self, team: N64Team) -> bytes | None:
        if self.assets_dir is not None:
            own = self.assets_dir / "logos" / f"{team.team_id}.png"
            if own.is_file():
                return own.read_bytes()
        if not team.logo_url:
            return None
        try:
            return (self._transport or _http.default_transport)(team.logo_url, {}, 15.0)
        except Exception:  # noqa: BLE001 - a missing crest keeps the flag
            self.status(f"{team.name}: crest could not be downloaded")
            return None


def _prints(image: bytes | bytearray) -> list[tuple[str, str]]:
    """Each of the 100 nations' slots' (flag, kits) fingerprint, as `retail` takes them
    ("" for a flag entry that is not one)."""
    assets = R.assets(image)
    wanted = {asset for asset, _, _, _ in flags._BUNDLES} | {flags.PALETTE_ASSET}
    data = {k: R.read_asset(image, assets[k]) for k in wanted}
    stream = b"".join(R.read_asset(image, assets[k]) for k in kits.PACKS)
    out = []
    for team in range(R.NATIONAL_TEAMS):
        recs = [stream[kits._record(team, side) :][: kits.SIZE] for side in (0, 1)]
        tex, block, idx = flags.entry(image, team)
        try:
            asset, off = flags.texture_place(tex)
        except ValueError:
            out.append(("", retail.fingerprint(*recs)))
            continue
        p = flags.palette_place(block, idx)
        flag = (data[asset][off : off + flags.TEX_SIZE], data[flags.PALETTE_ASSET][p : p + 32])
        out.append((retail.fingerprint(*flag), retail.fingerprint(*recs)))
    return out


def _flags_shown(image: bytes | bytearray) -> dict[int, int]:
    """{slot: nation} for each slot that shows a nation's cartridge flag."""
    known = retail.flag_prints()
    return {s: known[f] for s, (f, _) in enumerate(_prints(image)) if f in known}


def _present(image: bytes | bytearray) -> dict[int, int]:
    """{slot: nation} for each slot that shows a nation's cartridge flag or kits."""
    flags_, kits_ = retail.flag_prints(), retail.kit_prints()
    out = {}
    for s, (f, k) in enumerate(_prints(image)):
        if k in kits_:
            out[s] = kits_[k]
        if f in flags_:
            out[s] = flags_[f]
    return out


def _menu_name(team: N64Team) -> str:
    """The club's full name when it is short enough to read on a 64-pixel
    label, else ESPN's short name (which ESPN itself cuts at 12 letters)."""
    if len(team.name) <= 13 or not team.short_name:
        return team.name
    return team.short_name


def _label(league: str) -> str:
    """The league's name as far as whole words fit the 12 letters team
    select's header shows: "Brasileirao Serie A" reads BRASILEIRAO."""
    words = league.split()
    out = words[0] if words else league
    for word in words[1:]:
        if len(out) + 1 + len(word) > 12:
            break
        out += " " + word
    return out[:12]


def _kit_spec(value: Any) -> KitSpec | None:
    """A kit as `Patcher` describes the `kits` option: a KitSpec, its dict,
    or {"image": png bytes or path} read with `kit_from_image`."""
    if isinstance(value, KitSpec):
        return value
    if isinstance(value, dict) and "image" in value:
        png = value["image"]
        return kit_from_image(png if isinstance(png, bytes) else Path(png).read_bytes())
    if isinstance(value, dict) and "template" in value:
        raise ValueError("iss2000-n64 has no kit templates")
    return KitSpec.from_dict(value)
