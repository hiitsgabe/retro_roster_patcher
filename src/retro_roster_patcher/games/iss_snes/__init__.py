"""International Superstar Soccer (Konami, 1994), for the SNES.

27 national-team slots of 15 players each, and no way for the ROM to say which
club belongs in which of them -- so this patcher requires an explicit slot
mapping, as `we2002` does and for the same reason. `patcher.py` explains why
that diverges from the code this was ported from.

One provider, ESPN, which needs no credential. The upstream took an API key for
a provider this library no longer has; that parameter is gone rather than
accepted and ignored.

The ROM writer is not a field writer. It patches 65816 machine code at ten fixed
addresses to relocate the in-game name tiles, compresses in Konami's own format,
rewrites three pointer tables in three different LoROM encodings and renders a
2bpp bitmap font. `rom_writer.py`'s docstring is the map.
"""

from .patcher import ISSPatcher

__all__ = ["ISSPatcher"]
