"""`--language`, from the command line to the patcher that honours it.

`Patcher.patch` ends in `**options`, so nothing downstream can tell a keyword it
does not understand from one it forgot to read: an unrecognised option is
dropped in silence and the run still reports success. That is the whole reason
`cli.commands._patch_options` exists, and it is what these tests aim at — that
the three translations the project ships are reachable from the surface the
Flutter app drives, and that a code the named game cannot honour is refused on
the protocol stream rather than ignored.

The stub's language set is deliberately `("aa", "bb")` and not WE2002's four
codes. A stub that agreed with the real patcher would let a `_patch_options`
which had the codes hardcoded pass every stub test.
"""

import json

import pytest

from retro_roster_patcher.cli.__main__ import build_parser, main
from retro_roster_patcher.core import registry
from retro_roster_patcher.core.models import MappedRosters, PatchResult, RomInfo, SlotMapping
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.games.we2002 import patcher as we2002_patcher_module
from retro_roster_patcher.games.we2002.patcher import WE2002Patcher
from retro_roster_patcher.games.we2002.translations.we2002 import LANGUAGE_CODES, LANGUAGES
from retro_roster_patcher.sports.models import League, LeagueData, Player, Team, TeamRoster
from tests.cli.conftest import events

# What the stub patchers saw. Module level rather than a class attribute so the
# two stubs, which are a class and its subclass, cannot shadow each other's
# recordings.
SEEN: list = []


class NoLanguagePatcher(Patcher):
    """A patcher with no `languages` attribute at all.

    That absence is the shape of every game in the registry that ships no
    translations — NHL94 is the real one — and it is what `_patch_options` reads
    as "this game has nothing to offer", so it needs a stub of its own rather
    than an empty tuple, which would be a different code path.
    """

    def analyze_rom(self, rom_path):
        return RomInfo(path=str(rom_path), size=0, game_id=self.game_id)

    def fetch(self, *, season, league_id=None, on_progress=None):
        if on_progress is not None:
            on_progress(0.5, "Fetching squads")
        SEEN.append(("fetch", season))
        return LeagueData(
            league=League(id=1, name="Test League", season=season),
            teams=[TeamRoster(team=Team(id=33, name="Team A"), players=[Player(id=1, name="One")])],
        )

    def map_rosters(self, data, slot_mapping=None):
        self.check_slot_mapping(slot_mapping)
        return MappedRosters(game_id=self.game_id, teams={0: data.teams[0]})

    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options):
        # The whole `options` dict, not `options.get("language")`: a
        # `_patch_options` that forwarded `{"language": ""}` for a run with no
        # flag would be invisible to a test that only read the value out.
        SEEN.append(("patch", options))
        output_path.write_bytes(b"patched")
        return PatchResult(output_path=str(output_path), teams_patched=1, players_patched=1)


class TwoLanguagePatcher(NoLanguagePatcher):
    """A patcher that declares two codes neither real game has."""

    languages: tuple[str, ...] = ("aa", "bb")


@pytest.fixture
def stubs():
    """Register both stubs for one test, then take them back out.

    Both at once, because two of the tests below run the same argv against each
    and compare the answers, and because `tests/cli/test_main.py` compares the
    registry's ids against a two-element list by equality — a leak fails there,
    not here.
    """
    SEEN.clear()
    try:
        registry.register("no-lang-game", platform="test", sport="test")(NoLanguagePatcher)
        registry.register("two-lang-game", platform="test", sport="test")(TwoLanguagePatcher)
        yield
    finally:
        registry._REGISTRY.pop("no-lang-game", None)
        registry._REGISTRY.pop("two-lang-game", None)


def _stub_argv(tmp_path, game, *extra):
    rom, out = tmp_path / "in.bin", tmp_path / "out.bin"
    rom.write_bytes(b"\x00" * 16)
    return [
        "patch",
        "--game",
        game,
        "--rom",
        str(rom),
        "--out",
        str(out),
        "--season",
        "2024",
        "--cache-dir",
        str(tmp_path / "cache"),
        "--json",
        *extra,
    ]


def _options_seen():
    """The `**options` the recorded `patch` call received."""
    return [call for call in SEEN if call[0] == "patch"][0][1]


# -- the flag reaches `patch` ------------------------------------------------


def test_a_language_the_game_declares_reaches_patch_as_an_option(tmp_path, stubs):
    code = main(_stub_argv(tmp_path, "two-lang-game", "--language", "bb"))
    assert code == 0
    # `bb` and not `aa`: the second element of the declared tuple, so a
    # `_patch_options` that forwarded `codes[0]` rather than what was typed is
    # not satisfied by this.
    assert _options_seen() == {"language": "bb"}


