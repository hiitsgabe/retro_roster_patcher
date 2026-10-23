"""Screen art: the EA SPORTS boot screen, the title and the menu wallpaper.

`patch(images=...)` replaces them and `export_images` writes them out. All art
goes through one routine, $80:C373, which picks its decoder by the stream's
magic: RefPack (`10 FB`, `formats.ea_tdb`) or EA Huffman (`30 FB`,
`formats.ea_huff`) among them. Each stream is loaded by a pair of immediate
loads, `LDA #$00bb` (bank) and `LDA #$aaaa` (address), so a picture is
written as RefPack streams in this module's part of the 2 MiB image (the ROM
grows; a bank of 32 KiB per slot from 0x160000, since no stream may cross a
bank) and those immediates are repointed. The cartridge's own streams stay
where they are: the playoff bracket still loads the menu's, and a patched ROM
can be patched again.

What each screen does with its picture was measured in Mesen 2 on the retail
cartridge:

* boot: the EA SPORTS screen is a Mode 7 plane that zooms in three times while
  36 sprites draw the EA logo. A picture is the plane at 1:1 for the whole
  screen (no zoom, no reveal, the sprites blanked) in one 256-colour load
  from colour 0. The game still fades rows 1-4 and loads rows 11-12 over it,
  so the picture gets the other 159 colours and 255 tiles (tile 0 is blank),
  none flipped: Mode 7 cannot flip a tile.
* title: lines 0-79 are a Mode 1 band (the "NHL" letters on BG2, the 94 puck
  on BG1) and a raster interrupt at line 80 ($9D:B52F) turns the rest of the
  screen into a Mode 7 layer for the zooming shields and pucks. That
  interrupt returns at once now, so BG2 holds the picture for the whole
  screen, in 8 rows of 15 colours and 318 tiles (0 and 256 stay blank: BG1
  draws tile 0 everywhere, and BG3's tile 0 is tile 256's bytes). The sprites
  stay: the letters fly in over the blank screen, the top 80 lines are then
  revealed in steps and the rest at once (about frame 1340 from power-on),
  and the credits and the two shields stay at the bottom. BG2's vertical
  scroll is 0, so the picture's row 0 is not shown and the screen's last line
  is the backdrop; and until the reveal, BG2's window ($9D:B14C, [0, 0])
  shows the picture's column 0 alone.
* menu: the wallpaper behind Game Setup, Player Select, Matchups, the playoff
  setup screens, the password screen and every pause screen, in palette row
  3 (15 colours; the loader adds $0C00 to the map). It takes 364 tiles: what
  video memory holds before the 148 of Ron Barr on Matchups. Tiles 0 and
  $60-$67 stay the cartridge's, since the gold frame around a player's
  portrait is drawn from them ($9A:E9F0), and the game copies the 56x56 box
  at 8,8 over the one at 192,8, so the picture's left box is pasted there
  before encoding. The goalie drawn over Game Setup and Player Select is
  blanked. Ron Barr, drawn on the same map on Matchups, has the cartridge's
  blue wallpaper drawn into the tiles around him in four colours of his own
  ($9A:E946, 1, 2, 3 and 5): they become the picture's average colour under
  them. The playoff bracket lays the wallpaper out its own way and keeps the
  cartridge's.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...core.errors import RomError
from ...formats import ea_huff, snes
from ...formats.ea_tdb import refpack_compress, refpack_decompress
from ..iss_snes import screens as iss

if TYPE_CHECKING:
    from PIL import Image

Guide = iss.Guide


@dataclass(frozen=True)
class Slot:
    name: str
    size: tuple[int, int]
    description: str
    #: Where the game draws over the picture, or what of it shows; for templates.
    guides: tuple[Guide, ...]
    #: File offset of the 32 KiB bank the slot's streams take in the 2 MiB image.
    bank: int
    #: Stream name to the cartridge's address of it and the (bank LDA, address
    #: LDA) pairs that load it, as the CPU addresses of their A9 opcodes.
    sites: Mapping[str, tuple[int, tuple[tuple[int, int], ...]]]
    #: (CPU address, cartridge bytes, patched bytes) the screen needs.
    pokes: tuple[tuple[int, bytes, bytes], ...] = ()


def _after(retail: int, *addresses: int) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Pairs whose bank LDA comes 5 bytes before the address LDA (`STA $0E` between)."""
    return retail, tuple((a - 5, a) for a in addresses)


