"""NHL 07, for the PlayStation Portable.

The first disc-based game in this library and the first that patches named
database records rather than byte offsets: rosters live in EA TDB tables inside
a BIGF archive on the ISO, and every write names a four-character field. The
three format layers are in `formats/ea_tdb.py`, shared with the two Phase 4
games that follow; the ISO 9660 Mode 1 helpers are in `rom_reader.py` and
travel with this game until `nhl05-ps2` gives them a second call site.

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step. Two providers, ESPN for the current season and the NHL API back to 1993.

A compressed disc image -- `.cso`, `.zso`, `.jso`, `.dax` -- is refused with a
message that names the format. `patcher.py` argues why.
"""

from .patcher import NHL07PSPPatcher

__all__ = ["NHL07PSPPatcher"]
