"""International Superstar Soccer Deluxe (SNES, USA) on the Patcher interface.

The 36 national-team slots take clubs, so the slot mapping is explicit, as in
`iss-snes`. A mapped slot gets the club's 20: the eleven as `iss-snes` and
`iss2000-n64` pick them, in the slot's own formation (the places the
cartridge gives that team), each line's places filled from the club's
lineup, then the bench; each player's name and nine skills, placed by league
percentile onto the cartridge's own spread for his position; each team's
looks from its players' head shots (`looks`). Main menu games start with the
first two mapped clubs instead of Brazil v England.
"""

from __future__ import annotations

from collections import Counter
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
from ...core.text import to_ascii
from ...formats.snes import fix_checksum
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import LeagueData, Player, TeamRoster
from ..iss2000_n64.mapper import N64StatMapper, _place, group_of
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
_LINE_CODES = {"GK": (R.GK,), "DF": (R.CB, R.FB), "MF": (R.MF, R.AM), "FW": (R.FW,)}
_FLANK = {"LB", "RB", "LWB", "RWB"}
_ATTACKING = {"AM", "AM-L", "AM-R"}


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
    national: bool = False  # a nation's own squad: it keeps its name, flag and kits
    #: Set by `looks.dress`; None leaves the cartridge's looks alone.
    look: ISSLook | None = None


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
    #: `patch(blank_unused=True)` empties the nations no club replaced: their
    #: players' names, their names and their flags.
    supports_blank_unused: bool = True
    #: The national slots are the 36 slots themselves; the app fills those no
    #: club takes with today's national squads (`national_slot_map`).
    supports_nationals: bool = True
    national_slot_base: int | None = 0
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

    def national_slot_map(self, rom_path: Path, data: LeagueData) -> list[SlotMapping]:
        """Pair the cartridge's 36 nations with `data`'s national teams by name
        (`iss2000_n64`'s `NATIONAL_ALIASES` for the cartridge's spellings)."""
        by_name = {to_ascii(r.team.name).upper(): r.team for r in data.teams}
        out = []
        for slot, name in enumerate(R.TEAMS):
            team = by_name.get(to_ascii(NATIONAL_ALIASES.get(name, name)).upper())
            if team is not None:
                out.append(SlotMapping(slot_index=slot, team_id=team.id, team_name=team.name))
        return out

    # --- mapping ---------------------------------------------------------------

    def suggest_squad_order(self, team_roster: TeamRoster) -> list[Player]:
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
            team.national = data.league.id == NATIONAL_LEAGUE
            team.league = "" if team.national else data.league.name
            teams[entry.slot_index] = team
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _map_team(
        self, roster: TeamRoster, pcts: dict[int, dict[str, float]], order_as_given: bool
    ) -> DeluxeTeam:
        if order_as_given:
            squad, _ = self.mapper.given_22(list(roster.players))
        else:
            squad, _ = self.mapper.select_22(roster)
        tags = {
            int(e["id"]): str(e.get("position", ""))
            for e in ((roster.extra or {}).get("lineup") or {}).get("starters", [])
            if "id" in e
        }
        squad = squad[: R.PLAYERS]
        # `iss-snes`'s rule: the initial and surname ("I.Gomes", as the
        # cartridge's own J.Platt) when it fits, else the surname; told apart
        # within the team.
        shirt_names = ISSStatMapper()._distinct_names(squad)
        players = []
        for k, (p, name) in enumerate(zip(squad, shirt_names, strict=True)):
            line = self.mapper._lines.get(p.id)
            group = ("GK", "DF", "MF", "FW")[line] if line is not None and k < 11 else group_of(p)
            q = RETAIL[group]
            got = pcts.get(p.id, {})
            skills = {s: _place(q[s], got.get(FEED[s], 50.0)) for s in R.SKILL_NAMES}
            players.append(
                DeluxePlayer(
                    name, group, skills, tags.get(p.id, ""), p.id, p.photo_url, p.nationality
                )
            )
        home, away = default_kits(roster.team)
        t = roster.team
        return DeluxeTeam(
            t.short_name or t.name, players, home, away, t.id, t.logo_url,
            (t.color, t.alternate_color), names.fit(t.name, t.short_name), t.name,
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
        # A nation's own squad keeps its name, flag and kits.
        clubs = {s: t for s, t in teams.items() if not t.national}
        # Templates read off the input, before any slot changes.
        given = options.get("kits", True)
        plans = {} if given is False else _kit_plans(image, clubs, given)
        crests, fallbacks = self._flags(clubs, logos=options.get("logos", True))
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
        pages.write(image, list(clubs), league)
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
        self, teams: dict[int, DeluxeTeam], *, logos: bool
    ) -> tuple[dict[int, Flag], dict[int, Flag]]:
        """Each club's flag: its crest (`assets_dir/logos/<team_id>.png`, else
        the provider's logo), falling back to a two-band flag in its colours;
        the two-band flag outright without a crest. A team with neither keeps
        the cartridge's flag."""
        out: dict[int, Flag] = {}
        fallbacks: dict[int, Flag] = {}
        for slot, team in sorted(teams.items()):
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
    image: bytes | bytearray, teams: dict[int, DeluxeTeam], given: Any
) -> dict[int, tuple[kits.Kit | None, kits.Kit | None]]:
    """Every mapped slot's (first, second) kit: `given[slot][side]` (a KIT as
    `kits.parse` reads it) when the `kits` option sets it, else the club's
    default kit; None keeps the cartridge's."""
    overrides = given if isinstance(given, dict) else {}
    plans: dict[int, tuple[kits.Kit | None, kits.Kit | None]] = {}
    for slot, team in teams.items():
        chosen = overrides.get(slot) or overrides.get(str(slot)) or {}
        sides: list[kits.Kit | None] = []
        for kit, (side, default) in enumerate((("home", team.kit_home), ("away", team.kit_away))):
            value = chosen.get(side)
            try:
                choice = kits.parse(value) if value is not None else default
                sides.append(None if choice is None else kits.resolve(image, choice, kit))
            except (CapabilityError, ValueError, TypeError, KeyError, OSError) as exc:
                raise CapabilityError(f"slot {slot} {side} kit: {exc}") from exc
        plans[slot] = (sides[0], sides[1])
    return plans


