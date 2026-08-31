"""Exception hierarchy.

Failures raise. `PatchResult` is a success payload only — a forgotten
`if result.success` fails silently, a forgotten `except` does not.

This module imports nothing from the rest of the package so that any module may
import it without creating a cycle.
"""


class RetroRosterError(Exception):
    """Base class for every error this library raises."""


class RomError(RetroRosterError):
    """The ROM is missing, unreadable, or not the expected game."""


class ApiError(RetroRosterError):
    """An upstream sports API failed, rate-limited, or returned nothing usable."""


class MappingError(RetroRosterError):
    """A slot mapping is missing, incomplete, or inconsistent with the ROM."""


class CapabilityError(RetroRosterError):
    """The caller used the API in a way this patcher does not support."""
