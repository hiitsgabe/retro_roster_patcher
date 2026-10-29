"""Screen art: the boot, title, legal and menu pictures `patch(images=...)` replaces.

Each slot is one picture the disc keeps in one or more archives, each copy
an `.ssh` shape (`formats.ssh`) that is either an archive member of its own,
RefPack-compressed (the boot screen), or a member of a nested BIGF beside the
screen's Flash movie (the rest; `bg0.big` holds `bg0.ssh`). Every copy is
rewritten where it sits, the archives keeping their size and directory.

The menu and title pictures are packed shapes, whose size never changes with
the picture, so they always fit. The boot screen is a plain one, compressed:
a picture that compresses too large for its slot is retried with fewer
colours, and skipped whole, with a status line, when 32 do not fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from ...formats import ssh
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_parse,
    bigf_replace,
    refpack_compress,
    refpack_decompress,
)

if TYPE_CHECKING:
    from PIL import Image

# Palette sizes tried in turn until a plain picture fits its slot.
COLOUR_STEPS = (256, 128, 96, 64, 48, 32)


@dataclass(frozen=True)
class Copy:
    """One place a slot's picture is stored: the archive, its member and,
    when the member is a BIGF of its own, the `.ssh` inside it."""

    archive: str
    member: str
    shape: str | None = None


@dataclass(frozen=True)
class Guide:
    """A region of a slot's picture, in the picture's own pixels, for templates."""

    x: int
    y: int
    w: int
    h: int
    label: str


@dataclass(frozen=True)
class Slot:
    name: str
    size: tuple[int, int]
    copies: tuple[Copy, ...]
    description: str = ""
    #: Where the game draws over the picture, or what of it shows; for templates.
    guides: tuple[Guide, ...] = ()


SCREEN = (640, 448)


def _on_screen(x: int, y: int, w: int, h: int, label: str) -> Guide:
    """A guide given in screen pixels, for a 512x512 picture stretched to the screen."""
    sx, sy = 512 / SCREEN[0], 512 / SCREEN[1]
    return Guide(round(x * sx), round(y * sy), round(w * sx), round(h * sy), label)


_WHOLE_SCREEN = Guide(0, 0, 512, 512, "whole screen, shown stretched to 640x448")

SLOTS: tuple[Slot, ...] = (
    Slot(
        "boot",
        (512, 512),
        (
            Copy("PRELOAD/FMISC.VIV", "bootscreen.ssh"),
            Copy("FE/LOADSCR.VIV", "bootscreen.ssh"),
        ),
        "the splash with the logo before the memory card check, stretched to 640x448",
        (_WHOLE_SCREEN, _on_screen(248, 262, 144, 24, "the loading dots (game)")),
    ),
    Slot(
        "title",
        (512, 512),
        (
            Copy("FE/PNAPT.VIV", "scrTitle.big", "scrTitle.ssh"),
            Copy("FE/PNAPT.VIV", "scrDemo.big", "scrDemo.ssh"),
        ),
        "the title screen behind PRESS START, stretched to 640x448",
        (
            _WHOLE_SCREEN,
            _on_screen(150, 300, 340, 60, "PRESS START and the copyright line (game text)"),
        ),
    ),
    Slot(
        "legal",
        (512, 512),
        (
            Copy("PRELOAD/FBOOT.VIV", "scrLegal.big", "scrLegal.ssh"),
            Copy("FE/PNAPT.VIV", "scrLegal.big", "scrLegal.ssh"),
        ),
        "the backdrop of the trademark notice, stretched to 640x448",
        (_WHOLE_SCREEN, _on_screen(60, 60, 400, 330, "the notice (game text)")),
    ),
    Slot(
        "menu",
        (512, 512),
        (Copy("FE/BGAPT.VIV", "bg0.big", "bg0.ssh"),),
        "the arena behind the main menu, stretched to 640x448",
        (
            _WHOLE_SCREEN,
            _on_screen(70, 50, 240, 140, "the NHL 2005 logo"),
            _on_screen(70, 200, 330, 200, "menu items (game text)"),
            _on_screen(380, 40, 260, 400, "the player photo covers this"),
        ),
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}


def _unpacked(stored: bytes) -> tuple[bytes, bool]:
    """A member's bytes and whether it was stored RefPack-compressed."""
    if stored[:2] == b"\x10\xfb":
        return refpack_decompress(stored), True
    return stored, False


