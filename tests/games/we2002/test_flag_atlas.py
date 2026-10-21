"""The flag atlas: the game's LZSS, the style geometry, the 2D data file layout
and the disc-level rewrite.

No disc image may enter the repository, so the disc tests build a tiny raw
image by hand: a PVD, a root directory, a `BIN` directory, an executable holding
a file table, and 2D data files whose blocks are compressed with the encoder
under test and decoded back with the decoder under test. The two agree with the
game only because `lz_decompress` was transcribed from the game's own routine;
`test_the_decoder_follows_the_games_token_rules` pins that transcription with
streams written by hand from those rules.
"""

import random
import struct

import pytest

from retro_roster_patcher.core.errors import RomError
from retro_roster_patcher.games.we2002 import flag_atlas as fa
from retro_roster_patcher.games.we2002.flag_atlas import (
    VALID_STYLES,
    LogoImages,
    Vram,
    assign_styles,
    big_rect,
    infer_base,
    lz_compress,
    lz_decompress,
    medium_rect,
    mini_rect,
    parse_data_file,
    patch_flag_atlas,
    rebuild_data_file,
)

# ---------------------------------------------------------------------------
# LZSS
# ---------------------------------------------------------------------------


def test_the_decoder_follows_the_games_token_rules():
    # flag 0b00001111: four controls then four literals.
    #   0xca: literal run of 0xca-0xb8 = 18 bytes
    #   0x90: short copy, length (9-6)=3, distance 1
    #   0x24 0x01: long copy, length (0x24>>2)+3 = 12, distance 1
    #   0xff: end
    stream = bytes([0x0F, 0xCA]) + bytes(range(1, 19)) + bytes([0x90, 0x24, 0x01, 0xFF])
    out, end = lz_decompress(stream)
    assert out == bytes(range(1, 19)) + bytes([18] * 3) + bytes([18] * 12)
    assert end == len(stream)


def _pack(tokens):
    """A stream from `(flag_bit, payload)` tokens, eight to a flag byte."""
    out = bytearray()
    for n in range(0, len(tokens), 8):
        group = tokens[n : n + 8]
        out.append(sum(bit << k for k, (bit, _payload) in enumerate(group)))
        for _bit, payload in group:
            out += payload
    return bytes(out)


def test_a_long_copy_reaches_back_up_to_1023_bytes():
    rng = random.Random(1)
    data = bytes(rng.randrange(256) for _ in range(1023))
    tokens = []
    for pos in range(0, len(data), 70):
        chunk = data[pos : pos + 70]
        tokens.append((1, bytes([len(chunk) + 0xB8]) + chunk))
    # Copy 3 bytes from distance 1023: control (0<<2)|3, low byte 0xff.
    tokens.append((1, bytes([0x03, 0xFF])))
    tokens.append((1, b"\xff"))
    out, _ = lz_decompress(_pack(tokens))
    assert out == data + data[:3]


def test_a_copy_before_the_output_starts_is_an_error():
    with pytest.raises(ValueError):
        lz_decompress(bytes([0x01, 0x90, 0xFF]))


def test_a_stream_without_a_terminator_is_an_error():
    with pytest.raises(ValueError):
        lz_decompress(bytes([0x00, 1, 2, 3]))


@pytest.mark.parametrize("seed", range(6))
def test_compress_round_trips_structured_and_random_data(seed):
    rng = random.Random(seed)
    if seed % 2:
        data = bytes(rng.randrange(256) for _ in range(3000))
    else:
        row = bytes(rng.choice([0x00, 0x11, 0x22, 0x12]) for _ in range(12))
        data = b"".join(row if rng.random() < 0.8 else bytes(12) for _ in range(400))
    comp = lz_compress(data)
    out, end = lz_decompress(comp)
    assert out == data
    assert end == len(comp)


def test_compress_handles_the_empty_and_one_byte_cases():
    for data in (b"", b"\x07", b"\x07\x07", b"\x07" * 100):
        assert lz_decompress(lz_compress(data))[0] == data


