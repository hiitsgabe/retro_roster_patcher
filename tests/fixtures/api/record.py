"""Re-record the API responses the sports client tests replay.

The suite never touches the network: every client takes a `transport`, and the
tests hand it a body read from this directory. That leaves two gaps this script
fills. It is the provenance record — the only thing that remembers which URL each
opaque blob of JSON came from — and running it is the one way to notice that a
provider started refusing `_http.DEFAULT_USER_AGENT`, since the replay path never
can.

    .venv/bin/python tests/fixtures/api/record.py                 # all fixtures
    .venv/bin/python tests/fixtures/api/record.py espn_nhl_teams.json

Fetches through `_http.default_transport`, so a recording carries the same headers
the library will really send. Not named `test_*.py`, so pytest does not collect it.
"""

from __future__ import annotations

import json
import pathlib
import sys

from retro_roster_patcher.sports import _http

HERE = pathlib.Path(__file__).parent

# fixture filename -> the URL it was recorded from
SOURCES = {
    "espn_nhl_teams.json": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams",
    # The client asks for /standings/now; this pins a date so re-recording mid-season
    # cannot swap the fixture's team set out from under the tests. Same payload shape.
    "nhl_standings.json": "https://api-web.nhle.com/v1/standings/2025-01-01",
    # The season the client defaults to, so this is the URL `get_hockey_squad("BOS")`
    # really builds — the fixture and the pinned URL in test_nhl.py agree.
    "nhl_roster.json": "https://api-web.nhle.com/v1/roster/BOS/20252026",
    # Soccer, from the same core API the hockey/baseball/basketball leaders calls
    # use. Liverpool (364) in eng.1, season pinned for the reason above. These three
    # are what let WE2002 fetch a squad and its per-player stats without an API key.
    "espn_soccer_roster.json": (
        "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/teams/364/roster"
    ),
    # Enumerates the athletes with stats this season; each entry links its own
    # statistics document, which is the next fixture.
    "espn_soccer_leaders.json": (
        "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"
        "/seasons/2025/types/1/teams/364/leaders"
    ),
    # One athlete's full season statistics: 96 fields across defensive, general,
    # goalKeeping and offensive. This is the document every soccer rating is
    # now derived from.
    "espn_soccer_athlete_stats.json": (
        "https://sports.core.api.espn.com/v2/sports/soccer/leagues/eng.1"
        "/seasons/2025/types/1/teams/364/athletes/304901/statistics"
    ),
}


def record(name: str) -> int:
    """Fetch one fixture, overwrite it on disk, and return the byte count.

    Raises rather than writing if the response is not JSON. A 200 is no guarantee
    of one — an HTML interstitial, a captive portal, and a truncated body all
    arrive that way — and this script's whole job is replacing the file the suite
    depends on. Clobbering a known-good fixture with junk surfaces much later and
    somewhere else, as a bewildering assertion failure in a client test.
    """
    body = _http.default_transport(SOURCES[name], {}, _http.DEFAULT_TIMEOUT)
    try:
        json.loads(body)
    except ValueError as exc:
        raise ValueError(f"not JSON ({exc}); body starts {body[:120]!r}") from exc
    (HERE / name).write_bytes(body)
    return len(body)


def main(argv: list[str]) -> int:
    names = argv or sorted(SOURCES)
    unknown = sorted(set(names) - set(SOURCES))
    if unknown:
        print(f"unknown fixture(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"known: {', '.join(sorted(SOURCES))}", file=sys.stderr)
        return 2
    failed = False
    for name in names:
        try:
            size = record(name)
        except Exception as exc:
            # Keep going: one dead endpoint should not block re-recording the rest.
            print(f"{name}: left unchanged, {exc}", file=sys.stderr)
            failed = True
            continue
        print(f"{name}: {size} bytes from {SOURCES[name]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
