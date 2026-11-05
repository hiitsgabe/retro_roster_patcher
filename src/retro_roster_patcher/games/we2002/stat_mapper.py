"""Maps real-world player stats to WE2002's 1-9 attribute scale."""

from collections import Counter
from itertools import zip_longest

from ...sports.espn import LINEUP_GROUPS
from ...sports.lineup import DEPTH as ROLE_DEPTH
from ...sports.lineup import GROUP_ROLE, nearest_shape, parse_shape, seat_line
from .models import (
    Player,
    PlayerStats,
    TeamRoster,
    WEPlayerAttributes,
    WEPlayerRecord,
    WETeamRecord,
)
from .rom_writer import _PLAYERS_PER_NAT

#: The shapes the disc has presets for, in its order (a tie in `nearest_shape` goes to the first).
FORMATIONS = ("4-5-1", "4-4-2", "4-3-3", "3-6-1", "3-5-2", "3-4-3", "5-4-1", "5-3-2")


def _preset(roles: str, x: str, y: str) -> bytes:
    """A preset's 30 bytes, from the hex of its role bytes, its x and its y."""
    return bytes.fromhex(f"{roles} {x} {y}")


#: The disc's 16 formation presets in its order, by name: a shape and a variant, A with one holding
#: midfielder (DH) and B with two. Each is the 30 bytes of a team's formation record: the role byte
#: of places 2-11 (place 1 is the keeper and has none), then their x, then their y. A role byte is
#: a family and a side (2-6 CB, 7-8 SB, 9-11 DH, 12-13 SH, 14-16 OH, 17-19 CF, 20-21 WG); x is
#: the depth, 0 at the team's own goal and 48 the most advanced; y is the width, 0-112, small on
#: the team's left and 52 in the middle. Every preset lists its back line, then its midfield, then
#: its attack. Constants, not read from the image: a test holds them to the disc's tables.
FORMATION_PRESETS: dict[str, bytes] = {
    "4-5-1A": _preset(
        "02 06 07 08 0a 0c 0d 0e 10 12",
        "09 09 0b 0b 12 1a 1a 22 22 2b",
        "29 3f 13 55 34 1d 4b 28 40 34",
    ),
    "4-5-1B": _preset(
        "02 06 07 08 09 0b 0c 0d 0f 12",
        "09 09 0b 0b 12 12 1a 1a 22 2b",
        "29 3f 13 55 2b 3d 1d 4b 34 34",
    ),
    "4-4-2A": _preset(
        "02 06 07 08 0a 0c 0d 0f 11 13",
        "09 09 0b 0b 12 1a 1a 22 2b 2b",
        "29 3f 13 55 34 1d 4b 34 26 42",
    ),
    "4-4-2B": _preset(
        "02 06 07 08 09 0b 0e 10 11 13",
        "09 09 0b 0b 12 12 1e 1e 2b 2b",
        "29 3f 13 55 2b 3d 22 46 26 42",
    ),
    "4-3-3A": _preset(
        "02 06 07 08 0a 0e 10 12 14 15",
        "09 09 0b 0b 12 1e 1e 2b 2b 2b",
        "29 3f 13 55 34 28 40 34 20 48",
    ),
    "4-3-3B": _preset(
        "02 06 07 08 09 0b 0f 12 14 15",
        "09 09 0b 0b 12 12 1e 2b 2b 2b",
        "29 3f 13 55 2b 3d 34 34 20 48",
    ),
    "3-6-1A": _preset(
        "02 03 06 0a 0c 0d 0f 0e 10 12",
        "09 09 09 12 1a 1a 1a 22 22 2b",
        "20 34 48 34 1b 4d 34 28 40 34",
    ),
    "3-6-1B": _preset(
        "02 03 06 09 0b 0c 0d 0e 10 12",
        "09 09 09 12 12 1a 1a 22 22 2b",
        "20 34 48 2b 3d 1b 4d 28 40 34",
    ),
    "3-5-2A": _preset(
        "02 03 06 0a 0c 0d 0e 10 11 13",
        "09 09 09 12 1a 1a 22 22 2b 2b",
        "20 34 48 34 1b 4d 28 40 26 42",
    ),
    "3-5-2B": _preset(
        "02 03 06 09 0b 0c 0d 0f 11 13",
        "09 09 09 12 12 1a 1a 22 2b 2b",
        "20 34 48 2b 3d 1b 4d 34 26 42",
    ),
    "3-4-3A": _preset(
        "02 03 06 0a 0c 0d 0f 12 14 15",
        "09 09 09 12 1a 1a 22 2b 2b 2b",
        "20 34 48 34 1b 4d 34 34 20 48",
    ),
    "3-4-3B": _preset(
        "02 03 06 09 0b 0e 10 12 14 15",
        "09 09 09 12 12 1e 1e 2b 2b 2b",
        "20 34 48 2b 3d 22 46 34 20 48",
    ),
    "5-4-1A": _preset(
        "02 03 06 07 08 0a 0c 0d 0f 12",
        "09 09 09 0c 0c 12 1a 1a 22 2b",
        "20 34 48 11 57 34 1d 4b 34 34",
    ),
    "5-4-1B": _preset(
        "02 03 06 07 08 09 0b 0e 10 12",
        "09 09 09 0c 0c 12 12 1e 1e 2b",
        "20 34 48 11 57 2b 3d 22 46 34",
    ),
    "5-3-2A": _preset(
        "02 03 06 07 08 0a 0e 10 11 13",
        "09 09 09 0c 0c 12 22 22 2b 2b",
        "20 34 48 11 57 34 28 40 26 42",
    ),
    "5-3-2B": _preset(
        "02 03 06 07 08 09 0b 0f 11 13",
        "09 09 09 0c 0c 12 12 22 2b 2b",
        "20 34 48 11 57 2b 3d 34 26 42",
    ),
}

