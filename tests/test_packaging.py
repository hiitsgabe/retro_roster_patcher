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


def test_the_registry_is_populated_by_importing_the_root_package():
    ids = {info.game_id for info in retro_roster_patcher.list_patchers()}
    assert ids == {"nhl94-genesis", "we2002"}


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
    assert _game_ids(proc.stdout) == {"nhl94-genesis", "we2002"}


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
    assert _game_ids(proc.stdout) == {"nhl94-genesis", "we2002"}
