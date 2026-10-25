"""Ken Griffey Jr. Presents Major League Baseball (SNES) on the unified Patcher interface.

The team tables are found by searching for a 14-byte marker, not by a fixed
offset, so a 512-byte copier header needs no arithmetic anywhere in this package:
the search simply finds the marker 512 bytes later and the recorded offset is
already a file offset. `rom_writer.update_snes_checksum` is the one exception,
because the SNES header it edits sits at a fixed address.

The reader accepts that match wherever it lands, and 25 280 bytes of team data
follow it. `_team_data_fits` is the bound.

The SNES checksum is recomputed by `patch`, not by `finalize`, which is where
this game's upstream put it; the NBA Live 95 port does its equivalent inside
`finalize`. Do not harmonise them. It runs after every other writer.

The ROM's team names are never parsed, so `RomSlot.current_name` carries the
first player read out of the slot, labelled as such, and `display_name` takes the
`KGJ_TEAM_ORDER` constant.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.concurrency import parallel_map
from ...core.errors import ApiError, RomError, as_rom_error
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
from ...core.skin import face_ita, tone
from ...sports import _http
from ...sports.espn import EspnClient
from ...sports.models import League, LeagueData, TeamRoster
from . import expansion, identity, looks, screens
from .models import (
    AL_TEAMS,
    BATTERS_PER_TEAM,
    HR_DERBY_BATTERS_PER_LEAGUE,
    KGJ_TEAM_ORDER,
    LINEUP_POSITIONS,
    MODERN_MLB_TO_KGJ,
    PLAYERS_PER_TEAM,
    STARTERS_PER_TEAM,
    TEAM_COUNT,
    KGJPlayerRecord,
    KGJTeamRecord,
)
from .rom_reader import TEAM_DATA_SPAN, KGJRomReader
from .rom_writer import KGJRomWriter
from .stat_mapper import KGJStatMapper, _stat

if TYPE_CHECKING:
    from PIL import Image


def _team_data_fits(reader: KGJRomReader) -> bool:
    """Do all 28 team blocks lie inside the file, given where the marker was found?

    A correctly-sized image can still match `FIRST_TEAM_MARKER` near its end, and
    then `get_team_offset` hands out addresses past the end for most of the
    league. Nothing crashes: reads answer `{}`, writes answer `False`, and the
    patch reports success having copied the input unchanged. This is
    `write_player`'s own bound -- `off + PLAYER_LENGTH > len(self.data)` --
    evaluated once for the last of the 700 records.
    """
    if reader.data is None:
        return False
    return reader.first_team_offset + TEAM_DATA_SPAN <= len(reader.data)


#: The slot whose #24 the game draws as Griffey (`looks.free_number_24`).
_SEATTLE = MODERN_MLB_TO_KGJ["SEA"]

#: How many head shots one batch measures before reporting status to the app.
#: The app kills a job that stays silent for 15 minutes, so a whole league of
#: them at once would trip it on a slow connection.
_HEADSHOT_CHUNK = 25


def _is_al(slot: int) -> bool:
    """Does this team slot play in the American League (a DH, the AL derby)?"""
    return slot < AL_TEAMS or slot == expansion.TAMPA_BAY


def _derby(rosters: MappedRosters) -> tuple[list[KGJPlayerRecord], list[KGJPlayerRecord]]:
    """The Home Run Derby's batters, (NL, AL): each league's patched hitters
    with the most home runs, 3 apiece. A league nothing was patched for is
    empty, so it keeps the cartridge's."""
    leagues: tuple[list[KGJPlayerRecord], list[KGJPlayerRecord]] = ([], [])
    for slot, team in rosters.teams.items():
        if 0 <= slot < expansion.TEAMS:
            hitters = team.players[:BATTERS_PER_TEAM]
            leagues[_is_al(slot)].extend(r for r in hitters if r and not r.is_pitcher)
    nl, al = (
        sorted(league, key=lambda r: r.home_runs, reverse=True)[:HR_DERBY_BATTERS_PER_LEAGUE]
        for league in leagues
    )
    return nl, al


def _stats(roster: TeamRoster) -> dict:
    """The fetched season lines by player id (str). `leaders` is what fetches
    before full stat lines stored there."""
    return roster.extra.get("stats") or roster.extra.get("leaders") or {}


