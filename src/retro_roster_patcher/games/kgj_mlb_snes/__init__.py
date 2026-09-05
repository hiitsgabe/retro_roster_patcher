"""Ken Griffey Jr. Presents Major League Baseball, for the SNES.

Teams map to ROM slots automatically by abbreviation, so there is no manual slot
mapping step.

A player record is a fixed 32 bytes, attributes are a 1-10 scale stored as
`value - 1` in a nibble, names use a private character encoding rather than
ASCII, and the team tables are located by searching the image for a 14-byte
marker rather than by any fixed offset.
"""

from .patcher import KGJMLBPatcher

__all__ = ["KGJMLBPatcher"]
