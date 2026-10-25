"""Screen art: the stadium behind the title and the front-end menus, and the
Nintendo licence screen.

`patch(images=...)` replaces them and `export_images` writes them out. Each is
three resources of the game's archive (`resources`): a tile set, a map of 28
rows of 32 words, and a 256-byte palette (rows 0-7). A picture goes through
ISS's encoder (`iss_snes.screens.encode`: 15 colours per palette row, each 8x8
tile in the row that draws it best, flips, and the closest tiles merged until
they fit the slot's budget). The three resources are written stored (codec 6)
in this module's part of a 4 MiB image (0x300000-0x3FFFFF; the ROM grows) and
their directory entries repointed. The cartridge's own streams stay where they
are, unused, so a patched ROM can be patched again. An entry that is neither
the cartridge's nor already in its slot's part is refused: the image is not
the cartridge this module was written against.

What each screen does with its picture was measured in Mesen 2 on the retail
cartridge:

* title: the stadium. BG2 of the intro swing and the title (tiles at VRAM word
  $7000), BG1 of the front-end menus (Select Game Type, Number of Players,
  All-Star, World Series, Season), which take its 256 tiles with an 8 KiB DMA
  from WRAM. The season / World Series result screen ($83:D9B3) draws it too,
  dimmed, under the team's name, its message and Ken Griffey Jr. (tiles at
  $2000), and so does a screen at $85:C4E2 that shows the logo and the
  copyright over it, probably before a game (tiles at $1000); both were
  reached only by jumping to them. It takes rows 6 and 7;
  rows 0-5 are the logo, Griffey, the intro animation and the menu boxes and
  keep the cartridge's colours. Griffey (entry 2137) stays.
* boot: the Nintendo screen at power-on, BG1, faded in and out by brightness.
  The first 176 tiles of its set stay: the credits screen after it draws the
  MLB logo from tiles 0-33, and a hidden credits sequence (B, A, Down, B, Up,
  B, B, A on the title) loads the same set and palette and ends on a Software
  Creations screen drawn from tiles 34-144 in palette row 0. So the picture
  takes tiles 176-1023 (VRAM $2B00-$5FFF, empty at boot) and rows 2-7: row 0
  stays and row 1 is reloaded right after. The cartridge's map is a 4-row
  strip loaded at row 12 (LDX #$0300 at $83:DBF0); a picture's is 28 rows, so
  that operand becomes 0.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.errors import RomError
from ...formats import snes
from ..iss_snes import screens as iss
from . import resources
from .rom_reader import header_size

if TYPE_CHECKING:
    from PIL import Image

Guide = iss.Guide


@dataclass(frozen=True)
class Slot:
    name: str
    size: tuple[int, int]
    description: str
    #: Where the game draws over the picture, for templates.
    guides: tuple[Guide, ...]
    #: Directory entries of the tile set, the map and the palette.
    tiles: int
    tile_map: int
    palette: int
    #: Palette rows the picture takes, 15 colours each.
    rows: tuple[int, ...]
    #: Tiles kept from the cartridge's set, before the picture's.
    first_tile: int
    #: Tiles the picture may use.
    budget: int
    #: File offset (headerless, 4 MiB image) of the slot's data.
    at: int
    #: (file offset, cartridge bytes, patched bytes) the screen needs.
    pokes: tuple[tuple[int, bytes, bytes], ...] = ()


#: The operand of LDX #$0300 at $83:DBF0: the byte the boot map loads to.
_BOOT_MAP_AT = 0x1DBF1
_MAP_BYTES = 28 * 64
_PALETTE_BYTES = 256
_ROW = 16
_SIZE = 0x400000
#: Each slot's part of the screens region, from `Slot.at`.
_SPAN = 0x10000
#: (codec, stream length, file offset) of each entry on the retail cartridge.
_RETAIL: Mapping[int, tuple[int, int, int]] = {
    2139: (4, 4665, 0x17BDF2),
    2509: (8, 1425, 0x1C1EE7),
    2255: (4, 240, 0x1B70CA),
    2449: (4, 2556, 0x1BA971),
    2469: (8, 100, 0x1BC528),
    2254: (4, 29, 0x1B70AD),
}

SLOTS: tuple[Slot, ...] = (
    Slot(
        "title",
        (256, 224),
        "the stadium behind the intro swing, the title (with Ken Griffey Jr.), the "
        "front-end menus and (dimmed) the season result screen, in 30 colours "
        "(2 palette rows)",
        # Measured in Mesen on the retail screens; the result text is the union
        # of its four messages and the team's name over them.
        (
            Guide(0, 5, 200, 97, "KGJ logo (game's)"),
            Guide(42, 117, 59, 38, "PUSH START (game's)"),
            Guide(120, 31, 136, 193, "Griffey (game's)"),
            Guide(96, 107, 156, 92, "menu box (game's)"),
            Guide(16, 16, 176, 160, "season result text (game's)"),
            Guide(16, 205, 118, 8, "copyright (game's)"),
        ),
        tiles=2139,
        tile_map=2509,
        palette=2255,
        rows=(6, 7),
        first_tile=0,
        # 8 KiB: the menus' DMA from WRAM, and VRAM $7000-$7FFF on the title.
        budget=256,
        at=0x300000,
    ),
    Slot(
        "boot",
        (256, 224),
        "the Nintendo licence screen at power-on, faded in and out, in 90 colours (6 palette rows)",
        (Guide(0, 0, 256, 224, "fades in and out"),),
        tiles=2449,
        tile_map=2469,
        palette=2254,
        rows=(2, 3, 4, 5, 6, 7),
        first_tile=176,
        # A map word's 10-bit tile number: tiles 176-1023.
        budget=1024 - 176,
        at=0x310000,
        pokes=((_BOOT_MAP_AT, b"\x00\x03", b"\x00\x00"),),
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}


def _iss(slot: Slot) -> iss.Slot:
    """`slot` as ISS's encoder takes it (it reads the size, colours, tile numbers)."""
    none = iss.Stream(0, 0, ())
    return iss.Slot(
        slot.name,
        slot.size,
        slot.description,
        slot.guides,
        tiles=none,
        tile_map=none,
        colours=tuple(iss.Colours(0, row) for row in slot.rows),
        first_tile=slot.first_tile,
        budget=slot.budget,
        keep=(),
        priority=0,
        backdrop=0,
        bank=0,
    )


