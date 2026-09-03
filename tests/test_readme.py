"""The README, checked against the code it describes.

`README.md` is the only tracked documentation in this repository, so it has to
carry a 30-name public API, four CLI verbs, a five-event NDJSON protocol two
external applications code against, and the registry a new game plugs into.
Prose that size drifts. Every claim below is therefore derived by running the
thing described -- building the real parser, driving the real renderer, reading
the real `__all__` -- and compared with `==` against what the file says.

Comparisons are two-way on purpose. A flag added to the parser and left out of
the README is the same defect as a README documenting a flag that does not
exist, and only equality catches both. `<=` would have let the first ship.

Every set extracted from the markdown also has its size pinned, or is compared
against a set the code says is non-empty. That is not belt-and-braces: a test
that extracts zero rows from a table it failed to find and then asserts all zero
of them are correct passes forever and proves nothing. Each helper below raises
`LookupError` rather than returning `[]` when it finds nothing, for the same
reason.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import unicodedata
from pathlib import Path

import pytest

import retro_roster_patcher as rrp
from retro_roster_patcher import sports
from retro_roster_patcher.cli import commands, render
from retro_roster_patcher.cli.__main__ import build_parser
from retro_roster_patcher.core.models import PatchResult, RomInfo
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.core.registry import PatcherInfo, register
from retro_roster_patcher.games.we2002.translations.we2002 import LANGUAGES
from retro_roster_patcher.sports.models import League, LeagueData

README = Path(__file__).resolve().parent.parent / "README.md"
TEXT = README.read_text(encoding="utf-8")

#: Backticked `identifier`, the only markup this file reads a name out of.
_NAME = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


# -- extraction ------------------------------------------------------------
#
# Each of these raises instead of returning an empty result. A silent `[]` is
# what turns a broken extractor into a green test.


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table(header: list[str]) -> list[list[str]]:
    """Rows of the one markdown table whose header cells are exactly `header`.

    Matched on the full header rather than the first cell: two tables in this
    README start with a `Game` column, and matching loosely would silently read
    the wrong one.
    """
    lines = TEXT.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("|") and _cells(line) == header:
            rows = []
            # +2 skips the header and the `| --- |` separator beneath it.
            for row in lines[index + 2 :]:
                if not row.startswith("|"):
                    break
                rows.append(_cells(row))
            if not rows:
                raise LookupError(f"README table {header} has no rows")
            return rows
    raise LookupError(f"README has no table with header {header}")


def _fences() -> list[tuple[str, str]]:
    """(info string, body) for every fenced block in the README."""
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```", TEXT, re.M | re.S)
    if not blocks:
        raise LookupError("README has no fenced code blocks")
    return [(info.strip(), body) for info, body in blocks]


def _python_blocks(info: str) -> list[str]:
    return [body for fence_info, body in _fences() if fence_info == info]


def _section(heading: str) -> str:
    """The body of one `###` section, up to the next heading of any level."""
    match = re.search(rf"^{re.escape(heading)}\n(.*?)^#", TEXT, re.M | re.S)
    if match is None:
        raise LookupError(f"README has no section {heading!r}")
    return match.group(1)


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    """Every verb's parser, by name.

    `_actions` is argparse's private surface and there is no public equivalent:
    `add_subparsers` returns the action on the way in and the built parser hands
    back nothing. Reaching for it here is what makes the flag table a derived
    fact instead of a second copy of it.
    """
    parser = build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if len(actions) != 1:
        raise LookupError("build_parser() no longer has exactly one subparsers action")
    return dict(actions[0].choices)


def _parser_flags() -> dict[str, dict[str, bool]]:
    """`{verb: {"--flag": required}}` for every verb the CLI offers."""
    return {
        verb: {
            option: action.required
            for action in sub._actions
            for option in action.option_strings
            if option.startswith("--")
        }
        for verb, sub in _subparsers().items()
    }


# -- the code examples -----------------------------------------------------


def test_the_readme_has_the_expected_mix_of_fenced_blocks():
    # Pinned so the two tests below cannot quietly start covering nothing. If a
    # block is added or its info string is changed, this is the failure that
    # says which of the two now has a different job.
    counted = {}
    for info, _ in _fences():
        counted[info] = counted.get(info, 0) + 1
    assert counted == {"bash": 3, "python": 2, "python no-run": 2, "json": 1}


def test_every_runnable_python_example_executes(tmp_path, monkeypatch):
    """The `python` blocks run, on this interpreter, with no network and no ROM.

    Run rather than merely compiled: a block that imports a name this package
    stopped exporting compiles perfectly well.

    `HOME` and the working directory both move to `tmp_path`, so an example that
    writes a file writes it there. That is also what keeps the examples honest
    about paths -- one that reached outside would escape the sandbox and be
    visible in the diff rather than silently polluting the tree.
    """
    blocks = _python_blocks("python")
    assert len(blocks) == 2

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    failures = []
    for source in blocks:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(source, str(README), "exec"), {"__name__": "__readme__"})
        except BaseException as exc:  # noqa: BLE001 - report every failure, not the first
            failures.append(f"{type(exc).__name__}: {exc}\n{source}")
    assert failures == []


def test_every_no_run_python_example_is_valid_python():
    """The two blocks marked `no-run` need a provider or would mutate global state.

    They still have to parse. `compile` is weaker than execution and this test
    says so rather than implying more; the two tests below take the same blocks
    further, against the interpreter's own view of the API they use.
    """
    blocks = _python_blocks("python no-run")
    assert len(blocks) == 2
    assert [b for b in blocks if _syntax_error(b) is not None] == []


def _syntax_error(source: str) -> SyntaxError | None:
    try:
        compile(source, str(README), "exec")
    except SyntaxError as exc:
        return exc
    return None


def _readme_class_def(name: str) -> ast.ClassDef:
    for source in _python_blocks("python no-run"):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ClassDef) and node.name == name:
                return node
    raise LookupError(f"no README example defines class {name}")


def test_the_new_game_example_implements_every_abstract_method():
    """The extension example is complete against `Patcher`.

    `__abstractmethods__` is the interface a new game must satisfy, so an
    example that omits one shows a class Python refuses to instantiate, and an
    example that keeps a method the base class dropped teaches dead work.
    """
    example = _readme_class_def("MyGamePatcher")
    defined = {node.name for node in example.body if isinstance(node, ast.FunctionDef)}
    assert defined == set(Patcher.__abstractmethods__)


def test_the_new_game_example_passes_register_arguments_it_accepts():
    """`@register(...)` in the example binds against the real signature."""
    example = _readme_class_def("MyGamePatcher")
    calls = [
        node
        for node in example.decorator_list
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "register"
    ]
    assert len(calls) == 1
    keywords = {kw.arg for kw in calls[0].keywords}
    assert keywords == {"platform", "sport", "providers"}
    accepted = set(inspect.signature(register).parameters) - {"game_id"}
    assert keywords - accepted == set()


# -- the CLI ---------------------------------------------------------------


def test_the_flag_table_matches_the_parser_flag_for_flag():
    """Both directions, and requiredness with them.

    The table's cells are `req` (the verb refuses to run without it), `yes`
    (accepted) and `-` (not accepted). Comparing the whole nested mapping with
    `==` is what makes an undocumented new flag fail here as loudly as a
    documented flag that was deleted -- and it catches the subtler drift too, a
    flag that became required while the table still called it optional.
    """
    expected = _parser_flags()
    assert sorted(expected) == ["analyze", "fetch", "list", "patch"]

    header = ["Flag", "`list`", "`analyze`", "`fetch`", "`patch`", "Meaning"]
    rows = _table(header)
    verbs = [cell.strip("`") for cell in header[1:5]]

    documented: dict[str, dict[str, bool]] = {verb: {} for verb in verbs}
    for row in rows:
        flag = row[0].strip("`")
        for verb, cell in zip(verbs, row[1:5], strict=True):
            if cell == "req":
                documented[verb][flag] = True
            elif cell == "yes":
                documented[verb][flag] = False
            elif cell != "-":
                raise LookupError(f"unreadable cell {cell!r} for {flag} under {verb}")

    assert len(rows) == 13
    assert documented == expected


def _invocations() -> list[str]:
    found = re.findall(r"^retro-roster .*$", TEXT, re.M)
    if not found:
        raise LookupError("README shows no `retro-roster` invocations")
    return found


def test_every_retro_roster_invocation_in_the_readme_parses():
    """Every example command line is accepted by the real parser.

    Argparse exits rather than raising on a bad argv, so `SystemExit` is the
    failure being caught here: a typo'd flag, a `--season` that is not an
    integer, a verb that no longer exists.
    """
    parser = build_parser()
    failures = []
    verbs = set()
    for line in _invocations():
        argv = line.split()[1:]
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                verbs.add(parser.parse_args(argv, argparse.Namespace(json=False)).verb)
        except SystemExit:
            failures.append(line)
    assert failures == []
    # Derived, and it is what rules out a vacuous pass: an extractor that found
    # no invocations yields an empty set, which is not the four verbs the parser
    # offers. It also holds the README to showing all four.
    assert verbs == set(_subparsers())


def _cli(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a child process with `HOME` pointed at `tmp_path`.

    `PYTHONPATH` is set from the imported package's own location rather than
    inherited: without it a child interpreter can silently resolve a different
    copy of the library than the one this suite is testing.
    """
    package_root = Path(rrp.__file__).resolve().parent.parent
    env = {**os.environ, "PYTHONPATH": str(package_root), "HOME": str(tmp_path)}
    return subprocess.run(
        [sys.executable, "-m", "retro_roster_patcher.cli", *argv],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        check=False,
    )


