"""NHL 94 for the Sega Genesis.

Teams map to ROM slots automatically by three-letter code, so no manual slot
mapping step is needed. Two providers: ESPN for the current season, the NHL
official API for seasons back to 1993.
"""

from .patcher import NHL94GenesisPatcher

__all__ = ["NHL94GenesisPatcher"]
