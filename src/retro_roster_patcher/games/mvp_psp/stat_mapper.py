"""Turn ESPN's MLB rosters and team leaders into MVP Baseball's 0-99 ratings.

Two inputs per player: the roster entry (`sports.models.Player`) and, when the
provider had one, a dict of season totals keyed by ESPN's own abbreviations --
`AVG`, `OPS`, `ERA`, `WHIP` and so on. **The leaders endpoint only returns each
category's leaders**, so most players have a partial dict and some have none at
all, and the position defaults below are what a player with no dict gets.

Everything here is arithmetic on those two inputs. Nothing reads the ROM and
nothing reaches the network, so `map_rosters` runs on a machine that has never
seen the ISO.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...sports.models import Player
from .models import (
    ATTR_MAX,
    ATTR_MIN,
    BATTERS_PER_TEAM,
    MODERN_MLB_TO_MVP,
    MVP_ABBREV_TO_INDEX,
    PITCH_CHANGEUP,
    PITCH_FASTBALL,
    PITCH_SLIDER,
    RELIEVERS_PER_TEAM,
    SELECTION_POSITIONS,
    STARTERS_PER_TEAM,
    MVPPitch,
    MVPPlayerRecord,
)

# The position strings a provider may use, mapped to the nine this game has.
# `OF` and `IF` are the two that collapse: a player listed as a generic
# outfielder becomes a centre fielder and a generic infielder a shortstop.
POSITION_ALIASES: dict[str, str] = {
    "C": "C",
    "1B": "1B",
    "2B": "2B",
    "3B": "3B",
    "SS": "SS",
    "LF": "LF",
    "CF": "CF",
    "RF": "RF",
    "DH": "DH",
    "OF": "CF",
    "IF": "SS",
}

# What an unrecognised position becomes.
DEFAULT_POSITION = "CF"

# The provider position strings that mean "pitcher".
PITCHER_POSITIONS = frozenset({"P", "SP", "RP", "CL", "CP"})

# The one provider position string that means "starting pitcher". Everything
# else in `PITCHER_POSITIONS` is treated as relief, including the bare `P` that
# ESPN uses for most of a staff -- which is why `select_roster` has to top the
# rotation up out of the relief pool.
STARTER_POSITION = "SP"

# Name suffixes dropped from a surname, upper-cased and with any trailing dot
# removed before the comparison.
NAME_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV"})

# What a player with no name at all is called.
UNNAMED = "Player"

# Batting and throwing hand codes, as the `attrib` table stores them.
BATS_RIGHT = 0
BATS_LEFT = 1
BATS_SWITCH = 2
THROWS_RIGHT = 0
THROWS_LEFT = 1


@dataclass(frozen=True)
class PositionDefaults:
    """What a player at one position gets when the provider reported no stats.

    Frozen, and read through `POSITION_DEFAULTS[pos]`, so there is no shared
    mutable default object to hand to every record -- the defect
    `games/nhl94_genesis` fixed with `dataclasses.replace` and which this game
    did not have, because the source read its dicts and never stored one.
    """

    speed: int
    fielding: int
    arm_range: int
    throw_strength: int
    throw_accuracy: int
    contact: int
    power: int


POSITION_DEFAULTS: dict[str, PositionDefaults] = {
    "C": PositionDefaults(35, 60, 55, 65, 60, 55, 50),
    "1B": PositionDefaults(30, 50, 45, 55, 55, 60, 65),
    "2B": PositionDefaults(55, 65, 60, 50, 65, 55, 35),
    "3B": PositionDefaults(40, 55, 55, 70, 60, 55, 55),
    "SS": PositionDefaults(55, 70, 65, 65, 65, 55, 35),
    "LF": PositionDefaults(55, 50, 50, 55, 55, 60, 55),
    "CF": PositionDefaults(65, 60, 65, 60, 55, 55, 45),
    "RF": PositionDefaults(50, 55, 55, 70, 60, 60, 60),
    "DH": PositionDefaults(30, 30, 30, 40, 40, 65, 70),
}

# Ratings a pitcher gets for the things pitchers are not rated on. Applied
# whether or not stats were found, at slightly different values for each --
# the source's, preserved, and the difference is small enough to be an accident
# rather than a judgement, so it is named rather than harmonised.
PITCHER_NO_STATS_BATTING = (25, 15)  # contact, power
PITCHER_WITH_STATS_BATTING = (20, 10)

DEFAULT_STARTER_STAMINA = 70
DEFAULT_RELIEVER_STAMINA = 35
DEFAULT_PICKOFF = 50


def _clamp(val: int, lo: int = ATTR_MIN, hi: int = ATTR_MAX) -> int:
    return max(lo, min(hi, val))


def _scale(value: float, low: float, high: float) -> int:
    """Map `value` from the range `[low, high]` onto 0-99, clamped at both ends.

    An inverted or empty range answers 50, the scale's midpoint. That branch is
    reachable only from a caller that hardcodes the range, and every caller
    here hardcodes a valid one, so it is a guard against a future edit rather
    than a live path.
    """
    if high <= low:
        return 50
    return _clamp(round((value - low) / (high - low) * ATTR_MAX))


def _stat(stats: dict[str, Any], key: str, default: float) -> float:
    """One statistic as a float, with `default` for absent, None, empty or zero.

    **`or default` treats a reported zero as an absence**, which is the source's
    behaviour and is preserved because it is right for the keys it is used on
    and wrong for none of them: a pitcher with an ERA of exactly 0.00 has not
    thrown enough innings for the number to mean anything, and a batter with an
    OPS of exactly 0.000 is a pitcher taking at-bats. It is stated here because
    it is not what the expression looks like it does.
    """
    return float(stats.get(key, default) or default)


class MVPStatMapper:
    """Maps ESPN player data onto `MVPPlayerRecord`.

    Stateless. The source's class was too, apart from `team_stats`, which lived
    on the *patcher* rather than here.
    """

    # -- batters ------------------------------------------------------------

    def map_batter(self, player: Player, stats: dict[str, Any] | None = None) -> MVPPlayerRecord:
        """One position player.

        With stats, every rating is derived. Without, the position defaults are
        copied in and the four that have no positional default -- durability,
        plate discipline, bunting, baserunning, stealing -- take fixed values.
        """
        pos = self.normalize_position(player.position)
        defaults = POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS[DEFAULT_POSITION])

        rec = MVPPlayerRecord(
            first_name=self.first_name(player.name),
            last_name=self.last_name(player.name),
            jersey=player.number or 0,
            bats=self.map_bat_hand(player.bats or player.handedness),
            throws=self.map_throw_hand(player.handedness),
            primary_position=pos,
            # DELIBERATE DIVERGENCE: the source never read a weight from
            # anywhere, so `MVPPlayerRecord.weight` kept its default of 190 and
            # every one of the 750 patched players was written at 190 lb. ESPN's
            # MLB roster endpoint reports a weight per athlete; it was simply
            # never parsed into `sports.models.Player`, which has had the field
            # all along. A player the provider has no weight for keeps 0 here,
            # and `patcher._build_attrib_fields` then omits the column so the
            # disc's own value survives.
            weight=int(player.weight),
            is_pitcher=False,
        )

        if stats:
            return self._apply_batter_stats(rec, stats, defaults)

        rec.speed = defaults.speed
        rec.fielding = defaults.fielding
        rec.arm_range = defaults.arm_range
        rec.throw_strength = defaults.throw_strength
        rec.throw_accuracy = defaults.throw_accuracy
        rec.contact_rhp = defaults.contact
        rec.power_rhp = defaults.power
        rec.contact_lhp = defaults.contact
        rec.power_lhp = defaults.power
        rec.durability = 50
        rec.plate_discipline = 50
        rec.bunting = 40
        rec.baserunning = defaults.speed
        rec.stealing = defaults.speed
        return rec

    def _apply_batter_stats(
        self,
        rec: MVPPlayerRecord,
        stats: dict[str, Any],
        defaults: PositionDefaults,
    ) -> MVPPlayerRecord:
        """Derive a batter's ratings from his season totals.

        The source also read `OPS` here, with a default of 0.700, and never used
        the value. Dropped: `_stat` is pure, so reading it had no effect beyond
        suggesting to a reader that on-base-plus-slugging fed a rating. It does
        feed one -- `select_roster`'s batting order -- and not any of these.
        """
        avg = _stat(stats, "AVG", 0.250)
        hr = _stat(stats, "HR", 0)
        rbi = _stat(stats, "RBI", 0)
        sb = _stat(stats, "SB", 0)
        slg = _stat(stats, "SLG", 0.400)
        obp = _stat(stats, "OBP", 0.320)
        hits = _stat(stats, "H", 0)
        gp = _stat(stats, "GP", 0)

        # Contact from average, with a quarter of the on-base rating added.
        rec.contact_rhp = _clamp(_scale(avg, 0.200, 0.330) + _scale(obp, 0.280, 0.420) // 4)
        # Five points lower against same-side pitching. Applied before the
        # switch-hitter check below, which averages the two back together.
        rec.contact_lhp = _clamp(rec.contact_rhp - 5)

        # Power two-thirds from home runs and one-third from slugging.
        rec.power_rhp = _clamp((_scale(hr, 0, 45) * 2 + _scale(slg, 0.350, 0.600)) // 3)
        rec.power_lhp = _clamp(rec.power_rhp - 5)

        if rec.bats == BATS_SWITCH:
            # A switch hitter has no weak side, so the two splits collapse to
            # their mean -- which is 2 or 3 points below the stronger side, not
            # equal to it, because the mean of x and x-5 is x-2.
            rec.contact_rhp = rec.contact_lhp = (rec.contact_rhp + rec.contact_lhp) // 2
            rec.power_rhp = rec.power_lhp = (rec.power_rhp + rec.power_lhp) // 2

        rec.speed = _scale(sb, 0, 40)
        if _stat(stats, "3B", 0) >= 5:
            rec.speed = _clamp(rec.speed + 5)
        rec.baserunning = _clamp(rec.speed + 5)
        rec.stealing = rec.speed

        rec.fielding = defaults.fielding
        if gp > 120:
            rec.fielding = _clamp(rec.fielding + 5)
        rec.arm_range = defaults.arm_range
        rec.throw_strength = defaults.throw_strength
        rec.throw_accuracy = defaults.throw_accuracy

        # On-base minus average is a walk-rate proxy: the gap is entirely walks
        # and hit-by-pitches.
        rec.plate_discipline = _scale(obp - avg, 0.040, 0.120)
        rec.durability = _scale(gp, 60, 155)
        rec.bunting = 30 if rec.speed < 50 else _clamp(rec.speed - 10)
        rec.starpower = _scale(hits * 0.3 + hr * 2 + rbi * 0.5, 20, 200)
        return rec

    # -- pitchers -----------------------------------------------------------

    def map_pitcher(
        self,
        player: Player,
        stats: dict[str, Any] | None = None,
        *,
        is_starter: bool = True,
    ) -> MVPPlayerRecord:
        """One pitcher.

        DELIBERATE DIVERGENCE -- **the arsenal is no longer overwritten.** The
        source ended this method with an unconditional

            rec.pitches = self._default_pitches(is_starter)

        *outside* the `if stats:` branch, after `_apply_pitcher_stats` had
        already computed a velocity from strikeouts and a control from WHIP and
        ERA and stored them through `_default_pitches(is_starter, vel,
        control)`. The second assignment discarded the first, so every pitcher
        in the game shipped with the same 50-velocity, 50-control arsenal and
        the whole derivation -- twelve lines, four statistics -- was dead code.
        Here the arsenal is set once, on whichever branch ran.
        """
        rec = MVPPlayerRecord(
            first_name=self.first_name(player.name),
            last_name=self.last_name(player.name),
            jersey=player.number or 0,
            bats=self.map_bat_hand(player.bats or player.handedness),
            throws=self.map_throw_hand(player.handedness),
            primary_position=STARTER_POSITION if is_starter else "RP",
            weight=int(player.weight),  # see `map_batter`
            is_pitcher=True,
        )

        if stats:
            return self._apply_pitcher_stats(rec, stats, is_starter)

        rec.stamina = DEFAULT_STARTER_STAMINA if is_starter else DEFAULT_RELIEVER_STAMINA
        rec.pickoff = DEFAULT_PICKOFF
        rec.speed = 35
        rec.fielding = 40
        rec.contact_rhp, rec.power_rhp = PITCHER_NO_STATS_BATTING
        rec.contact_lhp, rec.power_lhp = PITCHER_NO_STATS_BATTING
        rec.pitches = self.default_pitches(is_starter)
        return rec

    def _apply_pitcher_stats(
        self,
        rec: MVPPlayerRecord,
        stats: dict[str, Any],
        is_starter: bool,
    ) -> MVPPlayerRecord:
        """Derive a pitcher's ratings from his season totals."""
        era = _stat(stats, "ERA", 4.00)
        k = _stat(stats, "K", 0)
        whip = _stat(stats, "WHIP", 1.30)
        w = _stat(stats, "W", 0)
        sv = _stat(stats, "SV", 0)
        qs = _stat(stats, "QS", 0)

        if is_starter:
            # Quality starts, then a bonus for a fifteen-win season, then a
            # floor of 40 -- so a starter with no quality starts at all still
            # goes six innings rather than being pulled in the second.
            rec.stamina = _scale(qs, 5, 25)
            if w >= 15:
                rec.stamina = _clamp(rec.stamina + 5)
            rec.stamina = _clamp(rec.stamina, 40, ATTR_MAX)
        else:
            rec.stamina = _clamp(25 + (5 if sv > 20 else 0))

        rec.pickoff = DEFAULT_PICKOFF

        # Strikeout total as a velocity proxy, on the range each role can reach:
        # 250 strikeouts is a league-leading starter, 90 a league-leading
        # reliever.
        velocity = _scale(k, 60, 250) if is_starter else _scale(k, 20, 90)

        # Control is the mean of two inverted scales, so a *lower* WHIP and a
        # *lower* ERA both raise it.
        control = (_scale(1.60 - whip, 0.0, 0.70) + _scale(6.0 - era, 0.0, 4.0)) // 2

        rec.pitches = self.default_pitches(is_starter, velocity, control)

        rec.contact_rhp, rec.power_rhp = PITCHER_WITH_STATS_BATTING
        rec.contact_lhp, rec.power_lhp = PITCHER_WITH_STATS_BATTING
        rec.speed = 30
        rec.fielding = 40

        if is_starter:
            composite = w * 3 + k * 0.1 + (6.0 - era) * 10
        else:
            composite = sv * 3 + k * 0.1 + (4.0 - era) * 5
        rec.starpower = _scale(composite, 10, 80)
        return rec

    def default_pitches(
        self,
        is_starter: bool,
        velocity: int = 50,
        control: int = 50,
    ) -> list[MVPPitch]:
        """A pitcher's arsenal: three pitches for a starter, two for a reliever.

        The fastball is the hardest and the changeup the softest, and each pitch
        after the first trades control for movement. The `type` of the first
        pitch is stored but never written -- column 8 is the first type column
        and pitch 1 is always a fastball, so the game has no column for it.

        **`movement` is not clamped for the fastball** and is for the other two.
        `velocity // 2` cannot exceed 49 so the clamp would never fire, which is
        presumably why the source omitted it; it is left omitted so the two
        forms do not look like they mean different things.
        """
        pitches = [
            MVPPitch(
                type=PITCH_FASTBALL,
                movement=velocity // 2,
                control=control,
                velocity=_clamp(velocity + 10),
            )
        ]
        if is_starter:
            pitches.append(
                MVPPitch(
                    type=PITCH_SLIDER,
                    movement=_clamp(velocity // 2 + 5),
                    control=_clamp(control - 5),
                    velocity=_clamp(velocity - 5),
                )
            )
            pitches.append(
                MVPPitch(
                    type=PITCH_CHANGEUP,
                    movement=_clamp(velocity // 3),
                    control=control,
                    velocity=_clamp(velocity - 15),
                )
            )
        else:
            pitches.append(
                MVPPitch(
                    type=PITCH_SLIDER,
                    movement=_clamp(velocity // 2),
                    control=_clamp(control - 5),
                    velocity=_clamp(velocity - 5),
                )
            )
        return pitches

    # -- roster selection ---------------------------------------------------

    def select_roster(
        self,
        players: list[Player],
        stats: dict[str, Any] | None = None,
    ) -> list[Player]:
        """Order a squad into the 25 slots the game has, best first within each group.

            [0-14]  batters -- one per lineup position, then the best of the rest
            [15-19] the rotation
            [20-24] the bullpen, closer first

        Shorter than 25 when the squad is: a team the provider gave nine players
        for produces nine, and `patch` writes nine.

        The three sort keys are composites rather than tuples, so a single
        number decides each: on-base-plus-slugging times a thousand plus hits
        for a batter, wins times a hundred plus innings for a starter, saves
        times a hundred plus ten-minus-ERA for a reliever. Each is the source's.
        The reliever key's `10 - era` term is what breaks ties between pitchers
        with no saves, and it can go negative for an ERA above 10 without
        disordering anything, because it is bounded by the 100-point save step.
        """
        stats = stats or {}
        pitchers = [p for p in players if self.is_pitcher(p)]
        batters = [p for p in players if not self.is_pitcher(p)]

        def batter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "OPS", 0) * 1000 + _stat(ps, "H", 0)

        def starter_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "W", 0) * 100 + _stat(ps, "IP", 0)

        def reliever_sort(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            return _stat(ps, "SV", 0) * 100 + (10 - _stat(ps, "ERA", 9.0))

        starters = [p for p in pitchers if (p.position or "").upper() == STARTER_POSITION]
        relievers = [p for p in pitchers if (p.position or "").upper() != STARTER_POSITION]
        starters.sort(key=starter_sort, reverse=True)
        relievers.sort(key=reliever_sort, reverse=True)

        selected_starters = starters[:STARTERS_PER_TEAM]
        selected_relievers = relievers[:RELIEVERS_PER_TEAM]

        # Whichever group came up short takes from whatever is left over, in
        # the order the leftovers already have -- surplus starters first, then
        # surplus relievers. The rotation is topped up before the bullpen, so a
        # staff ESPN lists entirely as `P` fills five rotation slots out of the
        # relief pool before any of them reaches the bullpen.
        remaining = starters[STARTERS_PER_TEAM:] + relievers[RELIEVERS_PER_TEAM:]
        while len(selected_starters) < STARTERS_PER_TEAM and remaining:
            selected_starters.append(remaining.pop(0))
        while len(selected_relievers) < RELIEVERS_PER_TEAM and remaining:
            selected_relievers.append(remaining.pop(0))

        batters.sort(key=batter_sort, reverse=True)
        return self._select_position_players(batters) + selected_starters + selected_relievers

    def _select_position_players(self, batters: list[Player]) -> list[Player]:
        """Fifteen batters: one per lineup position first, then the best left over.

        `batters` arrives sorted best-first, so the player taken for a position
        is the best available at it. A position nobody plays is skipped rather
        than filled, and the bench then runs longer.

        Identity, not equality, decides whether a player has been used: two
        distinct `Player` objects with the same id are two roster entries as far
        as this is concerned, which is the source's behaviour and the only one
        available -- `Player` is not hashable and comparing by id would drop a
        provider's duplicate entry silently.

        The order positions are filled in is `SELECTION_POSITIONS`, which is
        **not** the order the lineup is written in; `models` says why.
        """
        by_pos: dict[str, list[Player]] = {}
        for p in batters:
            by_pos.setdefault(self.normalize_position(p.position), []).append(p)

        selected: list[Player] = []
        used: set[int] = set()

        for pos in SELECTION_POSITIONS:
            for candidate in by_pos.get(pos, []):
                if id(candidate) not in used:
                    selected.append(candidate)
                    used.add(id(candidate))
                    break

        for p in batters:
            if len(selected) >= BATTERS_PER_TEAM:
                break
            if id(p) not in used:
                selected.append(p)
                used.add(id(p))

        return selected[:BATTERS_PER_TEAM]

    # -- small conversions --------------------------------------------------

    @staticmethod
    def is_pitcher(player: Player) -> bool:
        return (player.position or "").upper() in PITCHER_POSITIONS

    @staticmethod
    def normalize_position(position: str) -> str:
        """A provider's position string as one of this game's nine.

        The source's version took an `is_pitcher` flag and returned `"SP"` when
        it was set. Every call site passed `False`; the pitcher branch was
        never taken, and `map_pitcher` sets the position itself from
        `is_starter`. The parameter is gone.
        """
        return POSITION_ALIASES.get((position or "").upper(), DEFAULT_POSITION)

    @staticmethod
    def map_bat_hand(handedness: str | None) -> int:
        """0 right, 1 left, 2 switch. Anything unrecognised is right-handed."""
        if not handedness:
            return BATS_RIGHT
        h = handedness.upper()
        if h == "L":
            return BATS_LEFT
        if h in ("S", "B"):
            return BATS_SWITCH
        return BATS_RIGHT

    @staticmethod
    def map_throw_hand(handedness: str | None) -> int:
        """0 right, 1 left. There is no switch-throwing code."""
        if not handedness:
            return THROWS_RIGHT
        return THROWS_LEFT if handedness.upper() == "L" else THROWS_RIGHT

    @staticmethod
    def first_name(full_name: str) -> str:
        parts = full_name.strip().split()
        return parts[0] if parts else UNNAMED

    @staticmethod
    def last_name(full_name: str) -> str:
        """Everything after the first word, minus a generational suffix.

        A one-word name is its own surname. A name that is *only* a first name
        and a suffix -- "Ken Jr." -- keeps the last word, because dropping it
        would leave the surname empty.
        """
        parts = full_name.strip().split()
        if len(parts) <= 1:
            return parts[0] if parts else UNNAMED
        kept = [p for p in parts[1:] if p.rstrip(".").upper() not in NAME_SUFFIXES]
        return " ".join(kept) if kept else parts[-1]

    @staticmethod
    def get_team_slot(team_abbrev: str) -> int | None:
        """The ROM slot a provider's team abbreviation maps to, or None."""
        mvp_abbrev = MODERN_MLB_TO_MVP.get(team_abbrev.upper())
        if mvp_abbrev is None:
            return None
        return MVP_ABBREV_TO_INDEX.get(mvp_abbrev)

    @staticmethod
    def get_mvp_abbrev(team_abbrev: str) -> str | None:
        return MODERN_MLB_TO_MVP.get(team_abbrev.upper())