def test_compress_finds_the_repeats_in_flag_like_data():
    data = bytes([0x11] * 12 + [0x22] * 12 + [0x44] * 12) * 20
    assert len(lz_compress(data)) < len(data) // 8


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def test_the_gap_in_the_style_numbering_is_excluded():
    assert 43 in VALID_STYLES and 52 in VALID_STYLES
    assert not any(44 <= s < 52 for s in VALID_STYLES)
    assert len(VALID_STYLES) == 53


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (0, (832, 256, 64, 28)),
        (2, (864, 256, 64, 28)),
        (4, (832, 284, 64, 28)),
        (35, (880, 480, 64, 28)),
        (36, (768, 256, 64, 28)),
        (43, (816, 284, 64, 28)),
        (52, (448, 0, 64, 28)),
        (60, (448, 56, 64, 28)),
    ],
)
def test_big_flag_cells(style, expected):
    assert big_rect(style) == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (0, (768, 432, 24, 16)),
        (2, (780, 432, 24, 16)),
        (10, (768, 448, 24, 16)),
        (43, (786, 496, 24, 16)),
        (52, (792, 496, 24, 16)),  # 52 - 8 = 44: row 4, column 4
        (57, (822, 496, 24, 16)),
        (58, (482, 72, 24, 16)),
        (60, (494, 72, 24, 16)),
    ],
)
def test_medium_flag_cells(style, expected):
    assert medium_rect(style) == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        (0, (768, 312, 24, 8)),
        (9, (822, 312, 24, 8)),
        (35, (798, 336, 24, 8)),
        (60, (464, 64, 24, 8)),
    ],
)
def test_mini_flag_cells(style, expected):
    assert mini_rect(style) == expected


def test_no_two_styles_share_a_cell():
    cells = [
        (kind, r)
        for s in VALID_STYLES
        for kind, r in (("b", big_rect(s)), ("m", medium_rect(s)), ("n", mini_rect(s)))
    ]
    assert len(cells) == len(set(cells))


def test_an_absent_style_has_no_rects():
    with pytest.raises(ValueError):
        fa.style_rects(47)


# ---------------------------------------------------------------------------
# The VRAM model
# ---------------------------------------------------------------------------


def test_paint_then_pixels_round_trips_and_touches_nothing_else():
    v = Vram()
    v.data[:] = bytes([0xAB]) * len(v.data)
    rect = (770, 300, 24, 8)
    img = [[(x + y) % 16 for x in range(24)] for y in range(8)]
    v.paint(rect, img)
    assert v.pixels(rect) == img
    # The halfword before and after the painted span are untouched.
    before = ((300 * fa.VRAM_W) + 769) * 2
    after = ((300 * fa.VRAM_W) + 776) * 2
    assert v.data[before : before + 2] == b"\xab\xab"
    assert v.data[after : after + 2] == b"\xab\xab"


def test_paint_refuses_the_wrong_image_size():
    with pytest.raises(ValueError):
        Vram().paint((768, 312, 24, 8), [[0] * 24] * 7)


# ---------------------------------------------------------------------------
# A synthetic 2D data file
# ---------------------------------------------------------------------------

BASE = 0x800F0000


def _record(kind, x, y, w, h, ptr):
    return struct.pack("<HHHHHHI", kind, x, y, w, h, 0, ptr)


def _data_file(blocks, *, raw_blocks=(), extra_list_streams=(), base=BASE):
    """`blocks`: [(x, y, w_hw, h, raw_bytes)] compressed into list 0.

    `raw_blocks` are kind-0 (uncompressed) records; `extra_list_streams` are
    compressed streams placed *between* the lists, pointed at by a third list,
    which is how DATSEL.BIN lays out its late uploads.
    """
    streams = []
    for x, y, w, h, raw in blocks:
        streams.append((fa.KIND_IMAGE, x, y, w, h, lz_compress(raw)))
    for x, y, w, h, raw in raw_blocks:
        streams.append((0x00, x, y, w, h, raw))
    header_words = 3 if extra_list_streams else 2
    header_len = 4 * header_words
    body = bytearray()
    offsets = []
    for _kind, _x, _y, _w, _h, data in streams:
        offsets.append(header_len + len(body))
        body += data + b"\x00" * (-len(data) % 4)
    list0_off = header_len + len(body)
    list0 = b"".join(
        _record(kind, x, y, w, h, base + off)
        for (kind, x, y, w, h, _data), off in zip(streams, offsets, strict=True)
    ) + _record(fa.KIND_END, 0, 0, 0, 0, 0)
    body += list0
    list1_off = header_len + len(body)
    body += _record(fa.KIND_END, 0, 0, 0, 0, 0)  # an empty list
    header = [base + list0_off, base + list1_off]
    if extra_list_streams:
        late = []
        for x, y, w, h, raw in extra_list_streams:
            off = header_len + len(body)
            data = lz_compress(raw)
            body += data + b"\x00" * (-len(data) % 4)
            late.append((x, y, w, h, off))
        list2_off = header_len + len(body)
        body += b"".join(
            _record(fa.KIND_IMAGE, x, y, w, h, base + off) for x, y, w, h, off in late
        ) + _record(fa.KIND_END, 0, 0, 0, 0, 0)
        header.append(base + list2_off)
    return struct.pack(f"<{len(header)}I", *header) + bytes(body)


