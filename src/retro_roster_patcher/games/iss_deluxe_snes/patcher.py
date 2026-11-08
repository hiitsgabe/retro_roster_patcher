"""International Superstar Soccer Deluxe (SNES, USA) on the Patcher interface.

The 36 national-team slots take clubs, so the slot mapping is explicit, as in
`iss-snes`. A mapped slot gets the club's 20: the eleven as `iss-snes` and
`iss2000-n64` pick them, then the bench; each player's name and nine skills,
placed by league percentile onto the cartridge's own spread for his position;
his position code from his role; each team's looks from its players' head
shots (`looks`). Main menu games start with the first two mapped clubs instead
of Brazil v England.

A club with a formation (`extra["formation"]`, a shape such as "4-3-3", else its
lineup's shape: `team_formation`) is written the cartridge's formation nearest it
(`formations`): the team's record becomes that formation's default and the ten
outfielders, in the order the mapper gave them (its lines filled for that
formation; with `order_as_given`, the team sheet's), are cut into its lines by
index; the cut also gives each of the eleven the group whose spread rates his
skills. Without a formation the slot keeps its own record and the players go to
the lines of their groups. Either way a place is the record's index and its line
comes from the record's flags (never the skill record's byte 5, the shirt number),
and each line seats its players on its places by role: the sides across the pitch,
the deeper role deeper. A player without a role (a roster fetched before roles)
has the one his lineup code reads as.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.errors import CapabilityError, MappingError, RomError, as_rom_error
from ...core.headshots import measure_faces
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
from ...formats.snes import fix_checksum
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.lineup import lineup_role, nearest_shape, parse_shape, seat_line, team_formation
from ...sports.models import LeagueData, Player, TeamRoster
from ...sports.nationals import cartridge_slot, cartridge_slot_map
from ..iss2000_n64.mapper import GROUPS, N64StatMapper, _place, group_of, parse_formation
from ..iss2000_n64.patcher import NATIONAL_ALIASES, NATIONAL_LEAGUE
from ..iss_snes.models import Flag, ISSLook
from ..iss_snes.patcher import ISSPatcher, _parse_hex_colour
from ..iss_snes.rom_writer import two_band_flag
from ..iss_snes.stat_mapper import ISSStatMapper
from . import bignames, flags, kits, looks, names, pages, screens
from . import rom as R

_Q = tuple[int, int, int, int, int]
#: Cartridge spread (2nd, 10th, 50th, 90th, 98th percentile of the stored
#: value) per position group, measured on the 720 USA players.
RETAIL: dict[str, dict[str, _Q]] = {
    "GK": dict(speed=(4, 5, 6, 7, 8), dash=(4, 5, 6, 7, 8), curl=(1, 2, 3, 4, 6),
               shot=(4, 4, 5, 6, 6), intelligence=(2, 3, 4, 5, 6), balance=(4, 5, 6, 8, 8),
               jump=(4, 5, 6, 7, 8), dribble=(3, 3, 4, 6, 7), stamina=(4, 5, 6, 8, 8)),
    "DF": dict(speed=(4, 5, 6, 8, 8), dash=(4, 4, 5, 7, 8), curl=(2, 3, 4, 6, 7),
               shot=(5, 5, 6, 7, 7), intelligence=(3, 4, 5, 7, 7), balance=(4, 5, 6, 8, 8),
               jump=(4, 5, 6, 8, 9), dribble=(3, 4, 5, 7, 8), stamina=(4, 5, 6, 8, 8)),
    "MF": dict(speed=(5, 5, 7, 8, 8), dash=(5, 5, 6, 8, 8), curl=(4, 5, 6, 8, 8),
               shot=(4, 4, 5, 6, 7), intelligence=(4, 5, 6, 8, 8), balance=(4, 5, 6, 8, 8),
               jump=(4, 4, 6, 8, 8), dribble=(4, 5, 6, 8, 8), stamina=(4, 5, 7, 8, 8)),
    "FW": dict(speed=(4, 5, 7, 8, 8), dash=(4, 5, 6, 8, 9), curl=(4, 5, 6, 7, 8),
               shot=(4, 4, 5, 6, 6), intelligence=(4, 5, 6, 8, 8), balance=(3, 5, 6, 8, 8),
               jump=(3, 4, 6, 7, 9), dribble=(4, 5, 6, 8, 8), stamina=(4, 5, 7, 8, 9)),
}  # fmt: skip
#: Which league percentile (`N64StatMapper.league_percentiles`) feeds a skill.
FEED = dict(
    speed="speed", dash="dash", curl="curve", shot="shoot", intelligence="passing",
    balance="defence", jump="jump", dribble="keep", stamina="stamina",
)  # fmt: skip
_CODE = {"GK": R.GK, "DF": R.CB, "MF": R.MF, "FW": R.FW}
#: A role's position code (`rom` says what each plays): the back line's players play defence, the
#: wing backs and the holding midfielder defence or midfield, the attacking midfielder and the
#: wingers midfield or attack.
ROLE_CODE = {
    "GK": R.GK, "CB": R.CB, "RB": R.CB, "LB": R.CB, "RWB": R.FB, "LWB": R.FB, "CDM": R.FB,
    "CM": R.MF, "RM": R.MF, "LM": R.MF, "CAM": R.AM, "RW": R.AM, "LW": R.AM, "ST": R.FW,
}  # fmt: skip
#: The code that plays only the line (0 keeper, 1 defence, 2 midfield, 3 attack).
_OWN_CODE = (R.GK, R.CB, R.MF, R.FW)
#: The widest lateral offset of any place of the cartridge's 43 team records, which `_spot`
#: divides by: the default formations' flanks are at 32 and a few retail records go out to 40.
_TOUCHLINE = 40


@dataclass
class DeluxePlayer:
    name: str
    group: str
    skills: dict[str, int] = field(default_factory=dict)
    tag: str = ""  # his ESPN lineup position, when he started
    #: The provider's id and head shot, and the nationality, for `looks`.
    player_id: int = 0
    photo_url: str = ""
    nationality: str = ""
    #: Byte 6 bit 4, the team's alternate look; None keeps the cartridge's.
    alternate: bool | None = None
    #: His `Player.role`, else the one his lineup `tag` reads as; "" takes the position code of
    #: his group.
    role: str = ""


@dataclass
class DeluxeTeam:
    name: str
    players: list[DeluxePlayer] = field(default_factory=list)  # the XI by line, then the bench
    kit_home: KitSpec | None = None
    kit_away: KitSpec | None = None
    team_id: int | None = None
    logo_url: str = ""
    colours: tuple[str, str] = ("", "")  # the provider's primary and alternate, hex
    label: str = ""  # the scoreboard name (`names.fit`)
    full_name: str = ""
    league: str = ""
    national: bool = False  # a nation on its own slot: it keeps its name, flag and kits
    #: A national team's own slot on the cartridge, whose retail flag and kits it wears on
    #: another slot; None for a club or a nation the cartridge lacks.
    nation: int | None = None
    #: Set by `looks.dress`; None leaves the cartridge's looks alone.
    look: ISSLook | None = None
    #: The club's formation (`team_formation`: `extra["formation"]`, else its lineup's shape,
    #: "4-3-3"); "" keeps the slot's own.
    formation: str = ""


@register(
    "iss-deluxe-snes",
    platform="snes",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class ISSDeluxePatcher(Patcher):
    """36 national-team slots of 20 players."""

    #: `patch(kits=...)` as `Patcher` describes it: colours, the pattern as
    #: the nearest of the cartridge's chest designs, or `{"template": n}`
    #: (`kit_templates`); `kit_preview_png` draws one on a match player.
    supports_kits: bool = True
    #: `patch` draws each club's crest as its flag unless `logos=False`.
    supports_logos: bool = True
    team_slots: int | None = len(R.TEAMS)
    #: `patch(blank_unused=True)` empties the nations no club replaced: their
    #: players' names, their names and their flags.
    supports_blank_unused: bool = True
    #: The national slots are the 36 slots themselves; the app fills those no
    #: club takes with today's national squads (`national_slot_map`).
    supports_nationals: bool = True
    national_slot_base: int | None = 0
    #: `sports.nationals.fetch_top_nations` fills the slots a league leaves (`fill_slots`)
    #: with today's best-ranked national teams. A nation on its own slot is `national`; one
    #: written elsewhere goes the clubs' way (scoreboard name, big name, flag) in its own
    #: retail flag and kits when the cartridge has it (`kits.retail`), else ESPN's flag and
    #: colours, and stays off the league's pages.
    supports_nation_fill: bool = True
    #: The order a caller should fill slots in (`rom.SLOT_ORDER`): the stadium
    #: select shows eight nations' names, so their slots go last.
    slot_order: tuple[int, ...] = R.SLOT_ORDER
    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the cartridge's current art for each. See `screens.SLOTS`.
    image_slots: tuple[str, ...] = screens.IMAGE_SLOT_NAMES
    #: `patch(looks=True)`, the default: each mapped team's two looks and its
    #: keeper's from the players' head shots (`looks`), in the match, the
    #: close-ups and the team photo. `looks=False` downloads nothing and keeps
    #: the cartridge's looks and each slot's alternate bit.
    supports_looks: bool = True
    #: `patch` writes each club's formation: the team's record becomes the default of the nearest
    #: of these the cartridge has (it has 16; these are the shapes real clubs play).
    writes_formation: bool = True
    formations: tuple[str, ...] = (
        "4-4-2", "4-3-3", "4-5-1", "3-5-2", "3-4-3", "5-3-2", "5-4-1", "4-2-4",
    )  # fmt: skip

    fetch = ISSPatcher.fetch

    def _download(self, url: str) -> bytes:
        return (self._transport or _http.default_transport)(url, {}, 15.0)

    def _crest(self, team: DeluxeTeam) -> Flag | None:
        """`team`'s crest as a flag, or None (with a status line if it failed).
        Raises `ImportError` without Pillow."""
        own = (
            self.assets_dir / "logos" / f"{team.team_id}.png"
            if self.assets_dir is not None and team.team_id is not None
            else None
        )
        if own is not None and not own.is_file():
            own = None
        if own is None and not team.logo_url:
            return None
        from ..iss_snes.logo import logo_flag

        try:
            return logo_flag(own.read_bytes() if own else self._download(team.logo_url))
        except ImportError:
            raise
        except Exception as exc:  # noqa: BLE001 - a bad download or image
            self.status(f"{team.name}: crest skipped: {exc}")
            return None

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

    @staticmethod
    def _image(rom_path: Path) -> bytes:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        data = Path(rom_path).read_bytes()
        if len(data) == R.ROM_SIZE + 512:
            data = data[512:]  # copier header
        if not R.is_deluxe(data):
            raise RomError(f"Not an ISS Deluxe (USA) cartridge image: {rom_path}")
        return data

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        with as_rom_error(rom_path):
            try:
                image, valid = self._image(rom_path), True
            except RomError:
                image, valid = b"", False
        slots = [
            RomSlot(
                index=t,
                current_name=f"First player: {R.decode_name(image[R.name_at(t, 0) :][:8])}",
                display_name=name,
            )
            for t, name in enumerate(R.TEAMS)
        ] if valid else []  # fmt: skip
        size = Path(rom_path).stat().st_size if Path(rom_path).exists() else 0
        return RomInfo(
            path=str(rom_path), size=size, game_id=self.game_id, is_valid=valid, slots=slots
        )

    def fill_slots(self, rom_path: Path) -> list[int]:
        """All 36 slots, in the order clubs take them (`slot_order`)."""
        return list(self.slot_order)

    def national_slot_map(self, rom_path: Path, data: LeagueData) -> list[SlotMapping]:
        """Pair the cartridge's 36 nations with `data`'s national teams by name
        (`iss2000_n64`'s `NATIONAL_ALIASES` for the cartridge's spellings)."""
        return cartridge_slot_map(R.TEAMS, NATIONAL_ALIASES, data)

    # --- mapping ---------------------------------------------------------------

    def _shape(self, roster: TeamRoster) -> tuple[int, int, int] | None:
        """The players per line (defenders, midfielders, forwards) of the formation this cartridge
        writes for `roster`, the nearest it offers to `team_formation`; None for a roster with no
        formation. The mapper fills the eleven's lines for it, so that `_cut_groups` finds each
        player in his own line: filled for ISS 2000's nearest shape instead, a 5-4-1 (a 5-3-2
        there) would spill its right midfielder to the attack and cut its striker into
        midfield."""
        formation = team_formation(roster)
        return parse_shape(nearest_shape(formation, self.formations)) if formation else None

    def suggest_squad_order(self, team_roster: TeamRoster) -> list[Player]:
        squad, _ = self.mapper.select_22(team_roster, self._shape(team_roster))
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
        teams: dict[int, DeluxeTeam] = {}
        for entry in slot_mapping or []:
            if not 0 <= entry.slot_index < len(R.TEAMS):
                raise MappingError(f"Slot {entry.slot_index} is outside 0..{len(R.TEAMS) - 1}")
            if entry.slot_index in teams:
                raise MappingError(f"Slot {entry.slot_index} is mapped more than once")
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    "which is not in the fetched league data"
                )
            team = self._map_team(roster, pcts, order_as_given)
            if data.league.id == NATIONAL_LEAGUE:
                team.nation = cartridge_slot(R.TEAMS, NATIONAL_ALIASES, roster.team)
                team.national = team.nation == entry.slot_index
            else:
                team.league = data.league.name
            teams[entry.slot_index] = team
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _map_team(
        self, roster: TeamRoster, pcts: dict[int, dict[str, float]], order_as_given: bool
    ) -> DeluxeTeam:
        extra = roster.extra or {}
        formation = team_formation(roster) or ""
        by_formation = parse_formation(formation) is not None
        if order_as_given:
            # With a formation the team sheet's order stands: the keeper, then the outfield as
            # given. The shape `given_22` returns is one of ISS 2000's seven, not ours.
            squad, _ = self.mapper.given_22(
                list(roster.players), formation if by_formation else None
            )
        else:
            squad, _ = self.mapper.select_22(roster, self._shape(roster))
        lineup = extra.get("lineup") or {}
        tags = {
            int(e["id"]): str(e.get("position", ""))
            for e in lineup.get("starters", [])
            if "id" in e
        }
        squad = squad[: R.PLAYERS]
        # The group of each of the eleven under a formation: its line by index over the shape
        # written, not the line `given_22` gives (ISS 2000's nearest shape).
        cut = _cut_groups(formation, self.formations) if by_formation else []
        # `iss-snes`'s rule: the initial and surname ("I.Gomes", as the
        # cartridge's own J.Platt) when it fits, else the surname; told apart
        # within the team.
        shirt_names = ISSStatMapper()._distinct_names(squad)
        players = []
        for k, (p, name) in enumerate(zip(squad, shirt_names, strict=True)):
            line = self.mapper._lines.get(p.id)
            if by_formation and k < 11:
                group = cut[k]
            else:
                group = (
                    ("GK", "DF", "MF", "FW")[line] if line is not None and k < 11 else group_of(p)
                )
            q = RETAIL[group]
            got = pcts.get(p.id, {})
            skills = {s: _place(q[s], got.get(FEED[s], 50.0)) for s in R.SKILL_NAMES}
            player = DeluxePlayer(
                name,
                group,
                skills,
                tags.get(p.id, ""),
                p.id,
                p.photo_url,
                p.nationality,
                role=p.role,
            )
            # A roster fetched before roles: his lineup code says what he plays.
            player.role = player.role or lineup_role(p.id, lineup)
            players.append(player)
        home, away = default_kits(roster.team)
        t = roster.team
        return DeluxeTeam(
            t.short_name or t.name, players, home, away, t.id, t.logo_url,
            (t.color, t.alternate_color), names.fit(t.name, t.short_name), t.name,
            formation=formation,
        )  # fmt: skip

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
        # Checked before the ROM is touched: an unknown slot or an unreadable
        # picture never leaves a half-written output behind.
        pictures = screens.load_images(options.get("images"), self.status)
        with as_rom_error(rom_path):
            image = bytearray(self._image(rom_path))
        teams: dict[int, DeluxeTeam] = {int(s): t for s, t in rosters.teams.items()}
        # A nation on its own slot keeps its name, flag and kits.
        clubs = {s: t for s, t in teams.items() if not t.national}
        # Templates read off the input, before any slot changes; a nation's own off the retail
        # cartridge, whatever a patch wrote over its slot.
        retail = kits.retail(image)
        given = options.get("kits", True)
        plans = {} if given is False else _kit_plans(image, retail, clubs, given)
        crests, fallbacks = self._flags(retail, clubs, logos=options.get("logos", True))
        if options.get("looks", True):
            teams = self._dress(teams)
            looks.write_records(image)
        players = 0
        for i, (slot, team) in enumerate(sorted(teams.items())):
            if on_progress is not None:
                on_progress(i / max(len(teams), 1), f"Patching {team.name}...")
            players += write_team(image, slot, team)
            if team.look is not None:
                looks.write(image, slot, team.look)
        write_default_teams(image, list(clubs) or list(teams))
        for slot, sides in plans.items():
            kits.write(image, slot, sides)
        if on_progress is not None:
            on_progress(0.9, "Writing flags...")
        unused = (
            [t for t in range(len(R.TEAMS)) if t not in teams]
            if options.get("blank_unused", False)
            else []
        )
        for slot in unused:
            for k in range(R.PLAYERS):
                image[R.name_at(slot, k) : R.name_at(slot, k) + R.NAME_LEN] = bytes(R.NAME_LEN)
        labels = {slot: team.label for slot, team in clubs.items() if team.label}
        names.write(image, labels | dict.fromkeys(unused, ""))
        big = {slot: bignames.candidates(t.full_name, t.name) for slot, t in clubs.items()}
        # The stadium select shows eight nations' big names: those stay.
        blank_big = {s: [""] for s in unused if s not in R.STADIUM_TEAMS}
        for slot, drawn in bignames.write(image, big | blank_big).items():
            if slot in clubs and drawn != big[slot][0]:
                self.status(f"{teams[slot].name}: big name shortened to {drawn} to fit")
        leagues = Counter(t.league for t in clubs.values() if t.league)
        league = leagues.most_common(1)[0][0] if leagues else ""
        pages.write(
            image,
            [s for s, t in clubs.items() if t.league],
            league,
            [s for s, t in teams.items() if not t.league],
        )
        pins = {slot: home.pins for slot, (home, _) in plans.items() if home is not None}
        for slot, note in flags.write(image, crests, fallbacks, pins, unused).items():
            name = teams[slot].name if slot in teams else R.TEAMS_ALL[slot]
            self.status(f"{name}: flag {note}")
        if pictures:
            if on_progress is not None:
                on_progress(0.95, "Drawing screen art...")
            for note in screens.write(image, pictures):
                self.status(note)
        image = fix_checksum(image)
        Path(output_path).write_bytes(bytes(image))
        if on_progress is not None:
            on_progress(1.0, f"Done! Saved to {output_path}")
        return PatchResult(str(output_path), teams_patched=len(teams), players_patched=players)

    def _dress(self, teams: dict[int, DeluxeTeam]) -> dict[int, DeluxeTeam]:
        """Copies of `teams` dressed from their players' head shots
        (`looks.dress`); the caller's records stay as mapped."""
        urls = {
            p.player_id: p.photo_url
            for team in teams.values()
            for p in team.players
            if p.player_id and p.photo_url
        }
        faces = measure_faces(self.cache_dir, urls, self._transport, self.status)
        dressed = deepcopy(teams)
        for team in dressed.values():
            looks.dress(team, faces)
        return dressed

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        with as_rom_error(rom_path):
            return screens.export(self._image(rom_path), Path(out_dir))

    def _flags(
        self, retail: bytes, teams: dict[int, DeluxeTeam], *, logos: bool
    ) -> tuple[dict[int, Flag], dict[int, Flag]]:
        """Each club's flag: its crest (`assets_dir/logos/<team_id>.png`, else
        the provider's logo), falling back to a two-band flag in its colours;
        the two-band flag outright without a crest. A team with neither keeps
        the cartridge's flag. A nation the cartridge has wears its own, read off
        `retail` (`kits.retail`)."""
        out: dict[int, Flag] = {}
        fallbacks: dict[int, Flag] = {}
        for slot, team in sorted(teams.items()):
            if team.nation is not None:
                out[slot] = flags.read(retail, team.nation)
                continue
            primary, alternate = (_parse_hex_colour(c) for c in team.colours)
            band = two_band_flag(primary, alternate or primary) if primary is not None else None
            crest = None
            if logos:
                try:
                    crest = self._crest(team)
                except ImportError:
                    self.status("Crests need Pillow (the images extra); two-band flags instead")
                    logos = False
            if crest is not None:
                out[slot] = crest
                if band is not None:
                    fallbacks[slot] = band
            elif band is not None:
                out[slot] = band
        return out, fallbacks

    def kit_templates(self, rom_path: Path) -> list[KitTemplate]:
        """The 36 national teams' kits on the cartridge, by team, each with a
        preview of its first kit. `{"template": n}` copies kit n's colours, its
        shirt numbers, chest design and clash class. Needs Pillow."""
        image = self._image(rom_path)
        try:
            return [
                KitTemplate(n, R.TEAMS[n], kits.preview_png(image, n, 0))
                for n in range(kits.TEMPLATES)
            ]
        except ImportError as exc:
            raise CapabilityError("Kit templates need Pillow (the images extra)") from exc

    def kit_preview_png(
        self, rom_path: Path, slot: int, which: str = "home", choice: Any = None, home: Any = None
    ) -> bytes:
        """A 96x144 RGBA PNG of a match player in a slot's kit, drawn from the
        cartridge's own tiles. `which` "home" or "away"; `choice` one KIT of the
        `kits` option, None for the kit the image holds. The chest design is
        the team's, so an away preview draws `home`'s (the team's home KIT).
        Nothing is written. Needs Pillow."""
        if not 0 <= slot < len(R.TEAMS):
            raise CapabilityError(
                f"Unknown kit slot {slot}; iss-deluxe-snes has 0..{len(R.TEAMS) - 1}"
            )
        if which not in ("home", "away"):
            raise CapabilityError(f"Unknown kit {which!r}; one of home, away")
        image = self._image(rom_path)
        kit = ("home", "away").index(which)
        try:
            plan = None if choice is None else kits.resolve(image, kits.parse(choice), kit)
            design = None if home is None else kits.resolve(image, kits.parse(home), 0)
            return kits.preview_png(image, slot, kit, plan, design)
        except ImportError as exc:
            raise CapabilityError("Kit previews need Pillow (the images extra)") from exc
        except (ValueError, TypeError, KeyError, OSError) as exc:
            raise CapabilityError(f"slot {slot} {which} kit: {exc}") from exc


