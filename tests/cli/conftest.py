"""Guards and shared fixtures for the CLI tests.

Nothing here registers a patcher; the stub patcher and its registry cleanup live
in `test_fetch_patch.py`, which is the only file that needs one.
"""

import json
import os

import pytest

from retro_roster_patcher.games.we2002.rom_reader import _MIN_VALID_SIZE


def events(capsys) -> list[dict]:
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines()]


@pytest.fixture(autouse=True)
def _no_ambient_api_key(monkeypatch):
    """Keep a real `$RETRO_ROSTER_API_KEY` out of the CLI tests.

    `--api-key` defaults to that variable, so on a machine with a real key
    exported every patcher these tests build would receive it. The two tests that
    need a value put a dummy one back with `monkeypatch.setenv`; the one that
    covers the empty fallback relies on this deletion.
    """
    monkeypatch.delenv("RETRO_ROSTER_API_KEY", raising=False)


@pytest.fixture
def cache(tmp_path):
    """Never let a test touch the real `~/.cache` — WE2002 creates its cache dir."""
    return ["--cache-dir", str(tmp_path / "cache")]


@pytest.fixture
def unreadable_rom(tmp_path):
    """A file `Path.is_file()` accepts but `open()` refuses, big enough to be opened.

    Sized at exactly WE2002's `_MIN_VALID_SIZE`, imported rather than typed out,
    because WE2002's `validate_rom` is size-only: below that threshold
    `get_rom_info` never reaches the one line that opens the file, so a small
    fixture cannot exercise an unreadable ROM at all. It is a sparse file —
    `truncate` on an empty one — so a 100 MB fixture costs no disk and no time.

    Measured against this fixture: `analyze --game nhl94-genesis` and
    `analyze --game we2002` both exit 1 with `{"event":"error","type":"RomError"}`,
    and a sweep with no `--game` swallows both and exits 0 with an empty
    `matches`. Both patchers reject; neither crashes. Before `as_rom_error` the
    WE2002 arm raised `PermissionError` out of `main` with an empty stdout, which
    is the whole reason this fixture is no longer 4096 bytes.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the read bit, so the file would still be readable")
    rom = tmp_path / "locked.bin"
    with open(rom, "wb") as handle:
        handle.truncate(_MIN_VALID_SIZE)
    rom.chmod(0o000)
    return rom
