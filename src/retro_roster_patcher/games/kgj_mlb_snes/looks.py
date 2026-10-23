"""Each player's own look in Ken Griffey Jr. Presents MLB.

The game has no portraits: it draws a player from parts the look nibbles of his
record pick (`KGJBatterAppearance`, `KGJPitcherAppearance`). They are picked
per player here:

* skin from his head shot (`core.skin`), cut into the game's six tones at
  `SKIN_CUTS`; without a head shot, a pick weighted like the 2026 faces;
* build from his height and weight (`build`);
* everything else a stable pick from his provider id, from only the values
  the cartridge's 700 records use and weighted like them: hair colour and head
  by skin, as the cartridge pairs them, the mound head from the batter head,
  legs, arms, bat colour, the at-bat animation and the delivery.

Teammates whose cards would look the same are picked again (`dress`). `write`
puts a look into a record, every nibble clamped: one past each range glitches
the sprite, and some reset or hang the game (Mesen 2, retail cartridge).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

from .models import KGJPlayerRecord

#: The ITA (`core.skin`) between the game's tones 0-5, lightest first. Set by
#: eye on the 681 ESPN head shots of 2026 sorted by ITA, against the tones'
#: lightest shades (0 is 255,205,156 and 5 is 74,41,8 at the plate). The 2026
#: league comes out 66/13/7/7/4/2 % from light to dark; the 1994 cartridge's
#: batters are 47/8/9/8/19/8 %.
SKIN_CUTS = (30.0, 18.0, 10.0, -10.0, -40.0)

#: A player without a head shot: tones weighted like the 2026 faces, so about
#: one in sixteen comes out tone 4-5 (the cartridge's weights would make it one
#: in four).
SKIN_WITHOUT_PHOTO = (456, 85, 46, 51, 30, 13)

# The cartridge's own shares, as weights for each value from 0 up.
_HAIR_BY_SKIN = {0: (34, 12, 71, 66, 15), 1: (1, 0, 6, 13, 15)}  # tones 2-5: always black
_HEAD_BY_SKIN = (
    (11, 0, 0, 1, 157, 21, 4, 4),  # tone 0
    (5, 3, 0, 0, 19, 2, 3, 3),  # tone 1
    (107, 56, 12, 4, 2, 1, 0, 0),  # tones 2-5
)
_BODY = (199, 67, 70, 84)
_MOUND_BODY = (200, 16, 64)
_STANCE = (112, 66, 64, 116, 62)
_ARMS = (119, 116, 185)
_BAT = (148, 125, 147)
_WAGGLE = (0, 56, 64, 74, 67, 49, 64, 46)
_PIECE = (216, 204)
_PIECE_SET = (94, 171, 155)
_SEQUENCE = (83, 38, 108, 103, 88)
_DELIVERY = (241, 39)
#: The mound head the cartridge gives each batter head.
_MOUND_HEAD = (0, 3, 1, 1, 0, 3, 1, 2)
#: The head each value draws on builds 0-2 and on the small build 3 ($85:D22F).
_HEAD_DRAWN = ((0, 1, 6, 3, 4, 5, 6, 7), (0, 1, 6, 6, 4, 5, 6, 4))

#: How many times a player is picked again while his card matches a
#: teammate's. A dark-skinned pitcher of a given build has three mound heads
#: to choose from, so a match can be unavoidable.
_REPICKS = 16

#: $85:D1B4: LDA team / CMP #$3A (AL All-Stars) / BEQ / CMP #$32 (Seattle) /
#: BNE / LDA jersey / CMP #, then the jersey compared with: 24.
_GRIFFEY_RULE = bytes.fromhex("c93a00f005c93200d016adf512c9")


def _pick(weights: Sequence[int], key: str) -> int:
    """A weighted choice that the same key always makes."""
    h = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") % sum(weights)
    for value, weight in enumerate(weights):
        if h < weight:
            return value
        h -= weight
    raise AssertionError("unreachable")


def build(player_id: int, height_in: float, weight_lb: float) -> tuple[int, int]:
    """(Build at the plate, build on the mound). Cut where the 2026 league's
    shares come out like the cartridge's: small under 188 lb, big from a BMI of
    29.5, tall torso from 6'2"; on the mound heavy from a BMI of 30, tall from
    6'5". Without both height and weight, picked like the cartridge."""
    if height_in > 0 and weight_lb > 0:
        bmi = 703 * weight_lb / height_in**2
        plate = 3 if weight_lb < 188 else 2 if bmi >= 29.5 else 1 if height_in >= 74 else 0
        mound = 1 if bmi >= 30 else 2 if height_in >= 77 else 0
        return plate, mound
    key = f"{player_id}:"
    return _pick(_BODY, key + "body"), _pick(_MOUND_BODY, key + "pbody")


def fit(record: KGJPlayerRecord, height_in: float, weight_lb: float) -> None:
    """Give a freshly mapped record its build, then the rest of its look."""
    record.batter_appearance.body, record.pitcher_appearance.body = build(
        record.player_id, height_in, weight_lb
    )
    dress([record])


