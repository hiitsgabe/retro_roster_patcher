"""Turn provider roster and stat data into NHL 2005's 0-63 attribute scale.

**This module and `games/nhl07_psp/stat_mapper.py` are the same arithmetic.**
Measured: the two source files differ in the class and dataclass names, the
game's year in seven docstrings, and one number -- the name truncation width,
15 here and 19 there. Every formula, every scaling window and all 39 position
default values are identical.

They are *not* shared, and that is deliberate rather than an oversight. What
would be shared is a mapper parameterised on a name width and returning one of
two unrelated pairs of dataclasses, and the two games would then be coupled
through a module neither of them owns: a change made for NHL 2005's six-bit
`FACE` field would silently reach NHL 07's. The precedent in this library is
that a format is shared when it is a format (`formats/ea_tdb.py`,
`formats/iso9660.py`) and a *policy* is not. These numbers are a policy -- one
person's guess in 2024 at what a 40-goal season is worth on a 0-63 scale -- and
`games/mvp_psp` will have a third, different one for baseball.

The formulas, the scaling windows and the position defaults are transcribed
unchanged, including the places where they are visibly rough, because changing
them changes every rating on every patched disc and there is no reference to
check a change against; the audit that accompanies this migration asserts
value-identical output against the source over 4 000 mapped players, 600 roster
selections and 800 line-flag assignments.

Two exceptions, both labelled where they live: `_save_percentage`, and the
defence-pair spelling in `generate_team_line_flags`. The audit still holds for
the first -- every input it ran on reports `SV%` as a fraction, which
`_save_percentage` leaves alone.
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
# scratches; 25 is what the source asked for, so a larger number would simply be
# truncated by however many ROST rows the disc gives a team in `patcher.patch`.
MAX_PLAYERS = 25

# The shape `select_roster` builds towards: two goalies, four forward lines of
# three, two spare forwards, and seven defencemen.
GOALIES_PER_TEAM = 2
FORWARDS_PER_TEAM = 14
DEFENCEMEN_PER_TEAM = 7
FORWARD_LINES = 4

# Three pairs, and on this game all three now reach the disc: NHL 2005's ROST
# names `L1LD`/`L1RD` through `L3LD`/`L3RD`, which is exactly three. The source
# emitted `3nLD`/`3nRD` and lost the third to `rom_writer.roster_values`; see the
# DELIBERATE DIVERGENCE note on `generate_team_line_flags`.
DEFENCE_PAIRS = 3

SPECIAL_TEAMS_UNIT = 5

# Per-position starting points, used whole when a player has no stats and as the
# base for several derived ratings when he does. A frozen dict of dataclass
# instances would still be one shared object per position, so every read of these
# goes through `dataclasses.replace` -- see `_defaults_for`.
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

# Default weight in pounds for a player the provider reports nothing for. 190 lb
# is about the 2004 league average. Written to `WEIG` as raw pounds.
DEFAULT_WEIGHT = 190

# Default jersey number for a player without one. 1 is a goalie's number and is
# handed to skaters too; the disc's own number is overwritten either way.
DEFAULT_JERSEY = 1

# `HAND`: 0 is left, 1 is right, and right is the default. A provider that
# reports nothing therefore makes every player right-handed rather than leaving
# the disc's value, because `HAND` is always written.
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

    `or` rather than `in`, matching the source: a stat reported as `0`, as `""`
    or as `None` all fall through to the next name and finally to `default`.
    That makes a genuine zero indistinguishable from an absent stat, which for
    every caller below is the intended reading -- a skater with no recorded
    shots and a skater the provider has no shot data for both get the position
    default rather than a rating of zero.
    """
    for name in names:
        value = stats.get(name)
        if value:
            return float(value)
    return default


#: The largest `SV%` that can be a fraction. A goalie cannot stop more shots
#: than he faces, so anything above this is a percentage and not a rate.
SAVE_PCT_FRACTION_MAX = 1.0

#: What a percentage has to be divided by to become the fraction the scaling
#: windows are written in.
SAVE_PCT_PER_CENT = 100.0