def test_the_exit_code_table_matches_what_the_cli_returns(tmp_path):
    """The three documented codes, each produced by a run that reaches no network.

    A consumer reads the exit code before it reads anything else, so this is the
    claim in the file with the most riding on it and the least visible drift.
    """
    documented = {int(row[0].strip("`")) for row in _table(["Exit", "Meaning", "On the stream"])}
    assert documented == {0, 1, 2}

    success = _cli(tmp_path, "--json", "list")
    usage = _cli(tmp_path, "--json", "fetch", "--game", "bogus", "--season", "2025")
    typed = _cli(tmp_path, "--json", "analyze", "--rom", str(tmp_path / "missing.bin"))

    assert success.returncode == 0
    assert usage.returncode == 2
    assert typed.returncode == 1
    assert {success.returncode, usage.returncode, typed.returncode} == documented

    assert json.loads(success.stdout.splitlines()[-1])["event"] == "result"
    assert json.loads(usage.stdout.splitlines()[-1])["type"] == "UsageError"
    assert json.loads(typed.stdout.splitlines()[-1])["type"] == "RomError"


def test_argparse_rejecting_the_argv_writes_no_json(tmp_path):
    """The exit-code table's one exception, which a consumer has to handle.

    `--json` is parsed by the very parser that is refusing the argv, so no
    renderer exists yet and exit 2 arrives with plain text on stderr and an
    empty protocol stream. A consumer that waits for a terminal event here waits
    forever.
    """
    rejected = _cli(tmp_path, "--json", "fetch", "--game", "we2002")
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert "the following arguments are required: --season" in rejected.stderr