def _off_cartridge(rom: bytes | bytearray, slot: Slot) -> int | None:
    """The slot's first entry that is neither the cartridge's own nor a patch's
    (in its slot's region), or None when all three entries are known shapes."""
    for i in (slot.tiles, slot.tile_map, slot.palette):
        codec, _length, at = found = resources.entry(rom, i)
        patched = codec == resources.STORED and slot.at <= at < slot.at + _SPAN
        if found != _RETAIL[i] and not patched:
            return i
    return None


def _check(rom: bytes | bytearray, slot: Slot) -> None:
    bad = _off_cartridge(rom, slot)
    if bad is not None:
        raise RomError(
            f"The {slot.name} screen's resource {bad} is not the cartridge's this patcher "
            f"knows; screen art left alone"
        )
    for at, retail, ours in slot.pokes:
        if bytes(rom[at : at + len(retail)]) not in (retail, ours):
            raise RomError(
                f"The {slot.name} screen's code at 0x{at:05X} is not the cartridge's this "
                f"patcher knows; screen art left alone"
            )


def _kept(rom: bytes | bytearray, slot: Slot) -> bytes:
    if not slot.first_tile:
        return b""
    tiles = resources.load(rom, slot.tiles)[: 32 * slot.first_tile]
    if len(tiles) < 32 * slot.first_tile:
        raise RomError(f"The {slot.name} tiles are shorter than the cartridge's")
    return tiles


