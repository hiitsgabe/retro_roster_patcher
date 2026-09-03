"""What a CLI or IPC consumer sees when the world misbehaves.

Every test here drives `main` and asserts on the exit code and on the NDJSON
stream, because the harm these cover is not "the wrong exception type" — it is a
Dart bridge watching a pipe close with no terminal event. Each one reproduced a
real escape before `core/errors.py` grew its two conversion points and before
the three API-Football errors joined the hierarchy; at that commit the last event
on the stream was a `progress` or a `status`, or there was no event at all.

The API-Football payloads below are the shapes the provider really returns for a
free-plan season and for an exhausted daily quota; `_check_plan_error` matches on
`errors.plan` containing "Free plans" and `_request` on a non-empty
`errors.requests`.
"""

import json
import os

import pytest

from retro_roster_patcher.cli.__main__ import main
from retro_roster_patcher.sports import _http

from .conftest import events

_PLAN_RESTRICTED = json.dumps(
    {"errors": {"plan": "Free plans do not have access to this season."}, "response": []}
).encode()

_QUOTA_EXHAUSTED = json.dumps(
    {"errors": {"requests": "You have reached the request limit for the day"}, "response": []}
).encode()

_ONE_LEAGUE = json.dumps(
    {
        "errors": [],
        "response": [
            {
                "league": {"id": 71, "name": "Serie A"},
                "country": {"name": "Brazil"},
                "seasons": [{"year": 1990}],
            }
        ],
    }
).encode()


def _fetch_argv(cache_dir, season=1990):
    # `--provider` is explicit because every failure below is API-Football's own
    # — a free-plan season and an exhausted daily quota are shapes only it
    # returns — and WE2002's default provider is now the keyless ESPN one, which
    # has neither a plan nor a quota. Without the flag these tests would drive a
    # client that cannot produce the condition they are named after and would
    # still exit 1, on a different error.
    return [
        "--json",
        "fetch",
        "--game",
        "we2002",
        "--provider",
        "api-football",
        "--api-key",
        "not-a-real-key",
        "--season",
        str(season),
        "--league-id",
        "71",
        "--cache-dir",
        str(cache_dir),
    ]


def _serve(monkeypatch, by_endpoint):
    """Answer API-Football from a dict of endpoint fragment -> response body.

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


# -- the provider: a season the plan does not cover --------------------------


def test_a_free_plan_season_fails_on_the_first_request_of_the_fetch(tmp_path, monkeypatch):
    """Pins where it fails, so the two tests below are not about a later call."""
    asked = _serve(monkeypatch, {"": _PLAN_RESTRICTED})
    main(_fetch_argv(tmp_path / "cache"))
    assert asked == ["leagues"]


def test_a_free_plan_season_exits_one(tmp_path, monkeypatch):
    _serve(monkeypatch, {"": _PLAN_RESTRICTED})
    assert main(_fetch_argv(tmp_path / "cache")) == 1


def test_a_free_plan_season_ends_the_stream_with_a_terminal_error(tmp_path, monkeypatch, capsys):
    _serve(monkeypatch, {"": _PLAN_RESTRICTED})
    main(_fetch_argv(tmp_path / "cache"))
    evts = events(capsys)
    # The whole sequence, not just the last event: the escape this covers left a
    # lone `progress` on the stream, which `evts[-1]["event"] == "error"` alone
    # would not have distinguished from a stream that never started.
    assert [e["event"] for e in evts] == ["progress", "error"]
    assert evts[-1]["type"] == "SeasonNotAvailableError"
    assert evts[-1]["msg"] == "Season 1990 not available on current plan"


# -- the provider: the daily quota, on a request outside the graceful loop ----


def test_an_exhausted_quota_on_the_teams_call_exits_one(tmp_path, monkeypatch):
    _serve(monkeypatch, {"/leagues": _ONE_LEAGUE, "/teams": _QUOTA_EXHAUSTED})
    assert main(_fetch_argv(tmp_path / "cache")) == 1


def test_an_exhausted_quota_on_the_teams_call_is_a_terminal_error(tmp_path, monkeypatch, capsys):
    """`fetch` degrades gracefully per team, but only from request three on.

    Requests one and two — leagues and teams — are outside that loop, so the
    same provider condition used to be a per-team status message in one place
    and an uncaught traceback in the other.
    """
    asked = _serve(monkeypatch, {"/leagues": _ONE_LEAGUE, "/teams": _QUOTA_EXHAUSTED})
    main(_fetch_argv(tmp_path / "cache"))
    evts = events(capsys)
    assert asked == ["leagues", "teams"]
    assert [e["event"] for e in evts] == ["progress", "progress", "error"]
    assert evts[-1]["type"] == "DailyLimitError"
    assert evts[-1]["msg"] == "You have reached the request limit for the day"


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
        "--api-key",
        "not-a-real-key",
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