# -- the NDJSON protocol ---------------------------------------------------

#: One call per method of the `Renderer` protocol, so every documented event can
#: be produced rather than described. The event names themselves are read back
#: off the wire; only the arguments are written down here, and the test below
#: fails if this table and the protocol stop naming the same methods.
_RENDERER_CALLS: dict[str, tuple[object, ...]] = {
    "status": ("Validating ROM...",),
    "progress": (0.42, "Fetching Boston Bruins..."),
    "partial": ({"teams": []},),
    "result": ({"kind": "patch"},),
    "error": (ValueError("boom"),),
}


def _emitted_events() -> dict[str, set[str]]:
    """`{event: other keys}` for every event `JsonRenderer` can write."""
    protocol_methods = {name for name in vars(render.Renderer) if not name.startswith("_")}
    assert set(_RENDERER_CALLS) == protocol_methods

    emitted = {}
    for name, args in _RENDERER_CALLS.items():
        stream = io.StringIO()
        getattr(render.JsonRenderer(out=stream), name)(*args)
        payload = json.loads(stream.getvalue())
        emitted[payload["event"]] = set(payload) - {"event"}
    return emitted


def test_the_event_table_lists_every_event_with_its_keys():
    """Names and payload keys, both derived by driving the renderer.

    The keys matter as much as the names: a consumer destructures them. `result`
    is documented as carrying the payload's own keys too, so `kind` here comes
    from the payload the table above passes in, not from the renderer.
    """
    emitted = _emitted_events()
    assert len(emitted) == 5

    rows = _table(["`event`", "Other keys", "Emitted"])
    documented = {row[0].strip("`"): set(_NAME.findall(row[1])) for row in rows}
    assert len(documented) == 5
    assert documented == emitted


