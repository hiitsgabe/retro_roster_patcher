"""Maps a player's season onto NHL 94's 0-6 ratings the way `nhl94_genesis`
does (its `stat_mapper` says how): the two cartridges carry the same player
records, so each rating ranks the same stats against the league fetched and
takes the value at that rank of the cartridge's spread (the Genesis counts:
this cartridge's differ by a handful of players). A rating none of whose stats
the line has keeps the position default, and a player without a stat line
keeps them all."""

from dataclasses import dataclass, replace

from ...core.hockey import (
    SV_PRIOR,
    deployment,
    goalie_games,
    goalie_starts,
    shrunk,
    stat,
)
from ...core.text import to_ascii
from ...sports.models import Player
from ..nhl94_genesis import stat_mapper as genesis
from ..nhl94_genesis.stat_mapper import LeagueStats, rate
from .models import (
    MODERN_NHL_TO_NHL94,
    NHL94GoalieAttributes,
    NHL94PlayerAttributes,
    NHL94PlayerRecord,
)
from .rom_writer import MAX_NAME_LENGTH, abbreviate

POSITION_DEFAULTS = {
    pos: NHL94PlayerAttributes(**vars(attrs)) for pos, attrs in genesis.POSITION_DEFAULTS.items()
}
#: A goalie with no stat line.
GOALIE_DEFAULTS = NHL94GoalieAttributes(**vars(genesis.GOALIE_DEFAULTS))


def goalie_rank(stats: dict) -> tuple[float, float]:
    """Higher starts first: the game always starts the first goalie, so the
    starter is the one who played most (his share of the team's starts when
    ESPN gives it), then the better (shrunk) save %."""
    espn = {"gameStarted", "dressed"} & stats.keys()
    played = deployment(stats, goalie=True) if espn else goalie_starts(stats)
    return played, shrunk(stat(stats, "SV%"), SV_PRIOR, goalie_games(stats))


def display_name(name: str) -> str:
    """The name folded to ASCII, at most `MAX_NAME_LENGTH` characters: a longer
    one loses its first name to an initial first."""
    name = " ".join(to_ascii(name).split())
    if len(name) > MAX_NAME_LENGTH:
        name = abbreviate(name)
    return name[:MAX_NAME_LENGTH]


@dataclass
class NHL94StatMapper:
    def map_player(
        self,
        player: Player,
        team_abbrev: str,
        stats: dict | None = None,
        league: LeagueStats | None = None,
    ) -> NHL94PlayerRecord:
        """`league` ranks his stats; without one his line is ranked alone."""
        pos = player.position.upper() if player.position else "C"
        is_goalie = pos == "G"

        # Copied, not shared: `attributes` is public and mutable, so the module
        # constants must not be handed out.
        attrs: NHL94PlayerAttributes | NHL94GoalieAttributes
        if is_goalie:
            attrs = replace(GOALIE_DEFAULTS)
        else:
            attrs = replace(POSITION_DEFAULTS.get(pos, POSITION_DEFAULTS["C"]))
        if stats:
            rate(attrs, player, stats, league)

        jersey = player.number or 1

        if player.weight > 0:
            weight_class = self._map_weight(int(player.weight))
        else:
            weight_class = 7  # 196 lbs

        hand = 0  # 0=L, 1=R
        if player.handedness == "R":
            hand = 1

        return NHL94PlayerRecord(
            name=display_name(player.name),
            jersey_number=jersey,
            weight_class=weight_class,
            handedness=hand,
            is_goalie=is_goalie,
            attributes=attrs,
            photo_url=player.photo_url,
        )

    def select_roster(
        self,
        players: list[Player],
        stats: dict | None = None,
        num_goalies: int = 2,
        num_forwards: int = 14,
        num_defensemen: int = 7,
    ) -> list[Player]:
        """Build the roster in ROM order, goalies first:

        Goalies (2)       - indices 0..1, most starts first
        Forwards (N)      - indices 2..2+N-1, LW/C/RW per line
        Defencemen (M)    - indices 2+N..2+N+M-1

        A position with fewer players than asked for gives a shorter block.

        The header's line assignments reference players by absolute index and
        assume exactly this ordering.
        """
        stats = stats or {}

        def sort_key(p: Player) -> float:
            return deployment(stats.get(str(p.id), {}), goalie=False)

        centers = sorted(
            [p for p in players if p.position == "C"],
            key=sort_key,
            reverse=True,
        )
        left_wings = sorted(
            [p for p in players if p.position == "LW"],
            key=sort_key,
            reverse=True,
        )
        right_wings = sorted(
            [p for p in players if p.position == "RW"],
            key=sort_key,
            reverse=True,
        )
        defensemen = sorted(
            [p for p in players if p.position == "D"],
            key=sort_key,
            reverse=True,
        )
        goalies = sorted(
            [p for p in players if p.position == "G"],
            key=lambda p: goalie_rank(stats.get(str(p.id), {})),
            reverse=True,
        )

        selected_goalies = goalies[:num_goalies]

        # forwards in line order, LW/C/RW per line
        all_fwd = sorted(
            centers + left_wings + right_wings,
            key=sort_key,
            reverse=True,
        )

        num_lines = num_forwards // 3
        num_extras = num_forwards - (num_lines * 3)

        forwards = []
        used_fwd = set()
        lw_idx, c_idx, rw_idx = 0, 0, 0

        for _line in range(num_lines):
            lw = None
            while lw_idx < len(left_wings):
                if id(left_wings[lw_idx]) not in used_fwd:
                    lw = left_wings[lw_idx]
                    lw_idx += 1
                    break
                lw_idx += 1
            if lw is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        lw = f
                        break
            if lw:
                used_fwd.add(id(lw))
                forwards.append(lw)

            c = None
            while c_idx < len(centers):
                if id(centers[c_idx]) not in used_fwd:
                    c = centers[c_idx]
                    c_idx += 1
                    break
                c_idx += 1
            if c is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        c = f
                        break
            if c:
                used_fwd.add(id(c))
                forwards.append(c)

            rw = None
            while rw_idx < len(right_wings):
                if id(right_wings[rw_idx]) not in used_fwd:
                    rw = right_wings[rw_idx]
                    rw_idx += 1
                    break
                rw_idx += 1
            if rw is None:
                for f in all_fwd:
                    if id(f) not in used_fwd:
                        rw = f
                        break
            if rw:
                used_fwd.add(id(rw))
                forwards.append(rw)

        extras = [f for f in all_fwd if id(f) not in used_fwd]
        forwards.extend(extras[:num_extras])
        forwards = forwards[:num_forwards]

        defense = defensemen[:num_defensemen]

        # ROM order: G, then F, then D. A short block stays short: a player
        # topped up after the defencemen would be read as one.
        return selected_goalies + forwards + defense

    def _map_weight(self, weight_pounds: int) -> int:
        """Weight class = (lbs - 140) / 8, clamped to 0-14."""
        weight_class = (weight_pounds - 140) // 8
        return max(0, min(14, weight_class))

    def get_team_slot(self, team_abbrev: str) -> int | None:
        return MODERN_NHL_TO_NHL94.get(team_abbrev.upper())
