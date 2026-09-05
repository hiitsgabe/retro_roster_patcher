"""NHL 2005, for the PlayStation 2.

The second of the three EA TDB games and structurally the sibling of
`games/nhl07_psp`: rosters live in EA TDB tables inside a BIGF archive on the
ISO, every write names a four-character field, and the format layers are shared
-- `formats/ea_tdb.py` for RefPack/BIGF/TDB and `formats/iso9660.py` for the
Mode 1 / 2048 directory walk.

Four things are genuinely different from NHL 07 and each is somewhere a
copy-paste port writes the wrong bytes. They are argued where they live:

  * `db.viv` is at `/DB/DB.VIV`, one directory from the root and not three
    (`rom_reader.py`).
  * The archive holds **two** TDBs, not three. There is no `nhlbioatt.tdb`, so
    the bio and attribute tables have no mirror and only ROST does
    (`patcher.py`).
  * `STL` and `SJ` swap slots 24 and 25 (`models.py`).
  * The ROST table has **64** line-assignment flags where NHL 07 has 30, and
    they are not a superset: `33LD`/`33RD` are absent, and the defence pairs are
    `L1LD`..`L3RD` rather than NHL 07's `31LD`..`33RD` (`rom_writer.py`,
    `stat_mapper.py`). This is the one place the port is not byte-identical to
    the source; the source's copy-pasted `3n` spelling put the first two pairs
    on the five-on-three units and dropped the third.

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step. Two providers, ESPN for the current season and the NHL API back to 1993.

**The game is NHL 2005 and not NHL 06.** The upstream pygame front end searched
a ROM library for "NHL 06" while the patcher it launched extracted
`nhl2005.tdb`, a file that exists only on an NHL 2005 disc. The search term was
a copy-paste slip in a screen that is not part of this library, and it is not
carried over.
"""

from .patcher import NHL05PS2Patcher

__all__ = ["NHL05PS2Patcher"]
