"""NHL 94 for the Super Nintendo.

Teams map to ROM slots automatically by three-letter code, so there is no manual
slot mapping step.

The twin of `games.nhl94_genesis`, same 8-byte packed attribute records -- but a
player name's length prefix is little-endian here and big-endian there, and this
ROM stores each team's forward and defenceman counts in the image.
"""

from .patcher import NHL94SNESPatcher

__all__ = ["NHL94SNESPatcher"]
