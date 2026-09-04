<p align="center">
  <img src="assets/logo.png" alt="" width="132">
</p>

<h1 align="center">Retro Roster Patcher</h1>

<p align="center">
  Patch real-world sports rosters into retro game ROMs.
</p>

<p align="center">
  <a href="https://github.com/hiitsgabe/retro_roster_patcher/actions/workflows/ci.yml"><img src="https://github.com/hiitsgabe/retro_roster_patcher/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.11 | 3.12 | 3.13">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT">
</p>

Retro sports games shipped with the rosters of their release year and no way to change
them. This tool fetches a squad from a live sports API, maps each player onto the attribute
scale the game actually stores, and writes the result into the binary team tables of a ROM
you already own — so a 1994 cartridge lines up with a 2025 season.

| Game | `--game` | Platform | Sport | Providers |
| --- | --- | --- | --- | --- |
| International Superstar Soccer (SNES) | `iss-snes` | `snes` | `soccer` | `espn` |
| Ken Griffey Jr. Presents MLB (SNES) | `kgj-mlb-snes` | `snes` | `baseball` | `espn` |
| NBA Live 95 (Genesis) | `nbalive95-genesis` | `genesis` | `basketball` | `espn` |
| NHL 94 (Genesis) | `nhl94-genesis` | `genesis` | `hockey` | `espn`, `nhl` |
| NHL 94 (SNES) | `nhl94-snes` | `snes` | `hockey` | `espn`, `nhl` |
| Winning Eleven 2002 | `we2002` | `psx` | `soccer` | `espn` |

Every provider is keyless: no credential to supply, and no flag or environment variable for
one. WE2002 also gets its menus translated out of Japanese as part of the same run.

**This project ships no game data.** It patches a ROM or ISO you supply, and never
redistributes one. The tests build synthetic images byte by byte rather than committing a
real dump.

Zero runtime dependencies — the standard library only, so it drops into an embedded or
sandboxed interpreter with no wheel to build. Usable as a library or as a CLI, and the CLI
speaks newline-delimited JSON so another process can drive it. It was extracted from a
pygame launcher for exactly that reason; a Flutter app over embedded CPython is the other
consumer.

## Install

```bash
pip install retro-roster-patcher
```

## Quick start

```bash
retro-roster list
retro-roster analyze --rom nhl94.bin
retro-roster fetch --game nhl94-genesis --provider nhl --season 2025 --out rosters.json
retro-roster patch --game nhl94-genesis --rom nhl94.bin --out nhl94-2025.bin --rosters rosters.json
```

`fetch` and `patch` are separate verbs so you can inspect — or hand-edit — `rosters.json`
before anything touches a ROM. `patch --season N` does both in one run instead.

## Command line

Four verbs: `list`, `analyze`, `fetch`, `patch`. `req` below means the verb refuses to run
without the flag, `yes` means it accepts it, `-` means it does not.

| Flag | `list` | `analyze` | `fetch` | `patch` | Meaning |
| --- | --- | --- | --- | --- | --- |
| `--help` | yes | yes | yes | yes | usage for that verb |
| `--json` | yes | yes | yes | yes | newline-delimited JSON on stdout instead of human text |
| `--game` | - | yes | req | req | patcher id from the table above |
| `--rom` | - | req | - | req | input ROM or ISO |
| `--out` | - | - | yes | req | where to write the rosters file / the patched ROM |
| `--season` | - | - | req | yes | season year, e.g. `2025` |
| `--rosters` | - | - | - | yes | patch from a `fetch` file instead of `--season` |
| `--slot-map` | - | - | - | yes | JSON list of slot mappings |
| `--language` | - | - | - | yes | menu language, for a game that ships translations |
| `--provider` | - | - | yes | yes | data provider, when the game offers more than one |
| `--league-id` | - | - | yes | yes | provider league id |
| `--cache-dir` | - | yes | yes | yes | where caches and generated assets live |
| `--assets-dir` | - | - | yes | yes | directory of user-supplied assets, read only |

`--json` and `--help` are accepted on either side of the verb. `analyze` without `--game`
probes every registered patcher and reports the ones that recognise the file. `patch` needs
exactly one of `--season` and `--rosters`; both, or neither, is a usage error.

### What each game requires

