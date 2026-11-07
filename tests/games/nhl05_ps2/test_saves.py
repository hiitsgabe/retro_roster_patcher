"""`nhl05_ps2.saves`: a patched disc's own memory card folder, so the game never
auto-loads a roster saved with another disc over the patched one."""

from retro_roster_patcher.formats import iso9660
from retro_roster_patcher.games.nhl05_ps2 import saves
from tests.fixtures import synthetic_nhl05_iso as fixture
from tests.games.nhl05_ps2.test_patcher import build, league

# The retail executable's bytes around the name: NUL-terminated, the next
# string (the European folder) right after it.
RETAIL_ELF = b"\x7fELF" + bytes(60) + b"\x00\x00BASLUS-21001NHL\x00BESLES-52673NHL\x00" + bytes(64)


def folder(elf: bytes) -> bytes:
    start = elf.index(b"BASLUS-21001")
    return elf[start : start + 15]


def test_the_folder_suffix_comes_from_the_roster():
    new = saves.own_folder(RETAIL_ELF, b"one roster")
    assert len(new) == len(RETAIL_ELF)
    assert folder(new) == b"BASLUS-21001" + saves.suffix(b"one roster")
    assert folder(new) != b"BASLUS-21001NHL"
    # Only the three suffix bytes change; the European name stays.
    assert new.replace(folder(new), b"BASLUS-21001NHL") == RETAIL_ELF


def test_the_same_roster_keeps_its_folder_and_another_gets_a_new_one():
    once = saves.own_folder(RETAIL_ELF, b"2026 roster")
    assert saves.own_folder(once, b"2026 roster") == once
    assert folder(saves.own_folder(once, b"2027 roster")) != folder(once)


def test_an_executable_without_exactly_one_folder_is_left_alone():
    assert saves.own_folder(b"no folder here", b"x") is None
    twice = RETAIL_ELF + b"BASLUS-21001NHL\x00"
    assert saves.own_folder(twice, b"x") is None


def test_the_suffix_is_never_the_retail_one():
    for i in range(20000):
        assert saves.suffix(str(i).encode()) != b"NHL"


def _elf_of(path) -> bytes:
    with open(path, "rb") as f:
        root = iso9660.read_root(f)
        assert root is not None
        entry = iso9660.find_entry(f, root, saves.ELF_PATH)
        assert entry is not None
        f.seek(entry.extent.offset)
        return f.read(entry.extent.size)


def test_patch_renames_the_disc_save_folder(tmp_path):
    source = tmp_path / "game.iso"
    fixture.write_iso(source, fixture.DiscSpec(elf=RETAIL_ELF))
    patcher = build(tmp_path)
    out = tmp_path / "patched.iso"
    patcher.patch(rom_path=source, output_path=out, rosters=patcher.map_rosters(league()))
    elf = _elf_of(out)
    assert folder(elf) != b"BASLUS-21001NHL"
    assert elf.replace(folder(elf), b"BASLUS-21001NHL") == RETAIL_ELF
    assert folder(_elf_of(source)) == b"BASLUS-21001NHL"