def _block(seed, w_hw, h):
    rng = random.Random(seed)
    return bytes(rng.choice([0x11, 0x22, 0x33, 0x00]) for _ in range(w_hw * h * 2))


def test_parse_reads_the_header_and_every_list():
    data = _data_file(
        [(768, 256, 32, 128, _block(1, 32, 128))],
        extra_list_streams=[(544, 384, 32, 128, _block(2, 32, 128))],
    )
    parsed = parse_data_file(data, BASE)
    assert len(parsed.header) == 3
    assert [len(lst) for lst in parsed.lists] == [1, 0, 1]
    rec = parsed.lists[0][0]
    assert (rec.kind, rec.x, rec.y, rec.w, rec.h) == (fa.KIND_IMAGE, 768, 256, 32, 128)
    assert rec.ptr == BASE + 12


def test_the_base_is_inferred_from_the_pointers_alone():
    data = _data_file([(768, 256, 32, 128, _block(1, 32, 128))], base=0x800C8000)
    assert infer_base(data) == 0x800C8000


def test_a_file_that_is_not_a_data_file_is_refused():
    with pytest.raises(RomError):
        infer_base(b"\x00" * 64)
    with pytest.raises(RomError):
        parse_data_file(b"\x00" * 64, BASE)


def test_rebuild_moves_later_items_and_fixes_every_pointer():
    blocks = [(768, 256, 32, 128, _block(1, 32, 128)), (800, 256, 32, 128, _block(2, 32, 128))]
    late = [(544, 384, 32, 128, _block(3, 32, 128))]
    data = _data_file(blocks, extra_list_streams=late)
    parsed = parse_data_file(data, BASE)
    first = parsed.lists[0][0]
    rng = random.Random(9)
    bigger = lz_compress(bytes(rng.randrange(256) for _ in range(8192)))
    assert len(bigger) > len(lz_compress(blocks[0][4]))
    rebuilt = rebuild_data_file(parsed, {first.ptr - BASE: bigger})
    again = parse_data_file(rebuilt, BASE)
    # Every stream still decodes to what its record promises.
    for before, after in zip(parsed.records(), again.records(), strict=True):
        assert (after.x, after.y, after.w, after.h) == (before.x, before.y, before.w, before.h)
    decoded = [lz_decompress(rebuilt, r.ptr - BASE)[0] for r in again.records()]
    assert decoded[0] == lz_decompress(bigger)[0]
    assert decoded[1] == blocks[1][4]
    assert decoded[2] == late[0][4]
    assert len(rebuilt) == len(data) + (len(bigger) + (-len(bigger) % 4)) - (
        len(lz_compress(blocks[0][4])) + (-len(lz_compress(blocks[0][4])) % 4)
    )


def test_rebuild_with_no_replacements_is_the_identity():
    data = _data_file([(768, 256, 32, 128, _block(1, 32, 128))])
    assert rebuild_data_file(parse_data_file(data, BASE), {}) == data


def test_rebuild_refuses_an_offset_that_is_not_a_stream():
    data = _data_file([(768, 256, 32, 128, _block(1, 32, 128))])
    with pytest.raises(ValueError):
        rebuild_data_file(parse_data_file(data, BASE), {13: b"x"})


# ---------------------------------------------------------------------------
# Style assignment
# ---------------------------------------------------------------------------


def _forma(assignments):
    forma = bytearray(95)
    for entry, style in assignments.items():
        forma[entry] = style
    return bytes(forma)


def test_styles_nobody_else_uses_come_first():
    # Every zeroed entry uses style 0, so 0 is taken; 7 belongs to untouched
    # entry 40; 3 only to patched entries; 1 and 2 to nobody.
    forma = _forma({0: 3, 63: 3, 1: 7, 40: 7})
    styles = assign_styles(forma, patched={0, 1, 63, 64}, wanted=[0, 1])
    assert styles == {0: 1, 1: 2}
    many = assign_styles(forma, patched={0, 1, 63, 64}, wanted=range(5))
    assert 3 in many.values() and 0 not in many.values() and 7 not in many.values()


