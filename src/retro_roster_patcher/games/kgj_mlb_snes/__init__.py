"""Ken Griffey Jr. Presents Major League Baseball, for the SNES.

Teams map to ROM slots automatically by abbreviation, so no manual slot mapping
step is needed. One provider, ESPN, and this is the first baseball game in the
library: `EspnClient.get_mlb_teams`, `get_baseball_squad` and
`get_baseball_team_leaders` existed and were unreachable until this patcher was
registered.

Nothing about the binary format is shared with the NHL 94 SNES port, which is
the only other SNES game here. A player record is a fixed 32 bytes, attributes
are a 1-10 scale stored as `value - 1` in a nibble, names use a private
character encoding rather than ASCII, and the team tables are located by
searching the image for a 14-byte marker rather than by any fixed offset.
"""

from .patcher import KGJMLBPatcher

__all__ = ["KGJMLBPatcher"]
