"""Prove the built distribution is complete, not just the source tree.

The classic `src/` layout failure is package data that passes every local test —
where the source tree is right there — and is missing from the built wheel.

Every content assertion below passes just as happily against `src/`, so on its
own this file proves nothing. `test_the_import_resolved_to_an_installed_package`
is what makes it evidence: it fails against the source tree, by design. That is
why the whole file carries the `packaging` marker and `addopts` deselects it
from the default run. The `wheel` CI job selects it back with `-m packaging`,
from a directory where `src/` cannot shadow site-packages.

A `pytest.skip` when not installed would be the wrong shape: a skip is exactly
the silent-green failure mode the provenance test exists to prevent.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import retro_roster_patcher
from retro_roster_patcher.core.assets import package_bytes

pytestmark = pytest.mark.packaging


def _game_ids(stdout: str) -> set[str]:
    """Pull the game ids out of `list --json`'s NDJSON, insisting on the shape.

    A substring search for an id would also match an error message that happens
    to name one, which is the whole reason this parses instead.
    """
    events = [json.loads(line) for line in stdout.splitlines()]
    assert [event["event"] for event in events] == ["result"]
    return {patcher["game_id"] for patcher in events[0]["patchers"]}


def test_the_import_resolved_to_an_installed_package():
    # Everything else in this file passes against `src/` too. Without this the
    # wheel job goes vacuous the moment anything puts `src/` back on the path
    # ahead of site-packages — a `pythonpath` reaching into it, or an editable
    # install landing after the wheel — and stays green forever while proving
    # nothing. The job's `cd /tmp` is not that mechanism: measured, running this
    # from inside the checkout still resolves to site-packages, because
    # `pythonpath = ["."]` inserts the repository root and the package lives one
    # level down under `src/`.
    installed = "site-packages" in Path(retro_roster_patcher.__file__).parts
    assert installed is True


def test_the_translation_ppf_is_readable_as_package_data():
    data = package_bytes("retro_roster_patcher.games.we2002.assets", "we2002_english.ppf")
    # Pins the format version too, which is what `apply_ppf` dispatches on. Not
    # the byte length: that is Task 19 generator output and any legitimate edit
    # to the translation would move it.
    assert data[:5] == b"PPF10"


def _shipped_game_packages() -> set[str]:
    """Game ids implied by the subpackages actually present in the distribution.

    Derived from the installed tree rather than from the registry, because the
    claim below is that importing the root package registers *every* game that
    shipped. Comparing the registry to itself would assert nothing; comparing it
    to the directories is what catches the real failure mode -- a game package
    that is present on disk and never imported, so `@register` never runs and the
    game silently does not exist. That is one forgotten line in
    `retro_roster_patcher/__init__.py`, and it has been forgotten before.

    The id is the package name with underscores as hyphens, which is the
    convention every game follows. A game that breaks it fails here, which is the
    intended outcome: the convention is what lets this check stay honest without
    a hand-maintained list.
    """
    games = Path(retro_roster_patcher.__file__).parent / "games"
    return {
        entry.name.replace("_", "-")
        for entry in games.iterdir()
        if entry.is_dir() and not entry.name.startswith("_")
    }


def test_the_registry_is_populated_by_importing_the_root_package():
    # Runs in a subprocess, and that is the whole test. In-process this claim
    # cannot be made: `tests/games/<id>/` imports each game package directly, so
    # `@register` has already run for every game by the time this file executes,
    # and the registry looks complete even when `retro_roster_patcher/__init__.py`
    # imports none of them. Measured -- commenting out one import line left this
    # green in-process and failed only the two subprocess tests below.
    #
    # A clean interpreter that imports nothing but the root package is the only
    # place the claim is falsifiable.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, retro_roster_patcher as r;"
            "print(json.dumps(sorted(i.game_id for i in r.list_patchers())))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    ids = set(json.loads(proc.stdout))
    shipped = _shipped_game_packages()
    # Guards against zero-over-zero without a count that rots on the next
    # migration: an empty `games/` would make both sides empty and the comparison
    # below vacuous, so two games that have shipped since v0.1 are pinned by name.
    assert "we2002" in shipped
    assert "nhl94-genesis" in shipped
    assert ids == shipped


def test_the_console_script_installed_by_project_scripts_runs():
    # `[project.scripts]` is only exercised through the generated launcher, so a
    # `python -m` run cannot stand in for this: it passes even when the entry
    # point is missing or names a symbol that does not exist. Resolved off the
    # running interpreter's own `bin` rather than `PATH`, which is deterministic
    # and works in the source-tree venv and the wheel venv alike.
    script = Path(sys.executable).parent / "retro-roster"
    # `check=True` is the exit-code assertion; a non-zero status raises here.
    proc = subprocess.run(
        [str(script), "list", "--json"], capture_output=True, text=True, check=True
    )
    # Compared against this process's registry, not a literal set. Not circular:
    # the claim is that a separate process, resolving the package through the
    # installed console script, sees the same games this one does.
    expected = {info.game_id for info in retro_roster_patcher.list_patchers()}
    assert _game_ids(proc.stdout) == expected


def test_the_cli_package_runs_as_a_module():
    # Covers a different failure from the launcher test above: that
    # `retro_roster_patcher.cli.__main__` is importable and runnable from the
    # installed tree, whatever `[project.scripts]` says.
    # `check=True` is the exit-code assertion; a non-zero status raises here.
    proc = subprocess.run(
        [sys.executable, "-m", "retro_roster_patcher.cli", "list", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    # As above: a separate process reaching the package through `-m` rather than
    # the launcher must see the same games.
    expected = {info.game_id for info in retro_roster_patcher.list_patchers()}
    assert _game_ids(proc.stdout) == expected


def test_the_formats_package_ships_and_works(tmp_path):
    # `formats/` is a subpackage under `retro_roster_patcher` that the root
    # package does not import. `packages.find` discovers it automatically, so
    # this is not guarding a hand-maintained list — it is guarding a gap the rest
    # of this file cannot see: the registry test only walks `games/`, and a game
    # importing `formats.ea_tdb` from a wheel that shipped `formats/__init__.py`
    # and nothing else fails at import time, in the field.
    #
    # A round trip rather than a bare import, because a `formats/` directory that
    # shipped its `__init__.py` and not `ea_tdb.py` would satisfy an import of
    # the package and fail every consumer. Like the rest of this file, it passes
    # against `src/` too; `test_the_import_resolved_to_an_installed_package` is
    # what makes it evidence about the built distribution.
    #
    # **Both modules, and every module `formats/` grows must be added here.**
    # `iso9660.py` is a single file with no package of its own, which is exactly
    # the shape that goes missing from a distribution without anything noticing.
    #
    # In a subprocess and from `tmp_path`: this file's siblings under
    # `tests/formats/` import the modules directly, so an in-process check would
    # pass on test ordering, and `-c` puts the child's cwd on its `sys.path`.
    source = textwrap.dedent(
        """
        import io

        from retro_roster_patcher.formats import ea_tdb, iso9660

        print(ea_tdb.refpack_decompress(ea_tdb.refpack_compress(b"AAAABBBBAAAA")))
        print(ea_tdb.tdb_crc(b"123456789"))

        # A PVD naming a root directory at sector 20, and nothing else. Enough
        # to prove `iso9660` parses rather than merely imports.
        image = bytearray(17 * iso9660.SECTOR_SIZE)
        image[iso9660.PVD_OFFSET] = 1
        image[iso9660.PVD_OFFSET + 158] = 20
        print(iso9660.read_root(io.BytesIO(bytes(image))))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    # The CRC is the published CRC-32/MPEG-2 check value, so a `formats` that
    # imported and then answered wrongly fails here too.
    assert proc.stdout.splitlines() == [
        "b'AAAABBBBAAAA'",
        "58124007",
        "Extent(lba=20, size=0)",
    ]
