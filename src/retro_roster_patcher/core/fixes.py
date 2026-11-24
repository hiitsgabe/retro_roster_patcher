"""Optional fixes to a cartridge's code: byte edits checked before they are made."""

from collections.abc import Mapping, Sequence

#: Per fix name, `(offset, retail bytes, fixed bytes)` in hex.
Fixes = Mapping[str, Sequence[tuple[int, str, str]]]


def set_fix(rom: bytearray, fixes: Fixes, name: str, enabled: bool, base: int = 0) -> bool:
    """Apply (or undo) the fix `name` in `rom`, its offsets `base` bytes in.
    False, writing nothing, when any of its bytes are neither the cartridge's
    nor the fix's."""
    edits = [(base + at, bytes.fromhex(old), bytes.fromhex(new)) for at, old, new in fixes[name]]
    if any(bytes(rom[at : at + len(old)]) not in (old, new) for at, old, new in edits):
        return False
    for at, old, new in edits:
        rom[at : at + len(old)] = new if enabled else old
    return True
