"""Maps real-world player stats onto ISS's nine ability fields.

Every field lands inside the range the 390 retail non-Super-Star records use,
never the full 0-15 scale: the kick-off condition takes up to 5 off every field
and speeds floor at 0x1C, so a value below retail plays worse than it reads.
The top value (15, or 7 on the 3-bit fields) is kept for the top few percent.

  * shooting 1-7: goals and shots on target.
  * technique 1-7: pass accuracy, averaged with dribble success when supplied.
  * stamina 7-15: minutes per appearance.
  * kick 2-7: passes per game, else the mean of shooting and technique.
  * offense / defense: the position's retail slot median, moved by up to 2 with
    the (goals + assists) and (tackles + interceptions) per game percentiles,
    ranked within the position so the median player keeps the baseline.
    Defense stays at the baseline when tackles are not supplied.
  * speed: nothing ESPN reports measures pace, so `dash` is the position's
    baseline with an age rule, `ball_dash` one less and `dribble` three less,
    which keeps retail's order dribble <= ball_dash <= dash.

A player with no appearances gets the retail medians, his position's offense
and defense, and the same speed rule.

The ROM has no formation id: a team's formation is a record of eleven places,
and the team's player k stands in place k. So a club is written a record
(`FORMATION_DONORS`) of the shape nearest its own and its fifteen are ordered
for it: the keeper, then the shape's defenders, midfielders and forwards, each
line seated on the record's places of that line by role, the sides across the
pitch and the deeper role deeper (`seat_line`). The suggested eleven goes in that order: its
outfield back to front by the depth of its roles (`DEPTH`), so cutting it by index puts each in the
line he plays, and each line from the left by the side of its lineup codes. A roster with neither a
lineup nor a role keeps the lines of the group fill, as it did before roles.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable

from ...core.shirt_numbers import shirt_numbers
from ...sports.espn import LINEUP_GROUPS
from ...sports.lineup import (
    DEPTH,
    GROUP_ROLE,
    lineup_role,
    nearest_shape,
    seat_line,
    slot_side,
    team_formation,
)
from ...sports.models import Player, PlayerStats, TeamRoster
from .models import (
    PLAYERS_PER_TEAM,
    ISSPlayerAttributes,
    ISSPlayerRecord,
    ISSTeamRecord,
)
from .rom_writer import _to_ascii

#: The cartridge's formation records, one per shape `ISSPatcher.formations` offers, in that order
#: (`nearest_shape` ends a tie on the first). Each is 44 bytes: eleven places of four, the keeper
#: first, then the defence, midfield and attack. A place is a depth from its own goal line (the
#: match anchor is the depth times 32), a lateral place in the high nibble of the second byte
#: (1 to 13, the low ones the team's left) with the line in its low two bits (0 keeper, 1 defence,
#: 2 midfield, 3 attack), a role code and two range nibbles. Place k is the team's player k.
#:
#: Constants, not read from the image: the donor slot may itself have been patched. Each is the
#: retail record of the slot named, byte for byte (a test holds them to the cartridge).
FORMATION_DONORS: dict[str, bytes] = {
    # Wales' flat 4-4-2.
    "4-4-2": bytes.fromhex(
        "00700037 0b210132 0a510242 0a910242 0bc10132 "
        "17220436 16520436 16920436 17c20436 22530600 22930600"
    ),
    # England's 4-3-3.
    "4-3-3": bytes.fromhex(
        "00700037 0e210132 0a510242 0a910242 0ec10132 "
        "14720374 18220454 18c20454 22230634 22c30630 24730630"
    ),
    # Holland's 3-4-3.
    "3-4-3": bytes.fromhex(
        "00700037 0a310242 09710242 0ab10242 12720375 "
        "16220454 16c20454 1a720573 20230600 20c30600 22730600"
    ),
    # Germany's 5-3-2, with a sweeper.
    "5-3-2": bytes.fromhex(
        "00700037 10110132 0a410242 09710732 0aa10242 "
        "10d10132 14720374 18320544 18b20544 22430600 22a30600"
    ),
    # Sweden's 4-5-1: two deep, two wide and one attacking midfielder.
    "4-5-1": bytes.fromhex(
        "00700037 0c210132 0a510242 0a910242 0cc10132 "
        "12520354 12920354 18220454 18c20484 1e820530 24630630"
    ),
    # Nigeria's 3-5-2.
    "3-5-2": bytes.fromhex(
        "00700037 0c310242 0a710242 0cb10242 12220354 "
        "12c20354 16720474 1a320544 1ab20544 22430630 22a30630"
    ),
    # Super Star's 4-2-4.
    "4-2-4": bytes.fromhex(
        "00700037 0a210132 0a510742 0a910242 0ac10132 "
        "16420578 16a20478 22230630 22530630 22930630 22c30630"
    ),
}

#: The shape of each slot's retail record (`TEAM_ENUM_ORDER`): what a club with no formation of its
#: own, and no lineup to say, is written when the mapper picks its eleven. An order the caller gives
#: is written `DEFAULT_SHAPE`, which `suggest_squad_order` cut it for. Counted from the lines of the
#: retail records.
SLOT_SHAPES = (
    "5-3-2", "4-4-2", "3-4-3", "3-4-3", "4-3-3", "4-4-2", "4-4-2", "4-3-3", "4-4-2", "4-5-1",
    "4-4-2", "4-4-2", "4-4-2", "4-4-2", "4-4-2", "4-5-1", "4-3-3", "5-3-2", "4-4-2", "4-4-2",
    "4-4-2", "3-4-3", "4-4-2", "3-5-2", "4-4-2", "3-5-2", "4-2-4",
)  # fmt: skip

#: The shape of a club with nothing to say what it plays and no slot to say what the slot plays:
#: the first the patcher offers, the 4-4-2 every club was once written.
DEFAULT_SHAPE = next(iter(FORMATION_DONORS))

#: The lateral (the high nibble of a place's second byte) of the middle of the pitch, and how far
#: the widest places of the retail records stand from it (Germany's wing backs, at 1 and 13): a
#: place's side for `seat_line` is its lateral's distance from the middle over that, so -1 is the
#: team's left touchline and 1 its right.
_MIDDLE = 7
_HALF_WIDTH = 6


def _donor_places(shape: str) -> list[tuple[int, float, int]]:
    """The line (0 keeper, 1 defence, 2 midfield, 3 attack), side (from -1 at the team's left
    touchline to 1 at its right) and depth of each of the eleven places of `shape`'s donor record,
    in place order."""
    record = FORMATION_DONORS[shape]
    places = []
    for k in range(11):
        depth, lateral_line = record[4 * k], record[4 * k + 1]
        side = ((lateral_line >> 4) - _MIDDLE) / _HALF_WIDTH
        places.append((lateral_line & 3, side, depth))
    return places


def _across(code: str) -> int:
    """How far across the pitch the place ESPN labels `code` is, from -2 (a left flank place: LB,
    LM, LW) through -1 (the left of a centre: CD-L, CM-L, AM-L) and 0 (the middle) to 1 and 2."""
    side = "LCR".index(slot_side(code)) - 1
    return side * (2 if code[:1] in ("L", "R") else 1)


#: (percentile floor, value) rows, highest first. Tuned so the league's p10,
#: median and p90 land on retail's.
Table = list[tuple[float, int]]


def _any_unsupplied(stats: PlayerStats, fields: tuple[str, ...]) -> bool:
    return any(name in stats.unsupplied for name in fields)


def _lookup(table: Table, percentile: float) -> int:
    return next((value for floor, value in table if percentile >= floor), table[-1][1])


def _ranks(raw: dict[int, float]) -> dict[int, float]:
    """Percentile of each value: the share below it, ties at their mean rank.

    Ties share the middle of the ranks they span, so a group that is all zero
    (keepers' goals) sits at the 50th percentile rather than at the bottom.
    """
    values = sorted(raw.values())
    n = len(values)
    out = {}
    for pid, value in raw.items():
        below = bisect_left(values, value)
        ties = bisect_right(values, value) - below
        out[pid] = (below + (ties - 1) / 2) / n * 100
    return out


def _per_game(total: Callable[[PlayerStats], float]) -> Callable[[PlayerStats], float]:
    return lambda s: total(s) / max(s.appearances, 1)


class ISSStatMapper:
    SHOOTING_TABLE: Table = [(97, 7), (92, 6), (65, 5), (35, 4), (15, 3), (5, 2), (0, 1)]
    TECHNIQUE_TABLE: Table = [(97, 7), (92, 6), (80, 5), (55, 4), (25, 3), (12, 2), (0, 1)]
    STAMINA_TABLE: Table = [
        (97, 15),
        (92, 14),
        (85, 13),
        (70, 12),
        (55, 11),
        (35, 10),
        (20, 9),
        (8, 8),
        (0, 7),
    ]
    KICK_TABLE: Table = [(97, 7), (85, 6), (65, 5), (40, 4), (15, 3), (0, 2)]
    #: How far offense and defense move off the position baseline.
    SHIFT_TABLE: Table = [(90, 2), (70, 1), (30, 0), (10, -1), (0, -2)]

    #: (defense, offense, dribble, dash, ball_dash) by position: the retail
    #: medians of the slots each position plays (keepers from slot 0 alone).
    POSITION_BASELINES = {
        "Goalkeeper": (12, 2, 5, 6, 5),
        "Defender": (11, 4, 7, 10, 9),
        "Midfielder": (8, 7, 7, 10, 9),
        "Attacker": (4, 10, 8, 11, 10),
    }

    #: No speed below retail's slowest (4), however old the player.
    MIN_SPEED = 4

    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    #: The `PlayerStats` fields each category's formula cannot do without. A
    #: player missing one is dropped from that category's ranking, not ranked on
    #: the zero his record carries. Spell the names exactly as on `PlayerStats`:
    #: a typo can never match `PlayerStats.unsupplied` and the gate silently
    #: stops gating.
    CATEGORY_INPUTS = {
        "shooting": ("goals", "shots_on"),
        "technique": ("passes_accuracy",),
        "stamina": ("minutes", "appearances"),
        "kick": ("passes_total", "appearances"),
        "offense": ("goals", "assists", "appearances"),
        "defense": ("tackles_total", "interceptions", "appearances"),
    }

    #: Inputs a category is *better* with and survives without. ESPN measures
    #: neither dribbling field, and it is the only provider this game has, so
    #: drop the dribbling *term* from the formula rather than the player from the
    #: ranking: `technique` has one other input, and dropping every player leaves
    #: the category empty and hands all 405 the same default rating.
    CATEGORY_OPTIONAL_INPUTS = {
        "technique": ("dribbles_success", "dribbles_attempts"),
    }

    #: Categories ranked within the player's position: offense and defense shift
    #: off a position baseline instead of restating it, and technique (pass
    #: accuracy) and kick (passes per game) would otherwise hand centre-backs
    #: the best touch and the league's top scorers the worst.
    RANKED_BY_POSITION = ("offense", "defense", "technique", "kick")

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
        order_as_given: bool = False,
        slot: int | None = None,
    ) -> ISSTeamRecord:
        """The team's record for ROM slot `slot` (`TEAM_ENUM_ORDER`): `_shape` says the formation
        it is written, and its fifteen come in that formation's places, the eleven seated by role
        (`_seated`). Without a slot, or with `order_as_given`, a club with no shape of its own is
        given `DEFAULT_SHAPE`."""
        # The order a caller hands back came from `suggest_squad_order`, which has no slot and cut
        # it for `DEFAULT_SHAPE`: the slot's own shape would cut it again.
        shape = self._shape(team_roster, None if order_as_given else slot)
        best_15 = self._seated(
            self._fifteen(team_roster, order_as_given, slot), shape, team_roster.extra.get("lineup")
        )
        # Rank only the players some team writes: bench and youth players
        # with stats would push every written fifteen up the scale. Another
        # team's slot is not known here, so the selection picks one with no
        # formation or lineup shape in `DEFAULT_SHAPE`; this team's own
        # fifteen is added to be sure.
        written = {p.id for r in all_rosters for p in self._fifteen(r, order_as_given)}
        written |= {p.id for p in best_15}
        all_stats = {
            pid: s for r in all_rosters for pid, s in r.player_stats.items() if pid in written
        }
        positions = {p.id: p.position for r in all_rosters for p in r.players}

        percentiles = self._compute_percentiles(all_stats, positions)

        iss_players = [
            ISSPlayerRecord(
                name=rom_name,
                shirt_number=number,
                position=self.POSITION_CODES.get(player.position, 2),
                attributes=self.map_player(
                    player, team_roster.player_stats.get(player.id), percentiles
                ),
                player_id=player.id,
                photo_url=player.photo_url,
                nationality=player.nationality,
            )
            for player, rom_name, number in zip(
                best_15, self._distinct_names(best_15), self._shirt_numbers(best_15), strict=True
            )
        ]

        return ISSTeamRecord(
            name=_to_ascii(team_roster.team.name),
            short_name=_to_ascii(
                team_roster.team.code[:3]
                if team_roster.team.code
                else team_roster.team.name[:3].upper()
            ),
            players=iss_players,
            formation=shape,
        )

    def map_player(
        self,
        player: Player,
        stats: PlayerStats | None,
        percentiles: dict[str, dict[int, float]],
    ) -> ISSPlayerAttributes:
        defense, offense, dribble, dash, ball_dash = self.POSITION_BASELINES.get(
            player.position, self.POSITION_BASELINES["Midfielder"]
        )
        age = player.age  # 0 when unknown: no adjustment
        step = 0
        if age and age <= 23:
            step = 1
        elif age and age >= 34:
            step = -2
        elif age and age >= 31:
            step = -1
        attrs = ISSPlayerAttributes(
            dribble=max(self.MIN_SPEED, dribble + step),
            dash=max(self.MIN_SPEED, dash + step),
            ball_dash=max(self.MIN_SPEED, ball_dash + step),
            defense=defense,
            offense=offense,
        )
        if not stats or stats.appearances == 0:
            return attrs

        pid = stats.player_id

        def pct(category: str) -> float | None:
            return percentiles.get(category, {}).get(pid)

        # An empty or gated category rates the player at the median.
        attrs.shooting = _lookup(self.SHOOTING_TABLE, percentiles.get("shooting", {}).get(pid, 50))
        attrs.technique = _lookup(
            self.TECHNIQUE_TABLE, percentiles.get("technique", {}).get(pid, 50)
        )
        attrs.stamina = _lookup(self.STAMINA_TABLE, percentiles.get("stamina", {}).get(pid, 50))
        kick = pct("kick")
        attrs.kick = (
            _lookup(self.KICK_TABLE, kick)
            if kick is not None
            else max(2, min(7, (attrs.shooting + attrs.technique + 1) // 2))
        )
        # Keepers keep their baseline: tackles, interceptions and goals say
        # little about them, and no traced code shows what they save with.
        if player.position != "Goalkeeper":
            if (off := pct("offense")) is not None:
                attrs.offense += _lookup(self.SHIFT_TABLE, off)
            if (dfn := pct("defense")) is not None:
                attrs.defense += _lookup(self.SHIFT_TABLE, dfn)
        return attrs

    def _shape(self, roster: TeamRoster, slot: int | None = None) -> str:
        """The shape written for `roster`: the nearest `FORMATION_DONORS` has to its formation
        (`team_formation`: `extra["formation"]`, else its lineup's shape). A club with neither is
        written the retail shape of ROM slot `slot`, else `DEFAULT_SHAPE`."""
        text = team_formation(roster)
        if text is not None:
            return nearest_shape(text, tuple(FORMATION_DONORS))
        return DEFAULT_SHAPE if slot is None else SLOT_SHAPES[slot]

    def _fifteen(
        self, roster: TeamRoster, order_as_given: bool, slot: int | None = None
    ) -> list[Player]:
        """The fifteen this mapper writes for `roster`, the eleven in line order: the keeper, then
        the defenders, midfielders and forwards of its `_shape`.

        With `order_as_given` the caller's eleven always stand, formation or not: the caller's list
        as it stands (`_given`), whose lines `_seated` cuts over the shape by index. Else the
        selection by lineup and starts, in the shape's counts.
        """
        if order_as_given:
            return self._given(roster.players)
        return self._select_best_15(
            roster.players,
            roster.player_stats,
            roster.extra.get("lineup"),
            self._shape(roster, slot),
        )

    def _given(self, players: list[Player]) -> list[Player]:
        """The caller's list as the fifteen: the keeper (the first goalkeeper listed, else the
        first player) in goal, then the first ten outfield players in the order given, whatever
        their positions, and the rest, a second keeper among them, as the bench in that order."""
        goal = next((p for p in players if p.position == "Goalkeeper"), None)
        if goal is None:
            return players[:PLAYERS_PER_TEAM]
        eleven = [goal, *[p for p in players if p.position != "Goalkeeper"][:10]]
        taken = {p.id for p in eleven}
        return (eleven + [p for p in players if p.id not in taken])[:PLAYERS_PER_TEAM]

    def _seated(
        self, players: list[Player], shape: str, lineup: dict | None = None
    ) -> list[Player]:
        """`players` (the fifteen, the eleven in line order) with each line's starters on the
        places of `shape`'s donor record: the line of a place is the record's, and a line's
        players take its places by role, the sides first (a full back on the widest place on his
        side) and then the deeper role deeper (`seat_line`). A player's role is his own, else the
        one his code in the `lineup` reads as (a roster fetched before roles); one with neither
        keeps his turn in the line."""
        places = _donor_places(shape)
        roles = [p.role or lineup_role(p.id, lineup) for p in players]
        out = list(players)
        for line in (1, 2, 3):
            at = [k for k in range(min(len(players), 11)) if places[k][0] == line]
            seats = seat_line([roles[k] for k in at], [places[k][1:] for k in at])
            for k, seat in zip(at, seats, strict=True):
                out[at[seat]] = players[k]
        return out

    def _technique_value(self, stats: PlayerStats) -> float:
        """Raw technique score: dribble success rate and pass accuracy, averaged.

        Pass accuracy alone when the provider does not measure dribbling.
        """
        if _any_unsupplied(stats, self.CATEGORY_OPTIONAL_INPUTS["technique"]):
            return stats.passes_accuracy
        return (
            (stats.dribbles_success / max(stats.dribbles_attempts, 1)) * 100 + stats.passes_accuracy
        ) / 2

    def _compute_percentiles(
        self, all_stats: dict[int, PlayerStats], positions: dict[int, str] | None = None
    ) -> dict[str, dict[int, float]]:
        """Compute league-wide percentiles for each stat category.

        `n` is per-category and not `len(all_stats)`: a player whose provider did
        not measure one of a category's required inputs is skipped for it. The
        `RANKED_BY_POSITION` categories rank each position (from `positions`)
        on its own.
        """
        if not all_stats:
            return {}

        categories: dict[str, Callable[[PlayerStats], float]] = {
            "shooting": lambda s: s.goals + s.shots_on * 0.3,
            "technique": self._technique_value,
            "stamina": _per_game(lambda s: s.minutes),
            "kick": _per_game(lambda s: s.passes_total),
            "offense": _per_game(lambda s: s.goals + s.assists),
            "defense": _per_game(lambda s: s.tackles_total + s.interceptions),
        }

        percentiles: dict[str, dict[int, float]] = {}
        for cat_name, extract_fn in categories.items():
            groups: dict[str, dict[int, float]] = {}
            for pid, stats in all_stats.items():
                if _any_unsupplied(stats, self.CATEGORY_INPUTS[cat_name]):
                    continue
                group = (positions or {}).get(pid, "")
                if cat_name not in self.RANKED_BY_POSITION:
                    group = ""
                groups.setdefault(group, {})[pid] = extract_fn(stats)
            percentiles[cat_name] = {
                pid: rank for raw in groups.values() for pid, rank in _ranks(raw).items()
            }

        return percentiles

    def _shirt_numbers(self, players: list[Player]) -> list[int]:
        """Unique numbers 1-15, the only ones retail uses. A real number of 15
        or below that is still free stays; everyone else takes the lowest free
        numbers in squad order."""
        return shirt_numbers((p.number or 0 for p in players), PLAYERS_PER_TEAM)

    #: Where a group's extra starters play when its places are full, nearest
    #: first. Keepers never swap with outfield players.
    SPILL = {
        "Defender": ("Midfielder", "Attacker"),
        "Midfielder": ("Attacker", "Defender"),
        "Attacker": ("Midfielder", "Defender"),
    }

    def _select_best_15(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
        lineup: dict | None = None,
        shape: str = DEFAULT_SHAPE,
    ) -> list[Player]:
        """The fifteen in ISS's order: the starting eleven, then four subs.

        The eleven is the keeper and the `shape`'s defenders, midfielders and
        forwards (a key of `FORMATION_DONORS`), in that order. It is the team's
        recent starting lineup when `lineup` (what `EspnClient.get_lineup`
        returns) has one: every listed starter plays, in his lineup group, and
        a group with more starters than places (a front three in a 4-4-2)
        sends the rest to the nearest group with room. Places still open go to
        the players with the most starts this season, in their group, then in
        a neighbouring one, and only then to a player who never started, so a
        forward with no starts never takes a place a midfielder with twenty
        would. Subs are the backup keeper, then the best of the rest the same
        way.

        The ten outfield players are those picked, and with a lineup or a role
        among them they stand in the lines of their roles, not of their groups
        (ESPN groups a 4-2-3-1's right winger with its midfield and the
        attacking midfielder with its attack): back to front by the depth of
        their roles (`DEPTH`; a player with none has the one his lineup code
        reads as, else his squad group's), a stable sort, cut by index into the
        shape's lines, and each line from the left by the side of its players'
        lineup codes. A roster with neither a lineup nor a role keeps the lines
        the group fill made, as it did before roles.
        """
        stats = player_stats or {}
        recent = {int(s["id"]): (i, s) for i, s in enumerate((lineup or {}).get("starters") or [])}

        def group(p: Player) -> str:
            if p.id in recent:
                code = str(recent[p.id][1].get("position", "")).upper()
                return LINEUP_GROUPS.get(code, p.position)
            return p.position

        def key(p: Player) -> tuple[int, int, int, int]:
            s = stats.get(p.id)
            return (
                recent[p.id][0] if p.id in recent else len(recent),
                -(s.lineups if s else 0),
                -(s.appearances if s else 0),
                -(s.minutes if s else 0),
            )

        def started(p: Player) -> bool:
            s = stats.get(p.id)
            return p.id in recent or bool(s and s.lineups)

        pool = sorted(players, key=key)
        defenders, midfielders, forwards = map(int, shape.split("-"))
        places = {
            "Goalkeeper": 1,
            "Defender": defenders,
            "Midfielder": midfielders,
            "Attacker": forwards,
        }
        chosen: dict[str, list[Player]] = {g: [] for g in places}
        taken: set[int] = set()

        def fill(eligible, spill: bool) -> None:
            for p in pool:
                if id(p) in taken or not eligible(p):
                    continue
                home = group(p)
                for target in (home, *(self.SPILL.get(home, ()) if spill else ())):
                    if target in places and len(chosen[target]) < places[target]:
                        chosen[target].append(p)
                        taken.add(id(p))
                        break

        for eligible in (lambda p: p.id in recent, started, lambda p: True):
            fill(eligible, spill=False)
            fill(eligible, spill=True)

        def role(p: Player) -> str:
            """His role, else the one his lineup code reads as, else his squad group's."""
            if p.role in DEPTH:
                return p.role
            return lineup_role(p.id, lineup) or GROUP_ROLE.get(p.position, "CM")

        def across(p: Player) -> int:
            return _across(str(recent[p.id][1].get("position") or "") if p.id in recent else "")

        outfield = [p for g in places if g != "Goalkeeper" for p in chosen[g]]
        eleven = list(chosen["Goalkeeper"])
        if recent or any(p.role in DEPTH for p in outfield):
            # The outfield back to front by the depth of its roles (stable: players of one depth
            # keep the order they were chosen in), cut by index into the shape's lines, so each
            # plays the line his role is on whatever group ESPN gives his code; then each line
            # from the left.
            outfield.sort(key=lambda p: DEPTH[role(p)])
            for count in (defenders, midfielders, forwards):
                eleven += sorted(outfield[:count], key=across)
                outfield = outfield[count:]
        else:
            # No lineup and no role to read: the lines the group fill made, as they always were
            # (a spilled attacker stays in the line that had room for him).
            eleven += outfield

        rest = [p for p in pool if id(p) not in taken]
        keepers = [p for p in rest if group(p) == "Goalkeeper"][:1]
        subs = keepers + [p for p in rest if p not in keepers]
        return (eleven + subs)[:PLAYERS_PER_TEAM]

    def _distinct_names(self, players: list[Player]) -> list[str]:
        """Each player's ROM name, told apart within the team. Players the
        surname alone would merge (Gabriel Silva, Lucas Silva) all become
        initial and surname ("G.Silva"), or, when that still merges them (Vitor
        Hugo, Victor Hugo), their first names; one-word names get a digit."""
        names = [self._format_player_name(p) for p in players]
        shared: dict[str, list[int]] = {}
        for i, name in enumerate(names):
            shared.setdefault(name, []).append(i)
        for name, idx in shared.items():
            if len(idx) < 2:
                continue
            words = [(_to_ascii(players[i].name) or name).split() for i in idx]
            others = set(names) - {name}
            picks: list[str | None] = []
            for make in (lambda w: f"{w[0][0]}.{w[-1]}"[:8], lambda w: w[0][:8]):
                picks = [make(w) if len(w) > 1 else None for w in words]
                if None not in picks and len(set(picks)) == len(picks) and not others & set(picks):
                    break
            else:
                picks = [name, *(f"{name[:7]}{k}" for k in range(2, len(idx) + 1))]
            for i, pick in zip(idx, picks, strict=True):
                names[i] = str(pick)
        return names

    def _format_player_name(self, player: Player) -> str:
        """Build a ROM-friendly name from a Player, 8 characters max: the first
        name's initial and the surname ("I.Gomes") when that fits, else the
        surname."""
        display = _to_ascii(player.name) if player.name else ""
        if not display:
            last = _to_ascii(player.last_name) if player.last_name else ""
            return (last or "PLAYER")[:8]

        words = display.split()
        if len(words) == 1:
            return display[:8]

        surname = words[-1]
        if len(surname) + 2 <= 8:
            return f"{words[0][0]}.{surname}"
        return surname[:8]