def test_every_wanted_slot_gets_a_distinct_valid_style():
    forma = bytes(range(0, 95)) if False else bytes(VALID_STYLES[i % 53] for i in range(95))
    wanted = list(range(0, 53))
    patched = set(wanted) | {63 + s for s in wanted if s < 32}
    styles = assign_styles(forma, patched, wanted)
    assert len(styles) == 53
    assert len(set(styles.values())) == 53
    assert set(styles.values()) <= set(VALID_STYLES)


def test_more_slots_than_styles_leaves_the_rest_unassigned():
    styles = assign_styles(bytes(95), patched=set(range(95)), wanted=range(60))
    assert len(styles) == 53
    assert 59 not in styles


def test_shared_styles_are_taken_by_fewest_untouched_users():
    forma = _forma({50: 1, 51: 1, 52: 1, 53: 2})  # style 1: 3 untouched users, style 2: one
    styles = assign_styles(forma, patched={0}, wanted=range(53))
    taken = list(styles.values())
    assert taken.index(2) < taken.index(1)


# ---------------------------------------------------------------------------
# The disc
# ---------------------------------------------------------------------------

SECTOR = 2352
USER = 2048
PVD_LBA = 16
ROOT_LBA = 20
BIN_LBA = 22
EXE_LBA = 24
EXE_SIZE = 4096
EXE_BASE = 0x80010000


def _dir_record(name, lba, size, *, is_dir=False):
    length = 33 + len(name)
    length += length % 2
    rec = bytearray(length)
    rec[0] = length
    struct.pack_into("<I", rec, 2, lba)
    struct.pack_into(">I", rec, 6, lba)
    struct.pack_into("<I", rec, 10, size)
    struct.pack_into(">I", rec, 14, size)
    rec[25] = 2 if is_dir else 0
    rec[32] = len(name)
    rec[33 : 33 + len(name)] = name
    return bytes(rec)


def _sector(user, lba):
    out = bytearray(SECTOR)
    out[0:12] = b"\x00" + b"\xff" * 10 + b"\x00"
    out[12:15] = bytes([0, 2, 0])
    out[15] = 2
    out[16:24] = bytes([0, 0, 8, 0, 0, 0, 8, 0])
    out[24 : 24 + len(user)] = user
    struct.pack_into("<I", out, 2072, fa._edc(bytes(out[16:2072])))
    return bytes(out)


def _atlas_files(logo_styles=()):
    """DATSEL-like contents: the four region-B blocks and eight region-A ones."""
    blocks = []
    for x, y in [(448, 0), (480, 0), (448, 128), (480, 128)]:
        blocks.append((x, y, 32, 128, _block(x * 7 + y, 32, 128)))
    for x in (768, 800, 832, 864):
        for y in (256, 384):
            blocks.append((x, y, 32, 128, _block(x * 3 + y, 32, 128)))
    return blocks


