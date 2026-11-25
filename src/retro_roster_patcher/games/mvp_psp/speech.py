"""Whose names the play-by-play says.

`DATA/AUDIO/SPCH_PBP/PBPHDR.BIG` holds one header per speech sample bank and
`PBPHDR.OFF` where each is, `offset,length,` a line in ASCII. A header opens
with two little-endian words: the speech event, and the key the event is asked
with. Events 89 and 3207 are a player's name, keyed by his `attrib` audio id
(`playerattrib_audioid`): in PPSSPP the game reads bank (89, 739) off the disc
as Edgar Renteria, audio id 739, comes to bat, (3207, 724) for Manny Ramirez
and (89, 588) as it introduces Eric Milton. Each of the retail disc's 841
players with an audio id of their own has an event-89 bank; no bank is keyed 0,
the id of its nine invented players (Jon Dowd), whom it never names.

So a player given a 2005 player's record must not keep his audio id: the
booth would call him by the old name. `Voices` hands him an id no name answers
to, and the old id's portrait with it (`portraits`).
"""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import BinaryIO

from ...formats import iso9660

FOLDER = ("PSP_GAME", "USRDIR", "DATA", "AUDIO", "SPCH_PBP")
HEADERS, OFFSETS = "PBPHDR.BIG", "PBPHDR.OFF"
NAME_EVENTS = (89, 3207)


def named_ids(f: BinaryIO) -> set[int]:
    """The audio ids a recorded name answers to; empty for an image without
    the speech index."""
    root = iso9660.read_root(f)
    folder = iso9660.walk(f, root, FOLDER) if root else None
    files = []
    for name in (HEADERS, OFFSETS):
        entry = iso9660.find_entry(f, folder, name) if folder else None
        if entry is None:
            return set()
        f.seek(entry.extent.lba * iso9660.SECTOR_SIZE)
        files.append(f.read(entry.extent.size))
    return names(*files)


def names(headers: bytes, offsets: bytes) -> set[int]:
    """The keys of the name events among `PBPHDR.BIG`'s headers."""
    named = set()
    for row in offsets.decode("ascii", errors="replace").split("\n"):
        at = row.split(",")[0].strip()
        if at.isdigit() and int(at) + 4 <= len(headers):
            event, key = struct.unpack_from("<HH", headers, int(at))
            if event in NAME_EVENTS:
                named.add(key)
    return named


@dataclass
class Voices:
    """Gives each new player an audio id no recorded name answers to.

    `named` is `named_ids` of the disc, `pictures` the ids its portrait archive
    has a picture for, `in_use` the audio ids its records hold. A player whose
    record's id is already nameless keeps it; another takes the lowest free
    nameless id in the disc's own range, and `moves` then says which id's
    picture the new id takes over. With nothing in `named` (no speech index
    on the image) every id is kept.
    """

    named: set[int] = field(default_factory=set)
    pictures: set[int] = field(default_factory=set)
    in_use: Iterable[int] = ()
    moves: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        taken = set(self.in_use) | self.named
        self._free = iter(i for i in range(1, max(taken, default=0) + 1) if i not in taken)

    def assign(self, current: int) -> int | None:
        """The audio id for a player whose record holds `current`; None when no
        nameless id is left (he then takes 0, the invented players')."""
        if not self.named or (current and current not in self.named):
            return current
        new = next(self._free, None)
        if new is not None and new not in self.pictures and current in self.pictures:
            self.moves[new] = current
        return new

    def pictured(self, audio_id: int) -> bool:
        """Does the id have a portrait to show, its own or one it takes over?"""
        return audio_id in self.pictures or audio_id in self.moves
