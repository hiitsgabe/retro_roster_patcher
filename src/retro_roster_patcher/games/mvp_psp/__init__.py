"""MVP Baseball, for the PSP (ULUS-10012, EA Sports 2005).

The odd one out among the three EA disc games. It shares `formats/ea_tdb.py`'s
RefPack layer with `games/nhl05_ps2` and `games/nhl07_psp` and nothing else:
there is no TDB and no ISO 9660 walk. `database.big` sits at a hardcoded LBA:
a directory, then eighteen independently compressed CSV tables at fixed
offsets, linked by nine-hex-digit record ids.

Thirty MLB slots, twenty-five players each -- fifteen batters, five starters,
five relievers. Teams map to slots by abbreviation, so there is no
slot-mapping step. One provider, ESPN. Each rating is the value at the
player's rank in his group of the disc's own spread of it (`stat_mapper`,
`spreads`).

A table that recompresses larger than its fixed allocation raises
(`rom_writer.rebuild_database_big`): a deliberate divergence from the source,
which dropped it silently and still reported a successful patch; keep the
raise.
"""

from .patcher import MVPPSPPatcher

__all__ = ["MVPPSPPatcher"]