def _dress(record: KGJPlayerRecord, skin: int, salt: int) -> None:
    """Everything but the build, for `skin`; `salt` > 0 picks again."""
    key = f"{record.player_id}:" + (f"{salt}:" if salt else "")
    bat, mound = record.batter_appearance, record.pitcher_appearance
    bat.skin = mound.skin = skin
    bat.hair_color = mound.hair_color = 4 if skin >= 2 else _pick(_HAIR_BY_SKIN[skin], key + "hair")
    bat.head = _pick(_HEAD_BY_SKIN[min(skin, 2)], key + "head")
    mound.head = _MOUND_HEAD[bat.head]
    bat.legs_size = 1 if bat.body == 3 else 0
    bat.legs_stance = _pick(_STANCE, key + "stance")
    bat.arms_stance = _pick(_ARMS, key + "arms")
    bat.bat_color = _pick(_BAT, key + "bat")
    bat.waggle = _pick(_WAGGLE, key + "a12h")
    bat.piece = _pick(_PIECE, key + "a12l")
    bat.piece_set = _pick(_PIECE_SET, key + "a13h")
    bat.sequence = _pick(_SEQUENCE, key + "a13l")
    mound.throwing_style = _pick(_DELIVERY, key + "style")


def _card(record: KGJPlayerRecord) -> tuple[int | str, ...]:
    """What his Line Up card (a batter) or Bullpen card (a pitcher) shows."""
    if record.is_pitcher:
        m = record.pitcher_appearance
        return ("mound", m.skin, m.head, m.hair_color, m.body)
    b = record.batter_appearance
    head = _HEAD_DRAWN[b.body == 3][min(max(b.head, 0), 7)]
    return ("plate", b.skin, head, b.hair_color, b.body, b.legs_stance, b.arms_stance, b.bat_color)


def dress(
    records: Sequence[KGJPlayerRecord | None], tones: Mapping[int, int] | None = None
) -> None:
    """Give one team's records their looks, keeping each one's build.

    `tones` is the skin tone measured from a player's head shot, by player id;
    a player it lacks gets the pick for no head shot. A player whose card would
    look like an earlier teammate's is picked again, up to `_REPICKS` times.
    """
    seen: set[tuple[int | str, ...]] = set()
    for record in records:
        if record is None:
            continue
        pid = record.player_id
        skin = (tones or {}).get(pid)
        if skin is None:
            skin = _pick(SKIN_WITHOUT_PHOTO, f"skin:{pid}")
        for salt in range(_REPICKS):
            _dress(record, skin, salt)
            if _card(record) not in seen:
                break
        seen.add(_card(record))


def _nibble(value: int, high: int, low: int = 0) -> int:
    return min(max(value, low), high)


def write(d: bytearray, off: int, record: KGJPlayerRecord) -> None:
    """A record's look at file offset `off`: bytes 0x0E-0x14, and 0x15-0x17
    for a pitcher (a batter's are zero, as on the cartridge), each nibble
    clamped to the values the cartridge uses. Leaves 0x11 hi and 0x14 lo, which
    the game never reads, and the throwing hand in 0x15 hi."""
    b = record.batter_appearance
    d[off + 0x0E] = _nibble(b.skin, 5) << 4 | _nibble(b.head, 7)
    d[off + 0x0F] = _nibble(b.hair_color, 4) << 4 | _nibble(b.body, 3)
    d[off + 0x10] = _nibble(b.legs_size, 1) << 4 | _nibble(b.legs_stance, 4)
    d[off + 0x11] = d[off + 0x11] & 0xF0 | _nibble(b.arms_stance, 2)
    d[off + 0x12] = _nibble(b.waggle, 7, 1) << 4 | _nibble(b.piece, 1)
    d[off + 0x13] = _nibble(b.piece_set, 2) << 4 | _nibble(b.sequence, 4)
    d[off + 0x14] = _nibble(b.bat_color, 2) << 4 | d[off + 0x14] & 0x0F
    if not record.is_pitcher:
        d[off + 0x15 : off + 0x18] = bytes(3)
        return
    m = record.pitcher_appearance
    d[off + 0x15] = d[off + 0x15] & 0xF0 | _nibble(m.skin, 5)
    d[off + 0x16] = _nibble(m.head, 4) << 4 | _nibble(m.hair_color, 4)
    d[off + 0x17] = _nibble(m.body, 2) << 4 | _nibble(m.throwing_style, 1)


def free_number_24(data: bytearray) -> bool:
    """Stop the game giving a #24 on Seattle or the AL All-Stars Griffey's own
    head and blonde hair ($85:D1B4), by comparing the jersey with 255, which no
    record holds. False when the image has no such rule."""
    at = data.find(_GRIFFEY_RULE)
    if at < 0:
        return False
    data[at + len(_GRIFFEY_RULE)] = 0xFF
    return True