def _shape_of(member: bytes, copy: Copy) -> tuple[bytes, bool, bytes] | None:
    """(the member's plain bytes, whether it was compressed, the shape) or None."""
    raw, packed = _unpacked(member)
    if copy.shape is None:
        return raw, packed, raw
    inner = next((e for e in bigf_parse(raw) if e.name.lower() == copy.shape.lower()), None)
    if inner is None:
        return None
    shape, _ = _unpacked(raw[inner.offset : inner.offset + inner.size])
    return raw, packed, shape


def read_picture(member: bytes, copy: Copy) -> Image.Image | None:
    """The picture a copy holds, or None when it cannot be read."""
    try:
        found = _shape_of(member, copy)
        if found is None:
            return None
        return ssh.decode(found[2])
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None


def _encoded(member: bytes, copy: Copy, image: Image.Image, colours: int) -> bytes | None:
    """The member with the copy's shape replaced by `image`, stored as it was."""
    try:
        found = _shape_of(member, copy)
        if found is None:
            return None
        raw, packed, shape = found
        data = ssh.encode(shape, image, colours)
    except (EaTdbError, ssh.SshError, IndexError, ValueError):
        return None
    if copy.shape is not None:
        inner = next(e for e in bigf_parse(raw) if e.name.lower() == copy.shape.lower())
        if raw[inner.offset : inner.offset + 2] == b"\x10\xfb":
            data = refpack_compress(data, tight=True)
        raw = bigf_replace(raw, inner.name, data)
    else:
        raw = data
    return refpack_compress(raw, room=len(member)) if packed else raw


def fitted(member: bytes, copy: Copy, image: Image.Image) -> bytes | None:
    """`image` in the copy's shape, with the most colours that fit the member's
    slot. None when the shape cannot be read or no palette size fits."""
    for colours in COLOUR_STEPS:
        data = _encoded(member, copy, image, colours)
        if data is None:
            return None
        if len(data) <= len(member):
            return data
    return None


def to_slot(image: Image.Image, slot: Slot) -> Image.Image:
    """`image` as the slot's RGBA picture, stretched to its size when it differs."""
    from PIL import Image

    rgba = image.convert("RGBA")
    if rgba.size != slot.size:
        rgba = rgba.resize(slot.size, Image.Resampling.LANCZOS)
    return rgba


def plan_writes(
    archives: Mapping[str, bytes], images: Mapping[str, Image.Image]
) -> tuple[dict[str, bytearray], dict[str, int], list[str]]:
    """Edited copies of `archives` with every slot in `images` drawn in place.

    Returns (archive path -> new bytes, for those that changed; slot -> copies
    written; slots skipped because a copy could not take the picture).
    `archives` maps each ISO path a slot names to the archive's bytes; a
    missing archive or member counts as a copy that cannot take it.
    """
    edited: dict[str, bytearray] = {}
    written: dict[str, int] = {}
    skipped: list[str] = []
    for name, image in images.items():
        slot = SLOTS_BY_NAME[name]
        picture = to_slot(image, slot)
        pending: list[tuple[str, int, bytes]] = []
        ok = True
        for copy in slot.copies:
            archive = edited.get(copy.archive) or archives.get(copy.archive)
            if archive is None:
                ok = False
                break
            members = [
                e for e in bigf_parse(bytes(archive)) if e.name.lower() == copy.member.lower()
            ]
            if not members:
                ok = False
                break
            for entry in members:
                original = bytes(archive[entry.offset : entry.offset + entry.size])
                data = fitted(original, copy, picture)
                if data is None:
                    ok = False
                    break
                pending.append((copy.archive, entry.offset, data.ljust(entry.size, b"\0")))
            if not ok:
                break
        if not ok:
            skipped.append(name)
            continue
        for path, offset, data in pending:
            target = edited.setdefault(path, bytearray(archives[path]))
            target[offset : offset + len(data)] = data
        written[name] = len(pending)
    return edited, written, skipped


def read_archives(f: BinaryIO, paths: set[str]) -> dict[str, bytes]:
    """The bytes of each archive path that exists on the ISO."""
    from ..nhl07_psp.rom_writer import iso_entry

    out = {}
    for path in sorted(paths):
        found = iso_entry(f, path)
        if found is None:
            continue
        f.seek(found[0].extent.offset)
        out[path] = f.read(found[0].extent.size)
    return out


def slot_archives(names: set[str] | None = None) -> set[str]:
    """Every archive path the named slots (all by default) read or write."""
    return {
        copy.archive
        for slot in SLOTS
        if names is None or slot.name in names
        for copy in slot.copies
    }