| Game | Requires |
| --- | --- |
| `iss-snes` | `--league-id` for `fetch` and for `patch --season` — there is no default league. `--slot-map` for every `patch`: the ROM's 27 slots are national teams and the data is a club league, so there is nothing to match them by. An 8 Mbit (1 048 576-byte) SNES dump, headerless or with the 512-byte copier header. Two different checks, on purpose: `patch` refuses only a file too short to hold the 296 140 bytes this patcher writes, because that one provably cannot be patched, while `analyze` additionally reports `is_valid: false` unless the file clears the 1 MB floor and all 27 entries of each of three pointer tables — selection-screen names, in-game name tiles and team descriptions — dereference to something the writer could use. That second check is a guess about content, so it never blocks a `patch` the user asked for by name. Neither has been run against a real cartridge. |
| `kgj-mlb-snes` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. A 2 097 152-byte SNES dump, headerless or with the 512-byte copier header — exactly those two sizes and nothing between, the strictest size test of any game here. That number is the ported reader's own and has never been checked against a real cartridge, so if the game is not a 16 Mbit ROM it refuses every genuine dump. The team tables are located by searching the image for a 14-byte marker rather than at a fixed offset, which is what makes the header need no arithmetic. `analyze` reports `is_valid: false` when that marker is missing, and also when it matches within 25 280 bytes of the end of the file, because the 28 team blocks would then run off the end and every write would be silently dropped. |
| `nbalive95-genesis` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. A 2 MB (2 097 152-byte) Genesis dump. `analyze` reports `is_valid: false` unless all 360 player pointers resolve, which rules out every file shorter than 2 064 604 bytes — the ported reader's own 1 572 864-byte floor accepts those and then silently patches nothing for the last twelve teams. |
| `nhl94-genesis` | No `--league-id`. It *refuses* `--slot-map`: it matches each team by its three-letter code. |
| `nhl94-snes` | No `--league-id`, and it *refuses* `--slot-map` for the same reason as its Genesis sibling. An 8 Mbit (1 048 576-byte) dump, headerless or with the 512-byte copier header. `analyze` reports `is_valid: false` for a file whose 28 team blocks it cannot find, which includes every file too short to hold the pointer table 927 207 bytes in. |
| `we2002` | `--league-id` for `fetch` and for `patch --season` — there is no default league, and without one both fail with `CapabilityError` before any request goes out. `--slot-map` for every `patch`: the ROM's team slots are unnamed, so there is nothing to match teams against. |

League ids are the provider's own. ESPN's soccer ids run from 2001 (Premier League) through
2016, in `sports.espn.ESPN_LEAGUES`. An id the chosen provider does not know is reported as
`ApiError: League N not found`.

A slot map is a JSON array of `{"slot_index", "team_id", "team_name"}` objects, which is
what `SlotMapping.to_dict()` produces:

```python
import json
from pathlib import Path

from retro_roster_patcher import SlotMapping

mappings = [
    SlotMapping(slot_index=0, team_id=359, team_name="Arsenal"),
    SlotMapping(slot_index=1, team_id=364, team_name="Liverpool"),
]
Path("slot-map.json").write_text(json.dumps([m.to_dict() for m in mappings], indent=2))
```

### Translations and user assets

`--language` is honoured by games that ship translations — `we2002` takes `en`, `es`, `fr`
and `pt` — and is a usage error on a game that does not, rather than a flag silently
dropped.

`--assets-dir` is a directory the tool only ever reads. Its one use today is the community
WE2002 menu translation `w202-english.ppf`, which this project does not redistribute: drop
that file in and its menu records are merged into whichever language PPF `--language`
selected. Without it the roster patch still applies and the menus stay Japanese.

## Library

Everything the CLI does is a call on a `Patcher`. `list_patchers()` describes what is
registered without instantiating anything:

```python
from retro_roster_patcher import list_patchers

for info in list_patchers():
    print(info.game_id, info.platform, info.sport, info.providers)
```

`get_patcher(game_id)` returns the class; you construct it. The four methods are
`analyze_rom`, `fetch`, `map_rosters` and `patch`, in that order — `fetch` and `map_rosters`
are split from `patch` so a caller can preview, cache or edit between them without repeating
the network step. The block below is not executed by the test suite: it needs a provider and
a ROM.

```python no-run
from pathlib import Path

from retro_roster_patcher import RetroRosterError, get_patcher

patcher = get_patcher("nhl94-genesis")(
    cache_dir=Path("~/.cache/retro-roster-patcher").expanduser(),
    provider="nhl",
)
try:
    data = patcher.fetch(season=2025)
    rosters = patcher.map_rosters(data)
    result = patcher.patch(
        rom_path=Path("nhl94.bin"),
        output_path=Path("nhl94-2025.bin"),
        rosters=rosters,
    )
except RetroRosterError as exc:
    raise SystemExit(f"{type(exc).__name__}: {exc}") from exc

print(result.output_path, result.teams_patched, result.players_patched)
```

