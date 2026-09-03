"""NHL 94 for the Super Nintendo.

Teams map to ROM slots automatically by three-letter code, so no manual slot
mapping step is needed. Two providers: ESPN for the current season, the NHL
official API for seasons back to 1993.

Structurally the twin of `games.nhl94_genesis`, with the same 8-byte packed
attribute records -- but the length prefix on a player name is little-endian
here and big-endian there, and this ROM stores each team's forward and
defenceman counts in the image rather than fixing them in code.
"""

from .patcher import NHL94SNESPatcher

__all__ = ["NHL94SNESPatcher"]
