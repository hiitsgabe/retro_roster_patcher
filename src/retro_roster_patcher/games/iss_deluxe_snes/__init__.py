"""International Superstar Soccer Deluxe (Konami, 1995), for the SNES, USA cartridge.

36 national-team slots of 20 players; an explicit slot mapping is required.
`rom.py` documents the layout.
"""

from .patcher import ISSDeluxePatcher

__all__ = ["ISSDeluxePatcher"]