def _before(retail: int, *addresses: int) -> tuple[int, tuple[tuple[int, int], ...]]:
    """Pairs whose bank LDA comes 5 bytes after the address LDA (`STA $0C` between)."""
    return retail, tuple((a + 5, a) for a in addresses)


#: The cartridge's streams a slot reads from, however often it was patched.
_MENU_TILES = 0x95F1AE
_MENU_COLOURS = 0x9AE121
_TITLE_COLOURS = 0x9ACCA9
_RON_TILES = 0x94D6A5
_RON_MAP = 0x9AC0CC
_RON_COLOURS = 0x9AE946


SLOTS: tuple[Slot, ...] = (
    Slot(
        "boot",
        (256, 224),
        "the EA SPORTS screen at power-on, held still for about 12 seconds, in 159 "
        "colours (8 bits per pixel) and 255 tiles, none flipped",
        (Guide(0, 0, 256, 224, "the whole screen; nothing is drawn over it"),),
        bank=0x160000,
        sites={
            "tiles": _before(0x949A00, 0x9FF54E),
            "map": _before(0x9ADC8D, 0x9FF566),
            "sprites": _before(0x94F8EC, 0x9FF5CA),
            "colours": _before(0x9AEE1C, 0x9FF4E7),
        },
        pokes=(
            # One load of 256 colours from colour 0 ($80:8723 copies them raw).
            (0x9FF4F1, b"\xa0\x30\x00", b"\xa0\x00\x00"),
            (0x9FF4F4, b"\xa2\x10\x00", b"\xa2\x00\x01"),
            # The Mode 7 matrix stays 1:1 ($0100, 0, 0, $0100): no zoom-ins.
            (0x9FFA98, b"\xa5\xd9", b"\xa9\x00"),
            (0x9FFA9D, b"\xa5\xda", b"\xa9\x01"),
            (0x9FFAA2, b"\xa5\xdb", b"\xa9\x00"),
            (0x9FFAA7, b"\xa5\xdc", b"\xa9\x01"),
            # The E, A and SPORTS cells never enter the reveal buffer (RTS) ...
            (0x9FF8F3, b"\xa0\x06\x00", b"\x60\x06\x00"),
            # ... which copies the plane itself, past its 6-byte header, ...
            (0x9FF5AB, b"\x00\x40\x7f", b"\x06\x40\x7f"),
            # ... and the first line on screen shows the picture's row 0.
            (0x9FF5EB, b"\xa9\x90\x01", b"\xa9\x8f\x01"),
        ),
    ),
    Slot(
        "title",
        (256, 224),
        "the title screen, the whole of it, under the credits and the two shields, "
        "in 120 colours (8 palette rows) and 318 tiles",
        (
            Guide(0, 0, 256, 80, "shown first, in steps; row 0 is not shown"),
            Guide(0, 0, 1, 224, "column 0 shows alone, for 4 s before the reveal"),
            Guide(60, 160, 140, 64, "credits scroll up here (game's)"),
            Guide(8, 176, 56, 48, "NHL shield (game's)"),
            Guide(206, 176, 42, 42, "NHLPA logo (game's)"),
        ),
        bank=0x168000,
        sites={
            "colours": _after(_TITLE_COLOURS, 0x9DB5BB),
            "tiles": _after(0x948000, 0x9DB64F),
            "bg1": _after(0x9ADDC6, 0x9DB678),
            "map": _after(0x9AD88C, 0x9DB6AA),
        },
        # The line-80 interrupt returns at once (RTL) instead of starting Mode 7.
        pokes=((0x9DB52F, b"\xe2\x20", b"\x6b\x20"),),
    ),
    Slot(
        "menu",
        (256, 224),
        "the wallpaper behind every menu and pause screen, in 15 colours (1 palette "
        "row) and 355 tiles; the right logo box repeats the left one, and the blue "
        "around Ron Barr on Matchups takes the picture's colours",
        (
            Guide(8, 8, 56, 56, "team logo or portrait, see-through in parts: keep it plain"),
            Guide(192, 8, 56, 56, "the game copies the left box here"),
            Guide(64, 16, 128, 32, "screen title (game's)"),
            Guide(8, 48, 240, 52, "team names (game's)"),
            Guide(8, 100, 248, 124, "menu text, no box behind it; Ron Barr on Matchups"),
        ),
        bank=0x170000,
        # Never the playoff bracket's ($9D:8C4A, 8C73, 8CA3): it keeps the
        # cartridge's wallpaper, laid out for its own 128 tiles.
        sites={
            "tiles": _after(_MENU_TILES, 0x9D8929, 0x9DDB2E),
            "map": _after(0x9ABA95, 0x80CBD7, 0x9B9204, 0x9D895C, 0x9DDB61),
            "colours": _after(_MENU_COLOURS, 0x9D8AC8, 0x9DDD05),
            "goalie_tiles": _after(0x938000, 0x9DDC24),
            "goalie_map": _after(0x9AB7A1, 0x9DDC4D),
            "ron_colours": _after(_RON_COLOURS, 0x9D8A8F),
        },
    ),
)

