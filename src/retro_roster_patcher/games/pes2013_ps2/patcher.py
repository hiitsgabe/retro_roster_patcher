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

import base64
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.errors import ApiError, CapabilityError, MappingError, RomError, as_rom_error
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
from ...sports.espn import LINEUP_GROUPS, EspnClient
from ...sports.models import LeagueData, Player, TeamRoster
from . import kit_textures
from .kits import AWAY, HOME, apply_spec
from .models import (
    CLUB_COUNT,
    CLUB_SQUAD_SIZE,
    DATABASE_OVERLAYS,
    DISPLAY_NAME_CHARS,
    GROUP_POSITIONS,
    IMAGE_SLOT_NAMES,
    IMAGE_SLOTS,
    LEAGUE_GROUPS,
    NATIONAL_NAMED,
    NATIONAL_SLOT_BASE,
    NATIONAL_SQUAD_SIZE,
    PART_PLAYERS,
    PLAYER_ENTRIES,
    PLAYER_RECORD_SIZE,
    POSITION_BYTE,
    POSITION_GROUPS,
    TEXT_AFS,
    PESPlayer,
    PESTeamRecord,
)
from .rom_reader import PlayerTables, RomReader
from .rom_writer import RomWriter, ascii_fold

GROUP_ORDER = ("Goalkeeper", "Defender", "Midfielder", "Attacker")
#: Squad places that are the club's lineup.
LINEUP_SIZE = 11
#: A 1x1 fully transparent PNG: what a blanked icon is painted with.
BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNgYGBgAAAABQABeqhXUAAAAABJRU5ErkJggg=="
)
#: Disc national-team names that ESPN spells differently (folded upper case).
NATIONAL_ALIASES = {
    "CZECH REPUBLIC": "CZECHIA",
    "BOSNIA-HERZEGOVINA": "BOSNIA AND HERZEGOVINA",
    "IVORY COAST": "COTE D'IVOIRE",
    "KOREA REPUBLIC": "SOUTH KOREA",
    "UNITED STATES": "USA",
    "UAE": "UNITED ARAB EMIRATES",
}
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
    first, last = player.first_name.strip(), (player.last_name.strip() or full.split()[-1])
    candidates = [full, f"{first[0]}. {last}" if first else "", last, last.split()[-1], first]
    for name in candidates:
        if name and len(name) <= DISPLAY_NAME_CHARS:
            return name.upper()
    return full[:DISPLAY_NAME_CHARS].upper()


#: The slot groups by league, for `Patcher.slot_groups`.
SLOT_GROUPS = tuple(
    (name, group.first, group.count)
    for name, group in zip(
        ("England", "France", "Italy", "Netherlands", "Spain"), LEAGUE_GROUPS, strict=True
    )
)
_GROUPED = LEAGUE_GROUPS[-1].first + LEAGUE_GROUPS[-1].count
SLOT_GROUPS += (("Other", _GROUPED, CLUB_COUNT - _GROUPED),)


def _image_bytes(value: Any) -> bytes | None:
    """The PNG of a `{"image": png bytes or path}` kit choice, else None."""
    if not isinstance(value, dict) or "image" not in value:
        return None
    png = value["image"]
    return Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)


def _as_spec(value: Any) -> KitSpec | None:
    """A `KitSpec` from the patch option: the object itself, its dict,
    `{"image": png bytes or path}` read with `kit_from_image`, or None."""
    if value is None or isinstance(value, KitSpec):
        return value
    value = dict(value)
    if "template" in value:
        raise CapabilityError("pes2013-ps2 has no jersey templates")
    png = _image_bytes(value)
    if png is not None:
        return kit_from_image(png)
    return KitSpec.from_dict(value)


def shirt_name(player: Player) -> str:
    """The short name on the pitch: the surname, as the disc has it for every
    player, or the display name for a player known by one name (a nickname).
    Full names in both fields cost 11 KB of room on the real disc."""
    return (player.last_name.strip() or pes_name(player)).upper()