def _save_percentage(stats: dict) -> float:
    """`SV%` as a fraction, whichever convention the provider reported it in.

    DELIBERATE DIVERGENCE, and the identical one `games/nhl07_psp` carries --
    this module is that one with the type names substituted, so a guard in one
    and not the other would be a game-shaped trap. The source read `SV%` as a
    bare float against a window of 0.880 to 0.930, so a line reporting `91.2`
    instead of `0.912` saturated `_scale` and gave the goalie 63 for all eight
    of the ratings save percentage drives. Every goalie came out identical and
    maximal and nothing reported it.

    Reachable: `cli` `patch --rosters` reads a whole `LeagueData` out of a JSON
    file the operator supplies, `extra["leaders"]` included, and human-facing
    sources of hockey statistics overwhelmingly print `91.2`.

    A unit conversion and not a guess -- a save percentage expressed as a
    fraction cannot exceed 1.0, because a goalie cannot save more shots than he
    faces. `1.0` itself stays a fraction, a perfect game.

    BYTE-IDENTITY: unchanged for any provider reporting fractions, which is
    every input in this repository. `games/nhl07_psp/stat_mapper.py` carries the
    long form of the argument, including why `select_roster`'s goalie sort key
    goes through this too: within one convention it moves nothing, across two it
    decides which goalie starts.
    """
    svp = _stat(stats, "SV%")
    if svp > SAVE_PCT_FRACTION_MAX:
        return svp / SAVE_PCT_PER_CENT
    return svp


