"""NHL 94 for the Sega Genesis.

Teams map to ROM slots automatically by three-letter code, so there is no manual
slot mapping step.
"""

from .patcher import NHL94GenesisPatcher

__all__ = ["NHL94GenesisPatcher"]