IMAGE_SLOT_NAMES: tuple[str, ...] = tuple(slot.name for slot in SLOTS)
SLOTS_BY_NAME: Mapping[str, Slot] = {slot.name: slot for slot in SLOTS}

#: This module's part of the 2 MiB image: the slots' three banks, whatever the
#: pictures. `write` clears a slot's whole bank and refuses a picture that
#: would leave it; the most a slot can take (its whole tile budget of
#: incompressible tiles, as RefPack) is about 18 KiB of the 32.
REGION = range(0x160000, 0x178000)

_TWO_MIB = 0x200000
_BANK = 0x8000
_ROW = 16
_GOALIE_COLOURS = 0x9AE972
#: Ron Barr's colours that are the cartridge's wallpaper, drawn around him.
_RON_BLUES = (1, 2, 3, 5)
#: Rows 1-4 of the cartridge's EA SPORTS screen, the last three loaded late.
_BOOT_ROWS = {1: 0x9AEE7C, 2: 0x9AEE5C, 3: 0x9AEE1C, 4: 0x9AEE3C}
#: Menu tiles that stay the cartridge's: blank, and the portrait frame.
_MENU_KEEP = (0, *range(0x60, 0x68))
#: A 32x28 map's header (width, height, 08 03) and a 128x128 Mode 7 plane's.
_MAP_HEAD = bytes.fromhex("20001c000803")
_PLANE_HEAD = bytes.fromhex("800080001000")
_MAP_BYTES = 32 * 28 * 2
#: Where the boot picture sits in the 128x128 plane (tile column, row).
_PLANE_AT = (48, 50)
#: Colours the boot picture may use: not 0 (the backdrop), nor rows 1-4 (the
#: game fades them) or 11-12 (loaded over).
_MODE7_COLOURS = tuple(i for i in range(1, 256) if i // 16 not in (1, 2, 3, 4, 11, 12))
_NONE = iss.Stream(0, 0, ())


def _encoding(name: str, rows: tuple[int, ...], budget: int, keep: tuple[int, ...]) -> iss.Slot:
    """A slot as ISS's encoder takes it (size, rows, budget, kept tiles)."""
    return iss.Slot(
        name,
        (256, 224),
        "",
        (),
        _NONE,
        _NONE,
        tuple(iss.Colours(0, row) for row in rows),
        first_tile=0,
        budget=budget,
        keep=keep,
        priority=0,
        backdrop=0,
        bank=0,
    )


#: Tiles 0-319 run from VRAM $4000 to BG1's map at $5400.
_TITLE_ENCODING = _encoding("title", tuple(range(8)), 320, (0, 256))
#: $1000-$2FFF hold 512 tiles, and Ron Barr takes 148 after the wallpaper.
_MENU_ENCODING = _encoding("menu", (0,), 364, _MENU_KEEP)


# --- the cartridge -----------------------------------------------------------


def _off(address: int) -> int:
    """The file offset of a LoROM CPU address."""
    return (address >> 16 & 0x7F) * _BANK + (address & 0x7FFF)


def _cpu(offset: int) -> int:
    return 0x800000 | offset >> 15 << 16 | 0x8000 | offset & 0x7FFF


def _aimed(rom: bytes | bytearray, pair: tuple[int, int]) -> int:
    """The CPU address an LDA pair loads."""
    bank, address = (_off(a) for a in pair)
    return rom[bank + 1] << 16 | rom[address + 2] << 8 | rom[address + 1]


def _aim(rom: bytearray, pair: tuple[int, int], target: int) -> None:
    bank, address = (_off(a) for a in pair)
    rom[bank + 1] = target >> 16
    rom[address + 1 : address + 3] = (target & 0xFFFF).to_bytes(2, "little")


def _stream(rom: bytes | bytearray, address: int) -> bytes:
    """What the art stream at `address` decodes to, by its magic."""
    at = _off(address)
    data = bytes(rom[at : at - (at % _BANK) + _BANK])
    if data[:2] == b"\x10\xfb":
        return refpack_decompress(data)
    if data[:2] == ea_huff.MAGIC:
        return ea_huff.decompress(data)
    raise RomError(f"No art stream this patcher reads at ${address:06X}")


def _current(rom: bytes | bytearray, slot: Slot, name: str) -> bytes:
    return _stream(rom, _aimed(rom, slot.sites[name][1][0]))


def _words(data: bytes | bytearray, at: int = 0) -> list[int]:
    return [data[i] | data[i + 1] << 8 for i in range(at, len(data) - 1, 2)]


def _check(rom: bytes | bytearray, slot: Slot) -> None:
    """Refuse a cartridge whose code at the slot's sites or pokes is not this one's:
    each pair must load the cartridge's stream, or one in the slot's bank."""
    for retail, pairs in slot.sites.values():
        for pair in pairs:
            bank, address = (_off(a) for a in pair)
            aimed = _off(_aimed(rom, pair))
            if (
                rom[bank] == rom[address] == 0xA9
                and rom[bank + 2] == 0
                and (aimed == _off(retail) or slot.bank <= aimed < slot.bank + _BANK)
            ):
                continue
            raise RomError(
                f"The {slot.name} screen's code at ${pair[1]:06X} is not the cartridge's "
                f"this patcher knows; screen art left alone"
            )
    for address, cart, ours in slot.pokes:
        at = _off(address)
        if bytes(rom[at : at + len(cart)]) not in (cart, ours):
            raise RomError(
                f"The {slot.name} screen's code at ${address:06X} is not the cartridge's "
                f"this patcher knows; screen art left alone"
            )


# --- encoding ----------------------------------------------------------------


def _with_rows(palette: bytes, colours: Mapping[int, list[int]]) -> bytes:
    """`palette` with colours 1-15 of each given row replaced."""
    out = bytearray(palette)
    for row, values in colours.items():
        for i, colour in enumerate(values[: _ROW - 1]):
            at = 2 * (row * _ROW + 1 + i)
            out[at : at + 2] = colour.to_bytes(2, "little")
    return bytes(out)


@dataclass
class Mode7:
    """A picture as the boot screen's tiles (256 of 64 bytes), plane and colours."""

    tiles: bytes
    plane: bytes
    colours: list[int]
    distinct: int
    used: int


def encode_mode7(image: Image.Image) -> Mode7:
    """`image` as a 128x128 Mode 7 plane with the picture at `_PLANE_AT`."""
    from PIL import Image

    picture = iss.fit(image, (256, 224))
    q = picture.quantize(len(_MODE7_COLOURS), method=Image.Quantize.MEDIANCUT, kmeans=1)
    flat = q.getpalette() or []
    colours = [0] * 256
    for k, entry in enumerate(_MODE7_COLOURS):
        if 3 * k + 2 < len(flat):
            colours[entry] = iss.to_bgr555(tuple(flat[3 * k : 3 * k + 3]))
    pixels = q.tobytes().translate(bytes(_MODE7_COLOURS).ljust(256, b"\0"))
    cells = [
        b"".join(
            pixels[(ty * 8 + y) * 256 + tx * 8 : (ty * 8 + y) * 256 + tx * 8 + 8] for y in range(8)
        )
        for ty in range(28)
        for tx in range(32)
    ]
    users = Counter(cells)
    patterns = list(users)
    weights = [users[p] for p in patterns]
    became = (
        {
            p: into  # ISS's merge, without flips: Mode 7 cannot flip a tile
            for p, (into, _flip) in iss._merge(
                patterns, [0] * len(patterns), weights, {0: colours}, 255, flips=False
            ).items()
        }
        if len(patterns) > 255
        else {}
    )
    index = {p: i for i, p in enumerate(patterns)}
    number: dict[int, int] = {}
    tiles = bytearray(256 * 64)
    plane = bytearray(128 * 128)
    x0, y0 = _PLANE_AT
    for n, cell in enumerate(cells):
        s = index[cell]
        while s in became:
            s = became[s]
        if s not in number:
            number[s] = len(number) + 1
            tiles[64 * number[s] : 64 * number[s] + 64] = patterns[s]
        ty, tx = divmod(n, 32)
        plane[(y0 + ty) * 128 + x0 + tx] = number[s]
    return Mode7(bytes(tiles), bytes(plane), colours, len(patterns), len(number))


def _boot(rom: bytes | bytearray, image: Image.Image) -> tuple[dict[str, bytes], int, int]:
    encoded = encode_mode7(image)
    palette = b"".join(c.to_bytes(2, "little") for c in encoded.colours)
    streams = {
        "tiles": refpack_compress(encoded.tiles),
        "map": refpack_compress(_PLANE_HEAD + encoded.plane),
        "sprites": refpack_compress(bytes(0xDD0)),
        "colours": palette,  # raw: $80:8723 copies colours as they are
    }
    return streams, encoded.distinct, encoded.used


def _title(rom: bytes | bytearray, image: Image.Image) -> tuple[dict[str, bytes], int, int]:
    encoded = iss.encode(image, _TITLE_ENCODING, {0: bytes(32), 256: bytes(32)})
    streams = {
        "colours": refpack_compress(_with_rows(_stream(rom, _TITLE_COLOURS), encoded.colours)),
        "tiles": refpack_compress(encoded.tiles),
        "bg1": refpack_compress(_MAP_HEAD + bytes(_MAP_BYTES)),
        "map": refpack_compress(_MAP_HEAD + encoded.words[:_MAP_BYTES]),
    }
    return streams, encoded.distinct, encoded.used


def _menu(rom: bytes | bytearray, image: Image.Image) -> tuple[dict[str, bytes], int, int]:
    picture = iss.fit(image, (256, 224))
    picture.paste(picture.crop((8, 8, 64, 64)), (192, 8))  # what the game shows anyway
    retail = _stream(rom, _MENU_TILES)
    kept = {i: retail[32 * i : 32 * i + 32] for i in _MENU_KEEP}
    encoded = iss.encode(picture, _MENU_ENCODING, kept)
    streams = {
        "tiles": refpack_compress(encoded.tiles),
        "map": refpack_compress(_MAP_HEAD + encoded.words[:_MAP_BYTES]),
        "colours": refpack_compress(_with_rows(_stream(rom, _MENU_COLOURS), encoded.colours)),
        "goalie_tiles": refpack_compress(bytes(32)),
        "goalie_map": refpack_compress(_MAP_HEAD + bytes(_MAP_BYTES)),
        "ron_colours": refpack_compress(_ron_colours(rom, encoded)),
    }
    return streams, encoded.distinct, encoded.used


def _ron_colours(rom: bytes | bytearray, encoded: iss.Encoded) -> bytes:
    """Ron Barr's colours with the wallpaper blues around him turned into the
    average colour of the encoded picture under the pixels that use each."""
    wall = bytearray(256 * 224)
    _layer(wall, encoded.tiles, _words(encoded.words))
    rgb = [(0, 0, 0)] + [iss.to_rgb(c) for c in encoded.colours[0]]
    ron = bytearray(256 * 224)
    _layer(ron, _stream(rom, _RON_TILES), _words(_stream(rom, _RON_MAP), 6), skip_blank=True)
    sums = {v: [0, 0, 0, 0] for v in _RON_BLUES}
    for at, v in enumerate(ron):
        if v in sums:
            total = sums[v]
            for i, c in enumerate(rgb[wall[at]]):
                total[i] += c
            total[3] += 1
    palette = bytearray(_stream(rom, _RON_COLOURS))
    for v, (r, g, b, n) in sums.items():
        if n:
            colour = iss.to_bgr555((r // n, g // n, b // n))
            palette[2 * v : 2 * v + 2] = colour.to_bytes(2, "little")
    return bytes(palette)


_ENCODERS: Mapping[
    str, Callable[[bytes | bytearray, Image.Image], tuple[dict[str, bytes], int, int]]
] = {"boot": _boot, "title": _title, "menu": _menu}


# --- writing -----------------------------------------------------------------


def write(rom: bytearray, pictures: Mapping[str, Image.Image]) -> list[str]:
    """Draw each slot in `pictures` into `rom` (headerless, in place), growing
    it to 2 MiB; status notes. The caller fixes the header checksum."""
    if pictures and len(rom) < 0x100000:
        raise RomError("Too small for NHL '94 screen art: expected a 1 MiB cartridge")
    for name in pictures:
        _check(rom, SLOTS_BY_NAME[name])
    notes: list[str] = []
    if pictures and len(rom) < _TWO_MIB:
        snes.grow(rom, _TWO_MIB)
        notes.append("ROM grown to 2 MiB")
    for name, image in pictures.items():
        slot = SLOTS_BY_NAME[name]
        streams, distinct, used = _ENCODERS[name](rom, image)
        if used < distinct:
            notes.append(f"{name}: {distinct} tiles merged to {used}")
        rom[slot.bank : slot.bank + _BANK] = b"\xff" * _BANK
        at = slot.bank
        for stream, data in streams.items():
            if at + len(data) > slot.bank + _BANK:
                raise RomError(f"The {name} picture does not fit its 32 KiB bank")
            rom[at : at + len(data)] = data
            for pair in slot.sites[stream][1]:
                _aim(rom, pair, _cpu(at))
            at += len(data)
        for address, _retail, ours in slot.pokes:
            rom[_off(address) : _off(address) + len(ours)] = ours
    return notes


def _header(size: int) -> int:
    """Bytes of copier header before the image, as `NHL94SNESRomReader` counts them."""
    return 512 if size % 0x8000 == 512 else 0


def draw(data: bytearray, pictures: Mapping[str, Image.Image]) -> list[str]:
    """`write` on a whole file's bytes, copier header or not, in place."""
    base = _header(len(data))
    body = data[base:]
    notes = write(body, pictures)
    data[base:] = body
    return notes


# --- rendering ---------------------------------------------------------------


def _layer(
    pixels: bytearray,
    tiles: bytes,
    words: list[int],
    *,
    row_add: int = 0,
    skip_blank: bool = False,
) -> None:
    """Draw a 32x28 map of 4bpp tiles into `pixels` (CGRAM indices, 256x224);
    colour 0 of a tile is transparent."""
    cache: dict[int, bytes] = {}
    for n, word in enumerate(words[: 32 * 28]):
        number = word & 0x3FF
        if skip_blank and not number:
            continue
        if number not in cache:
            cache[number] = iss.tile_indices(tiles[32 * number : 32 * number + 32].ljust(32, b"\0"))
        p = iss._flipped(cache[number], (word >> 14 & 1) | (word >> 15 & 1) << 1)
        row = ((word >> 10 & 7) + row_add) * _ROW
        ty, tx = divmod(n, 32)
        for y in range(8):
            at = (ty * 8 + y) * 256 + tx * 8
            for x, v in enumerate(p[y * 8 : y * 8 + 8]):
                if v:
                    pixels[at + x] = row + v


def _colours_at(cgram: list[int], data: bytes | bytearray, first: int, count: int) -> None:
    for i, colour in enumerate(_words(data)[:count]):
        cgram[first + i] = colour


def render(rom: bytes | bytearray, slot: Slot) -> Image.Image:
    """The slot's picture as the ROM holds it (sprites left out)."""
    from PIL import Image

    pixels = bytearray(256 * 224)
    cgram = [0] * 256
    if slot.name == "boot":
        tiles = _current(rom, slot, "tiles").ljust(256 * 64, b"\0")
        plane = _current(rom, slot, "map")[6:].ljust(128 * 128, b"\0")
        at = _off(_aimed(rom, slot.sites["colours"][1][0]))
        colours = bytes(rom[at : at + 512])
        poked = _off(slot.pokes[1][0])
        if bytes(rom[poked : poked + 3]) == slot.pokes[1][2]:
            _colours_at(cgram, colours, 0, 256)
        else:  # the cartridge's: white, then the rows the screen ends with
            cgram[0] = 0x7FFF
            for row, address in _BOOT_ROWS.items():
                _colours_at(cgram, rom[_off(address) : _off(address) + 32], 16 * row, 16)
        x0, y0 = _PLANE_AT
        for n in range(32 * 28):
            ty, tx = divmod(n, 32)
            number = plane[(y0 + ty) * 128 + x0 + tx]
            for y in range(8):
                at = (ty * 8 + y) * 256 + tx * 8
                pixels[at : at + 8] = tiles[64 * number + 8 * y : 64 * number + 8 * y + 8]
    elif slot.name == "title":
        _colours_at(cgram, _current(rom, slot, "colours"), 0, 256)
        tiles = _current(rom, slot, "tiles")
        _layer(pixels, tiles, _words(_current(rom, slot, "map"), 6))
        _layer(pixels, tiles, _words(_current(rom, slot, "bg1"), 6))
    else:
        _colours_at(cgram, _current(rom, slot, "colours"), 48, 32)
        _colours_at(cgram, _stream(rom, _GOALIE_COLOURS), 32, 16)
        _layer(
            pixels, _current(rom, slot, "tiles"), _words(_current(rom, slot, "map"), 6), row_add=3
        )
        _layer(
            pixels,
            _current(rom, slot, "goalie_tiles"),
            _words(_current(rom, slot, "goalie_map"), 6),
            row_add=2,
            skip_blank=True,
        )
    image = Image.frombytes("P", (256, 224), bytes(pixels))
    image.putpalette([c for colour in cgram for c in iss.to_rgb(colour)])
    return image.convert("RGB")


# --- files -------------------------------------------------------------------


def load_images(given: Any, status: Callable[[str], None]) -> dict[str, Any]:
    """`patch(images=...)`: ISS's, with this game's slots."""
    return iss.load_images(given, status, IMAGE_SLOT_NAMES)


def export(rom_path: Path, out_dir: Path) -> list[Path]:
    """Write each slot's current art as `<out_dir>/<slot>.png`."""
    data = Path(rom_path).read_bytes()
    rom = data[_header(len(data)) :]
    if len(rom) < 0x100000:
        raise RomError(f"Too small to hold NHL '94 screen art: {rom_path}")
    return iss.save_pngs(out_dir, ((slot.name, render(rom, slot)) for slot in SLOTS))