def test_without_the_flag_patch_is_given_no_language_at_all(tmp_path, stubs):
    """The empty default must not arrive as `language=""`.

    `--language` defaults to `""`, and forwarding that unconditionally would
    reach `WE2002Patcher.patch`, fail its `language not in LANGUAGES` guard and
    turn every plain `patch` into a `CapabilityError`. The patcher's own default
    is what has to win, which means the key must be absent.
    """
    code = main(_stub_argv(tmp_path, "two-lang-game"))
    assert code == 0
    assert _options_seen() == {}


# -- the flag is refused, and refused with the named game's codes -------------


def test_a_language_outside_the_games_set_is_a_usage_error(tmp_path, stubs, capsys):
    code = main(_stub_argv(tmp_path, "two-lang-game", "--language", "cc"))
    assert code == 2
    assert events(capsys)[-1]["msg"] == "two-lang-game has no language 'cc'; it has aa, bb"


def test_the_refusal_is_a_terminal_error_event_on_the_stream(tmp_path, stubs, capsys):
    """Round A's contract: every failure ends the NDJSON stream with one.

    `UsageError` is the one exception class in `src/` outside
    `RetroRosterError`, sanctioned in `tests/core/test_errors.py`, and `main`
    catches it above the typed clause. This pins that the refusal takes that
    route rather than escaping as a bare `ValueError`.
    """
    main(_stub_argv(tmp_path, "two-lang-game", "--language", "cc"))
    last = events(capsys)[-1]
    assert last["event"] == "error"
    assert last["type"] == "UsageError"


def test_a_language_on_a_game_that_ships_none_is_a_usage_error(tmp_path, stubs, capsys):
    code = main(_stub_argv(tmp_path, "no-lang-game", "--language", "aa"))
    assert code == 2
    # Not "has no language 'aa'": a game with nothing to offer cannot list an
    # alternative, so the two refusals are deliberately different sentences.
    assert events(capsys)[-1]["msg"] == "no-lang-game does not take --language"


def test_the_same_code_is_accepted_by_one_game_and_refused_by_the_other(tmp_path, stubs):
    """The check reads the named patcher, not a list `_patch_options` holds.

    Same argv but for `--game`, same `aa`, opposite answers. A `_patch_options`
    that validated against a fixed set would give both runs the same exit code.
    """
    assert main(_stub_argv(tmp_path, "two-lang-game", "--language", "aa")) == 0
    SEEN.clear()
    assert main(_stub_argv(tmp_path, "no-lang-game", "--language", "aa")) == 2


def test_a_bad_language_is_refused_before_the_fetch(tmp_path, stubs, capsys):
    """The check costs no I/O, so nothing expensive may precede it.

    The stub's `fetch` reports progress and records itself, so either would
    prove a network round trip was paid for before the flag was read.
    """
    main(_stub_argv(tmp_path, "two-lang-game", "--language", "cc"))
    assert SEEN == []
    assert [e["event"] for e in events(capsys)] == ["error"]


# -- end to end, through the real WE2002 patcher -----------------------------