`RetroRosterError` heads the hierarchy, so one `except` catches everything this library
reports, including the filesystem errors it converts at its boundaries. Catch a subclass
when you want to react differently: `RomError`, `ApiError`, `MappingError`,
`CapabilityError`, `StorageError`, `MissingAssetError`. Constructors take optional
`on_status` and `on_partial` callbacks and `fetch`/`patch` take `on_progress`; the CLI wires
those to the events below.

`league_data_to_dict` and `league_data_from_dict` are the round trip behind
`fetch --out` / `patch --rosters`, so a consumer can hold rosters in its own store.

### Root exports

`from retro_roster_patcher import ...`

- Errors: `RetroRosterError`, `ApiError`, `CapabilityError`, `MappingError`,
  `MissingAssetError`, `RomError`, `StorageError`
- Registry: `Patcher`, `PatcherInfo`, `get_patcher`, `list_patchers`, `register`
- Game-side models: `MappedRosters`, `PatchResult`, `RomInfo`, `RomSlot`, `SlotMapping`
- Sports models: `League`, `LeagueData`, `Player`, `PlayerStats`, `Team`, `TeamRoster`
- Serialisation: `league_data_from_dict`, `league_data_to_dict`
- Finding a ROM on disk: `RomFinder`, `RomFinderConfig`, `RomFinderResult`
- Also: `Transport`, `__version__`

`from retro_roster_patcher.sports import ...`

- Clients: `EspnClient`, `NhlApiClient`, `Transport`, `team_colors`
- Models: `League`, `LeagueData`, `Player`, `PlayerStats`, `Team`, `TeamRoster`

## The `--json` protocol

With `--json`, stdout carries protocol and nothing else: one JSON object per line, flushed
per line. Human logs and progress go to stderr. This is the surface the pygame launcher and
the Flutter bridge code against.

### Events

Every line has an `event` key.

| `event` | Other keys | Emitted |
| --- | --- | --- |
| `status` | `msg` | when a step begins |
| `progress` | `pct` (0.0-1.0), `msg` | during a fetch or a patch |
| `partial` | `data` | for an intermediate payload worth rendering before the end |
| `result` | `ok` (always true), `kind`, plus the payload's own keys | on success, as the last line |
| `error` | `type` (the exception class name), `msg` | on failure, as the last line |

```json
{"event":"status","msg":"Fetching NHL teams..."}
{"event":"progress","pct":0.42,"msg":"Fetching Boston Bruins..."}
{"kind":"patch","output_path":"nhl94-2025.bin","teams_patched":26,"players_patched":598,"event":"result","ok":true}
```

Guarantees a consumer may rely on:

- Exactly one terminal line per run, `result` or `error`, and nothing after it.
- `status`, `progress` and `partial` appear zero or more times, only before the terminal
  line. `list` and `analyze` emit the terminal line alone.
- `pct` is clamped to `[0.0, 1.0]` and restarts at 0 for each phase, so it is not monotone
  across a whole `patch --season` run — that run fetches and then writes.
- `result` sets `event` and `ok` last, so a payload carrying keys of those names cannot
  overwrite the two a consumer parses on.
- `fetch` without `--out` emits the entire rosters payload as a `partial` before its
  `result`, because there is no file to point at.

### Result payloads

The `kind` key says which payload a `result` carries.

| `kind` | Verb | Payload keys |
| --- | --- | --- |
| `patchers` | `list` | `patchers[]`, each `game_id`, `platform`, `sport`, `requires_slot_mapping`, `providers[]` |
| `rom_info` | `analyze` | `matches[]`, each `path`, `size`, `game_id`, `is_valid`, `slots[]`, `extra` |
| `rosters` | `fetch` | `league`, `season`, `teams`, `players`, `output_path` |
| `patch` | `patch` | `output_path`, `teams_patched`, `players_patched` |

### Exit codes

| Exit | Meaning | On the stream |
| --- | --- | --- |
| `0` | success | a `result` line |
| `1` | a typed error, an interrupt, or a bug in this library | an `error` line |
| `2` | usage error | an `error` line, unless argparse rejected the argv itself |