def _build_disc(tmp_path, files, table):
    """`files`: name -> (lba, data) under /BIN; `table`: [(lba, addr, sectors)] in the EXE."""
    path = tmp_path / "disc.bin"
    exe = bytearray(EXE_SIZE)
    for n, (lba, addr, sectors) in enumerate(table):
        struct.pack_into("<III", exe, 0x800 + 12 * n, lba, addr, sectors)
    root = _dir_record(b"\x00", ROOT_LBA, USER, is_dir=True) + _dir_record(
        b"\x01", ROOT_LBA, USER, is_dir=True
    )
    root += _dir_record(b"BIN", BIN_LBA, USER, is_dir=True)
    root += _dir_record(b"SLPM_870.56;1", EXE_LBA, EXE_SIZE)
    bin_dir = _dir_record(b"\x00", BIN_LBA, USER, is_dir=True) + _dir_record(
        b"\x01", ROOT_LBA, USER, is_dir=True
    )
    for name, (lba, data) in sorted(files.items(), key=lambda kv: kv[1][0]):
        bin_dir += _dir_record(name.encode() + b";1", lba, len(data))
    pvd = bytearray(USER)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[156:190] = _dir_record(b"\x00", ROOT_LBA, USER, is_dir=True)
    last = (
        max([lba + (len(data) + USER - 1) // USER for lba, data in files.values()] + [EXE_LBA])
        + 200
    )
    with open(path, "wb") as f:
        f.truncate(last * SECTOR)
        for lba, user in ((PVD_LBA, bytes(pvd)), (ROOT_LBA, root), (BIN_LBA, bin_dir)):
            f.seek(lba * SECTOR)
            f.write(_sector(user, lba))
        f.seek(EXE_LBA * SECTOR)
        for n in range(EXE_SIZE // USER):
            f.write(_sector(bytes(exe[n * USER : (n + 1) * USER]), EXE_LBA + n))
        for lba, data in files.values():
            for n in range((len(data) + USER - 1) // USER):
                f.seek((lba + n) * SECTOR)
                f.write(_sector(data[n * USER : (n + 1) * USER], lba + n))
    return path


def _read_file(path, lba, size):
    with open(path, "rb") as f:
        return fa._read_user(f, lba, size)


def _logo(fill, *, noisy=False):
    """A logo whose cells are a periodic pattern, or (noisy) incompressible."""
    rng = random.Random(fill)

    def img(w, h):
        if noisy:
            return [[rng.randrange(1, 16) for _ in range(w)] for _ in range(h)]
        return [[fill if (x + y) % 3 else 0 for x in range(w)] for y in range(h)]

    return LogoImages(
        palette=[(255, 0, 0)] * 15, big=img(64, 28), medium=img(24, 16), mini=img(24, 8)
    )


def test_logos_land_in_every_copy_and_a_file_grows_into_its_free_sectors(tmp_path):
    datsel = _data_file(_atlas_files())
    datsel3 = _data_file(_atlas_files()[4:])  # the region-A blocks only
    # Style 36's cells: big in (768,256), mini in (800,256), medium in (800,384).
    gdc = _data_file(_atlas_files()[4:8], base=0x800C8000)
    files = {"DATSEL.BIN": (100, datsel), "DATSEL3.BIN": (300, datsel3), "GDC_AD.BIN": (400, gdc)}
    table = [(100, BASE, (len(datsel) + USER - 1) // USER), (999, 0x800C8000, 50)]
    path = _build_disc(tmp_path, files, table)

    # Random blocks compress badly, so repainting them with a logo makes them
    # bigger: style 36's three cells touch region-A blocks only.
    report = patch_flag_atlas(str(path), {36: _logo(5)})
    assert sorted(report.files) == ["DATSEL.BIN", "DATSEL3.BIN", "GDC_AD.BIN"]
    assert not report.skipped

    with open(path, "rb") as f:
        disc = fa._disc_files(f)
    for name in files:
        entry = disc[name]
        data = _read_file(path, entry.lba, entry.size)
        parsed = parse_data_file(data, infer_base(data))
        vram = Vram()
        for rec in fa._atlas_blocks(parsed):
            vram.put_block(
                rec.x, rec.y, rec.w, rec.h, lz_decompress(data, rec.ptr - parsed.base)[0]
            )
        assert vram.pixels(big_rect(36)) == _logo(5).big, name
        assert vram.pixels(medium_rect(36)) == _logo(5).medium, name
        assert vram.pixels(mini_rect(36)) == _logo(5).mini, name
    # An untouched block is byte-identical to what it was.
    before = parse_data_file(datsel, BASE).lists[0][0]
    after = parse_data_file(_read_file(path, 100, disc["DATSEL.BIN"].size), BASE).lists[0][0]
    assert (
        lz_decompress(datsel, before.ptr - BASE)[0]
        == lz_decompress(_read_file(path, 100, disc["DATSEL.BIN"].size), after.ptr - BASE)[0]
    )


def test_a_grown_file_raises_its_sector_count_in_the_executable(tmp_path):
    datsel = _data_file(_atlas_files())
    old_sectors = (len(datsel) + USER - 1) // USER
    path = _build_disc(tmp_path, {"DATSEL.BIN": (100, datsel)}, [(100, BASE, old_sectors)])
    logos = {s: _logo(s, noisy=True) for s in (0, 1, 2, 3, 4, 5, 6, 7, 36, 37)}
    report = patch_flag_atlas(str(path), logos)
    assert report.grown == ["DATSEL.BIN"]
    with open(path, "rb") as f:
        disc = fa._disc_files(f)
    new_size = disc["DATSEL.BIN"].size
    assert new_size > len(datsel)
    exe = _read_file(path, EXE_LBA, EXE_SIZE)
    lba, addr, sectors = struct.unpack_from("<III", exe, 0x800)
    assert (lba, addr) == (100, BASE)
    assert sectors == (new_size + USER - 1) // USER > old_sectors


def test_a_file_the_game_reads_by_an_unknown_count_is_not_grown(tmp_path):
    datsel = _data_file(_atlas_files())
    edtr = _data_file(_atlas_files()[4:6])
    path = _build_disc(
        tmp_path,
        {"DATSEL.BIN": (100, datsel), "EDTR_2D.BIN": (300, edtr)},
        [(100, BASE, (len(datsel) + USER - 1) // USER)],
    )
    report = patch_flag_atlas(str(path), {s: _logo(s, noisy=True) for s in (36, 37, 38, 39, 40)})
    assert "DATSEL.BIN" in report.files
    assert "EDTR_2D.BIN" not in report.files
    assert any(line.startswith("EDTR_2D.BIN: needs") for line in report.skipped)
    assert _read_file(path, 300, len(edtr)) == edtr


def test_a_stadium_file_grows_up_to_the_stadium_slots_count(tmp_path):
    datsel = _data_file(_atlas_files())
    gdc = _data_file(_atlas_files()[4:6], base=0x800C8000)
    path = _build_disc(
        tmp_path,
        {"DATSEL.BIN": (100, datsel), "GDC_AD.BIN": (400, gdc)},
        [(100, BASE, 200), (999, 0x800C8000, 50)],
    )
    report = patch_flag_atlas(str(path), {s: _logo(s, noisy=True) for s in (36, 37, 38, 39, 40)})
    assert "GDC_AD.BIN" in report.files and "GDC_AD.BIN" in report.grown


def test_growth_stops_at_the_next_file(tmp_path):
    datsel = _data_file(_atlas_files())
    sectors = (len(datsel) + USER - 1) // USER
    files = {"DATSEL.BIN": (100, datsel), "DATSEL2.BIN": (100 + sectors, datsel)}
    path = _build_disc(tmp_path, files, [(100, BASE, sectors), (100 + sectors, BASE, sectors)])
    report = patch_flag_atlas(str(path), {s: _logo(s, noisy=True) for s in (0, 1, 2, 3, 36, 37)})
    assert "DATSEL.BIN" not in report.files
    assert any("DATSEL.BIN: needs" in line for line in report.skipped)


def test_a_block_whose_content_differs_from_the_atlas_is_left_alone(tmp_path):
    datsel = _data_file(_atlas_files())
    other = list(_atlas_files()[4:6])
    other[0] = (768, 256, 32, 128, _block(999, 32, 128))  # same place, different art
    gdc = _data_file(other, base=0x800C8000)
    path = _build_disc(
        tmp_path, {"DATSEL.BIN": (100, datsel), "GDC_AD.BIN": (400, gdc)}, [(100, BASE, 200)]
    )
    patch_flag_atlas(str(path), {36: _logo(3)})
    assert _read_file(path, 400, len(gdc)) == gdc


def test_an_image_without_the_atlas_files_is_refused(tmp_path):
    path = _build_disc(tmp_path, {}, [])
    with pytest.raises(RomError):
        patch_flag_atlas(str(path), {36: _logo(3)})


def test_a_file_without_a_volume_descriptor_is_refused(tmp_path):
    path = tmp_path / "blank.bin"
    path.write_bytes(bytes(SECTOR * 40))
    with pytest.raises(RomError):
        patch_flag_atlas(str(path), {36: _logo(3)})


def test_nothing_is_written_when_no_block_changes(tmp_path):
    datsel = _data_file(_atlas_files())
    path = _build_disc(tmp_path, {"DATSEL.BIN": (100, datsel)}, [(100, BASE, 200)])
    before = path.read_bytes()
    # A logo identical to the current content of its cells changes nothing.
    data = _read_file(path, 100, len(datsel))
    parsed = parse_data_file(data, BASE)
    vram = Vram()
    for rec in fa._atlas_blocks(parsed):
        vram.put_block(rec.x, rec.y, rec.w, rec.h, lz_decompress(data, rec.ptr - BASE)[0])
    same = LogoImages(
        palette=[(0, 0, 0)] * 15,
        big=vram.pixels(big_rect(36)),
        medium=vram.pixels(medium_rect(36)),
        mini=vram.pixels(mini_rect(36)),
    )
    report = patch_flag_atlas(str(path), {36: same})
    assert report.files == []
    assert path.read_bytes() == before
