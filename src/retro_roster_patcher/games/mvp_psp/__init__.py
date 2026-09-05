"""MVP Baseball, for the PSP (ULUS-10012, EA Sports 2005).

The odd one out among the three EA disc games. It shares `formats/ea_tdb.py`'s
RefPack layer with `games/nhl05_ps2` and `games/nhl07_psp` and nothing else:
there is no TDB, no BIGF archive and no ISO 9660 walk. `database.big` sits at a
hardcoded LBA and holds nineteen independently compressed CSV tables at fixed
offsets, linked by nine-hex-digit record ids.

Thirty MLB slots, twenty-five players each -- fifteen batters, five starters,
five relievers -- on a 0-99 scale. Teams map to slots by abbreviation, so there
is no slot-mapping step. One provider, ESPN.

Three inherited defects, each labelled at the line it lives on:

  * A section that recompresses larger than its fixed allocation raises
    (`rom_writer.rebuild_database_big`). Deliberate divergence from the source,
    which dropped it silently and still reported a successful patch; keep the
    raise.
  * Preserved: every pitcher ships with the same 50-velocity, 50-control
    arsenal (`stat_mapper.map_pitcher`).
  * Preserved: every patched player is written at 6'0" and 190 lb from two
    fields nothing ever sets (`patcher._build_attrib_fields`).
"""

from .patcher import MVPPSPPatcher

__all__ = ["MVPPSPPatcher"]
