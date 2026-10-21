"""PES 2013 (PS2) on the unified Patcher interface: real clubs and squads into
the game's 139 club slots.

What a slot write changes: the club's name and three-letter code in every
language overlay, and the names and shirt numbers of the players its squad
already points at. Each incoming player takes over a record of the same
position group (goalkeeper, defender, midfielder, attacker), so the game keeps
a plausible lineup; the record's abilities are left as they were. Slots the
squad does not fill keep their original players.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ...core.errors import ApiError, MappingError, RomError, as_rom_error
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
from ...sports.models import LeagueData, Player, TeamRoster
from .models import (
    CLUB_COUNT,
    CLUB_SQUAD_SIZE,
    DATABASE_OVERLAYS,
    DISPLAY_NAME_CHARS,
    PLAYER_ENTRIES,
    POSITION_BYTE,
    POSITION_GROUPS,
    PESPlayer,
    PESTeamRecord,
)
from .rom_reader import PlayerTables, RomReader
from .rom_writer import RomWriter, ascii_fold

GROUP_ORDER = ("Goalkeeper", "Defender", "Midfielder", "Attacker")
#: ESPN spells attackers two ways across leagues.
ESPN_GROUPS = {"Forward": "Attacker", "Attacker": "Attacker"}


def position_group(record: bytes) -> str:
    return POSITION_GROUPS.get(record[POSITION_BYTE] >> 4, "Midfielder")


def player_group(player: Player) -> str:
    return ESPN_GROUPS.get(player.position, player.position) or "Midfielder"


def pes_name(player: Player) -> str:
    """The name the game shows: the full name when it fits the 15-character
    field (Brazilian players go by nickname or first name), else initial and
    surname, else the surname alone."""
    full = player.name.strip() or player.last_name
    if len(full) <= DISPLAY_NAME_CHARS:
        return full.upper()
    first, last = player.first_name.strip(), (player.last_name.strip() or full.split()[-1])
    short = f"{first[0]}. {last}" if first else last
    return (short if len(short) <= DISPLAY_NAME_CHARS else last).upper()


def squad_order(players: list[Player]) -> list[Player]:
    """Goalkeepers, defenders, midfielders, attackers; stable within a group."""
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    return sorted(players, key=lambda p: rank.get(player_group(p), 2))


@register(
    "pes2013-ps2",
    platform="ps2",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class PES2013PS2Patcher(Patcher):
    def __init__(
        self,
        cache_dir: Path | str,
        *,
        provider: str | None = None,
        on_status: StatusFn | None = None,
        on_partial: PartialFn | None = None,
        transport: _http.Transport | None = None,
    ) -> None:
        super().__init__(cache_dir, provider=provider, on_status=on_status, on_partial=on_partial)
        self.api: Any = EspnClient(str(self.cache_dir), on_status, transport=transport)

    # -- analyze -------------------------------------------------------------

    def analyze_rom(self, rom_path: Path) -> RomInfo:
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        with as_rom_error(rom_path):
            info = RomReader(str(rom_path)).get_rom_info()
        return RomInfo(
            path=info.path,
            size=info.size,
            game_id=self.game_id,
            is_valid=info.is_valid,
            slots=[
                RomSlot(index=c.index, current_name=c.name, display_name=f"{c.name} ({c.code})")
                for c in info.clubs
            ],
            extra={"player_count": info.player_count},
        )

    # -- fetch ---------------------------------------------------------------

    def fetch(
        self,
        *,
        season: int,
        league_id: int | None = None,
        on_progress: ProgressFn | None = None,
    ) -> LeagueData:
        if league_id is None:
            raise ApiError("pes2013-ps2 requires a league_id; there is no default league")
        if on_progress is not None:
            on_progress(0.05, "Fetching league info...")
        league = next(iter(self.api.get_leagues(id=league_id, season=season)), None)
        if league is None:
            raise ApiError(f"League {league_id} not found for season {season}")
        if on_progress is not None:
            on_progress(0.1, f"Fetching teams for {league.name}...")
        teams = self.api.get_teams(league_id, season)
        if not teams:
            raise ApiError(f"League {league_id} has no teams for season {season}")
        self.partial(
            LeagueData(league=league, teams=[TeamRoster(team=t, loading=True) for t in teams])
        )
        rosters: list[TeamRoster] = []
        for i, team in enumerate(teams):
            if on_progress is not None:
                on_progress(0.1 + 0.8 * (i / len(teams)), f"Fetching {team.name}...")
            roster = TeamRoster(team=team)
            try:
                roster.players = self.api.get_squad(team.id, season)
            except ApiError as exc:
                roster.error = str(exc)
            rosters.append(roster)
        if on_progress is not None:
            on_progress(1.0, "Done")
        return LeagueData(league=league, teams=rosters)

    def suggest_squad_order(self, team_roster: TeamRoster) -> list[Player]:
        return squad_order(team_roster.players)

    # -- map -----------------------------------------------------------------

    def map_rosters(
        self,
        data: LeagueData,
        slot_mapping: list[SlotMapping] | None = None,
        *,
        order_as_given: bool = False,
    ) -> MappedRosters:
        self.check_slot_mapping(slot_mapping)
        by_id = {roster.team.id: roster for roster in data.teams}
        teams: dict[int, PESTeamRecord] = {}
        for entry in slot_mapping or []:
            if not 0 <= entry.slot_index < CLUB_COUNT:
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the club range 0..{CLUB_COUNT - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            players = roster.players if order_as_given else squad_order(roster.players)
            teams[entry.slot_index] = PESTeamRecord(
                # The game's own names are upper case throughout.
                name=roster.team.name.upper(),
                code=(roster.team.code or ascii_fold(roster.team.name)[:3]).upper(),
                players=[
                    PESPlayer(
                        display_name=pes_name(p),
                        shirt_name=pes_name(p),
                        number=p.number,
                        position=player_group(p),
                    )
                    for p in players[:CLUB_SQUAD_SIZE]
                ],
                team_id=roster.team.id,
                logo_url=roster.team.logo_url,
            )
        return MappedRosters(game_id=self.game_id, teams=teams)

    # -- patch ---------------------------------------------------------------

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
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        with as_rom_error(rom_path):
            writer = RomWriter(str(rom_path), str(output_path))
            self.status("Copying ISO...")
            writer.copy_iso(on_progress)
            reader = writer.reader
            tables = {index: reader.player_tables(index) for index in PLAYER_ENTRIES}
            primary = tables[PLAYER_ENTRIES[0]]
            slots = sorted(slot for slot in rosters.teams if 0 <= slot < CLUB_COUNT)
            names: dict[int, tuple[str, str]] = {}
            players_patched = 0
            for i, slot in enumerate(slots):
                record = rosters.teams[slot]
                if on_progress is not None:
                    on_progress(0.4 + 0.4 * (i / max(len(slots), 1)), f"Writing {record.name}...")
                names[slot] = (record.name, record.code)
                assignments = self._assign(primary, slot, record.players)
                for squad_slot, index, player in assignments:
                    for t in tables.values():
                        RomWriter.set_player_names(t, index, player.display_name, player.shirt_name)
                        if player.number:
                            RomWriter.set_club_number(t, slot, squad_slot, player.number)
                players_patched += len(assignments)
            if on_progress is not None:
                on_progress(0.8, "Compressing player tables...")
            for index, t in tables.items():
                writer.write_player_tables(index, t)
            if on_progress is not None:
                on_progress(0.9, "Writing team names...")
            for overlay in DATABASE_OVERLAYS:
                try:
                    teams = reader.overlay_teams(overlay)
                except RomError:
                    continue  # an overlay without a team table is not a database
                writer.write_team_names(teams, names)
            if on_progress is not None:
                on_progress(1.0, "Done")
        return PatchResult(
            output_path=str(output_path),
            teams_patched=len(slots),
            players_patched=players_patched,
        )

    @staticmethod
    def _assign(
        tables: PlayerTables, club: int, players: list[PESPlayer]
    ) -> list[tuple[int, int, PESPlayer]]:
        """Pair each incoming player with a record of the club's squad.

        Records are handed out by position group first, then whatever is left,
        in squad order. Returns (squad slot, player index, player) triples.
        """
        squad = [
            (s, idx)
            for s, idx in enumerate(tables.club_squad(club))
            if 0 < idx < tables.player_count
        ]
        free: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for s, idx in squad:
            free[position_group(tables.record(idx))].append((s, idx))
        out: list[tuple[int, int, PESPlayer]] = []
        waiting: list[PESPlayer] = []
        for player in players:
            pool = free.get(player.position)
            if pool:
                s, idx = pool.pop(0)
                out.append((s, idx, player))
            else:
                waiting.append(player)
        rest = sorted((pair for pool in free.values() for pair in pool), key=lambda p: p[0])
        for player, (s, idx) in zip(waiting, rest, strict=False):
            out.append((s, idx, player))
        out.sort(key=lambda t: t[0])
        return out
