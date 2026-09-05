"""Turn provider roster and stat data into NHL 2005's 0-63 attribute scale.

This module and `games/nhl07_psp/stat_mapper.py` are the same arithmetic, and
they stay unshared on purpose: these numbers are a rating policy, not a format,
and a change made for one game must not reach the other. The one number that
already differs is the name truncation width, 15 here against 19 there.

The formulas, the scaling windows and the position defaults are transcribed
unchanged, including where they are visibly rough: changing them changes every
rating on every patched disc and there is no reference to check against.
"""

from __future__ import annotations

from dataclasses import replace

from ...sports.models import Player
from .models import (
    MODERN_NHL_TO_NHL05,
    NAME_FIELD_CHARS,
    NHL05GoalieAttributes,
    NHL05PlayerRecord,
    NHL05SkaterAttributes,
)

# Attribute floor and ceiling. Six bits, so 0-63 for everything except `FIGH`,
# which is two bits and is clamped separately where it is computed.
ATTR_MIN = 0
ATTR_MAX = 63

# How many players a mapped roster holds. An NHL team dresses 20 and carries
# scratches; anything larger is truncated by however many ROST rows the disc
# gives a team in `patcher.patch`.
MAX_PLAYERS = 25

# The shape `select_roster` builds towards: two goalies, four forward lines of
# three, two spare forwards, and seven defencemen.
GOALIES_PER_TEAM = 2
FORWARDS_PER_TEAM = 14
DEFENCEMEN_PER_TEAM = 7
FORWARD_LINES = 4

# Three pairs, of which only two reach the disc: `generate_team_line_flags`
# emits `3nLD`/`3nRD`, and this game's ROST has `31` and `32` but no `33`, so
# `rom_writer.roster_values` drops the third pair on the floor. NHL 2005's ROST
# names `L1LD`/`L1RD` through `L3LD`/`L3RD` and none of those is ever set. See
# the label on `generate_team_line_flags` for why that stands.
DEFENCE_PAIRS = 3

SPECIAL_TEAMS_UNIT = 5

# Per-position starting points, used whole when a player has no stats and as the
# base for several derived ratings when he does. Copied, not shared: every read
# goes through `dataclasses.replace`, so the module constant never reaches a
# record. See `_defaults_for`.
SKATER_DEFAULTS = {
    "C": NHL05SkaterAttributes(
        balance=35,
        penalty=30,
        shot_accuracy=35,
        wrist_accuracy=35,
        faceoffs=40,
        acceleration=35,
        speed=35,
        potential=35,
        deking=35,
        checking=30,
        toughness=25,
        fighting=1,
        puck_control=35,
        agility=35,
        hero=30,
        aggression=25,
        pressure=30,
        passing=38,
        endurance=35,
        injury=35,
        slap_power=30,
        wrist_power=30,
    ),
    "LW": NHL05SkaterAttributes(
        balance=33,
        penalty=30,
        shot_accuracy=35,
        wrist_accuracy=33,
        faceoffs=20,
        acceleration=35,
        speed=35,
        potential=35,
        deking=33,
        checking=33,
        toughness=30,
        fighting=1,
        puck_control=33,
        agility=35,
        hero=30,
        aggression=30,
        pressure=30,
        passing=30,
        endurance=35,
        injury=35,
        slap_power=33,
        wrist_power=33,
    ),
    "RW": NHL05SkaterAttributes(
        balance=33,
        penalty=30,
        shot_accuracy=35,
        wrist_accuracy=33,
        faceoffs=20,
        acceleration=35,
        speed=35,
        potential=35,
        deking=33,
        checking=33,
        toughness=30,
        fighting=1,
        puck_control=33,
        agility=35,
        hero=30,
        aggression=30,
        pressure=30,
        passing=30,
        endurance=35,
        injury=35,
        slap_power=33,
        wrist_power=33,
    ),
    "D": NHL05SkaterAttributes(
        balance=38,
        penalty=30,
        shot_accuracy=25,
        wrist_accuracy=25,
        faceoffs=15,
        acceleration=30,
        speed=30,
        potential=30,
        deking=25,
        checking=40,
        toughness=35,
        fighting=1,
        puck_control=28,
        agility=30,
        hero=28,
        aggression=33,
        pressure=35,
        passing=33,
        endurance=38,
        injury=35,
        slap_power=35,
        wrist_power=25,
    ),
}