#: ESPN lineup position codes as the game's registered-position nibble
#: (byte 53 >> 4): GK 0, CB 3, SB 4, DMF 5, CMF 7, SMF 8, AMF 9, SS 10, WF 11,
#: CF 12. The wide attacking midfielders of a 4-2-3-1 are side midfielders;
#: the wide forwards of a front three (LF/RF) are wing forwards.
LINEUP_ROLES = {
    "G": 0,
    "CD": 3,
    "SW": 3,
    "LB": 4,
    "RB": 4,
    "LWB": 4,
    "RWB": 4,
    "DM": 5,
    "CM": 7,
    "LM": 8,
    "RM": 8,
    "AM": 9,
    "AM-L": 8,
    "AM-R": 8,
    "SS": 10,
    "LW": 11,
    "RW": 11,
    "LF": 11,
    "RF": 11,
    "F": 12,
    "CF": 12,
    "ST": 12,
    "LCF": 12,
    "RCF": 12,
}


def lineup_role(player: Player, lineup: dict[str, Any] | None) -> int | None:
    """The nibble of the position the lineup had the player start in, or None
    when he did not start. A code the table lacks plays his group's position,
    so a listed starter is never sent to the bench."""
    for s in (lineup or {}).get("starters") or []:
        if int(s["id"]) == player.id:
            code = str(s.get("position", "")).upper()
            role = LINEUP_ROLES.get(code, LINEUP_ROLES.get(code.split("-")[0]))
            if role is None:
                role = GROUP_POSITIONS.get(lineup_group(player, lineup), 7)
            return role
    return None


def squad_order(players: list[Player], lineup: dict[str, Any] | None = None) -> list[Player]:
    """Starters first, each group in formation order, then the bench by group.

    `lineup` is what `EspnClient.get_lineup` returns (or `TeamRoster.extra["lineup"]`);
    without it the squad is just grouped goalkeepers, defenders, midfielders,
    attackers, keeping the provider's order within a group.
    """
    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    starters: dict[int, tuple[int, int]] = {}
    for s in (lineup or {}).get("starters") or []:
        group = LINEUP_GROUPS.get(str(s.get("position", "")).upper())
        starters[int(s["id"])] = (rank.get(group, 2) if group else 2, int(s.get("place") or 0))
    by_id = {p.id: p for p in players}
    first = [by_id[i] for i, _ in sorted(starters.items(), key=lambda kv: kv[1]) if i in by_id]
    rest = sorted(
        (p for p in players if p.id not in starters), key=lambda p: rank.get(player_group(p), 2)
    )
    return first + rest


def lineup_group(player: Player, lineup: dict[str, Any] | None) -> str:
    """The player's group, from the lineup position when he started, else ESPN's."""
    for s in (lineup or {}).get("starters") or []:
        if int(s["id"]) == player.id:
            return LINEUP_GROUPS.get(str(s.get("position", "")).upper(), player_group(player))
    return player_group(player)