def write(rom: bytearray, pictures: Mapping[str, Image.Image]) -> list[str]:
    """Draw each slot in `pictures` into `rom` (headerless), growing it to 4 MiB;
    status notes. The caller fixes the header checksum."""
    for name in pictures:
        _check(rom, SLOTS_BY_NAME[name])
    notes: list[str] = []
    snes.grow(rom, _SIZE)
    for name, image in pictures.items():
        slot = SLOTS_BY_NAME[name]
        encoded = iss.encode(image, _iss(slot), {})
        if encoded.used < encoded.distinct:
            notes.append(f"{name}: {encoded.distinct} tiles merged to {encoded.used}")
        palette = bytearray(resources.load(rom, slot.palette).ljust(_PALETTE_BYTES, b"\0"))
        for row, colours in encoded.colours.items():
            words = (colours + [0] * _ROW)[: _ROW - 1]
            palette[32 * row + 2 : 32 * row + 32] = b"".join(c.to_bytes(2, "little") for c in words)
        at = resources.store(rom, slot.tiles, _kept(rom, slot) + encoded.tiles, slot.at)
        at = resources.store(rom, slot.tile_map, encoded.words[:_MAP_BYTES], at)
        resources.store(rom, slot.palette, bytes(palette[:_PALETTE_BYTES]), at)
        for at, _retail, ours in slot.pokes:
            rom[at : at + len(ours)] = ours
    return notes


def draw(data: bytearray, pictures: Mapping[str, Image.Image]) -> list[str]:
    """`write` on a whole file's bytes, copier header or not, in place."""
    base = header_size(len(data))
    body = data[base:]
    notes = write(body, pictures)
    data[base:] = body
    return notes


# --- rendering ---------------------------------------------------------------


def render(rom: bytes | bytearray, slot: Slot) -> Image.Image:
    """The slot's picture as the ROM holds it, colour 0 black."""
    from PIL import Image

    tiles = resources.load(rom, slot.tiles)
    words = resources.load(rom, slot.tile_map)
    palette = resources.load(rom, slot.palette).ljust(_PALETTE_BYTES, b"\0")
    # The cartridge's boot map is a strip that loads at row 12.
    top = (rom[_BOOT_MAP_AT] | rom[_BOOT_MAP_AT + 1] << 8) // 64 if slot.pokes else 0
    w, h = slot.size
    pixels = bytearray(w * h)
    for i in range(min(len(words) // 2, (h // 8 - top) * 32)):
        word = words[2 * i] | words[2 * i + 1] << 8
        number = word & 0x3FF
        p = iss._flipped(
            iss.tile_indices(tiles[32 * number : 32 * number + 32].ljust(32, b"\0")),
            (word >> 14 & 1) | (word >> 15 & 1) << 1,
        )
        row = (word >> 10 & 7) * _ROW
        tx, ty = i % 32, top + i // 32
        for y in range(8):
            at = (ty * 8 + y) * w + tx * 8
            pixels[at : at + 8] = bytes(row + v if v else 0 for v in p[y * 8 : y * 8 + 8])
    colours = [palette[2 * i] | palette[2 * i + 1] << 8 for i in range(_PALETTE_BYTES // 2)]
    colours[0] = 0
    image = Image.frombytes("P", (w, h), bytes(pixels))
    image.putpalette([c for colour in colours for c in iss.to_rgb(colour)])
    return image.convert("RGB")


# --- files -------------------------------------------------------------------


def load_images(given: Any, status: Callable[[str], None]) -> dict[str, Any]:
    """`patch(images=...)`: ISS's loader, checked against this game's slots."""
    return iss.load_images(given, status, IMAGE_SLOT_NAMES)


def export(rom_path: Path, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`.

    A ROM that is not the cartridge this module was written against (or already
    patched) is refused up front, the same thing `write` checks, instead of
    failing mid-decode with a codec error.
    """
    data = Path(rom_path).read_bytes()
    rom = data[header_size(len(data)) :]
    for slot in SLOTS:
        bad = _off_cartridge(rom, slot)
        if bad is not None:
            raise RomError(
                f"The {slot.name} screen's resource {bad} is not the cartridge's this patcher knows"
            )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for slot in SLOTS:
        path = out / f"{slot.name}.png"
        render(rom, slot).save(path)
        written.append(path)
    return written