# The position `SKATER_DEFAULTS` falls back to. Left wing and right wing are
# identical to each other and centre is not, so a player whose position string
# the provider spelled some other way gets a centre's faceoff rating of 40
# rather than a winger's 20.
DEFAULT_POSITION = "C"

GOALIE_DEFAULTS = NHL05GoalieAttributes(
    breakaway=35,
    rebound_ctrl=35,
    shot_recovery=35,
    speed=25,
    poke_check=30,
    intensity=35,
    potential=35,
    toughness=25,
    fighting=0,
    agility=40,
    five_hole=35,
    passing=25,
    endurance=40,
    glove_high=35,
    stick_high=35,
    glove_low=35,
    stick_low=35,
)

# Default weight in pounds, about the 2004 league average. Written to `WEIG` as
# raw pounds.
DEFAULT_WEIGHT = 190

# The encoded `HEIG` every player gets, about 5'10". Upstream behaviour, known
# wrong, preserved deliberately: the branch that would compute another one cannot
# run. See `map_player`.
DEFAULT_HEIGHT = 16

# `HEIG` is five bits and encodes inches above `HEIGHT_BASE_INCHES`, so the
# scale runs 5'6" to 8'1".
HEIGHT_BASE_INCHES = 66
HEIGHT_MAX = 31

# Default jersey number. 1 is a goalie's number and is handed to skaters too; the
# disc's own number is overwritten either way.
DEFAULT_JERSEY = 1

# `HAND`: 0 is left, 1 is right, and right is the default. `HAND` is always
# written, so a provider that reports nothing makes every player right-handed
# rather than leaving the disc's value.
HAND_LEFT = 0
HAND_RIGHT = 1