def _lineup(slot: int) -> tuple[str, ...]:
    """The positions a team slot's starting batters field, one each.

    AL clubs (slots 0-13) bat nine with a DH, NL clubs eight without. Upstream's
    `verify_team_lineups` refuses anything else, and every lineup on the 1994
    cartridge matches. Arizona and Tampa Bay play only the All-Star Game, in
    Baltimore's park, so both bat nine.
    """
    nine = _is_al(slot) or slot in expansion.NAMES
    return LINEUP_POSITIONS if nine else LINEUP_POSITIONS[:-1]


_OUTFIELD = ("LF", "CF", "RF")
_INFIELD = ("2B", "3B", "SS")
_NEAR = {pos: _OUTFIELD for pos in ("OF", *_OUTFIELD)}
_NEAR |= {pos: _INFIELD for pos in ("IF", *_INFIELD)}


def _lineup_positions(own: list[str], wanted: tuple[str, ...]) -> list[str]:
    """`own`, the starters' positions in order, made to hold each of `wanted` once.

    A player keeps his own position while it is free. The others (duplicates,
    OF, IF, a DH in an NL lineup) take what is missing: outfielders and
    infielders first, from their own part of the field when it has a gap.
    """
    out = list(own)
    free = [pos for pos in wanted if pos not in own]
    spare = [i for i, pos in enumerate(own) if pos not in wanted or pos in own[:i]]
    for i in sorted(spare, key=lambda i: own[i] not in _NEAR):
        out[i] = next((pos for pos in _NEAR.get(own[i], ()) if pos in free), free[0])
        free.remove(out[i])
    return out