def _kit_plans(
    image: bytes | bytearray, retail: bytes, teams: dict[int, DeluxeTeam], given: Any
) -> dict[int, tuple[kits.Kit | None, kits.Kit | None]]:
    """Every mapped slot's (first, second) kit: `given[slot][side]` (a KIT as
    `kits.parse` reads it) when the `kits` option sets it, else a nation's own
    (its template, read off `retail`), else the club's default kit; None keeps
    the cartridge's."""
    overrides = given if isinstance(given, dict) else {}
    plans: dict[int, tuple[kits.Kit | None, kits.Kit | None]] = {}
    for slot, team in teams.items():
        chosen = overrides.get(slot) or overrides.get(str(slot)) or {}
        sides: list[kits.Kit | None] = []
        for kit, (side, default) in enumerate((("home", team.kit_home), ("away", team.kit_away))):
            value = chosen.get(side)
            try:
                if value is None and team.nation is not None:
                    sides.append(kits.resolve(retail, team.nation, kit))
                    continue
                choice = kits.parse(value) if value is not None else default
                sides.append(None if choice is None else kits.resolve(image, choice, kit))
            except (CapabilityError, ValueError, TypeError, KeyError, OSError) as exc:
                raise CapabilityError(f"slot {slot} {side} kit: {exc}") from exc
        plans[slot] = (sides[0], sides[1])
    return plans


