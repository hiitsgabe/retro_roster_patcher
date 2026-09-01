import atexit
import os
import pathlib
import struct
import tempfile

import pytest

from retro_roster_patcher.core import assets
from retro_roster_patcher.core.assets import MissingAssetError, package_bytes, package_path
from retro_roster_patcher.games.we2002.translations.we2002 import (
    LANGUAGES,
    ensure_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.english_ppf import (
    generate_english_ppf,
)

WE2002_ASSETS = "retro_roster_patcher.games.we2002.assets"

# What `_translate_record` turns the synthetic community record into, per
# language. Each is padded back to the original five bytes, so the merged PPF is
# the same size whichever language ran — which is why the size assertions below
# are not left to carry the claim alone.
TRANSLATED = {"es": b"TIRO ", "fr": b"TIR  ", "pt": b"CHUTE"}


@pytest.fixture(autouse=True)
def isolate_materialised_assets(monkeypatch):
    """Give each test its own `package_path` memo.

    The real memo is process-wide by design, so without this a path cached under
    one test's `tmp_path` would be handed to the next test after pytest had
    deleted the directory out from under it.
    """
    monkeypatch.setattr(assets, "_materialised", {})


def _synthetic_community_ppf() -> bytes:
    """A minimal but well-formed PPF2 carrying one translatable text record.

    `menu_records._parse_ppf2` raises `ValueError` unless the first four bytes
    are `PPF2`, and it starts reading records at offset 1084, which is the
    5-byte magic, the encoding byte, the 50-byte description, the 4-byte size
    and the 1024-byte validation block. A record is a little-endian `uint32`
    offset, a one-byte length and that many bytes of data.

    The payload is upper-case because `_TRANSLATIONS` is keyed on upper-case
    text: `b"Shoot"` comes back unchanged, `b"SHOOT"` comes back translated.
    """
    header = b"PPF20" + b"\x00" + b"X" * 50 + struct.pack("<I", 0) + b"\x00" * 1024
    return header + struct.pack("<I", 0x1000) + bytes([5]) + b"SHOOT"


def _refuse_to_write(data):
    """Stand-in for a temp-file write that fails after the file already exists."""
    raise OSError("no space left on device")


def test_the_packaged_english_ppf_loads_through_importlib_resources():
    data = package_bytes(WE2002_ASSETS, "we2002_english.ppf")
    assert data[:5] == b"PPF10"
    assert len(data) == 1912


def test_the_packaged_ppf_matches_what_the_generator_produces():
    """Guards against the committed asset drifting from its source."""
    assert package_bytes(WE2002_ASSETS, "we2002_english.ppf") == generate_english_ppf()


def test_package_path_memoises_one_temporary_copy_per_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *args: registered.append((fn, args)))

    first = package_path(WE2002_ASSETS, "we2002_english.ppf")
    second = package_path(WE2002_ASSETS, "we2002_english.ppf")
    third = package_path(WE2002_ASSETS, "we2002_english.ppf")

    assert second == first
    assert third == first
    expected = package_bytes(WE2002_ASSETS, "we2002_english.ppf")
    assert pathlib.Path(first).read_bytes() == expected
    assert [args for _, args in registered] == [(first,)]
    assert [p.name for p in tmp_path.iterdir()] == [pathlib.Path(first).name]


def test_a_temporary_copy_deleted_from_underneath_is_materialised_again(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first = package_path(WE2002_ASSETS, "we2002_english.ppf")
    pathlib.Path(first).unlink()

    second = package_path(WE2002_ASSETS, "we2002_english.ppf")

    assert pathlib.Path(second).exists() is True
    assert pathlib.Path(second).read_bytes() == package_bytes(WE2002_ASSETS, "we2002_english.ppf")


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


def test_a_failed_write_leaves_the_orphan_registered_and_the_memo_empty(tmp_path, monkeypatch):
    """The hook is registered before the write, so the empty file is still cleaned up."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *args: registered.append((fn, args)))
    real_factory = tempfile.NamedTemporaryFile

    def exploding_factory(*args, **kwargs):
        handle = real_factory(*args, **kwargs)
        handle.write = _refuse_to_write
        return handle

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", exploding_factory)

    with pytest.raises(OSError, match="no space left"):
        package_path(WE2002_ASSETS, "we2002_english.ppf")

    assert len(registered) == 1
    hook, args = registered[0]
    orphan = pathlib.Path(args[0])
    assert orphan.exists() is True
    assert assets._materialised == {}, "a half-written file must not be memoised"

    hook(*args)
    assert list(tmp_path.iterdir()) == []


def test_a_missing_asset_raises_a_named_error():
    with pytest.raises(MissingAssetError) as excinfo:
        package_bytes(WE2002_ASSETS, "nope.ppf")

    assert str(excinfo.value) == f"Missing packaged asset {WE2002_ASSETS}:nope.ppf"
    assert isinstance(excinfo.value.__cause__, FileNotFoundError) is True


def test_a_missing_package_raises_a_named_error():
    """`ModuleNotFoundError` is not an `OSError`, so it has to be caught by name."""
    with pytest.raises(MissingAssetError) as excinfo:
        package_bytes("retro_roster_patcher.games.we2002.no_such_package", "x.ppf")

    assert str(excinfo.value) == (
        "Missing packaged asset retro_roster_patcher.games.we2002.no_such_package:x.ppf"
    )
    assert isinstance(excinfo.value.__cause__, ModuleNotFoundError) is True


@pytest.mark.parametrize(
    "name", ["../assets/we2002_english.ppf", "sub/x.ppf", "a\\b", "", ".", ".."]
)
def test_a_name_that_is_not_a_single_filename_is_refused(name):
    """`importlib.resources` would resolve a separator and read outside the package."""
    with pytest.raises(MissingAssetError) as excinfo:
        package_bytes(WE2002_ASSETS, name)

    assert str(excinfo.value) == f"Not a single filename: {WE2002_ASSETS}:{name}"
    assert excinfo.value.__cause__ is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
def test_an_unreadable_asset_is_not_reported_as_missing(tmp_path, monkeypatch):
    """A misdiagnosis here sends the reader hunting for a packaging bug."""
    package = tmp_path / "rrp_locked_pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    asset = package / "locked.ppf"
    asset.write_bytes(b"secret")
    asset.chmod(0o000)
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(PermissionError):
        package_bytes("rrp_locked_pkg", "locked.ppf")


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


def test_english_falls_back_to_generation_when_community_assets_are_supplied(tmp_path):
    """The `has_community` half of the dispatcher's English short-circuit."""
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())

    path = ensure_ppf(str(cache_dir), "en", assets_dir=str(assets_dir))

    assert path == str(cache_dir / "we2002_english.ppf")
    assert (cache_dir / "we2002_english.ppf").exists() is True


