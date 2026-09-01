import atexit
import pathlib
import struct
import tempfile

import pytest

from retro_roster_patcher.core.assets import MissingAssetError, package_bytes, package_path
from retro_roster_patcher.games.we2002.translations.we2002 import (
    LANGUAGES,
    ensure_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.english_ppf import (
    generate_english_ppf,
)

WE2002_ASSETS = "retro_roster_patcher.games.we2002.assets"


def _synthetic_community_ppf() -> bytes:
    """A minimal but well-formed PPF2 carrying one translatable text record.

    `menu_records._parse_ppf2` raises `ValueError` unless the first four bytes
    are `PPF2`, and it starts reading records at offset 1084, which is the
    5-byte magic, the encoding byte, the 50-byte description, the 4-byte size
    and the 1024-byte validation block. A record is a little-endian `uint32`
    offset, a one-byte length and that many bytes of data.
    """
    header = b"PPF20" + b"\x00" + b"X" * 50 + struct.pack("<I", 0) + b"\x00" * 1024
    return header + struct.pack("<I", 0x1000) + bytes([5]) + b"Shoot"


def test_the_packaged_english_ppf_loads_through_importlib_resources():
    data = package_bytes(WE2002_ASSETS, "we2002_english.ppf")
    assert data[:5] == b"PPF10"
    assert len(data) == 1912


def test_the_packaged_ppf_matches_what_the_generator_produces():
    """Guards against the committed asset drifting from its source."""
    assert package_bytes(WE2002_ASSETS, "we2002_english.ppf") == generate_english_ppf()


def test_package_path_hands_back_a_fresh_copy_each_call(tmp_path, monkeypatch):
    """Pins the asymmetry the dispatcher docstring describes: English is not cached."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first = package_path(WE2002_ASSETS, "we2002_english.ppf")
    second = package_path(WE2002_ASSETS, "we2002_english.ppf")

    assert (first == second) is False
    expected = package_bytes(WE2002_ASSETS, "we2002_english.ppf")
    assert pathlib.Path(first).read_bytes() == expected
    assert pathlib.Path(second).read_bytes() == expected


def test_the_temporary_copy_is_registered_for_deletion_at_interpreter_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *args: registered.append((fn, args)))

    path = package_path(WE2002_ASSETS, "we2002_english.ppf")
    assert pathlib.Path(path).exists() is True
    assert [args for _, args in registered] == [(path,)]

    hook, args = registered[0]
    hook(*args)
    assert pathlib.Path(path).exists() is False
    hook(*args)  # a second run at exit must not raise on the now-absent file
    assert list(tmp_path.iterdir()) == []


def test_a_missing_asset_raises_a_named_error():
    with pytest.raises(MissingAssetError, match="nope.ppf"):
        package_bytes(WE2002_ASSETS, "nope.ppf")


def test_the_community_ppf_is_not_shipped():
    """Third-party translation; excluded deliberately, not by accident."""
    with pytest.raises(MissingAssetError):
        package_bytes(WE2002_ASSETS, "w202-english.ppf")


def test_english_is_served_from_the_package_without_writing_anything(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), "en")

    assert cache_dir.exists() is False, "the packaged asset needs no cache write"
    assert path.endswith("we2002_english.ppf") is True


@pytest.mark.parametrize("lang", ["es", "fr", "pt"])
def test_other_languages_are_generated_into_the_cache_directory(tmp_path, lang):
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), lang)

    assert str(cache_dir) in path
    assert (cache_dir / f"we2002_{LANGUAGES[lang].lower()}.ppf").exists() is True


def test_generation_is_cached_between_calls(tmp_path):
    cache_dir = tmp_path / "cache"
    first = ensure_ppf(str(cache_dir), "fr")
    stamp = (tmp_path / "cache" / "we2002_french.ppf").stat().st_mtime_ns
    second = ensure_ppf(str(cache_dir), "fr")

    assert first == second
    assert (tmp_path / "cache" / "we2002_french.ppf").stat().st_mtime_ns == stamp


def test_a_community_assets_dir_is_read_but_never_written(tmp_path):
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    before = {p.name: p.read_bytes() for p in assets_dir.iterdir()}

    path = ensure_ppf(str(cache_dir), "fr", assets_dir=str(assets_dir))

    after = {p.name: p.read_bytes() for p in assets_dir.iterdir()}
    assert after == before, "assets_dir is read-only"
    # The community record was merged, so the cached PPF is larger than the
    # 1912-byte team-names-only patch.
    assert len(pathlib.Path(path).read_bytes()) == 1922


def test_an_absent_assets_dir_is_not_an_error(tmp_path):
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), "pt", assets_dir=str(tmp_path / "does-not-exist"))

    assert path == str(cache_dir / "we2002_portuguese.ppf")
    assert (cache_dir / "we2002_portuguese.ppf").exists() is True