def _cut_groups(formation: str, offered: Sequence[str]) -> list[str]:
    """The group of each of the eleven, by index, over the shape written for `formation` (the
    nearest of `offered`): the keeper, then its defenders, midfielders and forwards."""
    d, m, f = map(int, nearest_shape(formation, offered).split("-"))
    return ["GK"] + ["DF"] * d + ["MF"] * m + ["FW"] * f


def _spot(depth: int, lateral: int) -> tuple[float, int]:
    """A place's side and depth, as `seat_line` takes them: the side runs from -1 at the team's
    left touchline (a lateral of `-_TOUCHLINE`) to 1 at its right."""
    return (lateral / _TOUCHLINE, depth)


def _places(
    image: bytes | bytearray, slot: int, team: DeluxeTeam, by_formation: bool
) -> list[DeluxePlayer]:
    """The XI in place order: place k is the slot's record k, on the line its flags give.

    Each line seats its players on its places by role (`seat_line`). With `by_formation` the
    lines are the formation's: the keeper (the first of the group GK, else the first player)
    takes place 0 and the others, as ordered, are cut by index over the record's lines: the
    defenders first, then the midfielders, the rest up front. Without, each starter plays the
    line of his group, and a line short of players takes those left over from the lines with more
    players than places, in the order given.
    """
    lines, offsets = R.slot_lines(image, slot), R.slot_offsets(image, slot)
    places = [[k for k in range(11) if lines[k] == line] for line in range(4)]
    xi = team.players[:11]
    if by_formation:
        goal = next((i for i, p in enumerate(xi) if p.group == "GK"), 0)
        defence, midfield = len(places[1]), len(places[2])
        want = [0] * len(xi)
        for k, i in enumerate(j for j in range(len(xi)) if j != goal):
            want[i] = 1 + (k >= defence) + (k >= defence + midfield)
    else:
        want = [GROUPS.index(p.group) for p in xi]
    mine = [[i for i, w in enumerate(want) if w == line][: len(places[line])] for line in range(4)]
    left = [i for i in range(len(xi)) if not any(i in line for line in mine)]
    out: list[DeluxePlayer | None] = [None] * 11
    for line, players in enumerate(mine):
        while len(players) < len(places[line]) and left:
            players.append(left.pop(0))
        roles = [xi[i].role for i in players] + [""] * (len(places[line]) - len(players))
        spots = [_spot(*offsets[k]) for k in places[line]]
        for i, seat in zip(players, seat_line(roles, spots), strict=False):
            out[places[line][seat]] = xi[i]
    return [p for p in out if p is not None]


