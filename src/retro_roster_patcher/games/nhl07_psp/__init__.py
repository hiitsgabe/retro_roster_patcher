"""NHL 07, for the PlayStation Portable.

Rosters live in EA TDB tables inside a BIGF archive on the ISO, and every write
names a four-character field rather than a byte offset. The three format layers
are in `formats/ea_tdb.py`; the ISO 9660 Mode 1 walk is in
`formats/iso9660.py`.

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step. Two providers, ESPN for the current season and the NHL API back to 1993.

A compressed disc image -- `.cso`, `.zso`, `.jso`, `.dax` -- is refused with a
message that names the format. `patcher.py` argues why.
"""

from .patcher import NHL07PSPPatcher

__all__ = ["NHL07PSPPatcher"]
