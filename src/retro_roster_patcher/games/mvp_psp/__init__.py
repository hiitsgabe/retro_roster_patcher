"""MVP Baseball, for the PSP (ULUS-10012, EA Sports 2005).

The third and last of the EA disc games, and the odd one out among them. It
shares `formats/ea_tdb.py`'s RefPack layer with `games/nhl05_ps2` and
`games/nhl07_psp` and nothing else: there is no TDB, no BIGF archive and no ISO
9660 walk. `database.big` sits at a hardcoded LBA and holds nineteen
independently compressed **CSV tables** at fixed offsets, linked by nine-hex-digit
record ids. That is a third patching model, alongside byte offsets into a
cartridge and named records in a TDB.

Thirty MLB slots, twenty-five players each -- fifteen batters, five starters,
five relievers -- on a 0-99 scale. Teams map to slots by abbreviation, so there
is no slot-mapping step. One provider, ESPN.

**Three inherited bugs live here. One is fixed and two are preserved**, each
argued and labelled at the line it lives on:

  * FIXED -- a section that recompressed larger than its fixed allocation was
    silently dropped, keeping the disc's original table and still reporting a
    successful patch. It now raises (`rom_writer.rebuild_database_big`). This is
    the one place where a byte the source wrote is not what this port writes,
    and the reason is that the source's byte was a disc patched halfway with no
    warning.
  * PRESERVED -- every pitcher ships with the same 50-velocity, 50-control
    arsenal, because `map_pitcher` overwrites the stat-derived one it has just
    computed (`stat_mapper.map_pitcher`).
  * PRESERVED -- every patched player is written at 6'0" and 190 lb from two
    fields nothing ever sets (`patcher._build_attrib_fields`). `Player.weight`
    is filled by `sports/espn.py` and this package does not read it.

The two preserved ones are wrong and known to be wrong. Nothing in this package
has ever been checked against a retail UMD, and writing a byte the source did not
write is a risk on hardware that better ratings and truer biographies do not buy
off.

**Things dropped from the source, listed because dropping is a behaviour
change.** None had a caller in the source package or the application above it:

  * `MVPPSPRomReader.get_team_roster`, `get_player_attribs`,
    `get_player_lr_attribs`, `get_pitch_attribs`, `get_existing_player_hashes`
    and `get_existing_team_hashes`; `MVPPSPRomWriter.remove_player_record`.
  * `models.TEAM_ABBREV_TO_HASH`, a byte-identical duplicate of `TEAM_HASHES`,
    and `rom_reader.HASH_TO_ABBREV`, recomputed inline at its one use.
  * `models.WRITABLE_TABLES`, which named seven tables where four are written,
    and `models.POSITIONS`, which listed nineteen position strings in an order
    no code used.
  * An unused `OPS` read in `_apply_batter_stats`, and the `is_pitcher`
    parameter of `_normalize_position`, which every call site passed False.
"""

from .patcher import MVPPSPPatcher

__all__ = ["MVPPSPPatcher"]
