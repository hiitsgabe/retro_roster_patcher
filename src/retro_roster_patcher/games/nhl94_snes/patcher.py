"""NHL 94 (SNES) on the unified Patcher interface.

Roster composition comes out of the ROM: byte 17 of a team block packs the
forward count in its high nibble and the defenceman count in its low nibble.
Goalies are not in it: bytes 19-20 hold one nibble per goalie (1 to 3 on the
retail cartridge). This package reports 2 goalies for every slot, writes 2 and
rewrites those bytes to count 2. A patch writes what it filled into byte 17, so
`analyze_rom` reports the larger of it and the cartridge's own counts
(`RETAIL_ROSTER_COUNTS`): a re-patch can fill a slot an earlier one left short.
`map_rosters` is given no ROM, so the counts travel in two hops --
`analyze_rom` publishes them in `RomInfo.extra["roster_counts"]`, a caller
passes them to `map_rosters(roster_counts=...)`, and each `NHL94TeamRecord`
records the forwards and defencemen it filled. `patch` writes the header from
the record, never from the ROM. A caller that skips the first hop gets
`(2, 14, 7)` everywhere.

The pointer table sits at file offset 0xE25E7, in bank $9C -- 927 207 bytes in,
so a file must be ~950 KB before one pointer can be read. `validate` accepts
anything from 649 728 bytes up and tests nothing else, so `patch` checks the
table is addressable (`_pointer_table_fits`) and `analyze_rom` checks the 28
blocks under it parse (`_looks_like_nhl94_snes`).

The cartridge's own 1 MiB keeps its checksum: the SNES does not verify the
header word at $FFDC/$FFDE and NHL '94 does not read it, so an in-place patch of
the same length boots unchanged. An image grown to 2 MiB (team logos and
colours in `identity`, head shots in `portraits`, screen art in `screens`) gets
its checksum fixed, last. The Genesis sibling needs both and does both.
"""

from __future__ import annotations

import hashlib
import importlib.util
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.concurrency import parallel_map
from ...core.errors import ApiError, MappingError, RomError, as_rom_error
from ...core.headshots import load_headshot
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
from ...formats import snes
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, Player, TeamRoster
from ...sports.nhl import NhlApiClient
from . import identity, portraits, screens
from .models import (
    DEFAULT_ROSTER_COUNTS,
    NHL94_TEAM_ORDER,
    RETAIL_ROSTER_COUNTS,
    TEAM_COUNT,
    NHL94PlayerRecord,
    NHL94TeamRecord,
)
from .rom_reader import POINTER_SIZE, NHL94SNESRomReader
from .rom_writer import (
    HEADER_GOALIE_SLOTS,
    LINE_ASSIGN_OFFSET,
    LINE_COUNT,
    LINE_SLOTS,
    NHL94SNESRomWriter,
    header_counts,
)
from .stat_mapper import NHL94StatMapper

#: One (goalies, forwards, defensemen) triple per ROM slot.
RosterCounts = list[tuple[int, int, int]]


def _pointer_table_fits(reader: NHL94SNESRomReader) -> bool:
    """Can every one of the 28 team pointers be read out of this file?

    `_read_team_pointer`'s own condition -- two bytes at `table + index * 4` --
    evaluated for the last index rather than for each in turn.
    """
    if reader.data is None:
        return False
    last = reader._ptr_table_offset() + (TEAM_COUNT - 1) * POINTER_SIZE
    return last + 2 <= len(reader.data)


def _starting_six_first(ordered: list[Player]) -> list[Player]:
    """`ordered` (G, F, D blocks) with the six who start in front: G1, the first
    trio in its LW, C, RW slots, D1 and D2, then G2, the other forwards and the
    other defencemen. Each block keeps its order, so `map_rosters` with
    `order_as_given=True` writes the same ROM from either list.
    """
    goalies = [p for p in ordered if p.position == "G"]
    forwards = [p for p in ordered if p.position not in ("G", "D")]
    defence = [p for p in ordered if p.position == "D"]
    return goalies[:1] + forwards[:3] + defence[:2] + goalies[1:] + forwards[3:] + defence[2:]


#: The counting stats a partial ESPN line can lack, which then take the lowest
#: value the team's list has: a player missing from a top-25 list is below it.
FLOOR_STATS = ("G", "A", "PTS", "PIM")

