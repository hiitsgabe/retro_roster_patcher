"""Every cache file the three clients can write, in one shared directory.

`cli.commands.default_cache_dir` hands the same `~/.cache/retro-roster-patcher`
to every patcher, and every patcher hands it straight to its client, so all
three clients write their `{key}.json` files side by side into one flat
directory. `_load_cache` validates only that what it reads back is a `dict`, so
two clients agreeing on a key would silently serve one provider's payload to the
other and the reader would parse it to an empty list rather than fail.

ESPN prefixes every key `espn_`, the NHL client prefixes `nhl_api_`, and
API-Football prefixes nothing at all (`leagues_`, `teams_`, `squad_`,
`players_`). Reading that off the source is how the reviewer found no collision;
it is not a proof, because a key template is easy to miss by eye and easy to add
later without noticing this constraint exists.

This file executes it instead. Each client's own test module already carries a
`NETWORK_CALLS` table naming every method that reaches the wire, and
`test_the_leak_guard_covers_every_public_member` holds each one complete: it
asserts `dir(client_class)`'s public names *equal* the union of that table and an
`OFFLINE_MEMBERS` set of members that issue no request at all. A method that
caches is by definition a method that requests, so it cannot hide in the second
set. Driving every entry in a table and listing the directory afterwards is
therefore a complete inventory of that client's key namespace rather than a
sample of it, and it stays complete: a new cached method left out of the table
fails that guard before it reaches this one.
"""

import json

import pytest

from retro_roster_patcher.sports.api_football import ApiFootballClient
from retro_roster_patcher.sports.espn import EspnClient
from retro_roster_patcher.sports.nhl import NhlApiClient
from tests.sports.test_api_football import NETWORK_CALLS as AF_CALLS
from tests.sports.test_espn import NETWORK_CALLS as ESPN_CALLS
from tests.sports.test_nhl import NETWORK_CALLS as NHL_CALLS

# One body that every parser in all three clients accepts, so that every method
# in every table reaches its `_save_cache`. The clients disagree about what makes
# a response worth caching — the teams methods cache only what parsed to at least
# one team, the leaders methods only a non-empty stat dict — so the envelopes are
# merged rather than chosen between: `sports` for the ESPN team lists,
# `categories` for ESPN leaders, `skaters` for NHL club stats, `response` for
# API-Football, and `athletes` for the roster parsers.
OMNIBUS = json.dumps(
    {
        "sports": [
            {
                "leagues": [
                    {"teams": [{"team": {"id": 1, "displayName": "Team", "abbreviation": "TOR"}}]}
                ]
            }
        ],
        "categories": [{"abbreviation": "G", "leaders": [{"athlete": {"id": 1}, "value": 1}]}],
        "skaters": [{"playerId": 1, "goals": 1}],
        "response": [{"players": [{"id": 1, "name": "Player", "position": "Midfielder"}]}],
        "athletes": [{"items": [{"id": 1, "displayName": "Player", "jersey": "1"}]}],
    }
).encode()


def _transport(url, headers, timeout):
    return OMNIBUS


def _drive(build, calls, cache_dir):
    """Call every method in a client's network table and list what it cached.

    `cache_dir` is created by the client's own constructor, which every one of
    the three does through `ensure_cache_dir`.
    """
    client = build(str(cache_dir))
    for name, (args, kwargs) in calls.items():
        getattr(client, name)(*args, **kwargs)
    return sorted(path.name for path in cache_dir.iterdir())


def _espn(cache_dir):
    return EspnClient(cache_dir, transport=_transport)


def _nhl(cache_dir):
    return NhlApiClient(cache_dir, transport=_transport)


def _api_football(cache_dir):
    return ApiFootballClient(api_key="dummy-key", cache_dir=cache_dir, transport=_transport)


CLIENTS = [(_espn, ESPN_CALLS), (_nhl, NHL_CALLS), (_api_football, AF_CALLS)]


# A directory per client, so that each list is that client's names and nothing
# else. The shared directory the CLI actually hands them has its own test at the
# bottom of the file.
@pytest.fixture
def espn_files(tmp_path):
    return _drive(_espn, ESPN_CALLS, tmp_path / "espn")


@pytest.fixture
def nhl_files(tmp_path):
    return _drive(_nhl, NHL_CALLS, tmp_path / "nhl")


@pytest.fixture
def api_football_files(tmp_path):
    return _drive(_api_football, AF_CALLS, tmp_path / "af")


def test_every_espn_key_is_namespaced_to_espn(espn_files):
    # The stem before the first underscore, as a set: one element means every
    # file agreed on it. Listing the eleven names instead would pass with the
    # prefix on ten of them and pin the arguments this file does not care about.
    assert {name.split("_")[0] for name in espn_files} == {"espn"}


def test_the_espn_table_wrote_a_file_for_every_method_it_names(espn_files):
    # Without this the assertion above holds vacuously for an empty directory,
    # which is what a client that quietly stopped caching would produce.
    assert len(espn_files) == len(ESPN_CALLS)


def test_every_nhl_key_is_namespaced_to_nhl_api(nhl_files):
    assert {"_".join(name.split("_")[:2]) for name in nhl_files} == {"nhl_api"}


def test_the_nhl_table_wrote_a_file_for_every_method_it_names(nhl_files):
    assert len(nhl_files) == len(NHL_CALLS)


def test_api_football_keys_carry_no_namespace_at_all(api_football_files):
    """The whole of the risk, named exactly.

    API-Football's four stems are ordinary English words that any later client
    for any other sport would reach for first. Nothing prevents the collision;
    what this pins is which four words are taken, so that adding a fifth, or a
    second client that wants one of them, is a decision someone makes rather
    than a file two clients overwrite in turn.
    """
    assert {name.split("_")[0] for name in api_football_files} == {
        "leagues",
        "teams",
        "squad",
        "players",
    }


def test_the_api_football_table_wrote_a_file_for_every_method_it_names(api_football_files):
    assert len(api_football_files) == len(AF_CALLS)


def test_no_two_clients_claim_the_same_cache_file_name(espn_files, nhl_files, api_football_files):
    """The claim the prefix discipline exists to make, stated over real names.

    Pairwise and not one three-way union, so a failure says which two clients
    collided. Set intersection and not a length comparison: the empty set is the
    claim, and it reads as the claim.
    """
    assert set(espn_files) & set(nhl_files) == set()
    assert set(espn_files) & set(api_football_files) == set()
    assert set(nhl_files) & set(api_football_files) == set()


def test_all_three_clients_share_one_directory_without_overwriting_each_other(tmp_path):
    """The arrangement the CLI actually creates, driven end to end.

    `default_cache_dir` is one path for every run of every verb, and each
    patcher passes it straight to its client's constructor, so this is not a
    hypothetical: it is what a machine that has run both games looks like. Every
    method of every client, into one directory, and the count has to be the sum —
    one file fewer is one client's answer sitting under another's name, which
    `_load_cache` would hand back without complaint because its only check is
    `isinstance(data, dict)`.
    """
    shared = tmp_path / "retro-roster-patcher"
    written = []
    for build, calls in CLIENTS:
        written = _drive(build, calls, shared)

    assert len(written) == len(ESPN_CALLS) + len(NHL_CALLS) + len(AF_CALLS)
