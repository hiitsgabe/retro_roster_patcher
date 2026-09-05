"""NBA Live 95 for the Sega Genesis.

Teams map to ROM slots automatically by abbreviation, so there is no manual slot
mapping step.

A player record is 69 fixed bytes followed by a variable-length name, records are
packed with no padding, and the 30 pointer tables that address them sit at
absolute file offsets transcribed from Team-95's ROM editor.
"""

from .patcher import NBALive95Patcher

__all__ = ["NBALive95Patcher"]