def _places(codes: list[int], team: DeluxeTeam) -> list[DeluxePlayer]:
    """The XI arranged into the slot's eleven places: each line's places take
    that line's starters, a flank player (an ESPN full or wing back) the
    full-back places first and an attacking midfielder the attacking places
    first; a place a line cannot fill takes whoever is left."""
    xi = team.players[:11]
    out: list[DeluxePlayer | None] = [None] * 11
    left = list(xi)
    for line, line_codes in _LINE_CODES.items():
        mine = [p for p in left if p.group == line]
        for code, prefer in ((line_codes[-1], True), (line_codes[0], False)):
            for k, c in enumerate(codes):
                if c != code or out[k] is not None or not mine:
                    continue
                pick = next(
                    (p for p in mine if (p.tag in _FLANK or p.tag in _ATTACKING) == prefer), mine[0]
                ) if len(line_codes) > 1 else mine[0]  # fmt: skip
                out[k] = pick
                mine.remove(pick)
                left.remove(pick)
    for k in range(11):
        if out[k] is None and left:
            out[k] = left.pop(0)
    return [p for p in out if p is not None]


def write_team(image: bytearray, slot: int, team: DeluxeTeam) -> int:
    """Write `team` over `slot`: record k is place k; returns players written."""
    codes = R.formation_codes(image, slot)
    order = _places(codes, team) + team.players[11 : R.PLAYERS]
    for k, p in enumerate(order):
        o = R.record_at(slot, k)
        code = codes[k] if k < 11 else _CODE[p.group]
        values = [max(0, min(15, p.skills[s])) for s in R.SKILL_NAMES] + [code]
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