def _defaults_for(position: str) -> NHL05SkaterAttributes:
    """A fresh copy of one position's defaults.

    `dataclasses.replace` with no changes, so the caller can never be handed the
    module-level instance itself. Handing that object out is how two of the
    migrated games ended up with one attribute record shared by every player on
    every team, where a single later mutation rewrote the whole league.
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

        The name is split on the *first* space only, so "Pierre-Luc Dubois"
        keeps its surname whole and "Ryan Nugent-Hopkins" does too, while
        "Jean-Sebastien Van Der Meer" would put everything after the first space
        in `LNME` and lose the tail to the 15-character field -- which is four
        characters shorter than NHL 07's, so a name that survives there is
        cut here. Both fields are
        truncated here as well as in the writer; the writer's truncation is the
        one that protects the record and this one is what the caller sees.

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

        record = NHL05PlayerRecord(
            first_name=first_name[:NAME_FIELD_CHARS],
            last_name=last_name[:NAME_FIELD_CHARS],
            position=pos,
            jersey_number=jersey,
            handedness=hand,
            weight=weight,
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

        # Shooting percentage, and 10% assumed for a player with no shots
        # recorded -- roughly league average, so he is neither rewarded nor
        # punished for the missing stat. `max(shots, 1)` guards the division
        # separately; with `shots` zero the ternary has already taken the other
        # branch, so the guard is unreachable and is kept as the source had it.
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

        `SV%` scales over 0.880 to 0.930, which is the whole practical range of
        NHL save percentages, and it drives eight of the seventeen. `GAA` is
        inverted -- 3.5 minus the average, over a 1.5 window -- so a lower
        average is a higher rating. Wins add a flat bonus of up to 10, capped at
        40 wins.

        The `SV%` window is a fraction. A provider reporting a percentage used
        to saturate all eight at 63; `_save_percentage` now converts.

        INHERITED DEFECT, PRESERVED DELIBERATELY: `toughness`, `fighting` and
        `passing` are constants, written from no stat at all and equal to
        `GOALIE_DEFAULTS`', so a goalie the provider has no line for and a
        Vezina winner come out equal on all three. Not fixed, and
        `games/nhl07_psp/stat_mapper.py` carries the reason: there is no goalie
        toughness input to fix it from, and inventing a derivation would be new
        behaviour dressed as a bug fix.
        """
        svp = _save_percentage(stats)
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

        The order is what `generate_team_line_flags` and `patcher.patch` both
        read afterwards, so it is part of the contract and not a presentation
        choice. Within each group players are sorted by production -- points for
        a skater, save percentage for a goalie -- highest first, so the first
        centre taken is the first-line centre.

        Forwards are built line by line rather than by taking the top 14: the
        best centre, best left wing and best right wing form line one, and so on
        for four lines, and only then are the remaining forwards appended by
        points. Taking the top 14 outright would put four centres on line one.

        `sort_key` reads `stats` by the player's id as a *string*, because both
        providers key their leaders payload that way.
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            ps = stats.get(str(p.id), {})
            if p.position == "G":
                # Scaled by 1000 only so goalies sort against each other on a
                # number of the same magnitude as a points total; nothing reads
                # the value itself. Through `_save_percentage` rather than
                # `_stat` so the module reads one field one way; no ordering can
                # change, since dividing by 100 is monotonic.
                return _save_percentage(ps) * 1000
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
        # person would be two entries in `players` and both belong in the pool
        # exactly once each, which is what object identity answers. The source
        # did the same.
        used = {id(p) for p in forwards}
        extras = sorted(
            [p for p in centers + left_wings + right_wings if id(p) not in used],
            key=sort_key,
            reverse=True,
        )
        forwards.extend(extras)
        forwards = forwards[:FORWARDS_PER_TEAM]

        selected = goalies[:GOALIES_PER_TEAM] + forwards + defensemen[:DEFENCEMEN_PER_TEAM]

        # Anything left -- a player whose position string is none of the five,
        # or a sixth defenceman past the seven -- fills the remaining slots by
        # production. This is the only way a player with an unrecognised
        # position reaches a record at all.
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

        Lines are built from natural positions first and gaps are filled with
        spare centres, which is why a team of nine centres and no wingers still
        ices four complete forward lines. A winger is never moved to centre --
        the pools are consumed left to right and only `c_pool` is drawn on twice
        -- so a team with no centres at all ices four lines with no centre.

        The power play takes line one's forwards and the top defence pair; the
        penalty kill takes line two's forwards and the next pair. `H1__`-`H5__`
        and `S1__`-`S5__` are five slots each and both are filled positionally,
        so a team short of defencemen gets a four-man power play rather than a
        forward in a defenceman's slot.

        A player who reaches none of those groups gets an empty dict, which
        `rom_writer.roster_values` turns into all sixty-four flags zeroed -- a
        dressed scratch, not a player left on whatever line the disc had him on.

        DELIBERATE DIVERGENCE -- **this no longer emits NHL 07's defence-pair
        spelling.** The source emitted `3{pair}{side}`, `31LD` through `33RD`,
        because this whole module is a copy of NHL 07's with the type names
        substituted, and on NHL 07 `3n` really is defence pair *n*. On NHL 2005
        it is not, and the result was a three-way loss on every patched player:

          * pairs one and two were written to `31LD`/`31RD` and `32LD`/`32RD`,
            which on this game are the two **five-on-three** units;
          * pair three was written to `33LD`/`33RD`, which this game's ROST does
            not have at all, so `rom_writer.roster_values` dropped it;
          * `L1LD` through `L3RD`, the pairs the game actually ices at even
            strength, were zeroed and never set -- so a patched team had no
            even-strength defence pairing whatsoever.

        Now `L{pair}{side}`, which is the same `L` family this function already
        puts the pair's *forwards* on two blocks above. That internal
        inconsistency is the argument on its own: whatever `3n` denotes, it is
        not the situation `L{line}C_` denotes, and a line's centre and its
        defencemen cannot be on two different ones. `rom_writer.LINE_FLAGS`
        carries the flag-shape evidence -- `31C_` exists and a pair has no
        centre -- and says what would settle it outright.

        This is the one path where the port is no longer byte-identical to the
        source: six line-assignment bits per ROST row, on any team with
        defencemen. Nothing else in the record moves.
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
            # DELIBERATE DIVERGENCE: `L`, not `3`. The source's `3{pair}{side}`
            # is NHL 07's spelling and puts this game's first two pairs on the
            # five-on-three units while its even-strength pairs stay zero. The
            # docstring above carries the evidence.
            result[idx][f"L{pair}{side}"] = 1
            d_line_indices.append(idx)

        # Three forwards and two defencemen, and `fwd_line_indices` holds them
        # in the order they were assigned, so [:3] is line one and [3:6] is line
        # two -- but only when line one was complete. A team missing a right
        # wing puts line two's centre in the third power-play slot, because the
        # list is positional and not keyed by line.
        pp_candidates = fwd_line_indices[:3] + d_line_indices[:2]
        for hi, idx in enumerate(pp_candidates[:SPECIAL_TEAMS_UNIT]):
            result[idx][f"H{hi + 1}__"] = 1

        pk_candidates = fwd_line_indices[3:6] + d_line_indices[2:4]
        for si, idx in enumerate(pk_candidates[:SPECIAL_TEAMS_UNIT]):
            result[idx][f"S{si + 1}__"] = 1

        return result