ESPN_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/hockey/nhl/standings?season={season}"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _leader_floors(leaders: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    floors: dict[str, float] = {}
    for key in FLOOR_STATS:
        values = [v for line in leaders.values() if (v := _number(line.get(key))) is not None]
        if values:
            floors[key] = min(values)
    return floors


def _goal_rates(leaders: Mapping[str, Mapping[str, Any]]) -> tuple[float, float] | None:
    """(goals for, goals against) per game, summed from a club's stat lines when
    they carry every skater's goals and every goalie's goals against (the NHL
    API's)."""
    lines = list(leaders.values())
    goalies = [line for line in lines if "GA" in line]
    skaters = [line for line in lines if "GA" not in line and "GP" in line]
    if not goalies or not skaters:
        return None
    gf = sum(_number(line.get("G")) or 0.0 for line in skaters)
    ga = sum(_number(line.get("GA")) or 0.0 for line in goalies)
    gp = max(_number(line.get("GP")) or 0.0 for line in skaters)
    return (gf / gp, ga / gp) if gp else None


def _strengths(rates: Mapping[str, tuple[float, float]]) -> dict[str, int]:
    """Header byte 14 per club: its rank in goals for per game (attack, high
    nibble) and in goals against (leakiness, low nibble) among `rates`, each
    spread over 0-7 -- the score simulator's two indexes ($9F:9F91). Fewer than
    two clubs rank nothing."""
    if len(rates) < 2:
        return {}

    def rank(column: int) -> dict[str, int]:
        order = sorted(rates, key=lambda code: rates[code][column])
        return {code: i * 8 // len(order) for i, code in enumerate(order)}

    attack, leak = rank(0), rank(1)
    return {code: attack[code] << 4 | leak[code] for code in rates}


#: Smallest team-block header this game's writer can be pointed at: the player
#: count sits at byte 17 and 8 lines of 8 bytes at byte 21, so anything shorter
#: would have the line table overwrite the first player record. The retail
#: cartridge's headers are exactly this long.
MIN_TEAM_HEADER_SIZE = LINE_ASSIGN_OFFSET + LINE_COUNT * LINE_SLOTS

#: Largest one. Team blocks are addressed by a 16-bit offset within bank $9C,
#: folded into one 32 KB window, so a whole block lives inside 0x8000 bytes.
MAX_TEAM_BLOCK_SIZE = 0x8000

#: Bounds on a player record's length word, which counts itself and the name.
#: Under 3 is the end-of-roster terminator; over 40 the reader refuses.
MIN_RECORD_LENGTH = 3
MAX_RECORD_LENGTH = 40


def _looks_like_nhl94_snes(reader: NHL94SNESRomReader) -> bool:
    """Does this image hold 28 team blocks in the shape this game's code reads?

    Each of the 28 pointers is dereferenced and its block required to have a
    header long enough for the line table, small enough for one bank, and a first
    player record whose length word is inside the reader's own bounds. The blocks
    must also be distinct, or a file of constant garbage points all 28 teams at
    one block that happens to parse.

    Detection only: `analyze_rom` decides `is_valid` with it, `patch` does not, so
    a genuine dump that breaks a bound still patches under `--game nhl94-snes`.
    """
    data = reader.data
    if data is None or not _pointer_table_fits(reader):
        return False

    bases: set[int] = set()
    for index in range(TEAM_COUNT):
        base = reader._read_team_pointer(index)
        if base is None or base + 2 > len(data):
            return False
        header_size = data[base] | (data[base + 1] << 8)
        if not MIN_TEAM_HEADER_SIZE <= header_size < MAX_TEAM_BLOCK_SIZE:
            return False
        first_record = base + header_size
        if first_record + 2 > len(data):
            return False
        length = data[first_record] | (data[first_record + 1] << 8)
        if not MIN_RECORD_LENGTH <= length <= MAX_RECORD_LENGTH:
            return False
        bases.add(base)
    return len(bases) == TEAM_COUNT


@register(
    "nhl94-snes",
    platform="snes",
    sport="hockey",
    requires_slot_mapping=False,
    providers=("espn", "nhl"),
)
class NHL94SNESPatcher(Patcher):
    """Teams map to ROM slots by three-letter code, so no manual mapping step.

    Providers: `espn` for the current season, `nhl` for seasons back to 1993.
    Only the `nhl` provider honours `fetch`'s `season`; ESPN's roster endpoint
    serves the current squad and nothing else.
    """

    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the cartridge's current art for each. See `screens.SLOTS`.
    image_slots: tuple[str, ...] = screens.IMAGE_SLOT_NAMES
    #: `patch` draws each mapped team's logo (Game Setup and the other menus,
    #: center ice, the title pucks) unless `logos=False`; see `identity`.
    supports_logos: bool = True

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
        # User-supplied, read-only: `logos/<team_id>.png` drawn instead of the
        # provider's logo.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self.mapper = NHL94StatMapper()
        self._transport = transport
        # `Any`: the two clients take different arguments for squad and leaders, so
        # no single type describes both.
        if self.provider == "nhl":
            self.api: Any = NhlApiClient(str(self.cache_dir), on_status, transport=transport)
        else:
            self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = NHL94SNESRomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        # `info.is_valid` is a size test alone; see `_looks_like_nhl94_snes`.
        is_valid = info.is_valid and _looks_like_nhl94_snes(reader)

        extra: dict[str, Any] = {"has_header": info.has_header}
        if is_valid:
            # Only for a valid image: on an invalid one every triple would be the
            # reader's `(2, 14, 7)` fallback, which reads as 28 measurements and is
            # 28 refusals to measure.
            extra["roster_counts"] = [
                [goalies, max(forwards, retail[0]), max(defence, retail[1])]
                for (goalies, forwards, defence), retail in zip(
                    (reader.read_team_player_counts(i) for i in range(TEAM_COUNT)),
                    RETAIL_ROSTER_COUNTS,
                    strict=True,
                )
            ]

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    current_name=slot.current_name,
                    display_name=slot.display_name,
                )
                for slot in info.team_slots
            ],
            extra=extra,
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        self.status("Fetching NHL teams...")
        teams = self.api.get_nhl_teams()
        if not teams:
            raise ApiError("The provider returned no NHL teams")

        # Expansion teams have no 1994 slot; fetching them costs a round trip for
        # a roster `map_rosters` discards.
        mapped = [t for t in teams if self.mapper.get_team_slot(t.code) is not None]
        if not mapped:
            raise ApiError("No fetched team matches an NHL94 SNES ROM slot")

        rosters: list[TeamRoster] = []
        for i, team in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            if self.provider == "nhl":
                players = self.api.get_hockey_squad(team.code, season)
                leaders = self.api.get_hockey_team_leaders(team.code, season)
            else:
                # ESPN's roster endpoint ignores the season, but it is still passed:
                # it is the cache key there, and a path segment for the leaders.
                players = self.api.get_hockey_squad(team.id, season)
                leaders = self.api.get_hockey_team_leaders(team.id, season)
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players or [],
                    extra={"leaders": leaders or {}},
                )
            )

        # Each club's strength for the score simulator (header byte 14), ranked
        # here against every club fetched -- ESPN's standings hold the whole
        # league -- so a map of a few teams still rates each against the league.
        # The NHL API's club stats already carry the goals, player by player.
        if self.provider == "nhl":
            rates = {
                roster.team.code: rate
                for roster in rosters
                if (rate := _goal_rates(roster.extra["leaders"])) is not None
            }
        else:
            rates = self._espn_standings(season)
        strengths = _strengths(rates)
        if not strengths:
            self.status("Team strengths kept from the cartridge: no goals for and against")
        for roster in rosters:
            if roster.team.code in strengths:
                roster.extra["strength"] = strengths[roster.team.code]

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # No league endpoint for this game, so every field but `season` is synthesised.
        return LeagueData(
            league=League(
                id=0,
                name="NHL",
                country="USA",
                country_code="US",
                logo_url="",
                season=season,
                teams_count=len(rosters),
            ),
            teams=rosters,
        )

    def _espn_standings(self, season: int) -> dict[str, tuple[float, float]]:
        """`{code: (goals for, goals against) per game}` for `season` from ESPN's
        standings, one request for the league; `{}` when it does not answer.

        Not cached, unlike the rosters: one request per fetch keeps an in-season
        fetch's standings current.
        """
        try:
            data = _http.get_json(
                ESPN_STANDINGS_URL.format(season=season), transport=self._transport
            )
        except Exception:  # noqa: BLE001 - the strengths are kept, and fetch says so
            return {}
        out: dict[str, tuple[float, float]] = {}
        nodes = [data]
        while nodes:
            node = nodes.pop()
            nodes.extend(node.get("children") or [])
            for entry in (node.get("standings") or {}).get("entries") or []:
                stats = {s.get("name"): _number(s.get("value")) for s in entry.get("stats") or []}
                code = (entry.get("team") or {}).get("abbreviation")
                gf, ga, gp = (stats.get(k) for k in ("pointsFor", "pointsAgainst", "gamesPlayed"))
                if code and gf is not None and ga is not None and gp:
                    out[code] = (gf / gp, ga / gp)
        return out

    # -- map ----------------------------------------------------------------

    def suggest_squad_order(self, team_roster):
        leaders = team_roster.extra.get("leaders") or {}
        # Default line counts (2 G / 14 F / 7 D) — a display order, not the
        # per-slot header the patch resolves from the ROM.
        ordered = self.mapper.select_roster(team_roster.players, leaders)
        return self._append_unused(_starting_six_first(ordered), team_roster.players)

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        roster_counts: Sequence[Sequence[int]] | None = None,
        order_as_given: bool = False,
    ) -> MappedRosters:
        """Reduce league data to one `NHL94TeamRecord` per matched ROM slot.

        `roster_counts` is `RomInfo.extra["roster_counts"]` passed straight back
        in; omit it and every slot is cut to `DEFAULT_ROSTER_COUNTS`. The players
        are the caller's list with `order_as_given`, else ranked
        (`select_roster`). Either way the record carries the forwards and
        defencemen actually filled, because `patch` writes the header from that
        and not from the ROM. A team with fewer than 2 goalies is left out, with
        a status line, and its slot keeps the cartridge's team: the line table
        always counts two. A stat line missing a counting stat (ESPN lists each
        category's top 25) takes the lowest value the team's list has. Each
        record's `strength` (header byte 14) is the one `fetch` ranked.
        """
        self.check_slot_mapping(slot_mapping)
        counts = self._resolve_roster_counts(roster_counts)
        teams: dict[int, NHL94TeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < TEAM_COUNT:
                continue
            leaders = roster.extra.get("leaders") or {}
            num_goalies, num_forwards, num_defensemen = counts[slot]
            # The slot's G, F, D blocks -- in the caller's order, or ranked -- and
            # a header counting what each block got: nothing is topped up after
            # the defencemen, where the line table would read it as one.
            players = (
                roster.players
                if order_as_given
                else self.mapper.select_roster(
                    roster.players,
                    leaders,
                    num_goalies=num_goalies,
                    num_forwards=num_forwards,
                    num_defensemen=num_defensemen,
                )
            )
            goalies = [p for p in players if p.position == "G"][:num_goalies]
            forwards = [p for p in players if p.position not in ("G", "D")][:num_forwards]
            defence = [p for p in players if p.position == "D"][:num_defensemen]
            if players and len(goalies) < HEADER_GOALIE_SLOTS:
                self.status(
                    f"{roster.team.name} skipped: {len(goalies)} goalie(s) in the list, "
                    f"NHL 94 needs {HEADER_GOALIE_SLOTS}; the slot keeps the cartridge's team"
                )
                continue
            selected = goalies + forwards + defence
            num_forwards, num_defensemen = len(forwards), len(defence)
            floors = _leader_floors(leaders)
            records = [
                self.mapper.map_player(
                    player,
                    roster.team.code,
                    {**floors, **line} if (line := leaders.get(str(player.id))) else {},
                )
                for player in selected
            ]
            # `MODERN_NHL_TO_NHL94` maps 32 codes onto 28 slots -- LAK/LA, NJD/NJ,
            # SJS/SJ and TBL/TB alias -- so an empty roster arriving second must not
            # wipe the populated one that already took the slot.
            existing = teams.get(slot)
            if not records and existing is not None and existing.players:
                continue
            strength = roster.extra.get("strength")
            teams[slot] = NHL94TeamRecord(
                index=slot,
                name=NHL94_TEAM_ORDER[slot],
                city="",
                acronym="",
                players=records,
                num_goalies=num_goalies,
                num_forwards=num_forwards,
                num_defensemen=num_defensemen,
                strength=strength if isinstance(strength, int) else None,
                team=roster.team,
            )
        return MappedRosters(game_id=self.game_id, teams=teams)

    def _resolve_roster_counts(self, supplied: Sequence[Sequence[int]] | None) -> RosterCounts:
        """One validated (G, F, D) triple per slot. Validated because the value has
        crossed a JSON boundary since `analyze_rom` built it, and a ragged row would
        otherwise surface as a wrong header byte."""
        if supplied is None:
            return [DEFAULT_ROSTER_COUNTS] * TEAM_COUNT
        if len(supplied) != TEAM_COUNT:
            raise MappingError(
                f"roster_counts must hold {TEAM_COUNT} entries, one per ROM slot; "
                f"got {len(supplied)}"
            )
        counts: RosterCounts = []
        for index, row in enumerate(supplied):
            if len(row) != 3:
                raise MappingError(
                    f"roster_counts[{index}] must be (goalies, forwards, defensemen); got {row!r}"
                )
            goalies, forwards, defensemen = row
            if not all(
                isinstance(value, int) and value >= 0 for value in (goalies, forwards, defensemen)
            ):
                raise MappingError(
                    f"roster_counts[{index}] must hold three non-negative integers; got {row!r}"
                )
            counts.append((goalies, forwards, defensemen))
        return counts

    # -- patch --------------------------------------------------------------

    def _draw_portraits(
        self,
        writer: NHL94SNESRomWriter,
        drawn: Mapping[int, Sequence[NHL94PlayerRecord]],
        *,
        head_shots: bool,
    ) -> None:
        """Each written player's head shot in his portrait, or the game's drawing
        of a skater or a goalie (`portraits`), so no 1993 face stays on a new
        player. Without Pillow every one gets the drawing, said in a status line
        when there were head shots to draw. `head_shots=False` keeps the
        cartridge's portraits, only an earlier patch's faces going back to the
        drawing. Left alone on an image whose portrait table is not the
        cartridge's.
        """
        data = writer.data
        base = writer.reader.header_offset
        if data is None or not drawn or not portraits.table_ok(data[base:]):
            return
        rom = data[base:]
        players = {
            slot: [(p.is_goalie, p.photo_url) for p in records] for slot, records in drawn.items()
        }
        if not head_shots:
            portraits.forget(rom, players)
            data[base:] = rom
            return
        load = None
        if any(url for records in players.values() for _goalie, url in records):
            if importlib.util.find_spec("PIL") is None:
                self.status(
                    "Portraits: Pillow is not installed; new players get the game's drawings"
                )
            else:
                self.status("Loading head shots...")

                def load(urls: list[str]) -> list[Any]:
                    return parallel_map(
                        lambda url: self._portrait_from(
                            load_headshot(self.cache_dir, url, self._transport)
                        ),
                        urls,
                    )

        lines = {}
        for slot in drawn:
            at = (writer.reader._read_team_pointer(slot) or 0) + LINE_ASSIGN_OFFSET
            lines[slot] = bytes(data[at : at + LINE_COUNT * LINE_SLOTS])
        drew, drawings = portraits.write(rom, players, lines, load)
        data[base:] = rom
        if load is not None:
            self.status(f"Portraits drawn: {drew} head shots, {drawings} drawings")

    @staticmethod
    def _portrait_from(png: bytes | None) -> Any:
        if not png:
            return None
        try:
            return portraits.headshot_portrait(png)
        except Exception:  # noqa: BLE001 - Pillow's errors (a decompression bomb among them) share no base
            return None

    def _write_identity(
        self,
        writer: NHL94SNESRomWriter,
        rosters: MappedRosters,
        targets: Sequence[int],
        *,
        logos: bool,
    ) -> None:
        """Each target slot's club as the game shows it: names, banner, colours,
        uniforms and, with `logos`, its logo (`identity`). A slot whose record
        names no club keeps the cartridge's."""
        teams = {
            slot: team
            for slot in targets
            if (team := identity.identity_of(rosters.teams[slot])) is not None
        }
        if not teams or writer.data is None:
            return
        base = writer.reader.header_offset
        if not identity.recognised(writer.data[base:]):
            self.status("Team names, colours and logos kept: not the retail cartridge's layout")
            return
        if logos and importlib.util.find_spec("PIL") is None:
            self.status("Pillow is not installed; team logos skipped")
        elif logos:
            self.status("Loading team logos...")
            slots = sorted(teams)
            pngs = parallel_map(
                lambda slot: self._logo_png(rosters.teams[slot], teams[slot]), slots
            )
            for slot, png in zip(slots, pngs, strict=True):
                teams[slot] = replace(teams[slot], logo=png)
        rom = writer.data[base:]
        identity.write(rom, teams, self.status)
        writer.data[base:] = rom
        self.status(f"Team identity written: {len(teams)}")

    def _logo_png(self, record: NHL94TeamRecord, team: identity.Identity) -> bytes | None:
        """The club's logo: `assets_dir/logos/<team_id>.png`, else the provider's
        (ESPN's for a provider that sends none), downloaded once into
        `cache_dir/logos`. None, with a status line, when it cannot be had."""
        club = record.team
        if self.assets_dir is not None and club is not None and club.id:
            override = self.assets_dir / "logos" / f"{club.id}.png"
            if override.is_file():
                return override.read_bytes()
        url = (club.logo_url if club is not None else "") or identity.logo_url(team.code)
        cache = self.cache_dir / "logos" / f"{hashlib.sha1(url.encode()).hexdigest()}.png"
        if cache.is_file():
            return cache.read_bytes()
        try:
            png = (self._transport or _http.default_transport)(url, {}, 15.0)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(png)
            return png
        except Exception as exc:  # noqa: BLE001 - a logo costs a picture, never the patch
            self.status(f"{team.club.city}: logo skipped: {exc}")
            return None

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
        # Before the ROM is touched: an unknown slot or an unreadable picture
        # never leaves a half-written output behind.
        pictures = screens.load_images(options.get("images"), self.status)
        self.status("Validating ROM...")
        reader = NHL94SNESRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid NHL94 SNES ROM: {rom_path}")
        if not _pointer_table_fits(reader):
            size = len(reader.data or b"")
            raise RomError(
                f"Not a valid NHL94 SNES ROM: {rom_path}: the team pointer table at "
                f"{reader._ptr_table_offset():#x} is past the end of a {size}-byte file"
            )

        self.status("Initializing ROM writer...")
        writer = NHL94SNESRomWriter(str(rom_path), str(output_path))
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")

        # `filled_slots()` is unusable here: this game's mapped value is an object,
        # so every one is truthy however empty, and an empty player list would
        # wipe a slot's roster. The range is re-checked because the reader guards
        # only `team_index >= TEAM_COUNT`: a negative key reads the bytes before
        # the pointer table and treats them as a team pointer.
        targets = sorted(
            slot for slot, team in rosters.teams.items() if 0 <= slot < TEAM_COUNT and team.players
        )

        # The team names first: they live in the team blocks, which
        # `write_rosters` then packs, strings and all.
        self._write_identity(writer, rosters, targets, logos=bool(options.get("logos", True)))

        # All 28 blocks are rebuilt at once: the names decide where each one fits.
        # A slot whose block does not parse is left alone.
        written_slots = writer.write_rosters(
            {slot: rosters.teams[slot].players for slot in targets}
        )
        targets = [slot for slot in targets if slot in written_slots]

        teams_patched = 0
        players_patched = 0
        drawn: dict[int, list[NHL94PlayerRecord]] = {}
        for index, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(index / len(targets), f"Writing {NHL94_TEAM_ORDER[slot]}...")
            team: NHL94TeamRecord = rosters.teams[slot]
            # The counts come off the record, not the ROM -- the line table
            # indexes forwards from 3 and defencemen from `3 + num_forwards`
            # (1-based), so a header written from a different triple than the one
            # that shaped this list labels real players with the wrong role -- and
            # `header_counts` clamps them to the players there are.
            written = len(team.players)
            forwards, defencemen = header_counts(written, team.num_forwards, team.num_defensemen)
            writer.write_team_header(slot, forwards, defencemen)
            writer.write_team_ratings(slot, team.players, team.strength)
            teams_patched += 1
            players_patched += written
            drawn[slot] = team.players

        # The retail sign bug maxes every attribute a cold night lowers;
        # `hot_cold_fix=False` keeps it. Nothing written, nothing changed.
        fix = bool(options.get("hot_cold_fix", True))
        if targets and not writer.set_hot_cold_fix(fix) and fix:
            self.status("Hot/cold fix skipped: the code at $9F:D232 is not the cartridge's")

        self._draw_portraits(writer, drawn, head_shots=bool(options.get("portraits", True)))

        if pictures and writer.data is not None:
            self.status("Drawing screen art...")
            for note in screens.draw(writer.data, pictures):
                self.status(note)

        if writer.data is not None and len(writer.data) - writer.reader.header_offset > 0x100000:
            # Last: a grown image is ours to keep valid. The cartridge's own size
            # is left as it was, checksum and all.
            base = writer.reader.header_offset
            writer.data[base:] = snes.fix_checksum(writer.data[base:])

        self.status("Saving patched ROM...")
        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )
