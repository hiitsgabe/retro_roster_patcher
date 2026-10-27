"""Team names, the team-select ranks, and a roster for a slot that has none.

A team block (`TEAM_ROSTER_ADDRESSES`) starts with 12 player pointers, then four
string pointers -- nickname ("Hawks"), city as the menus show it ("Atlanta"),
arena (0 for most) and the intro's "Live from" line ("Atlanta, GA") -- then a
4-character abbreviation ("atl "), a 0, and the five ranks team select shows
(scoring, rebounds, ball control, defense, overall; 1 is best). The strings sit
packed after the block, so new ones go to free space and the pointers move.

Slots 27-29 are the East and West All-Stars and the Slammers, which the patcher
gives to Toronto, Memphis and New Orleans. The All-Stars own twelve records
each; the Slammers have none (all twelve pointers are 0), so `adopt` writes
twelve records in free space for them. The game treats those slots by number,
so `unlock` changes the `moveq` limits that do (`UNLOCK`).
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence

from .models import PLAYER_SIZE, TEAM_ROSTER_ADDRESSES, NBALive95PlayerRecord
from .space import Space

#: ESPN code -> (city as the menus show it, "Live from" line, abbreviation).
INFO = {
    "ATL": ("Atlanta", "Atlanta, GA", "atl "),
    "BOS": ("Boston", "Boston, MA", "bos "),
    "BKN": ("Brooklyn", "Brooklyn, NY", "bkn "),
    "CHA": ("Charlotte", "Charlotte, NC", "char"),
    "CHI": ("Chicago", "Chicago, IL", "chi "),
    "CLE": ("Cleveland", "Cleveland, OH", "clev"),
    "DAL": ("Dallas", "Dallas, TX", "dal "),
    "DEN": ("Denver", "Denver, CO", "den "),
    "DET": ("Detroit", "Detroit, MI", "det "),
    "GS": ("Golden State", "San Francisco, CA", "gsw "),
    "HOU": ("Houston", "Houston, TX", "hst "),
    "IND": ("Indiana", "Indianapolis, IN", "indy"),
    "LAC": ("LA Clippers", "Inglewood, CA", "lac "),
    "LAL": ("LA Lakers", "Los Angeles, CA", "lal "),
    "MEM": ("Memphis", "Memphis, TN", "mem "),
    "MIA": ("Miami", "Miami, FL", "mia "),
    "MIL": ("Milwaukee", "Milwaukee, WI", "mil "),
    "MIN": ("Minnesota", "Minneapolis, MN", "min "),
    "NO": ("New Orleans", "New Orleans, LA", "nop "),
    "NY": ("New York", "New York City, NY", "ny  "),
    "OKC": ("Oklahoma City", "Oklahoma City, OK", "okc "),
    "ORL": ("Orlando", "Orlando, FL", "orl "),
    "PHI": ("Philadelphia", "Philadelphia, PA", "phl "),
    "PHX": ("Phoenix", "Phoenix, AZ", "phx "),
    "POR": ("Portland", "Portland, OR", "port"),
    "SAC": ("Sacramento", "Sacramento, CA", "sac "),
    "SA": ("San Antonio", "San Antonio, TX", "san "),
    "TOR": ("Toronto", "Toronto, ON", "tor "),
    "UTA": ("Utah", "Salt Lake City, UT", "utah"),
    "WSH": ("Washington", "Washington, DC", "was "),
}
ALIASES = {"GSW": "GS", "NYK": "NY", "SAS": "SA", "NOP": "NO", "UTAH": "UTA", "WAS": "WSH"}

_NAMES = 0x30  # nickname, city, arena, "Live from": four pointers
_ABBR = 0x40
_RANKS = 0x45
SLAMMERS = 29
MAX_RANK = 27
#: A record whose unknown bytes (0x3B-0x44) the Slammers' new records copy.
_TEMPLATE_SLOT = (28, 0)


def nickname(full_name: str, short_name: str) -> str:
    """ "Trail Blazers" from "Portland Trail Blazers" though ESPN's short
    name says "Trail Blazer"."""
    if short_name and full_name.endswith(short_name):
        return short_name
    return full_name.split(" ", 1)[-1]


def write_names(image: bytearray, slot: int, code: str, nick: str, free: Space) -> bool:
    """Point `slot`'s nickname, city and "Live from" line at new strings for
    the team `code` (no arena line); False for a code `INFO` does not know or
    when free space has run out."""
    info = INFO.get(ALIASES.get(code, code))
    if info is None:
        return False
    city, live, abbr = info
    block = TEAM_ROSTER_ADDRESSES[slot]
    pointers = []
    for text in (nick, city, None, live):
        if text is None:
            pointers.append(0)
            continue
        raw = text.encode("ascii", "replace") + b"\0"
        start = free.alloc(len(raw))
        if start is None:
            return False
        image[start : start + len(raw)] = raw
        pointers.append(start)
    struct.pack_into(">4I", image, block + _NAMES, *pointers)
    image[block + _ABBR : block + _ABBR + 4] = abbr.encode()
    return True


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def ranks(teams: Mapping[int, Sequence[NBALive95PlayerRecord]]) -> dict[int, list[int]]:
    """Each slot's five team-select ranks (1 best) among `teams`: scoring,
    rebounds, ball control, defense and overall, each the mean of his rating
    group over the team's first eight. The game shows "--" for every rank of a
    team whose scoring rank is past `MAX_RANK` (its marker for the All-Stars
    and custom teams), so thirty teams are spread over 1-27."""
    groups = ((0, 1, 2, 3), (6, 7), (8, 9, 14), (4, 5, 10), tuple(range(16)))
    scores = {
        slot: [_mean([p.ratings[i] for p in players[:8] for i in group]) for group in groups]
        for slot, players in teams.items()
    }
    out: dict[int, list[int]] = {slot: [] for slot in scores}
    for k in range(len(groups)):
        order = sorted(scores, key=lambda slot: -scores[slot][k])
        span = max(len(order) - 1, 1)
        for place, slot in enumerate(order):
            out[slot].append(1 + place * (min(len(order), MAX_RANK) - 1) // span)
    return out


def write_ranks(image: bytearray, slot: int, five: Sequence[int]) -> None:
    image[TEAM_ROSTER_ADDRESSES[slot] + _RANKS : TEAM_ROSTER_ADDRESSES[slot] + _RANKS + 5] = bytes(
        five
    )


#: `moveq #n,D0` (0x70 n) immediates the game tests a team number against, per
#: slot: (offset, retail n, new n). The pre-game matchup names team 27 "Eastern"
#: and 28 "Western" (four copies of the routine, each `beq`/`bne`, so 0x7F
#: matches no team) and every team from 29 "Custom", and Player Setup sends a
#: team from 29 to the custom-team editor; both limits move up one for 29.
#: Checked in genesis_plus_gx, Oct 27 2026.
UNLOCK = {
    27: [(a, 0x1B, 0x7F) for a in (0x277C8, 0x278E6, 0x27C04, 0x27D34)],
    28: [(a, 0x1C, 0x7F) for a in (0x277EE, 0x2790C, 0x27C2A, 0x27D5A)],
    29: [(a, 0x1D, 0x1E) for a in (0x27814, 0x27932, 0x27C50, 0x27D80, 0x1DF0C)]
    + [(a, 0x1C, 0x1D) for a in (0x1DD86, 0x1DE50, 0x1DF42, 0x1DF4A)],
}


def unlock(image: bytearray, slot: int) -> None:
    """Make the game treat `slot` as a club (`UNLOCK`). An instruction that is
    not the retail one is left alone."""
    for offset, retail, new in UNLOCK.get(slot, ()):
        if image[offset : offset + 2] == bytes((0x70, retail)):
            image[offset + 1] = new


def adopt(image: bytearray, slot: int, free: Space) -> bool:
    """Give an empty slot twelve records in free space, each a copy of
    `_TEMPLATE_SLOT`'s fixed bytes with an empty name; False when the slot
    already has players or there is no room."""
    block = TEAM_ROSTER_ADDRESSES[slot]
    if any(struct.unpack_from(">12I", image, block)):
        return False
    start = free.alloc(12 * PLAYER_SIZE)
    if start is None:
        return False
    team, index = _TEMPLATE_SLOT
    template = struct.unpack_from(">I", image, TEAM_ROSTER_ADDRESSES[team] + 4 * index)[0]
    record = bytes(image[template : template + 0x45]) + bytes(PLAYER_SIZE - 0x45)
    for i in range(12):
        image[start + i * PLAYER_SIZE : start + (i + 1) * PLAYER_SIZE] = record
        struct.pack_into(">I", image, block + 4 * i, start + i * PLAYER_SIZE)
    return True
