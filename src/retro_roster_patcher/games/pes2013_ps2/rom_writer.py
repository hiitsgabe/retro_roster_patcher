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
    NATIONAL_SLOT_BASE,
    OVER_AFS,
    PART_CLUB_NUMBERS,
    PART_PLAYERS,
    PLAYER_RECORD_SIZE,
    POSITION_BYTE,
    SHIRT_NAME_BYTES,
    SHIRT_NAME_CHARS,
    TEXT_AFS,
    TEXT_LANGUAGE_AFS,
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
    def set_position(tables: PlayerTables, index: int, nibble: int) -> None:
        """Set the registered position (the high nibble of byte 53)."""
        players = bytearray(tables.players)
        at = index * PLAYER_RECORD_SIZE + POSITION_BYTE
        players[at] = (players[at] & 0x0F) | ((nibble & 0xF) << 4)
        tables.parts[PART_PLAYERS] = bytes(players)

    @staticmethod
    def set_club_number(tables: PlayerTables, club: int, slot: int, number: int) -> None:
        numbers = bytearray(tables.parts[PART_CLUB_NUMBERS])
        numbers[club * CLUB_SQUAD_SIZE + slot] = max(1, min(99, number)) - 1
        tables.parts[PART_CLUB_NUMBERS] = bytes(numbers)

    def write_player_tables(
        self, index: int, tables: PlayerTables, block: bytes | None = None
    ) -> None:
        """Deflate the rebuilt part table back into `0_TEXT.AFS` entry `index`
        (or write `block`, the caller's already built copy)."""
        self._write_entry(TEXT_AFS, index, tables.build() if block is None else block)

    # -- team names ----------------------------------------------------------

    def write_team_names(self, teams: OverlayTeams, names: dict[int, tuple[str, str]]) -> None:
        """Overwrite team names and codes in one overlay, each cut to its room.
        Keys are club slots, or `NATIONAL_SLOT_BASE` + row for national teams."""
        base = self.reader.extent(OVER_AFS).offset + teams.entry.offset
        with open(self.output_path, "r+b") as f:
            for slot_index, (name, code) in names.items():
                if slot_index >= NATIONAL_SLOT_BASE:
                    slots = teams.nationals.get(slot_index - NATIONAL_SLOT_BASE)
                else:
                    slots = teams.clubs.get(slot_index)
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

    def blank_emblems(self, clubs: list[int]) -> int:
        """Make each club's crest fully transparent in every atlas that has it."""
        written = 0
        for index in EMBLEM_ENTRIES:
            header, payload = unwrap(self.reader.read(TEXT_AFS, index))
            atlas = Atlas.parse(payload)
            count = 0
            for club in clubs:
                try:
                    atlas.set_image(CLUB_TABLE, club, bytes(atlas.side * atlas.side))
                    count += 1
                except RomError:
                    continue
            if count:
                self._write_entry(TEXT_AFS, index, wrap(header, atlas.build()))
                written = max(written, count)
        return written

    # -- leagues -------------------------------------------------------------

    def write_league_name(self, name_offset: int, name: str) -> int:
        return self.write_league_names({name_offset: name})

    def write_league_names(self, names: dict[int, str]) -> int:
        """Rename leagues in every language's string tables; returns tables written.

        The tables list the leagues around "Ligue 1"; `names` maps places from
        it to the new name, each written over the old one wherever it appears
        in the same table, cut to the room the original took. All of a table's
        renames happen in one pass: a shorter name is NUL-padded to the old
        one's room, which would shift the places of anything renamed after it.
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
                    if not part:
                        continue
                    try:
                        ph, leaf = unwrap(part)
                    except RomError:
                        continue
                    if LEAGUE_NAME_ANCHOR.encode() + b"\0" not in leaf:
                        continue
                    new = _rename_league(leaf, names)
                    if new is not None and new != leaf:
                        table.parts[k] = wrap(ph, new)
                        changed = True
                if changed:
                    self._write_entry(afs_name, index, wrap(header, table.build()))
                    written += 1
        return written

    def write_league_icon(self, icon: str, png: bytes) -> int:
        """Paint `png` over every texture named `icon` in every language's bundles."""
        return self.write_texture(icon, png)

    def write_texture(self, name: str, png: bytes) -> int:
        """Paint `png` over every texture called `name`, in every language
        archive and every bundle that has one; returns the bundles rewritten."""
        written = 0
        for afs_name in TEXT_LANGUAGE_AFS:
            if not self.reader.has(afs_name):
                continue
            for entry, header, bundle in self.reader.bundles_with(afs_name, name):
                tex = bundle.texture(name)
                tex.paint(png)
                bundle.set_texture(name, tex)
                self._write_entry(afs_name, entry.index, wrap(header, bundle.build()))
                written += 1
        return written

    # -- archive plumbing ----------------------------------------------------

    def room(self, afs_name: str, index: int) -> int:
        """Bytes a rebuilt entry may take: the game reads whole sectors, so a
        block may run a little past the entry's size into the padding before
        the next entry (checked on the real disc); the size field itself is
        never changed."""
        extent = self.reader.extent(afs_name)
        entries = self.reader.entries(afs_name)
        entry = entries[index]
        following = [e.offset for e in entries if e.offset > entry.offset]
        return (min(following) if following else extent.size) - entry.offset

    def _write_entry(self, afs_name: str, index: int, block: bytes) -> None:
        """Put `block` where the entry sits, zero-padded to the entry's size.

        The size in the archive's table is never changed: the game hangs at
        boot when an entry's size differs from the original (seen on the real
        disc), so a rebuilt block has to fit the room the original took.
        """
        extent = self.reader.extent(afs_name)
        entry = self.reader.entries(afs_name)[index]
        room = self.room(afs_name, index)
        if len(block) > room:
            raise RomError(
                f"{afs_name} entry {index} needs {len(block)} bytes, {room} available; "
                f"the new names did not compress small enough"
            )
        with open(self.output_path, "r+b") as f:
            f.seek(extent.offset + entry.offset)
            f.write(block.ljust(max(entry.size, len(block)), b"\0"))


def _rename_league(leaf: bytes, names: dict[int, str]) -> bytes | None:
    """`leaf` with the league strings `names` places from the anchor renamed in
    place; None when the anchor is missing or no place holds a string."""
    strings = leaf.split(b"\0")
    try:
        anchor = strings.index(LEAGUE_NAME_ANCHOR.encode())
    except ValueError:
        return None
    replacements: dict[bytes, bytes] = {}
    for name_offset, name in names.items():
        target = anchor + name_offset
        if 0 <= target < len(strings) and strings[target]:
            old = strings[target]
            replacements[old] = shorten(name, len(old)).ljust(len(old), b"\0")
    if not replacements:
        return None
    return b"\0".join(replacements.get(part, part) for part in strings)


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
