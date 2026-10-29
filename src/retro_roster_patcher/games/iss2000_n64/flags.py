"""Team flags: the 48x32 picture each team waves on team select.

Table 0x87C80 (resident): per team id three halfwords, the flag texture's
resource id, its palette block's resource id and the palette's index in that
block. Resource ids resolve to fixed places in five asset bundles (found by
reading the game's texture cache at team select):

    texture 0x49E-0x4AD  asset 670 from 0x200, 768 bytes each (CI4 48x32)
            0x4AE-0x4BE  asset 671 from 0
            0x4BF-0x4CF  asset 672 from 0
            0x4D0-0x4E0  asset 673 from 0
            0x4E1-0x4F0  asset 674 from 0
    palette 0x48E-0x495  asset 669 from 0x1300, 512 bytes a block: 16 palettes
                         of 16 RGBA5551 colours

A texture is stored bottom row first. Plain stripe flags share three generic
textures (0x49E-0x4A0) recoloured by each team's palette, so a crest needs a
texture of its own: `allocate` gives each crested team one, taking first the
team's own, then the unused 0x4BD, then textures of teams left alone, which
fall back to a generic stripe flag in their own palette.
"""

from __future__ import annotations

import struct
from collections import Counter
from io import BytesIO

from . import rom as R

TABLE = 0x87C80
GENERIC = 0x49E
W, H = 48, 32
TEX_SIZE = W * H // 2

#: Asset 674 is laid out for 16 textures plus 256 bytes, but the game uses the
#: room of the last one (id 0x4F0, which no team on the cartridge uses) for
#: something else: a crest written there draws with its top rows replaced by
#: noise on team select, and in the match the players' heads go missing or
#: turn to noise (checked in Mupen64Plus-Next, Oct 29 2026: every patch whose
#: crest landed there broke, every other placement drew both right). So that
#: slot is not a flag texture, and the bundle holds 15.
_BUNDLES = (
    (670, 0x49E, 0x200, 16),
    (671, 0x4AE, 0, 17),
    (672, 0x4BF, 0, 17),
    (673, 0x4D0, 0, 17),
    (674, 0x4E1, 0, 15),
)
PALETTE_ASSET = 669
PALETTE_BASE = 0x1300
PALETTE_BLOCK0 = 0x48E


def entry(image: bytes | bytearray, team: int) -> tuple[int, int, int]:
    return struct.unpack_from(">HHH", image, TABLE + team * 6)


def set_entry(image: bytearray, team: int, tex: int, block: int, index: int) -> None:
    struct.pack_into(">HHH", image, TABLE + team * 6, tex, block, index)


def texture_place(tex: int) -> tuple[int, int]:
    """(asset index, byte offset) of texture resource `tex`."""
    for asset, first, base, count in _BUNDLES:
        if first <= tex < first + count:
            return asset, base + (tex - first) * TEX_SIZE
    raise ValueError(f"not a flag texture: {tex:#x}")


def palette_place(block: int, index: int) -> int:
    return PALETTE_BASE + (block - PALETTE_BLOCK0) * 0x200 + index * 32


def allocate(image: bytes | bytearray, crested: list[int], patched: set[int]) -> dict[int, int]:
    """A texture of its own for every team in `crested`, {team: texture id}.

    `patched` are the teams this patch rewrites; their textures are free to
    reuse. Teams outside it whose texture is taken get the generic stripe one.
    """
    teams = range(R.TEAMS)
    tex = {t: entry(image, t)[0] for t in teams}
    use = Counter(tex.values())
    out: dict[int, int] = {}
    taken: set[int] = set()
    for t in crested:
        if use[tex[t]] == 1:
            out[t] = tex[t]
            taken.add(tex[t])
    all_ids = {first + k for _, first, _, n in _BUNDLES for k in range(n)}
    spare = sorted(all_ids - set(tex.values()))
    spare += sorted(
        {tex[t] for t in teams if t in patched and t not in crested and use[tex[t]] == 1}
    )
    spare += sorted(
        {tex[t] for t in reversed(teams) if t not in patched and use[tex[t]] == 1},
        key=lambda x: -x,
    )
    spare = [s for s in spare if s not in taken and s != GENERIC]
    for t in crested:
        if t not in out and spare:
            out[t] = spare.pop(0)
    return out