@register(
    "kgj-mlb-snes",
    platform="snes",
    sport="baseball",
    requires_slot_mapping=False,
    providers=("espn",),
)
class KGJMLBPatcher(Patcher):
    """Teams map to ROM slots by abbreviation, so no manual mapping step.

    30 slots, all patchable: 14 AL then 14 NL, in the 1994 league order, then
    the NL and AL All-Stars, which Arizona and Tampa Bay -- neither of which
    existed in 1994 -- take (`expansion`). `patch(all_stars=True)` keeps the
    cartridge's All-Stars instead.
    """

    #: `patch` draws each club's logo (ESPN's, or `assets_dir/logos/<team
    #: id>.png`) unless `logos=False`, which keeps the cartridge's. Names,
    #: abbreviations, divisions and uniform colours are always the club's.
    supports_logos: bool = True
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
        # User-supplied, read-only: `logos/<team_id>.png` drawn instead of the
        # provider's logo.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._transport = transport
        self.mapper = KGJStatMapper()
        self.api = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze ------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        reader = KGJRomReader(str(rom_path))
        if not reader.load():
            raise RomError(f"Cannot read ROM: {rom_path}")
        info = reader.get_info()
        is_valid = info.is_valid and _team_data_fits(reader)
        # The All-Star teams, which Arizona and Tampa Bay take.
        all_stars = [RomSlot(index=s, display_name=n) for s, n in expansion.NAMES.items()]

        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=is_valid,
            slots=[
                RomSlot(
                    index=slot.index,
                    # Labelled, because it is a player and not a team.
                    current_name=(
                        f"First player: {slot.first_player}" if slot.first_player else ""
                    ),
                    display_name=slot.name,
                )
                for slot in info.team_slots
            ]
            + (all_stars if info.team_slots else []),
            # `has_header` decides which end of the file the checksum lands at,
            # and `first_team_offset` records where the marker matched.
            extra={
                "has_header": info.has_header,
                "first_team_offset": info.first_team_offset,
            },
        )

    # -- fetch --------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        self.status("Fetching MLB teams...")
        teams = self.api.get_mlb_teams()
        if not teams:
            raise ApiError("The provider returned no MLB teams")

        # Only teams that exist as a slot in the 1994 ROM are worth fetching.
        mapped = [(t, s) for t in teams if (s := self.mapper.get_team_slot(t.code)) is not None]
        if not mapped:
            raise ApiError("No fetched team matches a Ken Griffey Jr. MLB ROM slot")

        rosters: list[TeamRoster] = []
        for i, (team, slot) in enumerate(mapped):
            if on_progress is not None:
                on_progress(i / len(mapped), f"Fetching {team.name}...")
            # The squad endpoint has no season in its URL but does have one in
            # its cache key, so the season must be passed here too or the first
            # season ever fetched is served forever.
            players = self.api.get_baseball_squad(team.id, season) or []
            # Each player's whole season line, one request each (cached): the
            # pitching line for a pitcher, the batting line for anyone else.
            lines = parallel_map(
                lambda p: self.api.get_baseball_athlete_stats(p.id, season), players
            )
            stats = {
                str(p.id): line[kind]
                for p, line in zip(players, lines, strict=True)
                if (kind := "pitching" if self.mapper.is_pitcher(p) else "batting") in line
            }
            rosters.append(
                TeamRoster(
                    team=team,
                    players=players,
                    # `lineup`: the club's recent batting order, which the
                    # default order follows. `lineup_size`: how many of the
                    # batters start, 9 in the AL and 8 in the NL; see `_lineup`.
                    extra={
                        "stats": stats,
                        "lineup": self.api.get_baseball_lineup(team.id, season),
                        "lineup_size": len(_lineup(slot)),
                    },
                )
            )

        if on_progress is not None:
            on_progress(1.0, "Complete")
        # Every field but `season` is synthesised: this game has no league
        # endpoint. `teams_count` counts the rosters actually built, the
        # slot-mapped subset of what the provider returned, not `len(teams)`.
        return LeagueData(
            league=League(
                id=0,
                name="MLB",
                country="USA",
                country_code="US",
                logo_url="",
                season=season,
                teams_count=len(rosters),
            ),
            teams=rosters,
        )

    # -- map ----------------------------------------------------------------

    def suggest_squad_order(self, team_roster):
        """The lineup in batting order, the bench, the rotation, the bullpen,
        then everyone else: what `map_rosters` writes when not told otherwise."""
        batters, starters, relievers = self._groups(team_roster)
        return self._append_unused(batters + starters + relievers, team_roster.players)

    def _groups(self, roster: TeamRoster) -> tuple[list, list, list]:
        """`select_roster_groups` for one fetched club; the one selection both
        `suggest_squad_order` and `map_rosters` make, so the two agree."""
        slot = self.mapper.get_team_slot(roster.team.code)
        return self.mapper.select_roster_groups(
            roster.players,
            _stats(roster),
            roster.extra.get("lineup") or [],
            len(_lineup(slot)) if slot is not None else len(LINEUP_POSITIONS),
        )

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
    ) -> MappedRosters:
        """Reduce league data to one `KGJTeamRecord` per matched ROM slot.

        Sparse: a key exists only for a slot some fetched team mapped to.

        Batters fill slots 0-14 and pitchers start at slot 15 (the first five
        are the rotation), whatever the squad's shape. A short block is topped
        up with whoever neither block took, mapped as the slot's kind (a spare
        pitcher bats as an IF, the way the game bats pitchers), and a batter
        slot nobody is left for is `None`: the ROM keeps its record there. The
        first batters are then re-stamped to field every position of the
        club's lineup once; see `_lineup_positions`.
        """
        self.check_slot_mapping(slot_mapping)
        teams: dict[int, KGJTeamRecord] = {}
        for roster in data.teams:
            slot = self.mapper.get_team_slot(roster.team.code)
            if slot is None or not 0 <= slot < expansion.TEAMS:
                continue
            stats = _stats(roster)
            if order_as_given:
                # The caller's order sets the lineup, the bench, the rotation
                # and the bullpen within each block.
                batters = self._given_blocks(
                    roster.players, [(lambda p: not self.mapper.is_pitcher(p), BATTERS_PER_TEAM)]
                )
                pitchers = self._given_blocks(
                    roster.players, [(self.mapper.is_pitcher, PLAYERS_PER_TEAM - BATTERS_PER_TEAM)]
                )
            else:
                batters, starters, relievers = self._groups(roster)
                pitchers = starters + relievers
            rest = self._append_unused(batters + pitchers, roster.players)
            rest = rest[len(batters) + len(pitchers) :]
            short = BATTERS_PER_TEAM - len(batters)
            batters = batters + rest[:short]
            pitchers = (pitchers + rest[short:])[: PLAYERS_PER_TEAM - BATTERS_PER_TEAM]

            hitters = [self.mapper.map_batter(p, stats.get(str(p.id), {})) for p in batters]
            lineup = hitters[: len(_lineup(slot))]
            # Most-used first, so of two starters listed at one position the
            # regular keeps it and the other moves (to DH, say).
            usage = sorted(
                range(len(lineup)),
                key=lambda i: -(_stat(stats.get(str(batters[i].id), {}), "GS") or 0),
            )
            positions = _lineup_positions([lineup[i].position for i in usage], _lineup(slot))
            for i, position in zip(usage, positions, strict=True):
                lineup[i].position = position
            arms = [
                self.mapper.map_pitcher(
                    p, stats.get(str(p.id), {}), is_starter=j < STARTERS_PER_TEAM
                )
                for j, p in enumerate(pitchers)
            ]
            gap = [None] * (BATTERS_PER_TEAM - len(hitters)) if arms else []
            records: list[KGJPlayerRecord | None] = [*hitters, *gap, *arms]
            looks.dress(records)  # again as a team: no two cards alike

            # `MODERN_MLB_TO_KGJ` maps 33 codes onto 30 slots: CWS/CHW, OAK/ATH
            # and ARI/AZ each name one slot, so two entries in
            # `data.teams` can target the same one and an empty alias arriving
            # second must not wipe a populated record.
            existing = teams.get(slot)
            if not records and existing is not None and existing.players:
                continue
            teams[slot] = KGJTeamRecord(
                index=slot,
                name=KGJ_TEAM_ORDER[slot] if slot < TEAM_COUNT else roster.team.name,
                players=records,
            )
            identity.stamp(teams[slot], roster.team)
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
        rosters.require_game(self.game_id)
        if options.get("all_stars"):
            # Keep the cartridge's All-Star teams: Arizona and Tampa Bay sit out.
            kept = {s: t for s, t in rosters.teams.items() if s not in expansion.NAMES}
            rosters = MappedRosters(game_id=rosters.game_id, teams=kept)
        # Checked before the ROM is touched: an unknown slot or an unreadable
        # picture never leaves a half-written output behind.
        screen_art = screens.load_images(options.get("images"), self.status)
        self.status("Validating ROM...")
        reader = KGJRomReader(str(rom_path))
        if not reader.load() or not reader.validate():
            raise RomError(f"Not a valid Ken Griffey Jr. MLB ROM: {rom_path}")
        if not _team_data_fits(reader):
            size = len(reader.data or b"")
            raise RomError(
                f"Not a valid Ken Griffey Jr. MLB ROM: {rom_path}: the team tables start at "
                f"{reader.first_team_offset:#x} and run {TEAM_DATA_SPAN} bytes, past the end of a "
                f"{size}-byte file"
            )

        self.status("Initializing ROM writer...")
        dress = bool(options.get("looks", True))
        writer = KGJRomWriter(str(rom_path), str(output_path), looks=dress)
        if not writer.load():
            raise RomError(f"Failed to load ROM for writing: {rom_path}")
        if dress:
            rosters = self._skin_tones(rosters)

        # `write_team_roster` guards only `team_index >= TEAM_COUNT`, so a
        # negative key would reach `get_team_offset`, compute an offset below the
        # marker, and overwrite whatever the ROM keeps there.
        targets = sorted(
            slot for slot, team in rosters.teams.items() if 0 <= slot < TEAM_COUNT and team.players
        )

        # Before the clubs: a new block starts as the cartridge's All-Stars.
        added = expansion.write(writer, rosters.teams, self.status)
        teams_patched = sum(n > 0 for n in added.values())
        players_patched = sum(added.values())
        seattle = False
        for index, slot in enumerate(targets):
            if on_progress is not None:
                on_progress(index / len(targets), f"Writing {rosters.teams[slot].name}...")
            team: KGJTeamRecord = rosters.teams[slot]
            written = writer.write_team_roster(slot, team.players)
            if written <= 0:
                # -1 is the writer's error return, 0 means no record was written.
                continue
            teams_patched += 1
            # `written`, not `len(team.players)`: `write_player` answers False for
            # a record that would run off the end of the file.
            players_patched += written
            seattle |= slot == _SEATTLE

        writer.write_derby(*_derby(rosters))
        assert writer.data is not None
        if dress and (seattle or expansion.TAMPA_BAY in added):
            # Griffey's record is gone with Seattle's, so no #24 there or on
            # the AL All-Stars is him any more. ponytail: a Seattle squad too
            # short to reach his slot would keep him without his head, and so
            # would Tampa Bay in the AL All-Stars' place with Seattle unmapped.
            looks.free_number_24(writer.data)

        # Names, divisions, the team-select order, logos and uniform colours.
        pictures = self._logos(rosters) if options.get("logos", True) else {}
        for note in identity.draw(writer.data, rosters.teams, pictures):
            self.status(note)

        if screen_art:
            self.status("Drawing screen art...")
            for note in screens.draw(writer.data, screen_art):
                self.status(note)

        if on_progress is not None:
            on_progress(1.0, "Saving patched ROM...")

        # Explicitly here, not inside `finalize`, and last: it sums what every
        # writer above wrote. See this module's docstring.
        writer.update_snes_checksum()

        self.status("Saving patched ROM...")
        if not writer.finalize():
            raise RomError(f"Failed to write patched ROM to {output_path}")

        return PatchResult(
            output_path=str(output_path),
            teams_patched=teams_patched,
            players_patched=players_patched,
        )

    # -- looks --------------------------------------------------------------

    def _skin_tones(self, rosters: MappedRosters) -> MappedRosters:
        """`rosters` with each player whose head shot shows a face dressed for
        that face's skin tone (`looks.dress`), as a copy: the caller's are left
        as mapped. Without Pillow the looks stay as mapped, which says so in a
        status line when there were head shots to measure."""
        urls = {
            record.player_id: record.photo_url
            for team in rosters.teams.values()
            for record in team.players
            if record is not None and record.photo_url
        }
        if not urls:
            return rosters
        if importlib.util.find_spec("PIL") is None:
            self.status("Pillow is not installed; player skin tones not taken from head shots")
            return rosters
        self.status("Loading head shots...")

        def measure(url: str) -> float | None:
            png = load_headshot(self.cache_dir, url, self._transport)
            try:
                return face_ita(png) if png else None
            except Exception:  # noqa: BLE001 - Pillow's errors (a decompression bomb among them) share no base
                return None

        # A whole-league batch can run past the app's stall watchdog (it kills
        # a job that stays silent for 15 minutes) on a slow connection, so a
        # status names progress every chunk.
        unique = sorted(set(urls.values()))
        angles: dict[str, float | None] = {}
        done = 0
        for at in range(0, len(unique), _HEADSHOT_CHUNK):
            batch = unique[at : at + _HEADSHOT_CHUNK]
            angles.update(zip(batch, parallel_map(measure, batch), strict=True))
            done += len(batch)
            self.status(f"Head shots {done}/{len(unique)}...")
        tones = {
            pid: tone(angle, looks.SKIN_CUTS)
            for pid, url in urls.items()
            if (angle := angles[url]) is not None
        }
        self.status(f"Skin tones from head shots: {len(tones)} of {len(urls)} players")
        if not tones:
            return rosters
        dressed = copy.deepcopy(rosters)
        for team in dressed.teams.values():
            looks.dress(team.players, tones)
        return dressed

    # -- screen art ---------------------------------------------------------

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        with as_rom_error(rom_path):
            return screens.export(Path(rom_path), Path(out_dir))

    # -- logos --------------------------------------------------------------

    def _logos(self, rosters: MappedRosters) -> dict[int, Image.Image]:
        """Each mapped slot's logo: `assets_dir/logos/<team_id>.png`, else the
        provider's (downloaded once into `cache_dir/logos`). A slot with
        neither, or one that will not load, keeps the cartridge's; without
        Pillow every slot does, with one status line when there were logos
        to draw."""
        sources = {}
        for slot, record in rosters.teams.items():
            override = (
                self.assets_dir / "logos" / f"{record.team_id}.png"
                if self.assets_dir is not None and record.team_id is not None
                else None
            )
            if override is not None and not override.is_file():
                override = None
            if override is not None or record.logo_url:
                sources[slot] = (record, override)
        if not sources:
            return {}
        try:
            from PIL import Image
        except ImportError:
            self.status("Pillow is not installed; team logos skipped")
            return {}
        pictures: dict[int, Image.Image] = {}
        for slot, (record, override) in sources.items():
            self.status(f"Fetching {record.name or record.code} logo...")
            try:
                png = override.read_bytes() if override else self._load_logo(record.logo_url)
                picture = Image.open(io.BytesIO(png))
                picture.load()
            except Exception as exc:  # noqa: BLE001 - a bad download or an unreadable image
                self.status(f"{record.name}: logo skipped: {exc}")
                continue
            pictures[slot] = picture
        return pictures

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