@register(
    "pes2013-ps2",
    platform="ps2",
    sport="soccer",
    requires_slot_mapping=True,
    providers=("espn",),
)
class PES2013PS2Patcher(Patcher):
    #: `patch` draws each team's logo into the emblem atlases unless `logos=False`.
    supports_logos: bool = True
    supports_blank_unused: bool = True
    #: `patch(kits=...)` as `Patcher` describes it; `{"template": n}` is refused,
    #: the disc has no jersey designs to pick from.
    supports_kits: bool = True
    slot_groups: tuple[tuple[str, int, int], ...] = SLOT_GROUPS
    supports_nationals: bool = True
    national_slot_base: int | None = NATIONAL_SLOT_BASE
    #: Screen art `patch(images={name: png})` can replace; `export_images`
    #: writes the current art of each as a PNG to edit.
    image_slots: tuple[str, ...] = IMAGE_SLOT_NAMES

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
        # User-supplied, read-only: `logos/<team_id>.png` crests drawn instead
        # of the provider's logo.
        self.assets_dir = Path(assets_dir) if assets_dir is not None else None
        self._transport = transport
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
            ]
            + [
                RomSlot(
                    index=NATIONAL_SLOT_BASE + n.index,
                    current_name=n.name,
                    display_name=f"National: {n.name} ({n.code})",
                )
                for n in info.nationals
            ],
            extra={"player_count": info.player_count, "national_slot_base": NATIONAL_SLOT_BASE},
        )

    def national_slot_map(self, rom_path: Path, data: LeagueData) -> list[SlotMapping]:
        """Pair the disc's national teams with `data`'s teams by code, then by
        name, for `map_rosters`. Fetch league 2021 (international friendlies)
        for every side ESPN tracks."""
        with as_rom_error(rom_path):
            info = RomReader(str(rom_path)).get_rom_info()
        by_code = {t.team.code.upper(): t.team for t in data.teams if t.team.code}
        by_name = {ascii_fold(t.team.name).upper(): t.team for t in data.teams}
        out = []
        for slot in info.nationals:
            team = by_code.get(slot.code.upper()) or by_name.get(
                NATIONAL_ALIASES.get(ascii_fold(slot.name).upper(), ascii_fold(slot.name).upper())
            )
            if team is not None:
                out.append(
                    SlotMapping(
                        slot_index=NATIONAL_SLOT_BASE + slot.index,
                        team_id=team.id,
                        team_name=team.name,
                    )
                )
        return out

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
        if not league.logo_url and hasattr(self.api, "get_league_logo"):
            league = replace(league, logo_url=self.api.get_league_logo(league_id))
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
                # The latest match's starting eleven decides who fills the
                # first squad places; optional, and plain data for the JSON
                # boundary.
                if hasattr(self.api, "get_lineup"):
                    lineup = self.api.get_lineup(team.id, league_id)
                    if lineup:
                        roster.extra["lineup"] = lineup
            except ApiError as exc:
                roster.error = str(exc)
            rosters.append(roster)
        if on_progress is not None:
            on_progress(1.0, "Done")
        return LeagueData(league=league, teams=rosters)

    def suggest_squad_order(self, team_roster: TeamRoster) -> list[Player]:
        return squad_order(team_roster.players, team_roster.extra.get("lineup"))

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
            national = entry.slot_index >= NATIONAL_SLOT_BASE
            if not (
                0 <= entry.slot_index < CLUB_COUNT
                or NATIONAL_SLOT_BASE <= entry.slot_index < NATIONAL_SLOT_BASE + NATIONAL_NAMED
            ):
                raise MappingError(
                    f"Slot {entry.slot_index} is outside the club range 0..{CLUB_COUNT - 1} "
                    f"and the national range {NATIONAL_SLOT_BASE}.."
                    f"{NATIONAL_SLOT_BASE + NATIONAL_NAMED - 1}"
                )
            roster = by_id.get(entry.team_id)
            if roster is None:
                raise MappingError(
                    f"Slot {entry.slot_index} maps to team {entry.team_id}, "
                    f"which is not in the fetched league data"
                )
            lineup = roster.extra.get("lineup")
            players = roster.players if order_as_given else squad_order(roster.players, lineup)
            home_kit, away_kit = default_kits(roster.team)
            teams[entry.slot_index] = PESTeamRecord(
                # The game's own names are upper case throughout.
                name=roster.team.name.upper(),
                code=(roster.team.code or ascii_fold(roster.team.name)[:3]).upper(),
                players=[
                    PESPlayer(
                        display_name=pes_name(p),
                        shirt_name=shirt_name(p),
                        number=p.number,
                        position=lineup_group(p, lineup),
                        role=lineup_role(p, lineup),
                    )
                    for p in players[: NATIONAL_SQUAD_SIZE if national else CLUB_SQUAD_SIZE]
                ],
                team_id=roster.team.id,
                logo_url=roster.team.logo_url,
                league_name=data.league.name,
                league_logo_url=data.league.logo_url,
                national=national,
                kit_home=home_kit,
                kit_away=away_kit,
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
        # Every kit choice is read and checked before the disc is copied, so a
        # bad picture is refused up front instead of after a 2 GB write.
        kit_choices = self._resolve_kits(rosters, options.get("kits", True))
        with as_rom_error(rom_path):
            writer = RomWriter(str(rom_path), str(output_path))
            self.status("Copying ISO...")
            writer.copy_iso(on_progress)
            reader = writer.reader
            tables = {index: reader.player_tables(index) for index in PLAYER_ENTRIES}
            primary = tables[PLAYER_ENTRIES[0]]
            slots = sorted(slot for slot in rosters.teams if 0 <= slot < CLUB_COUNT)
            national_slots = sorted(
                slot
                for slot in rosters.teams
                if NATIONAL_SLOT_BASE <= slot < NATIONAL_SLOT_BASE + NATIONAL_NAMED
            )
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
            written, blocks = self._fit_player_tables(writer, tables, rosters, national_slots)
            players_patched += written
            for index, t in tables.items():
                writer.write_player_tables(index, t, blocks[index])
            blank_unused = bool(options.get("blank_unused", False))
            if blank_unused:
                # Before the logos: a blanked crest frees its palette entries.
                names.update(self._blank_unused(writer, rosters, slots, national_slots))
            if options.get("logos", True):
                self._write_logos(writer, rosters, slots, on_progress)
            self._write_leagues(writer, rosters, slots, options.get("logos", True), blank_unused)
            if kit_choices is not None:
                self._write_kits(writer, rosters, slots + national_slots, kit_choices)
            images = self._collect_images(options.get("images"))
            if images:
                self._write_images(writer, images)
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
            teams_patched=len(slots) + len(national_slots),
            players_patched=players_patched,
        )

    def _fit_player_tables(
        self,
        writer: RomWriter,
        tables: dict[int, PlayerTables],
        rosters: MappedRosters,
        national_slots: list[int],
    ) -> tuple[int, dict[int, bytes]]:
        """Write the national squads and deflate both player entries into their
        room; returns national players written and the built entries.

        Real names compress worse than the disc's, so a full patch outgrows
        the room. What gives, in order: the records no squad points at are
        blanked (names and abilities, ~9 KB on the real disc), then the
        national squads take fewer of those free records.
        """
        snapshot = {index: list(t.parts) for index, t in tables.items()}
        limit: int | None = None
        while True:
            written = pool_used = 0
            if national_slots:
                written, pool_used = self._write_nationals(tables, rosters, national_slots, limit)
            blocks = {index: t.build() for index, t in tables.items()}
            over = max(len(b) - writer.room(TEXT_AFS, index) for index, b in blocks.items())
            if over > 0:
                blanked = self._blank_free_records(tables)
                blocks = {index: t.build() for index, t in tables.items()}
                over = max(len(b) - writer.room(TEXT_AFS, index) for index, b in blocks.items())
                self.status(f"{blanked} records no squad uses blanked to make room")
            if over <= 0:
                return written, blocks
            if not pool_used:
                raise RomError(
                    f"{TEXT_AFS} player entries need {over} bytes more than their room; "
                    "the new names did not compress small enough"
                )
            limit = int(pool_used * 0.8)
            self.status(f"Player tables {over} bytes too big; nationals get {limit} free records")
            for index, t in tables.items():
                t.parts = list(snapshot[index])

    @staticmethod
    def _blank_free_records(tables: dict[int, PlayerTables]) -> int:
        """Zero every record no club or national squad points at, in every
        entry; returns how many."""
        used = tables[PLAYER_ENTRIES[0]].referenced()
        free = [i for i in range(1, tables[PLAYER_ENTRIES[0]].player_count) if i not in used]
        for t in tables.values():
            players = bytearray(t.parts[PART_PLAYERS])
            for i in free:
                players[i * PLAYER_RECORD_SIZE : (i + 1) * PLAYER_RECORD_SIZE] = bytes(
                    PLAYER_RECORD_SIZE
                )
            t.parts[PART_PLAYERS] = bytes(players)
        return len(free)

    def _write_nationals(
        self,
        tables: dict[int, PlayerTables],
        rosters: MappedRosters,
        slots: list[int],
        pool_limit: int | None = None,
    ) -> tuple[int, int]:
        """Give each national row its new squad without touching a club.

        A national squad shares records with the clubs its players play for,
        and renaming such a record would rename the club's player too. So a
        row only takes over the records no club squad points at: its own
        exclusive ones first, then named records no squad uses at all (a pool
        the rows share, at most `pool_limit` of them, spread over the rows).
        Records it must keep stay at the end of the row with their old names.
        Each record taken over gets the player's position. Returns players
        written and pool records used.
        """
        primary = tables[PLAYER_ENTRIES[0]]
        club_used = primary.club_referenced()
        pool = sorted(
            i
            for i in range(1, primary.player_count)
            if i not in primary.referenced() and (primary.record(i)[0] or primary.record(i)[2])
        )
        if pool_limit is not None:
            pool = pool[:pool_limit]
        per_row = -(-len(pool) // max(len(slots), 1))
        written = pool_used = 0
        for slot in slots:
            record = rosters.teams[slot]
            row = slot - NATIONAL_SLOT_BASE
            current = [idx for idx in primary.national_squad(row) if 0 < idx < primary.player_count]
            exclusive = [idx for idx in current if idx not in club_used]
            kept = [idx for idx in current if idx in club_used]
            wanted = len(record.players)
            take = exclusive[:wanted]
            share = per_row
            while len(take) < wanted and pool and share:
                take.append(pool.pop(0))
                share -= 1
                pool_used += 1
            new_row: list[int] = []
            for player, idx in zip(record.players, take, strict=False):
                for t in tables.values():
                    RomWriter.set_player_names(t, idx, player.display_name, player.shirt_name)
                    RomWriter.set_position(
                        t,
                        idx,
                        player.role
                        if player.role is not None
                        else GROUP_POSITIONS.get(player.position, 7),
                    )
                new_row.append(idx)
                written += 1
            leftovers = [idx for idx in exclusive if idx not in take] + kept
            new_row += leftovers[: NATIONAL_SQUAD_SIZE - len(new_row)]
            new_row += [0] * (NATIONAL_SQUAD_SIZE - len(new_row))
            for t in tables.values():
                t.set_national_squad(row, new_row)
            if len(take) < wanted:
                self.status(
                    f"{record.name}: {wanted - len(take)} players left out, no free records"
                )
        return written, pool_used

    def _blank_unused(
        self,
        writer: RomWriter,
        rosters: MappedRosters,
        slots: list[int],
        national_slots: list[int],
    ) -> dict[int, tuple[str, str]]:
        """Names to empty and crests to clear for the slots the mapping left
        out: clubs when the mapping has clubs, nationals when it has nationals.
        Returns the empty names for the overlay writer."""
        names: dict[int, tuple[str, str]] = {}
        if slots:
            unused = [slot for slot in range(CLUB_COUNT) if slot not in rosters.teams]
            names.update({slot: ("", "") for slot in unused})
            cleared = writer.blank_emblems(unused)
            self.status(f"Unused clubs blanked: {len(unused)} names, {cleared} crests")
        if national_slots:
            unused = [
                NATIONAL_SLOT_BASE + row
                for row in range(NATIONAL_NAMED)
                if NATIONAL_SLOT_BASE + row not in rosters.teams
            ]
            names.update({slot: ("", "") for slot in unused})
            self.status(f"Unused national teams blanked: {len(unused)} names")
        return names

    def _write_logos(
        self,
        writer: RomWriter,
        rosters: MappedRosters,
        slots: list[int],
        on_progress: ProgressFn | None,
    ) -> None:
        """Draw each mapped team's logo as the club's crest: the user's
        `assets_dir/logos/<team_id>.png` when there is one, else a download.

        Without Pillow, or with a logo that will not download, the crests are
        skipped with a status line; the roster patch stands.
        """
        logos: dict[int, bytes] = {}
        for i, slot in enumerate(slots):
            record = rosters.teams[slot]
            override = self._logo_override(record)
            if override is None and not record.logo_url:
                continue
            if on_progress is not None:
                on_progress(0.85 + 0.1 * (i / max(len(slots), 1)), f"Logo for {record.name}...")
            try:
                logos[slot] = (
                    override.read_bytes() if override else self._load_logo(record.logo_url)
                )
            except ApiError as exc:
                self.status(f"{record.name}: logo skipped: {exc}")
        if not logos:
            return
        try:
            count = writer.write_emblems(logos)
        except ImportError:
            self.status("Pillow is not installed; team logos skipped")
            return
        self.status(f"Team logos drawn: {count}")

    def export_images(self, rom_path: Path, out_dir: Path) -> list[Path]:
        """Write each slot's current art as `<out_dir>/<slot>.png`, to edit and feed back."""
        if not Path(rom_path).exists():
            raise RomError(f"ROM not found: {rom_path}")
        reader = RomReader(str(rom_path))
        with as_rom_error(rom_path):
            if not reader.load():
                raise RomError(f"Not a PES 2013 (PS2) image: {rom_path}")
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            written = []
            for slot, texture in IMAGE_SLOTS.items():
                target = out / f"{slot}.png"
                target.write_bytes(reader.texture_png(texture))
                written.append(target)
        return written

    def _collect_images(self, given: Any) -> dict[str, bytes]:
        """Screen art to write: `patch(images=...)`, slot name to PNG path or bytes."""
        images: dict[str, bytes] = {}
        for name, png in dict(given or {}).items():
            if name not in IMAGE_SLOTS:
                raise CapabilityError(
                    f"Unknown image slot {name!r}. Slots: {', '.join(IMAGE_SLOTS)}"
                )
            images[name] = Path(png).read_bytes() if isinstance(png, str | Path) else bytes(png)
        return images

    def _write_images(self, writer: RomWriter, images: dict[str, bytes]) -> None:
        for slot, png in images.items():
            try:
                count = writer.write_texture(IMAGE_SLOTS[slot], png)
            except ImportError:
                self.status("Pillow is not installed; screen images skipped")
                return
            self.status(f"Screen image {slot}: {count} bundles")

    def _write_leagues(
        self,
        writer: RomWriter,
        rosters: MappedRosters,
        slots: list[int],
        logos: bool,
        blank_unused: bool = False,
    ) -> None:
        """Give each slot group that received teams the league's name and icon;
        with `blank_unused`, groups that received none lose both."""
        names: dict[int, str] = {}
        icons: dict[str, str] = {}
        for group in LEAGUE_GROUPS:
            record = next((rosters.teams[s] for s in slots if group.holds(s)), None)
            if record is None:
                if blank_unused and slots:
                    names[group.name_offset] = ""
                    icons[group.icon] = ""
                continue
            if record.league_name:
                names[group.name_offset] = record.league_name.upper()
            if record.league_logo_url:
                icons[group.icon] = record.league_logo_url
        if names:
            tables = writer.write_league_names(names)
            self.status(f"League names {sorted(names.values())}: {tables} tables")
        if not logos:
            return
        for icon, url in icons.items():
            try:
                png = self._load_logo(url) if url else BLANK_PNG
                count = writer.write_league_icon(icon, png)
            except ApiError as exc:
                self.status(f"League logo skipped: {exc}")
                continue
            except ImportError:
                self.status("Pillow is not installed; league logos skipped")
                return
            self.status(f"League icon {icon}: {count} bundles" + (" (blank)" if not url else ""))

    @staticmethod
    def _resolve_kits(rosters: MappedRosters, given: Any) -> dict[int, dict[int, Any]] | None:
        """Every mapped team's kit choices, read and checked.

        `given` is the `kits` patch option: False keeps the disc's kits (None);
        anything else draws the record's default kits (the provider's two
        colours, plain) except where a dict `{slot: {"home": KIT, "away": KIT}}`
        names the slot. A KIT is a `KitSpec`, its dict or `{"image": png}`; a
        picture of the texture's own size (512x256) is kept as `("texture", png,
        fallback spec)` for `_write_kits`, anything else becomes `("spec",
        KitSpec, None)`. A choice that cannot be read is a `CapabilityError`
        naming the slot.
        """
        if given is False:
            return None
        overrides = given if isinstance(given, dict) else {}
        resolved: dict[int, dict[int, Any]] = {}
        for slot, record in rosters.teams.items():
            chosen = overrides.get(slot) or overrides.get(str(slot)) or {}
            entry: dict[int, Any] = {}
            for which, side, default in (
                (HOME, "home", record.kit_home),
                (AWAY, "away", record.kit_away),
            ):
                value = chosen.get(side)
                try:
                    png = _image_bytes(value)
                    if png is not None and kit_textures.is_texture_png(png):
                        # Teams without a licensed kit get the picture's colours
                        # instead; a flat picture has none and keeps the default.
                        try:
                            fallback: KitSpec | None = kit_from_image(png)
                        except ValueError:
                            fallback = default
                        entry[which] = ("texture", png, fallback)
                    else:
                        entry[which] = ("spec", _as_spec(value) or default, None)
                except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
                    raise CapabilityError(f"slot {slot} {side} kit: {exc}") from exc
            resolved[slot] = entry
        return resolved

    def _write_kits(
        self,
        writer: RomWriter,
        rosters: MappedRosters,
        slots: list[int],
        choices: dict[int, dict[int, Any]],
    ) -> None:
        """Draw each mapped team's home and away kits (see `_resolve_kits`).

        A spec is written into the kit record, which becomes parametric: the
        game composes it from the colours, licensed texture or not. A texture
        picture for a team with a licensed kit is painted into that kit's
        package (`kit_textures`) and the record stays licensed; on a team
        without one it gives its colours and pattern instead. Clubs past the
        last kit record (slots 130 and up) are skipped with a status line.
        """
        table = writer.reader.kits()
        licensed = kit_textures.licensed_slots(writer.reader)
        written = textures = 0
        for slot in slots:
            record = rosters.teams[slot]
            if slot >= NATIONAL_SLOT_BASE:
                rows, row = table.nationals, slot - NATIONAL_SLOT_BASE
            else:
                rows, row = table.clubs, slot
            if row >= len(rows):
                self.status(f"{record.name}: slot {slot} has no kit record, kit kept")
                continue
            kits = rows[row]
            for which, (kind, value, fallback) in choices.get(slot, {}).items():
                spec = value
                if kind == "texture":
                    if slot in licensed:
                        kit_textures.write_kit_png(writer, slot, which, value)
                        textures += 1
                        continue
                    spec = fallback
                if spec is not None:
                    apply_spec(kits[which], spec)
                    written += 1
        if written:
            writer.write_kits(table)
        if written or textures:
            self.status(f"Kits written: {written}, kit textures painted: {textures}")

    def _logo_override(self, record: PESTeamRecord) -> Path | None:
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

    @staticmethod
    def _assign(
        tables: PlayerTables, club: int, players: list[PESPlayer]
    ) -> list[tuple[int, int, PESPlayer]]:
        """Pair each incoming player with a record of the club's squad.

        The first eleven squad places are the club's lineup, and each record
        there holds the position its place plays in the club's formation. A
        starter (a player with a `role`) takes the place whose record has his
        position, else one of his group, else any place still free in the
        eleven that he can play, else he joins the bench. In the eleven a
        keeper plays only in goal and an outfielder never there (two keepers
        in a merged lineup, or none, must not put one up front or a forward
        in goal). The places of the eleven still free go to the first bench
        player of their group, then any left to the first bench player who can
        play them, so the disc's old player stays in the lineup only when the
        bench has nobody for it. The bench then takes the remaining
        records by position group. Whoever is still left (more keepers than
        the club has keeper records, say) takes a free bench place; one who
        fits nowhere is not written. Returns (squad place, player index,
        player) triples.
        """
        squad = [
            (s, idx)
            for s, idx in enumerate(tables.club_squad(club))
            if 0 < idx < tables.player_count
        ]
        free = {s: idx for s, idx in squad}
        out: list[tuple[int, int, PESPlayer]] = []

        def take(player: PESPlayer, places: list[int]) -> bool:
            for s in places:
                if s in free:
                    out.append((s, free.pop(s), player))
                    return True
            return False

        def role_of(s: int) -> int:
            return tables.record(free[s])[POSITION_BYTE] >> 4

        eleven = [s for s, _ in squad[:LINEUP_SIZE]]

        def can_play(s: int, player: PESPlayer) -> bool:
            keeper = player.position == "Goalkeeper"
            return s not in eleven or (role_of(s) == 0) == keeper

        starters = [p for p in players if p.role is not None][:LINEUP_SIZE]
        bench = [p for p in players if p not in starters]
        for player in starters:
            same_role = [s for s in eleven if s in free and role_of(s) == player.role]
            same_group = [
                s
                for s in eleven
                if s in free and position_group(tables.record(free[s])) == player.position
            ]
            anywhere = [s for s in eleven if s in free and can_play(s, player)]
            if not take(player, same_role + same_group + anywhere):
                bench.append(player)
        for by_group in (True, False):
            for s in [s for s in eleven if s in free]:
                group = position_group(tables.record(free[s]))
                fits = [p for p in bench if (p.position == group if by_group else can_play(s, p))]
                if fits:
                    take(fits[0], [s])
                    bench.remove(fits[0])
        waiting: list[PESPlayer] = []
        for player in bench:
            same_group = [
                s for s in sorted(free) if position_group(tables.record(free[s])) == player.position
            ]
            if not take(player, same_group):
                waiting.append(player)
        for player in waiting:
            take(player, [s for s in sorted(free) if can_play(s, player)])
        out.sort(key=lambda t: t[0])
        return out