# --- drawing ---------------------------------------------------------------------


def _rgba5551(r: int, g: int, b: int) -> int:
    return (r >> 3) << 11 | (g >> 3) << 6 | (b >> 3) << 1 | 1


def draw(logo_png: bytes | None, background: tuple[int, int, int]) -> tuple[bytes, bytes]:
    """A crest on a `background` field as (768-byte CI4 texture, 32-byte palette)."""
    from PIL import Image

    field = Image.new("RGB", (W, H), background)
    if logo_png:
        logo = Image.open(BytesIO(logo_png)).convert("RGBA")
        bbox = logo.getbbox()
        if bbox:
            logo = logo.crop(bbox)
        scale = min((W - 6) / logo.width, (H - 4) / logo.height)
        size = (max(1, round(logo.width * scale)), max(1, round(logo.height * scale)))
        logo = logo.resize(size, Image.Resampling.LANCZOS)
        field.paste(logo, ((W - size[0]) // 2, (H - size[1]) // 2), logo)
    q = field.quantize(colors=16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    flat = (q.getpalette() or [])[: 16 * 3]
    flat += [0] * (48 - len(flat))
    palette = b"".join(struct.pack(">H", _rgba5551(*flat[i * 3 : i * 3 + 3])) for i in range(16))
    px = list(q.transpose(Image.Transpose.FLIP_TOP_BOTTOM).getdata())
    tex = bytes((px[i] & 15) << 4 | (px[i + 1] & 15) for i in range(0, W * H, 2))
    return tex, palette


def write(
    image: bytearray,
    space: R.AssetSpace,
    crests: dict[int, tuple[bytes, bytes]],
    patched: set[int],
) -> None:
    """Give each team in `crests` ({team: (texture, palette)}) its crest."""
    assets = R.assets(image)
    owner = allocate(image, sorted(crests), patched)
    bundles: dict[int, bytearray] = {}

    def bundle(k: int) -> bytearray:
        if k not in bundles:
            bundles[k] = bytearray(R.read_asset(image, assets[k]))
        return bundles[k]

    # Teams left alone whose texture a crest takes wave the generic stripes.
    taken = set(owner.values())
    for t in range(R.TEAMS):
        tex, block, idx = entry(image, t)
        if t not in owner and tex in taken:
            set_entry(image, t, GENERIC, block, idx)
    used_pal: Counter[tuple[int, int]] = Counter(entry(image, t)[1:] for t in range(R.TEAMS))
    for team, (tex_bytes, pal_bytes) in crests.items():
        if team not in owner:
            continue
        tex, block, idx = entry(image, team)
        tex = owner[team]
        asset, off = texture_place(tex)
        bundle(asset)[off : off + TEX_SIZE] = tex_bytes
        if used_pal[(block, idx)] > 1:
            used_pal[(block, idx)] -= 1
            block, idx = _free_palette(used_pal)
            used_pal[(block, idx)] += 1
        p = palette_place(block, idx)
        bundle(PALETTE_ASSET)[p : p + 32] = pal_bytes
        set_entry(image, team, tex, block, idx)
    for k, data in bundles.items():
        space.put(assets[k], bytes(data))


def _free_palette(used: Counter[tuple[int, int]]) -> tuple[int, int]:
    for block in range(PALETTE_BLOCK0, PALETTE_BLOCK0 + 8):
        for idx in range(16):
            if not used[(block, idx)]:
                return block, idx
    raise ValueError("no free flag palette")
