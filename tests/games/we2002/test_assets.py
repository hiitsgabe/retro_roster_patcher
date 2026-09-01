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
    _build_kanji_records,
    _make_ppf1,
    generate_english_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.english_ppf import (
    ensure_ppf as ensure_english_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.french_ppf import (
    ensure_ppf as ensure_french_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.french_ppf import (
    generate_french_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.portuguese_ppf import (
    ensure_ppf as ensure_portuguese_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.portuguese_ppf import (
    generate_portuguese_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.spanish_ppf import (
    ensure_ppf as ensure_spanish_ppf,
)
from retro_roster_patcher.games.we2002.translations.we2002.spanish_ppf import (
    generate_spanish_ppf,
)

WE2002_ASSETS = "retro_roster_patcher.games.we2002.assets"

ALL_LANGS = ["en", "es", "fr", "pt"]

# The per-language module behind each code: the generator that produces the
# unmerged patch, and the module-level `ensure_ppf` the dispatcher delegates to.
GENERATORS = {
    "en": generate_english_ppf,
    "es": generate_spanish_ppf,
    "fr": generate_french_ppf,
    "pt": generate_portuguese_ppf,
}
MODULE_ENSURE = {
    "en": ensure_english_ppf,
    "es": ensure_spanish_ppf,
    "fr": ensure_french_ppf,
    "pt": ensure_portuguese_ppf,
}

# What the synthetic community record looks like once merged, per language. Each
# is padded back to the original five bytes, so the merged PPF is the same size
# whichever language ran — which is why the size assertions below are not left to
# carry the claim alone. English has no translation table, so its record merges
# through unchanged.
TRANSLATED = {"en": b"SHOOT", "es": b"TIRO ", "fr": b"TIR  ", "pt": b"CHUTE"}


def _cache_file(cache_dir: pathlib.Path, lang: str) -> pathlib.Path:
    return cache_dir / f"we2002_{LANGUAGES[lang].lower()}.ppf"


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
    # The memo is keyed on both halves, so neither a second name in the same
    # package nor the same name in a second package may collide with it.
    other = package_path(WE2002_ASSETS, "__init__.py")
    same_name_other_package = package_path("retro_roster_patcher.core", "__init__.py")

    assert second == first
    assert third == first
    assert (other == first) is False
    assert (same_name_other_package == other) is False
    assert pathlib.Path(first).read_bytes() == package_bytes(WE2002_ASSETS, "we2002_english.ppf")
    assert pathlib.Path(other).read_bytes() == package_bytes(WE2002_ASSETS, "__init__.py")
    assert pathlib.Path(same_name_other_package).read_bytes() == package_bytes(
        "retro_roster_patcher.core", "__init__.py"
    )
    assert [args for _, args in registered] == [(first,), (other,), (same_name_other_package,)]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [
            pathlib.Path(first).name,
            pathlib.Path(other).name,
            pathlib.Path(same_name_other_package).name,
        ]
    )


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


def test_english_returns_one_shared_path_rather_than_a_copy_per_call(tmp_path, monkeypatch):
    # `ensure_ppf`'s docstring described the opposite of this: a fresh temporary
    # file per call, and two calls returning two different paths. `package_path`
    # memoises on `(package, name)`, so every caller is handed the same file and
    # a caller that wrote to it would change what the next one reads.
    monkeypatch.setattr(assets, "_materialised", {})
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    cache_dir = tmp_path / "cache"

    first = ensure_ppf(str(cache_dir), "en")
    second = ensure_ppf(str(cache_dir), "en")

    assert second == first
    # One file materialised, not two, and `cache_dir` is still untouched.
    assert [p.name for p in tmp_path.iterdir()] == [pathlib.Path(first).name]
    assert cache_dir.exists() is False


def test_english_falls_back_to_generation_when_community_assets_are_supplied(tmp_path):
    """The `has_community` half of the dispatcher's English short-circuit."""
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())

    path = ensure_ppf(str(cache_dir), "en", assets_dir=str(assets_dir))

    assert path == str(cache_dir / "we2002_english.ppf")
    merged = pathlib.Path(path).read_bytes()
    # Routed *and* merged: 1912 is the team-names-only patch. English has no
    # translation table, so the record merges through untranslated.
    assert len(merged) == 1922
    assert TRANSLATED["en"] in merged


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


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_a_cache_predating_the_community_assets_is_rebuilt_with_them(tmp_path, lang):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    # Seeded from the generator rather than through `ensure_ppf`, because for
    # "en" the dispatcher serves the packaged asset and writes no cache at all.
    plain = _cache_file(cache_dir, lang)
    plain.write_bytes(GENERATORS[lang]())
    assert len(plain.read_bytes()) == 1912

    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    rebuilt = ensure_ppf(str(cache_dir), lang, assets_dir=str(assets_dir))

    assert rebuilt == str(plain)
    assert len(plain.read_bytes()) == 1922
    assert TRANSLATED[lang] in plain.read_bytes()


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_a_cache_already_merged_with_the_community_assets_is_left_alone(tmp_path, lang):
    """The old size heuristic rebuilt this every call, because 1922 is under 10 KB."""
    cache_dir = tmp_path / "cache"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    merged = ensure_ppf(str(cache_dir), lang, assets_dir=str(assets_dir))
    pathlib.Path(merged).write_bytes(b"SENTINEL")

    again = ensure_ppf(str(cache_dir), lang, assets_dir=str(assets_dir))

    assert again == merged
    assert pathlib.Path(again).read_bytes() == b"SENTINEL"


@pytest.mark.parametrize("lang", ALL_LANGS)
def test_a_warm_cache_is_not_rewritten_when_no_community_assets_are_present(
    tmp_path, lang, monkeypatch
):
    """Both halves of `has_community` in the language module's staleness guard.

    Drop either one and the guard opens: the cache matches the generator output,
    so it is unlinked and rewritten on every call. The bytes come out identical,
    which is why this checks the mtime instead — a warm cache in a read-only
    directory raises `PermissionError` under either mutation, and that is the
    exact failure the cache/assets split exists to prevent.

    Called through the language module rather than the dispatcher because for
    "en" the dispatcher serves the packaged asset and never reaches this code
    without community assets.
    """
    # `os.path.join("", "w202-english.ppf")` is a bare relative path, so without
    # the `bool(assets_dir)` guard this stray file makes the module believe the
    # caller supplied community assets. The cwd is the thing under test, which is
    # why this test moves it.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "w202-english.ppf").write_bytes(_synthetic_community_ppf())
    cache_dir = tmp_path / "cache"
    first = MODULE_ENSURE[lang](str(cache_dir))
    assert pathlib.Path(first).read_bytes() == GENERATORS[lang]()
    # A rewrite cannot preserve this; unlike an inode it cannot be reused, and
    # unlike a wall-clock comparison it is immune to mtime granularity.
    os.utime(first, (0, 0))

    second = MODULE_ENSURE[lang](str(cache_dir))

    assert second == first
    assert os.stat(second).st_mtime_ns == 0


def test_an_absent_assets_dir_is_not_an_error(tmp_path):
    cache_dir = tmp_path / "cache"
    path = ensure_ppf(str(cache_dir), "pt", assets_dir=str(tmp_path / "does-not-exist"))

    assert path == str(cache_dir / "we2002_portuguese.ppf")
    assert (cache_dir / "we2002_portuguese.ppf").exists() is True


# ── record merge order and the PPF1 record limit ──────────────────────────


def _community_ppf_with(offset: int, payload: bytes) -> bytes:
    """A synthetic PPF2 carrying one record at a caller-chosen offset."""
    header = b"PPF20" + b"\x00" + b"X" * 50 + struct.pack("<I", 0) + b"\x00" * 1024
    return header + struct.pack("<I", offset) + bytes([len(payload)]) + payload


def test_a_community_record_inside_the_kanji_span_is_written_before_the_kanji_name(tmp_path):
    # `generate_english_ppf` merges as `menu + records`. Both existing merge
    # assertions are order-insensitive, and the synthetic community record they
    # use sits at 0x1000, outside the kanji span entirely, so nothing before this
    # could tell `menu + records` from `records + menu`.
    #
    # PPF records are applied in file order, so the later one wins. Putting the
    # community record at the first kanji offset makes the order decide which
    # bytes reach the ROM.
    kanji_offset, kanji_data = _build_kanji_records()[0]
    assert kanji_offset == 2002316
    payload = b"\xee" * len(kanji_data)
    assert (payload == kanji_data) is False

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_community_ppf_with(kanji_offset, payload))

    merged = generate_english_ppf(str(assets_dir))

    # First record after the 56-byte header is the community one, in full.
    records = merged[56:]
    assert records[:4] == struct.pack("<I", kanji_offset)
    assert records[4] == len(payload)
    assert records[5 : 5 + len(payload)] == payload
    # Second record is the kanji name for the same offset, so it lands last.
    second = records[5 + len(payload) :]
    assert second[:4] == struct.pack("<I", kanji_offset)
    assert second[5 : 5 + len(kanji_data)] == kanji_data


def test_the_kanji_name_is_what_survives_at_a_contested_offset(tmp_path):
    # The consequence of that order, measured on a target rather than read off
    # the patch: the reversed merge would leave `\xee` here instead.
    from retro_roster_patcher.games.we2002.ppf import apply_ppf

    kanji_offset, kanji_data = _build_kanji_records()[0]
    payload = b"\xee" * len(kanji_data)
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "w202-english.ppf").write_bytes(_community_ppf_with(kanji_offset, payload))
    patch = tmp_path / "merged.ppf"
    patch.write_bytes(generate_english_ppf(str(assets_dir)))
    target = tmp_path / "rom.bin"
    target.write_bytes(bytes(kanji_offset + 4096))

    apply_ppf(str(target), str(patch))

    written = target.read_bytes()[kanji_offset : kanji_offset + len(kanji_data)]
    assert written == kanji_data
    assert (written == payload) is False


def test_no_record_this_package_generates_reaches_the_ppf1_length_limit():
    # A PPF1 record carries its length in one byte, so 255 is the format limit
    # and `_make_ppf1` splits anything longer. Nothing here gets near it: the
    # kanji records are `_LUN_NOMIK[i] * 2` bytes and the widest entry is 14.
    lengths = [len(data) for _, data in _build_kanji_records()]

    assert len(lengths) == 96
    assert max(lengths) == 28
    assert min(lengths) == 4


def test_a_record_longer_than_the_limit_becomes_several_at_consecutive_offsets():
    # The split branch has no live caller, which is why it survived every mutant
    # aimed at it. It is kept rather than deleted because `bytearray.append`
    # raises `ValueError` above 255, so a caller handing `_make_ppf1` a long
    # record would otherwise get a crash instead of a patch. 600 bytes is
    # 255 + 255 + 90, which is three records and not two.
    long_data = bytes(range(256)) * 2 + bytes(88)
    assert len(long_data) == 600

    patch = _make_ppf1("split", [(1000, long_data)])

    records = patch[56:]
    assert records[:4] == struct.pack("<I", 1000)
    assert records[4] == 255
    assert records[5:260] == long_data[:255]
    assert records[260:264] == struct.pack("<I", 1255)
    assert records[264] == 255
    assert records[265:520] == long_data[255:510]
    assert records[520:524] == struct.pack("<I", 1510)
    assert records[524] == 90
    assert records[525:615] == long_data[510:]
    assert len(records) == 615