#: A place of a preset is on the team's left below this y, on its right above `_RIGHT_ABOVE` and
#: central between (the retail records put a wide place at y 7-37 or 71-101 and a central one at
#: 31-77).
_LEFT_BELOW = 45
_RIGHT_ABOVE = 59


def _side(y: int) -> str:
    """`"L"`, `"C"` or `"R"`: the side of the pitch a place at width `y` is on."""
    return "L" if y < _LEFT_BELOW else "R" if y > _RIGHT_ABOVE else "C"


class StatMapper:
    PERCENTILE_TABLE = [
        (95, 9),
        (85, 8),
        (70, 7),
        (50, 6),
        (35, 5),
        (20, 4),
        (10, 3),
        (3, 2),
        (0, 1),
    ]

    FALLBACK_ATTRS = {
        "Goalkeeper": dict(
            offensive=2,
            defensive=7,
            body_balance=6,
            stamina=6,
            speed=4,
            acceleration=4,
            pass_accuracy=5,
            shoot_power=3,
            shoot_accuracy=2,
            jump_power=7,
            heading=5,
            technique=4,
            dribble=3,
            curve=3,
            aggression=4,
        ),
        "Defender": dict(
            offensive=3,
            defensive=7,
            body_balance=6,
            stamina=6,
            speed=5,
            acceleration=5,
            pass_accuracy=5,
            shoot_power=4,
            shoot_accuracy=3,
            jump_power=6,
            heading=6,
            technique=4,
            dribble=3,
            curve=3,
            aggression=6,
        ),
        "Midfielder": dict(
            offensive=5,
            defensive=5,
            body_balance=5,
            stamina=7,
            speed=5,
            acceleration=5,
            pass_accuracy=7,
            shoot_power=5,
            shoot_accuracy=5,
            jump_power=5,
            heading=5,
            technique=6,
            dribble=6,
            curve=5,
            aggression=5,
        ),
        "Attacker": dict(
            offensive=7,
            defensive=3,
            body_balance=5,
            stamina=5,
            speed=6,
            acceleration=6,
            pass_accuracy=5,
            shoot_power=7,
            shoot_accuracy=7,
            jump_power=5,
            heading=5,
            technique=6,
            dribble=6,
            curve=5,
            aggression=5,
        ),
    }

    # Never gate on `PlayerStats.unsupplied` here: a provider's filler zero must
    # rank like a measurement, even though that pins body_balance, technique and
    # dribble to 1 for every player under ESPN. These are the bytes the game has
    # been fed; do not "fix" them.
    POSITION_CODES = {
        "Goalkeeper": 0,
        "Defender": 1,
        "Midfielder": 2,
        "Attacker": 3,
    }

    def map_team_with_league_context(
        self,
        team_roster: TeamRoster,
        all_rosters: list[TeamRoster],
        order_as_given: bool = False,
    ) -> WETeamRecord:
        """Map team using league-wide percentile normalization.

        `order_as_given`: the squad is the caller's list as it stands (first 23,
        a national squad's places) instead of the stat-ranked best 23.

        A team whose `extra["formation"]` `parse_shape` reads is written the disc's
        preset nearest it (`FORMATIONS`, variant B when the eleven hold two or more
        CDMs, else A) and its eleven are ordered for it: the keeper, then the ten
        outfield players cut into the preset's lines by index and each line seated on
        the preset's places by role (`_seated`). The ten are the suggested eleven's
        (`_select_best_23`) or, with `order_as_given`, the caller's in the order given
        (`_given`), so an edited order or formation keeps its lines by index. Without
        a readable formation the order is the 4-4-2's and the record has no formation.
        """
        all_stats = {}
        for roster in all_rosters:
            for pid, ps in roster.player_stats.items():
                all_stats[pid] = ps

        percentiles = self._compute_percentiles(all_stats)

        formation = str(team_roster.extra.get("formation") or "")
        lined = parse_shape(formation) is not None
        if order_as_given:
            squad = (
                self._given(team_roster.players)
                if lined
                else team_roster.players[:_PLAYERS_PER_NAT]
            )
        else:
            squad = self._select_best_23(
                team_roster.players,
                team_roster.player_stats,
                team_roster.extra.get("lineup"),
                formation,
            )
        preset = None
        if lined:
            preset = nearest_shape(formation, FORMATIONS) + (
                "B" if sum(p.role == "CDM" for p in squad[:11]) >= 2 else "A"
            )
            squad = self._seated(squad, preset)

        we_players = []
        for player in squad:
            stats = team_roster.player_stats.get(player.id)
            attrs = self.map_player(player, stats, percentiles)
            rom_last, rom_first = self._format_player_name(player)
            we_players.append(
                WEPlayerRecord(
                    last_name=rom_last,
                    first_name=rom_first,
                    position=self.POSITION_CODES.get(player.position, 2),
                    shirt_number=player.number or 0,
                    attributes=attrs,
                    player_id=player.id or 0,
                    photo_url=player.photo_url or "",
                    nationality=player.nationality or "",
                    height_cm=round(player.height * 2.54) if player.height > 0 else 0,
                    age=player.age or 0,
                    role=player.role,
                )
            )

        from .rom_writer import _to_ascii

        return WETeamRecord(
            name=self._truncate_name(team_roster.team.name, 24),
            short_name=_to_ascii(
                team_roster.team.code[:3]
                if team_roster.team.code
                else team_roster.team.name[:3].upper()
            ),
            players=we_players,
            formation=FORMATION_PRESETS[preset] if preset else None,
        )

    def map_player(
        self,
        player: Player,
        stats: PlayerStats | None,
        percentiles: dict[str, dict[int, float]],
    ) -> WEPlayerAttributes:
        """Convert a real player's stats to WE2002 format.

        Ten of the fifteen attributes are league percentiles; the other five have
        no statistic behind them and are estimated from position and age.
        """
        if not stats or stats.appearances == 0:
            return self._fallback_attributes(player)

        attrs = WEPlayerAttributes(
            offensive=self._rate(stats, percentiles, "offensive"),
            defensive=self._rate(stats, percentiles, "defensive"),
            body_balance=self._rate(stats, percentiles, "body_balance"),
            stamina=self._rate(stats, percentiles, "stamina"),
            speed=self._estimate_speed(player),
            acceleration=self._estimate_speed(player),
            pass_accuracy=self._rate(stats, percentiles, "pass_accuracy"),
            shoot_power=self._rate(stats, percentiles, "shoot_power"),
            shoot_accuracy=self._rate(stats, percentiles, "shoot_accuracy"),
            jump_power=self._estimate_jump(player),
            heading=self._estimate_heading(player),
            technique=self._rate(stats, percentiles, "technique"),
            dribble=self._rate(stats, percentiles, "dribble"),
            curve=self._estimate_curve(player),
            aggression=self._rate(stats, percentiles, "aggression"),
        )
        return self._apply_position_adjustments(attrs, player.position)

    def _rate(
        self,
        stats: PlayerStats,
        percentiles: dict[str, dict[int, float]],
        category: str,
    ) -> int:
        """Rate one category from this player's league percentile.

        The `50` default only applies when `_compute_percentiles` produced no
        entry for this player, i.e. an empty league.
        """
        return self._percentile_to_rating(percentiles.get(category, {}).get(stats.player_id, 50))

    def _compute_percentiles(
        self, all_stats: dict[int, PlayerStats]
    ) -> dict[str, dict[int, float]]:
        """Compute league-wide percentiles for each stat category.

        Every record is ranked in every category, filler zeros included: an
        unmeasured player sorts below every measured one and still counts
        towards `n`. Do not skip him — see the note above `POSITION_CODES`.
        """
        if not all_stats:
            return {}

        categories = {
            "offensive": lambda s: (
                s.goals + s.assists * 0.7 + (s.shots_on * 0.3 if s.shots_on else 0)
            ),
            "defensive": lambda s: s.tackles_total + s.interceptions + s.blocks,
            "body_balance": lambda s: (s.duels_won / max(s.duels_total, 1)) * 100,
            "stamina": lambda s: s.minutes / max(s.appearances, 1),
            "pass_accuracy": lambda s: s.passes_accuracy,
            "shoot_power": lambda s: s.shots_total + s.goals,
            "shoot_accuracy": lambda s: (s.goals / max(s.shots_total, 1)) * 100,
            "technique": lambda s: (s.dribbles_success / max(s.dribbles_attempts, 1)) * 100,
            "dribble": lambda s: s.dribbles_success,
            "aggression": lambda s: s.fouls_committed + s.cards_yellow * 2 + s.cards_red * 5,
        }

        percentiles: dict[str, dict[int, float]] = {}
        for cat_name, extract_fn in categories.items():
            raw_values = {}
            for pid, stats in all_stats.items():
                raw_values[pid] = extract_fn(stats)

            sorted_values = sorted(raw_values.values())
            n = len(sorted_values)
            if n == 0:
                percentiles[cat_name] = {}
                continue

            cat_percentiles = {}
            for pid, value in raw_values.items():
                below = sum(1 for v in sorted_values if v < value)
                cat_percentiles[pid] = (below / n) * 100
            percentiles[cat_name] = cat_percentiles

        return percentiles

    def _percentile_to_rating(self, percentile: float) -> int:
        for threshold, rating in self.PERCENTILE_TABLE:
            if percentile >= threshold:
                return rating
        return 1

    def _apply_position_adjustments(
        self, attrs: WEPlayerAttributes, position: str
    ) -> WEPlayerAttributes:
        if position == "Goalkeeper":
            attrs.defensive = min(9, attrs.defensive + 2)
            attrs.jump_power = min(9, attrs.jump_power + 2)
            attrs.offensive = min(4, attrs.offensive)
            attrs.shoot_accuracy = min(3, attrs.shoot_accuracy)
        elif position == "Defender":
            attrs.defensive = min(9, attrs.defensive + 1)
            attrs.heading = min(9, attrs.heading + 1)
        elif position == "Midfielder":
            attrs.pass_accuracy = min(9, attrs.pass_accuracy + 1)
            attrs.technique = min(9, attrs.technique + 1)
            attrs.stamina = min(9, attrs.stamina + 1)
        elif position == "Attacker":
            attrs.offensive = min(9, attrs.offensive + 1)
            attrs.shoot_accuracy = min(9, attrs.shoot_accuracy + 1)
            attrs.shoot_power = min(9, attrs.shoot_power + 1)

        for field_name in [
            "offensive",
            "defensive",
            "body_balance",
            "stamina",
            "speed",
            "acceleration",
            "pass_accuracy",
            "shoot_power",
            "shoot_accuracy",
            "jump_power",
            "heading",
            "technique",
            "dribble",
            "curve",
            "aggression",
        ]:
            val = getattr(attrs, field_name)
            setattr(attrs, field_name, max(1, min(9, val)))

        return attrs

    def _fallback_attributes(self, player: Player) -> WEPlayerAttributes:
        """Position-and-age defaults for a player with no usable stats."""
        defaults = self.FALLBACK_ATTRS.get(player.position, self.FALLBACK_ATTRS["Midfielder"])
        attrs = WEPlayerAttributes(**defaults)

        age = player.age
        if age < 23:
            attrs.speed = min(9, attrs.speed + 1)
            attrs.acceleration = min(9, attrs.acceleration + 1)
            attrs.stamina = min(9, attrs.stamina + 1)
            attrs.technique = max(1, attrs.technique - 1)
        elif 31 <= age <= 33:
            attrs.speed = max(1, attrs.speed - 1)
            attrs.acceleration = max(1, attrs.acceleration - 1)
            attrs.stamina = max(1, attrs.stamina - 1)
            attrs.technique = min(9, attrs.technique + 1)
        elif age > 33:
            attrs.speed = max(1, attrs.speed - 2)
            attrs.stamina = max(1, attrs.stamina - 2)
            attrs.technique = min(9, attrs.technique + 1)

        return attrs

    def _estimate_speed(self, player: Player) -> int:
        base = {"Goalkeeper": 4, "Defender": 5, "Midfielder": 5, "Attacker": 6}
        val = base.get(player.position, 5)
        if player.age < 25:
            val += 1
        elif player.age > 32:
            val -= 1
        return max(1, min(9, val))

    def _estimate_jump(self, player: Player) -> int:
        base = {"Goalkeeper": 7, "Defender": 6, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 5)

    def _estimate_heading(self, player: Player) -> int:
        base = {"Goalkeeper": 5, "Defender": 6, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 5)

    def _estimate_curve(self, player: Player) -> int:
        base = {"Goalkeeper": 3, "Defender": 3, "Midfielder": 5, "Attacker": 5}
        return base.get(player.position, 4)

    #: The ten outfield places of the first 11 slots, after the keeper's: a 4-4-2.
    XI_LINES = (("Defender", 4), ("Midfielder", 4), ("Attacker", 2))
    #: The bench by line: with the eleven's places, a 3 / 7 / 6 / 6 squad. Per
    #: place, not per player: an eleven of six midfielders still gets two
    #: midfield subs.
    BENCH_PLACES = (("Goalkeeper", 2), ("Defender", 3), ("Midfielder", 2), ("Attacker", 4))
    #: ESPN lineup codes from the back to the front. The ten outfielders take
    #: the 4-4-2 in this order, so a line with more players than the 4-4-2 has
    #: moves its most advanced one up (a back five's wing-back to midfield)
    #: and a short line takes the next line's deepest (a back three a holding
    #: or wide midfielder), rather than a forward playing at the back.
    DEPTH = (
        ("SW", "CD", "CD-L", "CD-R", "LB", "RB", "LWB", "RWB")
        + ("DM", "LM", "RM", "CM", "CM-L", "CM-R", "AM-L", "AM-R", "AM")
        + ("LW", "RW", "LF", "RF", "CF-L", "CF-R", "LCF", "RCF", "F", "CF")
    )
    #: Where a player with no lineup code stands in his line.
    LINE_CODES = {"Defender": "CD", "Midfielder": "CM", "Attacker": "CF"}

    def _select_best_23(
        self,
        players: list[Player],
        player_stats: dict[int, PlayerStats] | None = None,
        lineup: dict | None = None,
        formation: str | None = None,
    ) -> list[Player]:
        """The 23 (a national squad's places, which a Master League club of the
        same slot number fields too): the starting eleven, then the bench.

        The eleven is the keeper, then ten outfielders in the 4-4-2 of the
        first 11 slots. When `lineup` (what `EspnClient.get_lineup` returns)
        has the team's recent starters, every one of them plays (a second
        keeper sits), back to front by his lineup position (`DEPTH`). Places
        still open go to the season's most frequent starters, from a line the
        lineup left short first. The bench then takes up to 2 keepers,
        3 defenders, 2 midfielders and 4 forwards (`BENCH_PLACES`), recent
        starters and regulars first, one of each line in turn so the first few
        subs cover every line; the best of the rest fill what is left.

        With a `formation` that `parse_shape` reads, the eleven's outfielders
        go back to front by the depth of their roles (`ROLE_DEPTH`; a player
        with none reads as his group's) and then by lineup position, so
        cutting them by index over the formation's lines puts each in the line
        he plays. The players picked are the same. The order is also the one
        `map_team_with_league_context` writes.
        """
        stats = player_stats or {}
        recent = {int(s["id"]): (i, s) for i, s in enumerate((lineup or {}).get("starters") or [])}

        def code(p: Player) -> str:
            return str(recent[p.id][1].get("position", "")).upper() if p.id in recent else ""

        def group(p: Player) -> str:
            return LINEUP_GROUPS.get(code(p), p.position)

        def depth(p: Player) -> int:
            c = code(p)
            return self.DEPTH.index(c if c in self.DEPTH else self.LINE_CODES.get(group(p), "CM"))

        def key(p: Player) -> tuple[int, int, int, int]:
            """Recent starters first, then lineups, appearances and minutes."""
            s = stats.get(p.id)
            return (
                recent[p.id][0] if p.id in recent else len(recent),
                -(s.lineups if s else 0),
                -(s.appearances if s else 0),
                -(s.minutes if s else 0),
            )

        # Stable, so players the key ties keep the provider's order.
        pool = sorted(players, key=key)
        keeper = [p for p in pool if group(p) == "Goalkeeper"][:1]
        outfield = [p for p in pool if p.id in recent and group(p) != "Goalkeeper"][:10]
        short = Counter(dict(self.XI_LINES)) - Counter(group(p) for p in outfield)
        for p in pool:
            if len(outfield) < 10 and short[group(p)] > 0 and p not in outfield:
                outfield.append(p)
                short[group(p)] -= 1
        spare = [p for p in pool if group(p) != "Goalkeeper" and p not in outfield]
        outfield += spare[: 10 - len(outfield)]
        lined = parse_shape(str(formation or "")) is not None

        def line_key(p: Player) -> tuple[int, int]:
            return (ROLE_DEPTH[self._role(p)] if lined else 0, depth(p))

        eleven = keeper + sorted(outfield, key=line_key)

        rest = [p for p in pool if p not in eleven]
        subs = [[p for p in rest if group(p) == g][:n] for g, n in self.BENCH_PLACES]
        bench = [p for row in zip_longest(*subs) for p in row if p is not None]
        bench += [p for p in rest if p not in bench]
        return (eleven + bench)[:_PLAYERS_PER_NAT]

    def _role(self, player: Player) -> str:
        """His role, else the one his squad group gives (`GROUP_ROLE`)."""
        return player.role if player.role in ROLE_DEPTH else GROUP_ROLE.get(player.position, "CM")

    def _given(self, players: list[Player]) -> list[Player]:
        """The caller's list as the squad, for a team with a readable formation: the first
        goalkeeper listed in goal, then the first ten outfield players in the order given (the
        formation cuts them into lines by index, whatever their positions), then everyone else
        in the order given, a second keeper among them, cut at 23. A list with no goalkeeper
        keeps its first player in goal."""
        at = range(len(players))
        goal = next((i for i in at if players[i].position == "Goalkeeper"), 0)
        outfield = [i for i in at if i != goal and players[i].position != "Goalkeeper"]
        eleven = ([goal] if players else []) + outfield[:10]
        order = eleven + [i for i in at if i not in eleven]
        return [players[i] for i in order][:_PLAYERS_PER_NAT]

    def _seated(self, squad: list[Player], preset: str) -> list[Player]:
        """`squad` (the keeper, ten outfield players in line order, then the rest) with each line
        of the ten on the places of the disc's `preset`, which is place k + 2 for the k-th.

        The ten are cut into the preset's lines by index and each line takes that line's places by
        role (`seat_line`): first by side, a place being on the left below y 45, on the right
        above 59 and central between, and then a deeper role on a deeper place, the place's x. A
        player with no role reads as his group's. A squad of fewer than eleven seats the places
        it has players for."""
        record = FORMATION_PRESETS[preset]
        xs, ys = record[10:20], record[20:]
        defenders, midfielders, _ = map(int, preset[:-1].split("-"))
        out = list(squad)
        edges = (0, defenders, defenders + midfielders, 10)
        for first, end in zip(edges, edges[1:], strict=False):
            at = list(range(first, min(end, len(squad) - 1)))
            places = [(_side(ys[k]), xs[k]) for k in at]
            seats = seat_line([self._role(squad[1 + k]) for k in at], places)
            for k, seat in zip(at, seats, strict=True):
                out[1 + at[seat]] = squad[1 + k]
        return out

    def _format_player_name(self, player: Player) -> tuple:
        """Build ROM-friendly (last_name, first_name) from a Player.

        The game displays at most 8 characters. A mononym is used as-is
        ("HULK", "NEYMAR"); two or more words become "F. Surname" ("V. Hugo",
        "G. Pique").

        Strip each provider string AFTER `_to_ascii` and before testing it:
        `_to_ascii` drops unrenderable characters but keeps the spaces between
        them, so a multi-word non-Latin name arrives as `" "`. Without the
        strip it is truthy, the fallback is skipped, and `words[-1]` raises
        `IndexError`. Stripping before conversion does not work — Cyrillic is
        not whitespace until `_to_ascii` has run.
        """
        from .rom_writer import _to_ascii

        display = _to_ascii(player.name).strip() if player.name else ""
        first = _to_ascii(player.first_name).strip() if player.first_name else ""

        if not display:
            last = _to_ascii(player.last_name).strip() if player.last_name else ""
            return (last or "")[:8], first[:8]

        words = display.split()

        if len(words) == 1:
            # Mononym: "HULK", "NEYMAR", "ENDRICK"
            return display[:8], ""

        # Multi-word: "F. Surname" — e.g. "V. Hugo", "G. Pique"
        surname = words[-1]
        initial = words[0][0]
        jersey = f"{initial}. {surname}"
        return jersey[:8], (first or words[0])[:8]

    def _truncate_name(self, name: str, max_bytes: int) -> str:
        """Fold to ASCII, then hard-truncate to `max_bytes`."""
        if not name:
            return ""
        from .rom_writer import _to_ascii

        ascii_name = _to_ascii(name)
        if len(ascii_name) <= max_bytes:
            return ascii_name
        return ascii_name[:max_bytes]
