"""NHL 2005, for the PlayStation 2.

The second of the three EA TDB games and structurally the sibling of
`games/nhl07_psp`: rosters live in EA TDB tables inside a BIGF archive on the
ISO, every write names a four-character field, and the format layers are shared
-- `formats/ea_tdb.py` for RefPack/BIGF/TDB and `formats/iso9660.py` for the
Mode 1 / 2048 directory walk.

Four things differ from NHL 07, and each is where a copy-paste port writes the
wrong bytes:

  * `DB.VIV` is at `/DB/DB.VIV`, one directory from the root and not three
    (`rom_reader.py`).
  * The archive holds two TDBs, not three. There is no `nhlbioatt.tdb`, so the
    bio and attribute tables have no mirror and only ROST does (`patcher.py`).
  * `STL` and `SJ` swap slots 24 and 25 (`models.py`).
  * The ROST table has 64 line-assignment flags where NHL 07 has 30, and they
    are not a superset: `33LD`/`33RD` are absent and the defence pairs are
    `L1LD`..`L3RD` (`rom_writer.py`, `stat_mapper.py`).

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step. Two providers, ESPN for the current season and the NHL API back to 1993.

The game is NHL 2005, not NHL 06: `nhl2005.tdb` exists only on an NHL 2005 disc.
"""

from .patcher import NHL05PS2Patcher

__all__ = ["NHL05PS2Patcher"]