@pytest.fixture
def we2002_run(tmp_path, monkeypatch):
    """Everything `patch --game we2002` needs except the flag under test.

    Returns a callable taking the extra argv. The ROM is a sparse 100 MB file
    because `WE2002Patcher.patch` applies `RomReader.validate_rom`, whose only
    test is that size; `truncate` allocates no blocks, so it costs nothing.
    `RomWriter` and `apply_ppf` are replaced because the real pair copies the
    input and writes PSX disc sectors into it, which is `test_patcher.py`'s
    subject and not this file's. `ensure_ppf` is replaced by a recorder: it is
    the first thing downstream of `patch` that sees the language, so it is what
    proves the code travelled the whole way rather than being validated and
    dropped.
    """
    langs: list[str] = []

    class FakeRomWriter:
        def __init__(self, rom_path, output_path):
            # The real writer `shutil.copy2`s the input from its constructor, so
            # the file the translation patches exists by the time it runs.
            open(output_path, "wb").close()

        def write_team(self, slot_index, team, players=None, include_flag=True):
            return len(players or [])

        def flush_tex_patches(self):
            return None

        def finalize(self):
            return None

    def _ensure(cache_dir, lang="en", assets_dir=""):
        langs.append(lang)
        return "unused.ppf"

    monkeypatch.setattr(we2002_patcher_module, "RomWriter", FakeRomWriter)
    monkeypatch.setattr(we2002_patcher_module, "ensure_ppf", _ensure)
    monkeypatch.setattr(
        we2002_patcher_module, "apply_ppf", lambda bin_path, ppf_path, skip_validation=False: "ok"
    )

    rom = tmp_path / "we2002.bin"
    with rom.open("wb") as handle:
        handle.truncate(100 * 1024 * 1024)

    rosters = tmp_path / "rosters.json"
    rosters.write_text(
        json.dumps(
            {
                "league": {"id": 39, "name": "Premier League", "season": 2024},
                "teams": [
                    {
                        "team": {"id": 33, "name": "Team A", "color": "#ff0000"},
                        "players": [{"id": 1, "name": "Player One"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    slot_map = tmp_path / "slots.json"
    slot_map.write_text(json.dumps([SlotMapping(slot_index=0, team_id=33).to_dict()]))

    def run(*extra):
        return (
            main(
                [
                    "patch",
                    "--game",
                    "we2002",
                    "--api-key",
                    "dummy-key",
                    "--rom",
                    str(rom),
                    "--out",
                    str(tmp_path / "out.bin"),
                    "--rosters",
                    str(rosters),
                    "--slot-map",
                    str(slot_map),
                    "--cache-dir",
                    str(tmp_path / "cache"),
                    "--json",
                    *extra,
                ]
            ),
            langs,
        )

    return run


@pytest.mark.parametrize("language", ["en", "es", "fr", "pt"])
def test_every_shipped_translation_is_reachable_from_the_command_line(we2002_run, language):
    """The finding this closes: `es`, `fr` and `pt` were in-process only.

    Parametrised over all four rather than the three that were unreachable, so
    the case that always worked is measured by the same assertion as the three
    that did not.
    """
    code, langs = we2002_run("--language", language)
    assert code == 0
    assert langs == [language]


def test_without_the_flag_we2002_still_gets_its_own_english_default(we2002_run):
    code, langs = we2002_run()
    assert code == 0
    assert langs == ["en"]


def test_an_unknown_language_on_we2002_lists_the_four_it_ships(we2002_run, capsys):
    code, _ = we2002_run("--language", "klingon")
    assert code == 2
    assert events(capsys)[-1]["msg"] == "we2002 has no language 'klingon'; it has en, es, fr, pt"


def test_a_bad_language_on_we2002_is_refused_before_the_rom_is_opened(we2002_run, capsys):
    """`--language` is checked ahead of the slot map and the roster file.

    Only the error reaches the stream: `cmd_patch`'s two `status` events and the
    `progress` the writer would produce are all downstream of the check.
    """
    we2002_run("--language", "klingon")
    assert [e["event"] for e in events(capsys)] == ["error"]


def test_a_language_on_nhl94_is_refused_by_name(tmp_path, capsys):
    """The real registered game that ships no translations, not a stub."""
    rom = tmp_path / "nhl.bin"
    rom.write_bytes(b"\x00" * 16)
    code = main(
        [
            "patch",
            "--game",
            "nhl94-genesis",
            "--rom",
            str(rom),
            "--out",
            str(tmp_path / "out.bin"),
            "--season",
            "1994",
            "--language",
            "es",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--json",
        ]
    )
    assert code == 2
    assert events(capsys)[-1]["msg"] == "nhl94-genesis does not take --language"


# -- what the class attribute and the help text promise ----------------------


def test_we2002_declares_exactly_the_four_codes_it_ships():
    """A literal, not `tuple(LANGUAGES)`.

    Deriving the expectation from the same mapping the attribute is derived from
    would hold for any set at all. These four are what
    `translations/we2002/__init__.py` documents and what the four `*_ppf`
    modules exist for, so dropping or reordering one has to fail here.
    """
    assert WE2002Patcher.languages == ("en", "es", "fr", "pt")


def test_the_declared_codes_are_the_ones_patch_itself_validates_against():
    """`patch` checks `options["language"] in LANGUAGES`, and this is that set.

    The two are derived from one mapping today; this is what fails if a later
    edit gives `languages` a hand-written literal that drifts from it.
    """
    assert set(WE2002Patcher.languages) == set(LANGUAGES)


def test_the_declared_codes_keep_the_menu_order_and_start_at_the_default():
    assert list(WE2002Patcher.languages) == LANGUAGE_CODES
    assert WE2002Patcher.languages[0] == "en"


def test_the_language_help_names_exactly_the_codes_we2002_ships(capsys, monkeypatch):
    """`--language` has no `choices=`, so its help text is the only list argparse
    ever shows a user, and nothing else keeps it honest.

    Two assertions, one claim each: the first pins the sentence as written, the
    second pins that the codes inside it are the ones the patcher declares. A
    fifth translation added without a help-text edit fails the second; a
    help-text edit that invents a code fails the first. `COLUMNS` is set because
    argparse wraps help to the terminal width, and a narrow one would split the
    list across two lines.
    """
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit):
        build_parser().parse_args(["patch", "--help"])
    help_text = capsys.readouterr().out

    assert "menu language, for a game that ships translations (we2002: en, es, fr, pt)" in help_text
    assert f"(we2002: {', '.join(WE2002Patcher.languages)})" in help_text
