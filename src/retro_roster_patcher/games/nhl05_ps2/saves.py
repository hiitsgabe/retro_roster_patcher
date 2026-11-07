"""A patched disc's own memory card folder.

At boot NHL 2005 loads the Rosters/Settings save from its memory card folder
("Load Successful.") over the disc's database. A save made with the retail
disc, or with an earlier patch, so puts the old roster rows back over the new
bios, and the game shows a mix of 2004 rosters and new names.

The executable names that folder once, `BASLUS-21001NHL` (`BA` region, the
serial, then a free suffix the PS2 browser does not read: it shows the title in
the save's own `icon.sys`). The patch replaces the three-letter suffix with one
taken from the new `DB.VIV`, so a disc never loads a save made with another
roster, and patching the same roster twice keeps the same folder. Old saves stay
on the card, unseen by the patched disc.
"""

from __future__ import annotations

import re
import zlib

ELF_PATH = "SLUS_210.01"

# The folder name, NUL-terminated in the executable; the retail suffix is NHL.
# Fixed length: the string sits among others in the ELF's data.
_FOLDER = re.compile(rb"BASLUS-21001([0-9A-Z]{3})(?=\x00)")
_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def suffix(db_viv: bytes) -> bytes:
    """Three base-36 characters from the archive's CRC-32. Never `NHL`, the
    retail folder."""
    n = zlib.crc32(db_viv)
    out = "".join(_DIGITS[n // 36**i % 36] for i in range(3)).encode()
    return b"RRP" if out == b"NHL" else out


def own_folder(elf: bytes, db_viv: bytes) -> bytes | None:
    """`elf` with its save folder named for `db_viv`, or None when the
    executable does not hold exactly one folder name (not this game)."""
    found = list(_FOLDER.finditer(elf))
    if len(found) != 1:
        return None
    start, end = found[0].span(1)
    return elf[:start] + suffix(db_viv) + elf[end:]
