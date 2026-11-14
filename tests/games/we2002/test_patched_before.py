"""`rom_writer.patched_before`: a disc a roster patch already wrote is told apart (#15)."""

from retro_roster_patcher.games.we2002 import rom_writer


def _image(tmp_path):
    path = tmp_path / "disc.bin"
    path.write_bytes(bytes(rom_writer._OFS_NOMI_SQ2 + 4096))
    return path


def test_an_unpatched_disc_is_not_reported(tmp_path, monkeypatch):
    path = _image(tmp_path)
    monkeypatch.setattr(rom_writer, "_UNPATCHED_NAMES", {rom_writer._names_digest(str(path))})

    assert not rom_writer.patched_before(str(path))


def test_one_team_name_written_is_enough(tmp_path, monkeypatch):
    path = _image(tmp_path)
    monkeypatch.setattr(rom_writer, "_UNPATCHED_NAMES", {rom_writer._names_digest(str(path))})
    with open(path, "r+b") as f:
        f.seek(rom_writer._nat_name_offset(rom_writer._OFS_NOMI_SQ2, 13, rom_writer._LUN_NOMI2))
        f.write(rom_writer._encode_team_name("Liverpool", 8, uppercase=True))

    assert rom_writer.patched_before(str(path))


def test_the_retail_disc_is_not_reported(disc_path):
    assert not rom_writer.patched_before(disc_path)