def test_an_empty_assets_dir_never_falls_through_to_the_working_directory(tmp_path, monkeypatch):
    # `os.path.join("", "w202-english.ppf")` is a bare relative path, so without
    # the `bool(assets_dir)` guard this read resolves against the cwd. The cwd is
    # the thing under test here, which is why this test moves it.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    (tmp_path / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    cache_dir = tmp_path / "cache"

    path = ensure_ppf(str(cache_dir), "en", assets_dir="")

    assert cache_dir.exists() is False
    assert path == package_path(WE2002_ASSETS, "we2002_english.ppf")


@pytest.mark.parametrize("lang", ["es", "fr", "pt"])
def test_other_languages_are_generated_into_the_cache_directory(tmp_path, lang):
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), lang)

    assert path == str(cache_dir / f"we2002_{LANGUAGES[lang].lower()}.ppf")
    assert (cache_dir / f"we2002_{LANGUAGES[lang].lower()}.ppf").exists() is True


def test_generation_is_cached_between_calls(tmp_path):
    cache_dir = tmp_path / "cache"
    first = ensure_ppf(str(cache_dir), "fr")
    pathlib.Path(first).write_bytes(b"SENTINEL")

    second = ensure_ppf(str(cache_dir), "fr")

    assert second == first
    assert pathlib.Path(second).read_bytes() == b"SENTINEL"


@pytest.mark.parametrize("lang", ["es", "fr", "pt"])
def test_a_community_assets_dir_is_read_but_never_written(tmp_path, lang):
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    before = {p.name: p.read_bytes() for p in assets_dir.iterdir()}

    path = ensure_ppf(str(cache_dir), lang, assets_dir=str(assets_dir))

    after = {p.name: p.read_bytes() for p in assets_dir.iterdir()}
    assert after == before, "assets_dir is read-only"
    merged = pathlib.Path(path).read_bytes()
    # The community record was merged, and merged in this language rather than
    # passed through: the 1912-byte team-names-only patch has neither property.
    assert len(merged) == 1922
    assert TRANSLATED[lang] in merged


def test_a_cache_predating_the_community_assets_is_rebuilt_with_them(tmp_path):
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    plain = ensure_ppf(str(cache_dir), "fr")
    assert len(pathlib.Path(plain).read_bytes()) == 1912

    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    rebuilt = ensure_ppf(str(cache_dir), "fr", assets_dir=str(assets_dir))

    assert rebuilt == plain
    assert TRANSLATED["fr"] in pathlib.Path(rebuilt).read_bytes()


def test_a_cache_already_merged_with_the_community_assets_is_left_alone(tmp_path):
    """The old size heuristic rebuilt this every call, because 1922 is under 10 KB."""
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    merged = ensure_ppf(str(cache_dir), "fr", assets_dir=str(assets_dir))
    pathlib.Path(merged).write_bytes(b"SENTINEL")

    again = ensure_ppf(str(cache_dir), "fr", assets_dir=str(assets_dir))

    assert again == merged
    assert pathlib.Path(again).read_bytes() == b"SENTINEL"


def test_an_absent_assets_dir_is_not_an_error(tmp_path):
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), "pt", assets_dir=str(tmp_path / "does-not-exist"))

    assert path == str(cache_dir / "we2002_portuguese.ppf")
    assert (cache_dir / "we2002_portuguese.ppf").exists() is True
