"""Screen art: the boot, title, menu and loading pictures `patch(images=...)` replaces.

Each slot is one picture that the disc keeps in several archives: the
`PRELOAD` copies the game draws from and the front-end copies it may reload.
Every copy is rewritten. An archive member is either a bare SHPM file or a
RefPack-compressed BIGF holding the screen's movie and its `.msh`.

Every copy is written where it sits, its archive keeping its size and its
directory, so the cached directories in `PRELOAD` (`<name>.bh`) stay valid.
A picture that compresses too large for a copy's slot is retried with fewer
colours; one that does not fit even at 32 is skipped whole, with a status
line, rather than leaving its copies disagreeing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from ...formats import shpm
from ...formats.ea_tdb import (
    EaTdbError,
    bigf_parse,
    bigf_replace,
    refpack_compress,
    refpack_decompress,
)

if TYPE_CHECKING:
    from PIL import Image

# Palette sizes tried in turn until a picture fits its slot.
COLOUR_STEPS = (256, 128, 96, 64, 48, 32)


@dataclass(frozen=True)
class Copy:
    """One place a slot's picture is stored.

    `member` names the archive member (every member of that name, compared
    case-insensitively); `msh` the `.msh` inside it when the member is a
    nested BIGF, None when the member is the SHPM file itself; `shape` the
    shape's name in that file's directory (the movie's character number).
    """

    archive: str
    member: str
    msh: str | None
    shape: str


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


SCREEN = (480, 272)


def _on_screen(x: int, y: int, w: int, h: int, label: str) -> Guide:
    """A guide given in screen pixels, for a 512x256 picture stretched to the screen."""
    sx, sy = 512 / SCREEN[0], 256 / SCREEN[1]
    return Guide(round(x * sx), round(y * sy), round(w * sx), round(h * sy), label)


_WHOLE_SCREEN = Guide(0, 0, 512, 256, "whole screen, shown stretched to 480x272")


_PRELOAD = "PSP_GAME/USRDIR/PRELOAD/"
_FE = "PSP_GAME/USRDIR/FE/"


def _both(member: str, msh: str, shape: str, *archives: str) -> tuple[Copy, ...]:
    return tuple(Copy(archive, member, msh, shape) for archive in archives)


_FBOOT = _PRELOAD + "FBOOT.VIV"

SLOTS: tuple[Slot, ...] = (
    Slot(
        "boot",
        (512, 256),
        (
            Copy(_PRELOAD + "FMISC.VIV", "bootscreen.msh", None, "boot"),
            Copy(_PRELOAD + "GLOADSCR.VIV", "bootscreen.msh", None, "boot"),
        ),
        "the splash before the autosave prompt, drawn stretched to 480x272",
        # From bootscreen.bin: LOADINGBARXCOORD 163, LOADINGBARYCOORD 195, a 128x64 bar.
        (_WHOLE_SCREEN, _on_screen(163, 195, 128, 64, "loading bar (game)")),
    ),
    Slot(
        "title",
        (512, 256),
        _both("scrTitle.big", "scrTitle.msh", "1", _FBOOT, _FE + "PNAPT.VIV"),
        "the title screen, drawn stretched to 480x272",
        # Measured in PPSSPP on the retail disc.
        (
            _WHOLE_SCREEN,
            _on_screen(150, 200, 180, 72, "PRESS START and the copyright lines (game text)"),
            _on_screen(4, 228, 112, 44, "the two badge slots"),
        ),
    ),
    Slot(
        "menu",
        (512, 256),
        _both("main.big", "main.msh", "1", _FBOOT, _FE + "CAFEAPT.VIV"),
        "the backdrop under the menus and the legal screen, drawn stretched to 480x272",
        (
            _on_screen(0, 0, 480, 44, "header: the game writes white text"),
            _on_screen(0, 44, 224, 188, "menu items: the game writes navy text"),
            _on_screen(224, 0, 256, 272, "the player slot covers this on the main menu"),
            _on_screen(0, 232, 480, 40, "button hints: the game writes white text"),
        ),
    ),
    Slot(
        "logo",
        (256, 64),
        _both("menu.big", "menu.msh", "11", _FBOOT, _FE + "BGAPT.VIV"),
        "the game logo above the main menu, on transparency, drawn about 237x52",
        (Guide(0, 0, 256, 64, "logo, shown about 237x52"),),
    ),
    Slot(
        "player",
        (256, 512),
        _both("menu.big", "menu.msh", "9", _FBOOT, _FE + "BGAPT.VIV"),
        "the player beside the main menu, on transparency; rows 0-271 show",
        (
            Guide(0, 0, 256, 272, "shown beside the main menu"),
            Guide(0, 272, 256, 240, "never shown"),
        ),
    ),
    Slot(
        "loading",
        (512, 256),
        tuple(Copy(_FE + "LOADSCR.VIV", f"loadscreen{i}.msh", None, "load") for i in range(8)),
        "all eight loading screens, drawn stretched to 480x272",
        # From loadscreenN.bin: LOADINGBARXCOORD 306, LOADINGBARYCOORD 206.
        (_WHOLE_SCREEN, _on_screen(306, 206, 128, 64, "loading bar (game)")),
    ),
    Slot(
        "league_badge",
        (128, 128),
        _both("scrLegal.big", "scrLegal.msh", "10", _FBOOT, _FE + "PNAPT.VIV")
        + _both("scrTitle.big", "scrTitle.msh", "6", _FBOOT, _FE + "PNAPT.VIV")
        + (
            Copy(_FE + "PNAPT.VIV", "scrMiniGameChooseTeam.big", "scrMiniGameChooseTeam.msh", "35"),
        ),
        "the NHL shield on the legal, title and mini-game team-select screens, on transparency",
        (Guide(0, 0, 128, 128, "badge"),),
    ),
    Slot(
        "players_badge",
        (128, 128),
        _both("scrLegal.big", "scrLegal.msh", "9", _FBOOT, _FE + "PNAPT.VIV")
        + _both("scrTitle.big", "scrTitle.msh", "5", _FBOOT, _FE + "PNAPT.VIV"),
        "the NHLPA logo on the legal and title screens, on transparency",
        (Guide(0, 0, 128, 128, "badge"),),
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}


def _shape_of(member: bytes, copy: Copy) -> tuple[bytes, shpm.Shpm] | None:
    """(the member's decompressed bytes, the parsed SHPM) or None."""
    raw = refpack_decompress(member) if member[:2] == b"\x10\xfb" else member
    if copy.msh is None:
        return raw, shpm.parse(raw)
    inner = next((e for e in bigf_parse(raw) if e.name.lower() == copy.msh.lower()), None)
    if inner is None:
        return None
    return raw, shpm.parse(raw[inner.offset : inner.offset + inner.size])


def _named(parsed: shpm.Shpm, name: str) -> shpm.Shape:
    """The shape with this directory name; IndexError when there is none."""
    for shape in parsed.shapes:
        if shape.name.rstrip(b" \0").decode("ascii", "replace") == name:
            return shape
    raise IndexError(name)


def read_picture(member: bytes, copy: Copy) -> Image.Image | None:
    """The picture a copy holds, or None when it cannot be read."""
    try:
        found = _shape_of(member, copy)
        if found is None:
            return None
        return shpm.decode_shape(_named(found[1], copy.shape))[0]
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None


def _encoded(member: bytes, copy: Copy, image: Image.Image) -> bytes | None:
    """The member with the copy's shape replaced by `image`, recompressed."""
    try:
        found = _shape_of(member, copy)
        if found is None:
            return None
        raw, parsed = found
        shpm.encode_shape(_named(parsed, copy.shape), image)
    except (EaTdbError, shpm.ShpmError, IndexError, ValueError):
        return None
    built = shpm.build(parsed)
    if copy.msh is not None:
        inner = next(e for e in bigf_parse(raw) if e.name.lower() == copy.msh.lower())
        built = bigf_replace(raw, inner.name, built)
    compressed = member[:2] == b"\x10\xfb"
    return refpack_compress(built) if compressed else built


def fitted(member: bytes, copy: Copy, image: Image.Image) -> bytes | None:
    """`image` in the copy's shape, with the most colours that fit the member's slot.

    None when the shape cannot be read or no palette size fits.
    """
    from PIL import Image

    for colours in COLOUR_STEPS:
        reduced = image
        if colours < COLOUR_STEPS[0]:
            reduced = image.quantize(colours, method=Image.Quantize.FASTOCTREE).convert("RGBA")
        data = _encoded(member, copy, reduced)
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
    from .rom_writer import iso_entry

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