A consumer that only reads the exit code still learns whether to look at stdout. Two edges
are worth knowing. When argparse rejects the argv — an unknown verb, a missing required flag
— it prints usage as plain text on stderr and exits 2 without writing any JSON, because no
renderer has been chosen yet. And an untyped exception is a bug in this project rather than
something you can act on: the `error` line is written and then the exception is re-raised
unchanged, so a Python traceback follows on stderr and the exit status is CPython's.

## How a player becomes a number

None of these games stores a rating anyone published. Every number below is this project's
own arithmetic over a provider's season totals, and the three do it differently. Some
attributes are measured; several are estimated from position and age, because no feed
publishes them at all.

### WE2002: ranked against the league, 1-9

Fifteen attributes per player. Ten are earned, five are always estimated.

The ten are grouped into categories — offensive, defensive, body balance, stamina, pass
accuracy, shoot power, shoot accuracy, technique, dribble, aggression — each a small formula
over `PlayerStats` fields (`offensive` is goals + 0.7 x assists + 0.3 x shots on target, and
so on). Every player in the *whole fetched league* is pooled, the formula is evaluated for
each, and a player's percentile is the share of the pool scoring strictly below him. That
percentile becomes the rating: 95th and up is a 9, then 85 → 8, 70 → 7, 50 → 6, 35 → 5,
20 → 4, 10 → 3, 3 → 2, and the rest a 1. Position adjustments follow (a midfielder gains a
point of passing, a goalkeeper is capped at 3 for shooting accuracy), then everything is
clamped back into 1-9.

So a 9 means top 5% *of the league you fetched*, not top 5% of the world. Patch the Premier
League and the Chilean Primera División from the same tool and the two ROMs are not on a
common scale.

The five that are never measured — speed, acceleration, jump power, heading and curve — come
from a position base nudged by age. A 24-year-old attacker is quick because attackers are
quick and he is young, not because anyone timed him.

**`unsupplied`, and why zero is not an answer.** Every count on `PlayerStats` is declared
`int` or `float` and is never `None`, so a consumer can do arithmetic without a guard. That
leaves a provider which does not measure duels nowhere to say so except by writing `0` — and
a zero reads as a *measurement*, ranking the player below everyone who was measured and
padding the denominator for everyone who was. `PlayerStats.unsupplied` names the fields
carrying filler rather than data. A player whose provider did not measure a category's
inputs is dropped from that category's ranking entirely, and his rating comes from a
position-and-age estimator instead of a percentile he was never in. ESPN, the only soccer
provider here, reports no duels and no dribbles, which is why body balance, technique and
dribble have estimators and the other seven categories do not.

A player with no stats at all, or with zero appearances, skips the percentiles and takes a
position default adjusted for age: under 23 gains pace and stamina and loses technique; from
31 it goes the other way.

### NHL94: measured against a fixed yardstick, 0-6

Twelve attributes on a 0-6 scale, and no league ranking anywhere. Each stat is scaled
linearly inside a fixed window, so a 40-goal season is a 6 no matter what the rest of the
league did:

| Attribute | From | Window |
| --- | --- | --- |
| Shot power, shot accuracy | goals | 0-40 |
| Pass accuracy | assists | 0-55 |
| Stick handling, offensive awareness | points | 0-90 |
| Defensive awareness | plus/minus | -30 to +40 |
| Roughness, aggression | penalty minutes | 0-80 |
| Agility (goalies) | save percentage | .880-.930 |
| Defensive awareness (goalies) | goals against average | 3.5 down to 2.0 |

A skater's speed, agility, checking and endurance are position defaults, with a single point
of speed and agility added past 50 points; a goalie's are fixed constants, and only the two
rows above are read off his stat line. Weight class is `(pounds - 140) // 8`, clamped to
0-14. A player the provider has no stats for gets the position defaults untouched.

### NBA Live 95: scaled inside fixed windows, 25-99

Sixteen attributes. The scale the ROM stores is 0-99, but the mapper clamps to a floor of
25, so nothing this tool writes is ever rated below 25. Every input is a per-game average
from ESPN's team-leaders endpoint, scaled linearly inside a window and clamped:

| Attribute | From | Window |
| --- | --- | --- |
| Field goals | field-goal percentage | .380-.550 |
| Three-point | three-point percentage | .250-.420 |
| Free throw | free-throw percentage | .600-.920 |
| Stealing | steals per game | 0.3-2.0 |
| Blocks | blocks per game | 0.1-2.5 |
| Offensive rebounding | offensive rebounds per game | 0.3-3.5 |
| Defensive rebounding | defensive rebounds per game | 1.0-9.0 |
| Passing | assists per game | 1.0-10.0 |
| Offensive awareness | points per game | 5.0-30.0 |
| Defensive awareness | 2 x steals + 1.5 x blocks + 0.5 x defensive rebounds | 1.0-12.0 |
| Quickness | 2 x steals + 0.5 x assists | 1.0-8.0 |
| Jumping | 2 x blocks, plus 5 if field-goal percentage is over .500 | 0.5-8.0 |
| Dribbling | 0.8 x assists + 2 x max(0, 2 - turnovers / assists) | 1.0-10.0 |
| Strength | 0.8 x rebounds, plus 2 for a centre or power forward | 1.0-10.0 |

The two attributes missing from that table, dunking and speed, are position constants with
one bonus each: dunking starts at 35 for a point guard through 60 for a power forward and
gains 10 above .520 shooting, and speed starts at 35 for a centre through 75 for a point
guard and gains 8 above 1.2 steals a game. A player the provider has no stats for gets a
whole row of position defaults and none of the windows above.

Height is always a position default — 6'2" at point guard through 6'11" at centre — because
ESPN's roster response carries no height. Weight is ESPN's when it reports one. Experience
is age minus 21, floored at zero. The roster is the top 12 by minutes played, two per
position and then the best remaining.

Two fields are written but never derived, and both are inherited from the code this port
came from. `season_stats` is 17 zeros for every patched player, which erases the 1994 season
line the cartridge shipped with — ESPN's leaders endpoint publishes averages, not the games,
minutes, makes and attempts those fields hold, so there is nothing honest to put there. Skin
tone and hair style are written as 0 for everyone, so every patched player looks the same.

In short: WE2002 grades on a curve, NHL94 and NBA Live 95 against absolute yardsticks, and
in all three the physical attributes are priors rather than measurements.

## Limits

- **Past seasons are not historical squads, except on the `nhl` provider.** ESPN's roster
  endpoints take no season parameter at all; they serve the current squad. `--season 2003`
  against ESPN therefore returns today's players labelled 2003, and reports success. For
  `nhl94-genesis`, `--provider nhl` is the real answer: the NHL API serves squads back to
  1993. For `we2002` and `nbalive95-genesis` there is no second provider, so a past season is
  a present squad with a past label. (Per-player soccer *statistics* are fetched per season
  and are genuinely that season's, as are NBA team leaders; only the squad list is current.)
- **Four patchers, three sports.** A fifth is a `Patcher` subclass and a `@register` line;
  see below.
- **No ROMs, no ISOs, no dumps.** You supply the image.
- **The community WE2002 English menu PPF is not redistributed.** See `--assets-dir`.

## Adding a game

A patcher is a `Patcher` subclass decorated with `@register`. Its package is imported from
`retro_roster_patcher/__init__.py`, at the bottom of the import block, so the decorator has
run by the time anyone calls `get_patcher`. The registry is an in-tree dict, not entry
points: every game here is first-party.

```python no-run
from retro_roster_patcher.core.patcher import Patcher
from retro_roster_patcher.core.registry import register


@register("mygame", platform="snes", sport="basketball", providers=("espn",))
class MyGamePatcher(Patcher):
    def analyze_rom(self, rom_path): ...
    def fetch(self, *, season, league_id=None, on_progress=None): ...
    def map_rosters(self, data, slot_mapping=None): ...
    def patch(self, *, rom_path, output_path, rosters, on_progress=None, **options): ...
```

`register`'s keyword arguments become the capability record `list` reports and the CLI
enforces. Set `requires_slot_mapping=True` if the ROM's team slots cannot be matched
automatically; `Patcher.check_slot_mapping` then rejects both a missing mapping and an
unwanted one.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check .
mypy src tests
```

That test run reports `5 deselected`: `tests/test_packaging.py` asserts the import came from
an installed distribution, so it fails against the source tree by design. Running that file
on its own selects nothing and exits `5`. CI's `wheel` job selects it with `-m packaging`
after installing a built wheel.

`tests/test_readme.py` pins this file against the code: the flag table against the argument
parser, the exported names against `__all__`, the event and payload tables against the
renderer and the command handlers, the games table against the registry, and every Python
example against the interpreter. A claim here that stops being true fails the build.