def _code(player: DeluxePlayer, line: int | None) -> int:
    """`player`'s position code: his role's, else his group's.

    On a place of `line` the game wants a code that plays it (`R.ALLOWED_LINES`), else it marks
    him out of position. The code that plays both his code's lines and that one is taken, else
    the one that plays only that line. `line` None (the bench) leaves the code as it is.
    """
    code = ROLE_CODE.get(player.role) or _CODE[player.group]
    if line is None or line in R.ALLOWED_LINES[code]:
        return code
    both = R.ALLOWED_LINES[code] | {line}
    return next((c for c, lines in R.ALLOWED_LINES.items() if both <= lines), _OWN_CODE[line])


def write_team(image: bytearray, slot: int, team: DeluxeTeam) -> int:
    """Write `team` over `slot`: record k is place k; returns players written.

    A readable `team.formation` first replaces the slot's formation record with the default of the
    nearest formation the patcher offers. The places' lines then come from the slot's record.
    """
    by_formation = parse_formation(team.formation) is not None
    if by_formation:
        shape = nearest_shape(team.formation, ISSDeluxePatcher.formations)
        at = R.formation_at(image, slot)
        image[at : at + R.FORMATION_RECORD] = R.default_record(image, R.FORMATIONS.index(shape))
    lines = R.slot_lines(image, slot)
    order = _places(image, slot, team, by_formation) + team.players[11 : R.PLAYERS]
    for k, p in enumerate(order):
        o = R.record_at(slot, k)
        values = [max(0, min(15, p.skills[s])) for s in R.SKILL_NAMES]
        values.append(_code(p, lines[k] if k < 11 else None))
        image[o : o + 5] = R.pack(values)
        image[o + 5] = k
        # Byte 6: bit 4 the alternate look; the low nibble, a cartridge star's
        # own hair style, is never kept.
        alternate = image[o + 6] & looks.ALTERNATE if p.alternate is None else p.alternate
        image[o + 6] = looks.ALTERNATE if alternate else 0
        image[R.name_at(slot, k) : R.name_at(slot, k) + R.NAME_LEN] = R.encode_name(p.name)
    return len(order)


def write_default_teams(image: bytearray, slots: list[int]) -> None:
    """The main menu's two default teams: the first two of `slots`."""
    if slots:
        image[R.DEFAULT_P1] = slots[0] * 2
    if len(slots) > 1:
        image[R.DEFAULT_P2] = slots[1] * 2
