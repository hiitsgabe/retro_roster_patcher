"""Write a patched PES 2013 (PS2) ISO: a copy of the input with the player
entries and the overlay team names rewritten in place.

An AFS entry is rewritten into its own slot (the room up to the next entry),
its size field in the archive's table updated. Team-name strings are
overwritten where they sit, cut to the room each has before the next string.
"""

from __future__ import annotations

import os
import unicodedata
from collections.abc import Callable

from ...core.errors import RomError
from .emblems import CLUB_TABLE, Atlas, render_logo
from .models import (
    CLUB_SQUAD_SIZE,
    DISPLAY_NAME_BYTES,
    DISPLAY_NAME_CHARS,
    EMBLEM_ENTRIES,
    OVER_AFS,
    PART_CLUB_NUMBERS,
    PART_PLAYERS,
    PLAYER_RECORD_SIZE,
    SHIRT_NAME_BYTES,
    SHIRT_NAME_CHARS,
    TEXT_AFS,
)
from .rom_reader import OverlayTeams, PlayerTables, RomReader

ProgressFn = Callable[[float, str], None]


#: Letters NFKD leaves alone, spelled the way shirts spell them.
_FOLD = str.maketrans(
    {
        "Ø": "O",
        "ø": "o",
        "Æ": "AE",
        "æ": "ae",
        "Œ": "OE",
        "œ": "oe",
        "ß": "SS",
        "Ł": "L",
        "ł": "l",
        "Đ": "D",
        "đ": "d",
        "Þ": "TH",
        "þ": "th",
        "İ": "I",
        "ı": "i",
    }
)


def ascii_fold(text: str) -> str:
    """Latin text reduced to ASCII: accents stripped, the rest dropped."""
    folded = unicodedata.normalize("NFKD", text.translate(_FOLD)).encode("ascii", "ignore").decode()
    return "".join(c for c in folded if 32 <= ord(c) < 127)


def display_name_bytes(name: str) -> bytes:
    raw = name[:DISPLAY_NAME_CHARS].encode("utf-16-le")
    return raw.ljust(DISPLAY_NAME_BYTES, b"\0")[:DISPLAY_NAME_BYTES]


def shirt_name_bytes(name: str) -> bytes:
    return ascii_fold(name)[:SHIRT_NAME_CHARS].encode("ascii").ljust(SHIRT_NAME_BYTES, b"\0")


class RomWriter:
    """Holds the *output* image: `copy_iso` makes it, every write goes to it."""

    def __init__(self, iso_path: str, output_path: str) -> None:
        self.iso_path = iso_path
        self.output_path = output_path
        self.reader = RomReader(output_path)

    def copy_iso(self, on_progress: ProgressFn | None = None) -> None:
        """Copy the source image, 4 MB at a time, then open the copy."""
        if os.path.abspath(self.iso_path) != os.path.abspath(self.output_path):
            output_dir = os.path.dirname(self.output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            total = os.path.getsize(self.iso_path)
            copied = 0
            with open(self.iso_path, "rb") as src, open(self.output_path, "wb") as dst:
                while chunk := src.read(4 * 1024 * 1024):
                    dst.write(chunk)
                    copied += len(chunk)
                    if on_progress is not None and total:
                        on_progress(
                            0.4 * copied / total, f"Copying ISO... {copied // (1024 * 1024)}MB"
                        )
                dst.flush()
                os.fsync(dst.fileno())
        if not self.reader.load():
            raise RomError(f"Not a PES 2013 (PS2) image: {self.output_path}")

    # -- players -------------------------------------------------------------

    @staticmethod
    def set_player_names(tables: PlayerTables, index: int, display: str, shirt: str) -> None:
        players = bytearray(tables.players)
        start = index * PLAYER_RECORD_SIZE
        if start + PLAYER_RECORD_SIZE > len(players):
            raise RomError(f"Player index {index} is outside the table")
        players[start : start + DISPLAY_NAME_BYTES] = display_name_bytes(display)
        shirt_at = start + DISPLAY_NAME_BYTES
        players[shirt_at : shirt_at + SHIRT_NAME_BYTES] = shirt_name_bytes(shirt)
        tables.parts[PART_PLAYERS] = bytes(players)

    @staticmethod
    def set_club_number(tables: PlayerTables, club: int, slot: int, number: int) -> None:
        numbers = bytearray(tables.parts[PART_CLUB_NUMBERS])
        numbers[club * CLUB_SQUAD_SIZE + slot] = max(1, min(99, number)) - 1
        tables.parts[PART_CLUB_NUMBERS] = bytes(numbers)

    def write_player_tables(self, index: int, tables: PlayerTables) -> None:
        """Deflate the rebuilt part table back into `0_TEXT.AFS` entry `index`."""
        self._write_entry(TEXT_AFS, index, tables.build())

    # -- team names ----------------------------------------------------------

    def write_team_names(self, teams: OverlayTeams, names: dict[int, tuple[str, str]]) -> None:
        """Overwrite club names and codes in one overlay, each cut to its room."""
        base = self.reader.extent(OVER_AFS).offset + teams.entry.offset
        with open(self.output_path, "r+b") as f:
            for club, (name, code) in names.items():
                slots = teams.clubs.get(club)
                if slots is None:
                    continue
                for slot, text in zip(slots, (name, code), strict=True):
                    raw = text.encode("utf-8")
                    while len(raw) > slot.capacity - 1:
                        text = text[:-1]
                        raw = text.encode("utf-8")
                    f.seek(base + slot.offset)
                    f.write(raw.ljust(slot.capacity, b"\0"))

    # -- emblems -------------------------------------------------------------

    def write_emblems(self, logos: dict[int, bytes]) -> int:
        """Draw each club's PNG logo into every emblem atlas; returns crests written.

        A club missing from an atlas's club table is skipped there. Needs
        Pillow: the caller catches `ImportError`.
        """
        from .afs import unwrap, wrap

        written = 0
        for index in EMBLEM_ENTRIES:
            header, payload = unwrap(self.reader.read(TEXT_AFS, index))
            atlas = Atlas.parse(payload)
            palette = atlas.palettes[CLUB_TABLE]
            present = {rec.ident for rec in atlas.tables[CLUB_TABLE]}
            count = 0
            for club, png in logos.items():
                if club in present:
                    atlas.set_image(CLUB_TABLE, club, render_logo(png, atlas.side, palette))
                    count += 1
            if count:
                self._write_entry(TEXT_AFS, index, wrap(header, atlas.build()))
                written = max(written, count)
        return written

    # -- archive plumbing ----------------------------------------------------

    def _write_entry(self, afs_name: str, index: int, block: bytes) -> None:
        """Put `block` where the entry sits, zero-padded to the entry's size.

        The size in the archive's table is never changed: the game hangs at
        boot when an entry's size differs from the original (seen on the real
        disc), so a rebuilt block has to fit the room the original took.
        """
        extent = self.reader.extent(afs_name)
        entry = self.reader.entry(afs_name, index)
        if len(block) > entry.size:
            raise RomError(
                f"{afs_name} entry {index} needs {len(block)} bytes, {entry.size} available; "
                f"the new names did not compress small enough"
            )
        with open(self.output_path, "r+b") as f:
            f.seek(extent.offset + entry.offset)
            f.write(block.ljust(entry.size, b"\0"))
