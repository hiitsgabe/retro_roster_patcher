"""What a CLI or IPC consumer sees when the world misbehaves.

Every test here drives `main` and asserts on the exit code and on the NDJSON
stream, because the harm these cover is not "the wrong exception type" — it is a
Dart bridge watching a pipe close with no terminal event. Each one reproduced a
real escape before `core/errors.py` grew its two conversion points; at that
commit the last event on the stream was a `progress` or a `status`, or there was
no event at all.

The two provider tests below are about WHERE in the stream a failure lands, not
about which provider produced it. `fetch` degrades gracefully per team, but only
from the squad loop on: its first two steps — resolving the league and listing
its teams — are outside that loop, and a failure in either used to escape.
API-Football's free-plan and quota envelopes once drove these; ESPN meters
neither, so they are driven instead by the two failures ESPN can produce at
exactly those two points. The event sequences they assert did not have to change
with them, which is the point: a sequence follows where the failure lands, not
which provider raised it.
"""

import json
import os

import pytest

from retro_roster_patcher.cli.__main__ import main
from retro_roster_patcher.sports import _http

from .conftest import events

# ESPN's teams endpoint answers with a list of groups, each holding teams. This
# is that shape carrying none, which is what an id ESPN serves no table for
# returns — a truthy body that parses to zero teams.
_NO_TEAMS = json.dumps({"sports": [{"leagues": [{"teams": []}]}]}).encode()

# 2001 is the Premier League in `ESPN_LEAGUES`; 71 is in no entry of it.
_KNOWN_LEAGUE_ID = 2001
_UNKNOWN_LEAGUE_ID = 71


def _fetch_argv(cache_dir, league_id, season=1990):
    return [
        "--json",
        "fetch",
        "--game",
        "we2002",
        "--season",
        str(season),
        "--league-id",
        str(league_id),
        "--cache-dir",
        str(cache_dir),
    ]


def _serve(monkeypatch, by_endpoint):
    """Answer the provider from a dict of URL fragment -> response body.

    Replaces `_http.default_transport`, which the suite-wide network guard has
    already replaced with a raising sentinel, so this reaches every call site in
    the client without the client knowing. The list of endpoints actually asked
    for comes back, because "which request failed" is half of what findings 13
    and 13b are about.
    """
    asked: list[str] = []

    def transport(url, headers, timeout):
        asked.append(url.split("?")[0].rsplit("/", 1)[-1])
        for fragment, body in by_endpoint.items():
            if fragment in url:
                return body
        raise AssertionError(f"no fixture for {url}")

    monkeypatch.setattr(_http, "default_transport", transport)
    return asked


# -- the provider: a league it does not serve, on fetch's first step ---------


def test_an_unknown_league_fails_before_any_request_is_made(tmp_path, monkeypatch):
    """Pins where it fails, so the two tests below are not about a later call.

    `EspnClient.get_leagues` filters a module constant, so an id outside it is
    refused without a socket. That is the earliest `fetch` can fail, which is
    the position these three tests are about.
    """
    asked = _serve(monkeypatch, {"": _NO_TEAMS})
    main(_fetch_argv(tmp_path / "cache", _UNKNOWN_LEAGUE_ID))
    assert asked == []


def test_an_unknown_league_exits_one(tmp_path, monkeypatch):
    _serve(monkeypatch, {"": _NO_TEAMS})
    assert main(_fetch_argv(tmp_path / "cache", _UNKNOWN_LEAGUE_ID)) == 1


def test_an_unknown_league_ends_the_stream_with_a_terminal_error(tmp_path, monkeypatch, capsys):
    _serve(monkeypatch, {"": _NO_TEAMS})
    main(_fetch_argv(tmp_path / "cache", _UNKNOWN_LEAGUE_ID))
    evts = events(capsys)
    # The whole sequence, not just the last event: the escape this covers left a
    # lone `progress` on the stream, which `evts[-1]["event"] == "error"` alone
    # would not have distinguished from a stream that never started.
    assert [e["event"] for e in evts] == ["progress", "error"]
    assert evts[-1]["type"] == "ApiError"
    assert evts[-1]["msg"] == "League 71 not found for season 1990"


# -- the provider: an empty table, on the step outside the graceful loop ------


def test_a_league_with_no_teams_exits_one(tmp_path, monkeypatch):
    _serve(monkeypatch, {"/teams": _NO_TEAMS})
    assert main(_fetch_argv(tmp_path / "cache", _KNOWN_LEAGUE_ID)) == 1


