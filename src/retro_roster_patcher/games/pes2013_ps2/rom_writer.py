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
from .afs import Table, unwrap, wrap
from .emblems import CLUB_TABLE, Atlas, paint_logos
from .models import (
    CLUB_SQUAD_SIZE,
    DISPLAY_NAME_BYTES,
    DISPLAY_NAME_CHARS,
    EMBLEM_ENTRIES,
    LEAGUE_NAME_ANCHOR,
    LEAGUE_NAME_ENTRIES,
    OVER_AFS,
    PART_CLUB_NUMBERS,
    PART_PLAYERS,
    PLAYER_RECORD_SIZE,
    SHIRT_NAME_BYTES,
    SHIRT_NAME_CHARS,
    TEXT_AFS,
    TEXT_LANGUAGE_AFS,
)
from .rom_reader import OverlayTeams, PlayerTables, RomReader
from .textures import Bundle

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
                    f.seek(base + slot.offset)
                    f.write(shorten(text, slot.capacity - 1).ljust(slot.capacity, b"\0"))

    # -- emblems -------------------------------------------------------------

    def write_emblems(self, logos: dict[int, bytes]) -> int:
        """Draw each club's PNG logo into every emblem atlas; returns crests written.

        A club missing from an atlas's club table is skipped there. Needs
        Pillow: the caller catches `ImportError`.
        """
        written = 0
        for index in EMBLEM_ENTRIES:
            header, payload = unwrap(self.reader.read(TEXT_AFS, index))
            atlas = Atlas.parse(payload)
            count = paint_logos(atlas, CLUB_TABLE, logos)
            if count:
                self._write_entry(TEXT_AFS, index, wrap(header, atlas.build()))
                written = max(written, count)
        return written

    # -- leagues -------------------------------------------------------------

    def write_league_name(self, name_offset: int, name: str) -> int:
        """Rename one league in every language's string tables; returns tables written.

        The tables list the leagues around "Ligue 1"; the string `name_offset`
        places from it is the one to replace, wherever else it appears in the
        same table, cut to the room the original took.
        """
        written = 0
        for afs_name in TEXT_LANGUAGE_AFS:
            if not self.reader.has(afs_name):
                continue
            for index in LEAGUE_NAME_ENTRIES:
                try:
                    block = self.reader.read(afs_name, index)
                    header, payload = unwrap(block)
                    table = Table.parse(payload)
                except RomError:
                    continue
                changed = False
                for k, part in enumerate(table.parts):
                    if not part or b"Ligue 1\0" not in part:
                        continue
                    ph, leaf = unwrap(part)
                    new = _rename_league(leaf, name_offset, name)
                    if new is not None and new != leaf:
                        table.parts[k] = wrap(ph, new)
                        changed = True
                if changed:
                    self._write_entry(afs_name, index, wrap(header, table.build()))
                    written += 1
        return written

    def write_league_icon(self, icon: str, png: bytes) -> int:
        """Paint `png` over every texture named `icon` in every language's bundles."""
        written = 0
        for afs_name in TEXT_LANGUAGE_AFS:
            if not self.reader.has(afs_name):
                continue
            extent = self.reader.extent(afs_name)
            needle = icon.encode("ascii").ljust(16, b"\0")
            with open(self.output_path, "rb") as f:
                entries = self.reader.entries(afs_name)
                for entry in entries:
                    if entry.size < 1024:
                        continue
                    f.seek(extent.offset + entry.offset)
                    block = f.read(entry.size)
                    if block[0] != 0 or block[2] not in (0, 1):
                        continue
                    try:
                        header, payload = unwrap(block)
                    except RomError:
                        continue
                    if needle not in payload:
                        continue
                    try:
                        bundle = Bundle.parse(payload)
                        tex = bundle.texture(icon)
                    except (RomError, ValueError):
                        continue
                    tex.paint(png)
                    bundle.set_texture(icon, tex)
                    self._write_entry(afs_name, entry.index, wrap(header, bundle.build()))
                    written += 1
        return written

    # -- archive plumbing ----------------------------------------------------

    def _write_entry(self, afs_name: str, index: int, block: bytes) -> None:
        """Put `block` where the entry sits, zero-padded to the entry's size.

        The size in the archive's table is never changed: the game hangs at
        boot when an entry's size differs from the original (seen on the real
        disc), so a rebuilt block has to fit the room the original took.
        """
        extent = self.reader.extent(afs_name)
        entries = self.reader.entries(afs_name)
        entry = entries[index]
        # The game reads whole sectors, so a block may run a little past the
        # entry's size into the padding before the next entry (checked on the
        # real disc); the size field itself is never changed.
        following = [e.offset for e in entries if e.offset > entry.offset]
        room = (min(following) if following else extent.size) - entry.offset
        if len(block) > room:
            raise RomError(
                f"{afs_name} entry {index} needs {len(block)} bytes, {room} available; "
                f"the new names did not compress small enough"
            )
        with open(self.output_path, "r+b") as f:
            f.seek(extent.offset + entry.offset)
            f.write(block.ljust(max(entry.size, len(block)), b"\0"))


def _rename_league(leaf: bytes, name_offset: int, name: str) -> bytes | None:
    """`leaf` with the league `name_offset` strings from the anchor renamed in place."""
    strings = leaf.split(b"\0")
    try:
        anchor = strings.index(LEAGUE_NAME_ANCHOR.encode())
    except ValueError:
        return None
    target = anchor + name_offset
    if not 0 <= target < len(strings) or not strings[target]:
        return None
    old = strings[target]
    raw = shorten(name, len(old)).ljust(len(old), b"\0")
    return b"\0".join(raw if s == old else s for s in strings)


def shorten(text: str, room: int) -> bytes:
    """UTF-8 of `text` in at most `room` bytes: whole trailing words dropped
    first ("BRIGHTON & HOVE ALBION" -> "BRIGHTON"), then characters."""
    words = text.split()
    while words:
        raw = " ".join(words).encode("utf-8")
        if len(raw) <= room:
            return raw
        words.pop()
        if words and words[-1] in ("&", "-", "DE", "DA", "DO", "E"):
            words.pop()
    raw = text.encode("utf-8")
    while len(raw) > room:
        text = text[:-1]
        raw = text.encode("utf-8")
    return raw
