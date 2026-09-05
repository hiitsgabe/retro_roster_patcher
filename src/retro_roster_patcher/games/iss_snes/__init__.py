"""International Superstar Soccer (Konami, 1994), for the SNES.

27 national-team slots of 15 players each. Nothing in the ROM names a team, so
an explicit slot mapping is required; keep it required.

One provider, ESPN, which needs no credential. Do not reintroduce an `api_key`.

The ROM writer is not a field writer. It patches 65816 machine code at ten fixed
addresses to relocate the in-game name tiles, compresses in Konami's own format,
rewrites three pointer tables in three different LoROM encodings and renders a
2bpp bitmap font. `rom_writer.py`'s docstring is the map.
"""

from .patcher import ISSPatcher

__all__ = ["ISSPatcher"]