def _clamp(val: int, lo: int = ATTR_MIN, hi: int = ATTR_MAX) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map `value` from the window [low, high] onto 0-63.

    An empty or inverted window answers 32, the midpoint, rather than dividing
    by zero. Values outside the window are clamped, so the window's ends are
    where the scale saturates and not where it starts to lie.
    """
    if high <= low:
        return 32
    ratio = (value - low) / (high - low)
    return _clamp(round(ratio * 63))


def _stat(stats: dict, *names: str, default: float = 0.0) -> float:
    """The first of `names` present and non-zero in `stats`, as a float.

    `or` rather than `in`: a stat reported as `0`, `""` or `None` falls through
    to the next name and finally to `default`, so a genuine zero and an absent
    stat both take the position default rather than a rating of zero.
    """
    for name in names:
        value = stats.get(name)
        if value:
            return float(value)
    return default


def _defaults_for(position: str) -> NHL05SkaterAttributes:
    """A fresh copy of one position's defaults.

    Copied, not shared: the module-level instance must not reach a record, or one
    later mutation rewrites every player on every team.
    """
    base = SKATER_DEFAULTS.get(position, SKATER_DEFAULTS[DEFAULT_POSITION])
    return replace(base)


class NHL05StatMapper:
    """Provider data in, NHL 2005 records out. Holds no state between calls."""

    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
    ) -> NHL05PlayerRecord:
        """Build one `NHL05PlayerRecord` from a provider `Player`.

        The name is split on the *first* space only, so everything after it goes
        to `LNME` and the tail past 15 characters is lost -- four characters
        shorter than NHL 07's field, so a name that survives there is cut here.

        `stats` empty or absent is not an error: it selects the position
        defaults, which is what a player with no games played gets.
        """
        pos = player.position.upper() if player.position else DEFAULT_POSITION
        is_goalie = pos == "G"

        parts = (player.name or "").split(" ", 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        jersey = player.number or DEFAULT_JERSEY

        hand = HAND_RIGHT
        if player.handedness == "L":
            hand = HAND_LEFT

        weight = int(player.weight) if player.weight > 0 else DEFAULT_WEIGHT

        # Upstream behaviour, known wrong, preserved deliberately: `Player` has
        # no `height`, so this branch never runs and every record leaves at
        # `DEFAULT_HEIGHT`, which the writer stamps over the disc's own heights.
        # Do not delete the dead branch and do not stop writing `HEIG`.
        height = DEFAULT_HEIGHT
        player_height = getattr(player, "height", 0) or 0
        if player_height > 0:
            height = max(0, min(HEIGHT_MAX, int(player_height) - HEIGHT_BASE_INCHES))

        record = NHL05PlayerRecord(
            first_name=first_name[:NAME_FIELD_CHARS],
            last_name=last_name[:NAME_FIELD_CHARS],
            position=pos,
            jersey_number=jersey,
            handedness=hand,
            weight=weight,
            height=height,
            team_index=MODERN_NHL_TO_NHL05.get(team_abbrev.upper(), 0),
            player_id=player.id if player.id else 0,
            is_goalie=is_goalie,
        )

        if is_goalie:
            record.goalie_attrs = (
                self._map_goalie_stats(stats) if stats else replace(GOALIE_DEFAULTS)
            )
        else:
            record.skater_attrs = (
                self._map_skater_stats(stats, pos) if stats else _defaults_for(pos)
            )
        return record

    def _map_skater_stats(self, stats: dict, pos: str) -> NHL05SkaterAttributes:
        """Derive 22 skater ratings from a season line.

        The scaling windows are one season's production, not a career:
        90 points, 40 goals and 55 assists each saturate their scale, and
        `+/-` is shifted by 30 so that -30 is the floor and +40 the ceiling.
        `PIM` drives toughness *and* aggression *and* fighting, so a penalty
        total is three of the twenty-two ratings on its own.
        """
        g = _stat(stats, "G")
        a = _stat(stats, "A")
        pts = _stat(stats, "PTS")
        pm = _stat(stats, "+/-")
        pim = _stat(stats, "PIM")
        shots = _stat(stats, "SOG", "Shots")
        fop = _stat(stats, "FO%", "FOW%")

        base = _defaults_for(pos)

        off_rating = _scale(pts, 0, 90)
        goal_rating = _scale(g, 0, 40)
        assist_rating = _scale(a, 0, 55)

        # Shooting percentage, and 10% -- roughly league average -- assumed for a
        # player with no shots recorded.
        shoot_pct = (g / max(shots, 1)) * 100 if shots > 0 else 10
        accuracy_rating = _scale(shoot_pct, 5, 20)

        def_rating = _scale(pm + 30, 0, 70)
        tough_rating = _scale(pim, 0, 80)

        speed_boost = 5 if pts > 50 else (3 if pts > 30 else 0)

        return NHL05SkaterAttributes(
            balance=_clamp(base.balance + (3 if pos == "D" else 0)),
            penalty=_clamp(base.penalty),
            shot_accuracy=_clamp(max(goal_rating, accuracy_rating)),
            wrist_accuracy=_clamp(max(goal_rating - 2, accuracy_rating)),
            faceoffs=_clamp(_scale(fop, 30, 60) if fop > 0 else base.faceoffs),
            acceleration=_clamp(base.acceleration + speed_boost),
            speed=_clamp(base.speed + speed_boost),
            potential=_clamp(off_rating + 5),
            deking=off_rating,
            checking=_clamp(def_rating if pos == "D" else base.checking),
            toughness=tough_rating,
            # Two bits, so 0-3, and clamped here rather than by `_clamp`, whose
            # ceiling is the six-bit 63. 40 penalty minutes to the point.
            fighting=min(3, max(0, int(pim / 40))),
            puck_control=off_rating,
            agility=_clamp(base.agility + speed_boost),
            hero=_clamp(off_rating),
            aggression=tough_rating,
            pressure=_clamp(def_rating),
            passing=assist_rating,
            endurance=_clamp(base.endurance + (3 if pts > 40 else 0)),
            injury=_clamp(base.injury),
            slap_power=goal_rating,
            wrist_power=_clamp(goal_rating - 3),
        )

    def _map_goalie_stats(self, stats: dict) -> NHL05GoalieAttributes:
        """Derive 17 goalie ratings from a season line.

        `SV%` scales over 0.880 to 0.930, the whole practical range of NHL save
        percentages, and it drives ten of the seventeen. `GAA` is inverted --
        3.5 minus the average, over a 1.5 window -- so a lower average is a
        higher rating. Wins add a flat bonus of up to 10, capped at 40 wins.

        Upstream behaviour, known wrong, preserved deliberately: `SV%` is read
        as a bare float against a window written as a fraction, so a line
        reporting `91.2` rather than `0.912` saturates ten of the seventeen
        ratings. Do not add the comparison against 1.0 here alone; a guard in one
        of the two games and not the other would be a game-shaped trap.

        `toughness`, `fighting` and `passing` are constants: no provider here
        supplies a goalie input for them, so do not invent a derivation.
        """
        svp = _stat(stats, "SV%")
        gaa = _stat(stats, "GAA", default=3.0)
        wins = _stat(stats, "W", "Wins")

        save_rating = _scale(svp, 0.880, 0.930)
        gaa_rating = _scale(3.5 - gaa, 0, 1.5)
        win_bonus = min(10, int(wins / 4))

        return NHL05GoalieAttributes(
            breakaway=_clamp(gaa_rating + win_bonus),
            rebound_ctrl=save_rating,
            shot_recovery=_clamp(save_rating - 3),
            speed=_clamp(25 + win_bonus),
            poke_check=_clamp(gaa_rating),
            intensity=_clamp(save_rating - 5 + win_bonus),
            potential=_clamp(save_rating + win_bonus),
            toughness=25,
            fighting=0,
            agility=save_rating,
            five_hole=save_rating,
            passing=25,
            endurance=_clamp(35 + win_bonus),
            glove_high=save_rating,
            stick_high=_clamp(save_rating - 2),
            glove_low=save_rating,
            stick_low=_clamp(save_rating - 2),
        )

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        max_players: int = MAX_PLAYERS,
    ) -> list[Player]:
        """Pick and order a roster: goalies, then forwards, then defence.

        `generate_team_line_flags` and `patcher.patch` both read this order, so
        it is part of the contract. Within each group players sort by production
        -- points for a skater, save percentage for a goalie -- highest first.

        Build forwards line by line rather than by taking the top 14: best
        centre, best left wing and best right wing form line one, and so on for
        four lines, then the rest by points. Taking the top 14 outright would put
        four centres on line one.

        `sort_key` reads `stats` by the player's id as a *string*: both providers
        key their leaders payload that way.
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            if p.position == "G":
                # Scaled by 1000 only so goalies sort on a number of the same
                # magnitude as a points total; nothing reads the value itself.
                # The bare `SV%` again: a roster file mixing the two conventions
                # starts the worse goalie. See `_map_goalie_stats`.
                return _stat(ps, "SV%") * 1000
            return _stat(ps, "PTS")

        def by_position(code: str) -> list[Player]:
            return sorted([p for p in players if p.position == code], key=sort_key, reverse=True)

        centers = by_position("C")
        left_wings = by_position("LW")
        right_wings = by_position("RW")
        defensemen = by_position("D")
        goalies = by_position("G")

        forwards: list[Player] = []
        for i in range(FORWARD_LINES):
            for pool in (centers, left_wings, right_wings):
                if i < len(pool):
                    forwards.append(pool[i])

        # `id()` and not the player's own id: two `Player` objects for the same
        # person are two entries in `players` and each belongs in the pool once.
        used = {id(p) for p in forwards}
        extras = sorted(
            [p for p in centers + left_wings + right_wings if id(p) not in used],
            key=sort_key,
            reverse=True,
        )
        forwards.extend(extras)
        forwards = forwards[:FORWARDS_PER_TEAM]

        selected = goalies[:GOALIES_PER_TEAM] + forwards + defensemen[:DEFENCEMEN_PER_TEAM]

        # Anything left -- an unrecognised position, or a defenceman past the
        # seven -- fills the remaining slots by production.
        all_used = {id(p) for p in selected}
        leftover = sorted([p for p in players if id(p) not in all_used], key=sort_key, reverse=True)
        remaining = max_players - len(selected)
        if remaining > 0:
            selected.extend(leftover[:remaining])

        return selected[:max_players]

    def get_team_slot(self, team_abbrev: str) -> int | None:
        """The ROM slot for a modern team code, or None if the game has none."""
        return MODERN_NHL_TO_NHL05.get(team_abbrev.upper())

    def generate_team_line_flags(
        self,
        players: list[NHL05PlayerRecord],
    ) -> list[dict[str, int]]:
        """One flag dict per player, in the order the players were given.

        Lines come from natural positions first and gaps are filled with spare
        centres. A winger is never moved to centre, so a team with no centres
        ices four lines with no centre.

        The power play takes line one's forwards and the top defence pair; the
        penalty kill takes line two's forwards and the next pair. `H1__`-`H5__`
        and `S1__`-`S5__` are five slots each and both fill positionally, so a
        team short of defencemen gets a four-man power play rather than a
        forward in a defenceman's slot.

        A player who reaches none of those groups gets an empty dict, which
        `rom_writer.roster_values` turns into all sixty-four flags zeroed.

        Upstream behaviour, known wrong, preserved deliberately: the defence
        pairs are emitted as `3{pair}{side}`, which is NHL 07's spelling. On this
        game `31`/`32` are five-on-three units, `33LD`/`33RD` do not exist, and
        the even-strength pairs `L1LD` through `L3RD` are left zero, so a patched
        team has no even-strength pairing. Do not change the `3` to an `L`
        without a field-name dump from a retail DVD.
        """
        result: list[dict[str, int]] = [{} for _ in players]

        goalies = [(i, p) for i, p in enumerate(players) if p.is_goalie]
        centers = [(i, p) for i, p in enumerate(players) if p.position == "C"]
        left_w = [(i, p) for i, p in enumerate(players) if p.position == "LW"]
        right_w = [(i, p) for i, p in enumerate(players) if p.position == "RW"]
        defense = [(i, p) for i, p in enumerate(players) if p.position == "D"]

        for gi, (idx, _) in enumerate(goalies[:GOALIES_PER_TEAM]):
            result[idx][f"G{gi + 1}__"] = 1

        c_pool = list(centers)
        lw_pool = list(left_w)
        rw_pool = list(right_w)

        fwd_line_indices: list[int] = []

        for line in range(FORWARD_LINES):
            line_num = line + 1

            if c_pool:
                idx, _ = c_pool.pop(0)
                result[idx][f"L{line_num}C_"] = 1
                fwd_line_indices.append(idx)

            for wing, pool in (("LW", lw_pool), ("RW", rw_pool)):
                source = pool if pool else c_pool
                if source:
                    idx, _ = source.pop(0)
                    result[idx][f"L{line_num}{wing}"] = 1
                    fwd_line_indices.append(idx)

        d_line_indices: list[int] = []
        for di, (idx, _) in enumerate(defense[: DEFENCE_PAIRS * 2]):
            pair = di // 2 + 1
            side = "LD" if di % 2 == 0 else "RD"
            # `3`, not `L`: NHL 07's spelling, kept deliberately. See above.
            result[idx][f"3{pair}{side}"] = 1
            d_line_indices.append(idx)

        # Three forwards and two defencemen. `fwd_line_indices` is positional and
        # not keyed by line, so [:3] is line one only when line one was complete;
        # a team missing a right wing puts line two's centre in the third slot.
        pp_candidates = fwd_line_indices[:3] + d_line_indices[:2]
        for hi, idx in enumerate(pp_candidates[:SPECIAL_TEAMS_UNIT]):
            result[idx][f"H{hi + 1}__"] = 1

        pk_candidates = fwd_line_indices[3:6] + d_line_indices[2:4]
        for si, idx in enumerate(pk_candidates[:SPECIAL_TEAMS_UNIT]):
            result[idx][f"S{si + 1}__"] = 1

        return result