def _kinds_from_source(module) -> set[str]:
    """Every literal `"kind"` a handler in `module` puts on a result payload."""
    kinds = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "kind":
                # `isinstance` on the value, not just `ast.Constant`: a
                # non-string kind would be a producer bug, and silently adding
                # it here would report it as a documentation one.
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    kinds.add(value.value)
    if not kinds:
        raise LookupError(f"no literal kinds found in {module.__name__}")
    return kinds


def _formatter_kinds() -> set[str]:
    """The kinds `HumanRenderer.result` has a formatter for."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(render.HumanRenderer.result)))
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            kinds |= {
                k.value
                for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    if not kinds:
        raise LookupError("HumanRenderer.result no longer dispatches on a dict literal")
    return kinds


def test_the_kind_table_matches_both_the_producers_and_the_formatters():
    """Three lists of the same four strings, held equal.

    `commands.py` puts a `kind` on every result payload and `render.py`
    dispatches the human output on it, so a verb whose payload gains a kind
    nobody formats is a real defect and not only a documentation one. Reading
    both out of the source is what lets this test see it.
    """
    rows = _table(["`kind`", "Verb", "Payload keys"])
    documented = {row[0].strip("`") for row in rows}
    assert len(documented) == 4
    assert documented == _kinds_from_source(commands)
    assert documented == _formatter_kinds()


def test_each_result_payloads_documented_keys_are_the_ones_it_carries():
    """The four payload rows, against the four objects that build them."""
    empty_league = LeagueData(league=League(id=0, name="", season=0), teams=[])
    expected = {
        "patchers": {"patchers", *PatcherInfo("", "", "", False, ()).to_dict()},
        "rom_info": {"matches", *RomInfo(path="", size=0, game_id="").to_dict()},
        "rosters": set(commands._summarise(empty_league, "")) - {"kind"},
        "patch": set(PatchResult(output_path="").to_dict()),
    }
    rows = _table(["`kind`", "Verb", "Payload keys"])
    # `[]` marks a list-valued key in the table; the name is what is compared.
    documented = {row[0].strip("`"): set(_NAME.findall(row[2].replace("[]", ""))) for row in rows}
    assert len(documented) == 4
    assert documented == expected


def test_the_kind_table_names_the_verb_each_payload_comes_from():
    documented = {row[1].strip("`") for row in _table(["`kind`", "Verb", "Payload keys"])}
    assert documented == set(_subparsers())


# -- the library surface ---------------------------------------------------


def test_the_root_exports_section_names_every_exported_name():
    """Equality against `__all__`, so an unlisted export fails here too.

    `tests/test_public_api.py` pins `__all__` against a written-out set; this
    pins the README against `__all__`. A name added to the package therefore has
    to reach both, which is the point: the export list a consumer reads is the
    one in this file.
    """
    section = _section("### Root exports")
    root, _, sports_part = section.partition("`from retro_roster_patcher.sports import ...`")
    assert sports_part != ""

    documented = set(_NAME.findall(root)) - {"from"}
    assert len(documented) == len(rrp.__all__)
    assert documented == set(rrp.__all__)


def test_the_sports_exports_section_names_every_exported_name():
    section = _section("### Root exports")
    _, _, sports_part = section.partition("`from retro_roster_patcher.sports import ...`")
    documented = set(_NAME.findall(sports_part))
    assert len(documented) == len(sports.__all__)
    assert documented == set(sports.__all__)


# -- the registry ----------------------------------------------------------


def test_the_games_table_matches_the_registry():
    """Ids, platforms, sports and providers, in the order the registry lists them.

    Providers as a tuple and not a set: `Patcher.__init__` defaults to
    `providers[0]`, so their order decides which one a caller who names none
    gets, and the table's first entry is a claim about that default.
    """
    rows = _table(["Game", "`--game`", "Platform", "Sport", "Providers"])
    documented = [
        (
            row[1].strip("`"),
            row[2].strip("`"),
            row[3].strip("`"),
            tuple(_NAME.findall(row[4])),
        )
        for row in rows
    ]
    expected = [
        (info.game_id, info.platform, info.sport, info.providers) for info in rrp.list_patchers()
    ]
    assert len(expected) == 2
    assert documented == expected


def test_the_per_game_requirements_table_covers_every_registered_game():
    documented = {row[0].strip("`") for row in _table(["Game", "Requires"])}
    assert documented == {info.game_id for info in rrp.list_patchers()}


def test_the_documented_language_codes_are_the_ones_we2002_ships():
    """Both of WE2002's own lists, against the sentence a user reads.

    `LANGUAGES` is the translation table and `WE2002Patcher.languages` is what
    `cmd_patch` validates `--language` against; they are pinned to each other
    elsewhere, and this holds the README to the pair.
    """
    # `\s` and not a literal space: the sentence is reflowed to the file's
    # margin, so a code can end up on the next line from the one before it.
    match = re.search(r"`we2002` takes ((?:`[a-z]{2}`(?:,|\s+and)?\s*)+)", TEXT)
    assert match is not None
    documented = tuple(_NAME.findall(match.group(1)))
    assert len(documented) == 4
    assert documented == tuple(LANGUAGES)
    assert documented == tuple(rrp.get_patcher("we2002").languages)


# -- house rules -----------------------------------------------------------


def test_the_readme_contains_no_emoji():
    """This project does not use them, including where they are conventional.

    Checked by Unicode category rather than against a list: `So` is
    "symbol, other", which is where the emoji live. Arrows and dashes are `Sm`
    and `Pd` and are used in the file deliberately.
    """
    found = [
        (index + 1, char, unicodedata.name(char, "unnamed"))
        for index, line in enumerate(TEXT.splitlines())
        for char in line
        if unicodedata.category(char) == "So"
    ]
    assert found == []


def test_the_readme_extractors_refuse_to_find_nothing():
    """The guard on every test above: no helper here can return an empty result.

    Each of the four raises `LookupError` when its target is missing, so a table
    that is renamed or a fence whose info string changes fails loudly instead of
    yielding an empty set that trivially equals another empty set. That failure
    mode is the one this file exists to avoid, so it is tested rather than
    trusted.
    """
    with pytest.raises(LookupError):
        _table(["No", "Such", "Table"])
    with pytest.raises(LookupError):
        _section("### No Such Section")
    with pytest.raises(LookupError):
        _readme_class_def("NoSuchClass")
    with pytest.raises(LookupError):
        _kinds_from_source(render)