def test_a_league_with_no_teams_is_a_terminal_error(tmp_path, monkeypatch, capsys):
    """`fetch` degrades gracefully per team, but only from the squad loop on.

    The teams call is outside that loop, so a failure there used to be a
    per-team status message in one place and an uncaught traceback in the other.
    The second `progress` is what distinguishes this position from the one
    above: the fetch got past the league and died on the request after it. The
    `status` between them is the client announcing the request it is about to
    make, which is what tells this apart from a league that resolved and then
    never asked anything.
    """
    asked = _serve(monkeypatch, {"/teams": _NO_TEAMS})
    main(_fetch_argv(tmp_path / "cache", _KNOWN_LEAGUE_ID))
    evts = events(capsys)
    assert asked == ["teams"]
    assert [e["event"] for e in evts] == ["progress", "progress", "status", "error"]
    assert evts[-1]["type"] == "ApiError"
    assert evts[-1]["msg"] == "League 2001 has no teams for season 1990"


# -- the filesystem: a cache directory that cannot be created ----------------


@pytest.fixture
def unwritable_cache_parent(tmp_path):
    """A directory the process may enter and list but not write into.

    `$HOME` is read-only on a stock Batocera install and on Android, and the
    default cache directory hangs off it, so this is a first-run failure rather
    than a contrived one.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the write bit, so the directory would still be writable")
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o500)
    return parent


@pytest.fixture
def any_rom(tmp_path):
    """A readable file, so `cmd_analyze`'s `is_file` guard is not what fails."""
    rom = tmp_path / "garbage.bin"
    rom.write_bytes(b"\x00" * 4096)
    return rom


def test_an_uncreatable_cache_dir_exits_one(any_rom, unwritable_cache_parent):
    denied = unwritable_cache_parent / "c"
    assert main(["--json", "analyze", "--rom", str(any_rom), "--cache-dir", str(denied)]) == 1


def test_an_uncreatable_cache_dir_is_a_terminal_storage_error(
    any_rom, unwritable_cache_parent, capsys
):
    denied = unwritable_cache_parent / "c"
    main(["--json", "analyze", "--rom", str(any_rom), "--cache-dir", str(denied)])
    evts = events(capsys)
    assert [e["event"] for e in evts] == ["error"]
    assert evts[-1]["type"] == "StorageError"
    assert evts[-1]["msg"] == f"Cannot create cache directory {denied}: Permission denied"


def test_an_uncreatable_cache_dir_really_was_not_created(any_rom, unwritable_cache_parent):
    """Otherwise the two tests above could be passing for some other reason."""
    denied = unwritable_cache_parent / "c"
    main(["--json", "analyze", "--rom", str(any_rom), "--cache-dir", str(denied)])
    assert denied.exists() is False


# -- the filesystem: a ROM that passes every guard and then cannot be read ----


def _patch_argv(rom, tmp_path, cache_dir):
    rosters = tmp_path / "rosters.json"
    rosters.write_text(
        json.dumps(
            {
                "league": {"id": 71, "name": "Serie A", "country": "Brazil", "season": 1990},
                "teams": [{"team": {"id": 33, "name": "Team A"}, "players": []}],
            }
        ),
        encoding="utf-8",
    )
    slot_map = tmp_path / "slots.json"
    slot_map.write_text(
        json.dumps([{"slot_index": 0, "team_id": 33, "team_name": "Team A"}]), encoding="utf-8"
    )
    return [
        "--json",
        "patch",
        "--game",
        "we2002",
        "--rom",
        str(rom),
        "--out",
        str(tmp_path / "patched.bin"),
        "--rosters",
        str(rosters),
        "--slot-map",
        str(slot_map),
        "--cache-dir",
        str(cache_dir),
    ]


def test_patching_an_unreadable_rom_exits_one(unreadable_rom, tmp_path):
    assert main(_patch_argv(unreadable_rom, tmp_path, tmp_path / "cache")) == 1


def test_patching_an_unreadable_rom_ends_the_stream_with_a_terminal_error(
    unreadable_rom, tmp_path, capsys
):
    """`validate_rom` is size-only, so the fixture passes it and the copy fails.

    This stream used to end after `Preparing ROM...` with a `PermissionError` on
    stderr and no fourth event, which is the shape a consumer cannot recover
    from: three `status` events and a closed pipe.
    """
    main(_patch_argv(unreadable_rom, tmp_path, tmp_path / "cache"))
    evts = events(capsys)
    assert [e["event"] for e in evts] == ["status", "status", "status", "error"]
    assert evts[-1]["type"] == "RomError"
    assert evts[-1]["msg"] == f"Cannot read or write {unreadable_rom}: Permission denied"


def test_patching_an_unreadable_rom_writes_no_output_file(unreadable_rom, tmp_path):
    """A half-written output would be worse than the crash it replaces."""
    main(_patch_argv(unreadable_rom, tmp_path, tmp_path / "cache"))
    assert (tmp_path / "patched.bin").exists() is False
