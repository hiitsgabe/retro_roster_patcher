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
them. This tool fetches a current squad from a live sports API, maps each player onto the
attribute scale the game actually stores, and writes the result directly into the binary
team tables of a ROM you already own — so a 1994 cartridge lines up with a 2025 season.

Two games are supported today:

| Game | Platform | Sport | Data source |
| --- | --- | --- | --- |
| NHL 94 | Sega Genesis | Hockey | ESPN or the NHL API, no key |
| Winning Eleven 2002 | PlayStation | Soccer | ESPN, no key (or API-Football with one) |

WE2002 also gets its menus translated out of Japanese — English, Spanish, French and
Portuguese patches are generated and applied as part of the same run.

**This project ships no game data.** It patches a ROM or ISO you supply, and never
redistributes one. The tests build synthetic images byte by byte rather than committing a
real dump.

Zero runtime dependencies — the standard library only, so it drops into an embedded or
sandboxed interpreter without a wheel to build. Usable as a library or as a CLI, and the
CLI speaks newline-delimited JSON so another process can drive it. It was extracted from a
pygame launcher for exactly that reason; a Flutter app over embedded CPython is the other
consumer.

## Install

```bash
pip install retro-roster-patcher
```

## Library

```python
from pathlib import Path
from retro_roster_patcher import get_patcher

patcher = get_patcher("nhl94-genesis")(cache_dir=Path("~/.cache/rrp").expanduser())
data = patcher.fetch(season=2025)
rosters = patcher.map_rosters(data)
result = patcher.patch(
    rom_path=Path("nhl94.bin"),
    output_path=Path("nhl94-2025.bin"),
    rosters=rosters,
)
print(result.output_path, result.teams_patched)
```

## CLI

```
retro-roster list
retro-roster analyze --rom PATH [--game ID] [--cache-dir DIR]
retro-roster fetch   --game ID --season N
                     [--league-id N] [--provider P] [--api-key K]
                     [--cache-dir DIR] [--assets-dir DIR] [--out rosters.json]
retro-roster patch   --game ID --rom IN --out OUT
                     (--season N | --rosters rosters.json)
                     [--league-id N] [--slot-map map.json] [--language CODE]
                     [--provider P] [--api-key K] [--cache-dir DIR] [--assets-dir DIR]
```

`list` reports what each game needs; several flags are optional to the parser and
mandatory to a particular game:

| Game | Needs |
| --- | --- |
| `nhl94-genesis` | No `--api-key` and no `--league-id`. It *refuses* `--slot-map`: it matches each team by its code. |
| `we2002` | `--league-id` for `fetch` and for `patch --season` — neither provider has a default league, and without one both fail with `CapabilityError` before any request goes out. `--slot-map` for every `patch`: the ROM's team slots are unnamed, so there is nothing to match teams against. `--api-key` only with `--provider api-football`; the default ESPN provider needs none. |

`--language` is honoured by games that ship translations — `we2002` takes `en`, `es`,
`fr`, `pt` — and is a usage error on one that does not.

`--assets-dir` is a directory the tool only ever reads. Its one use today is the
community WE2002 menu translation `w202-english.ppf`, which this project does not
redistribute: drop that file in and its menu records are merged into whichever
language PPF `--language` selected. Without it the roster patch still applies and
the menus stay Japanese.

Add `--json` to any command to get newline-delimited JSON on stdout instead of human text:

```json
{"event":"status","msg":"Validating ROM..."}
{"event":"progress","pct":0.42,"msg":"Fetching Boston Bruins..."}
{"event":"partial","data":{}}
{"kind":"patch","output_path":"nhl94-2025.bin","teams_patched":26,"players_patched":598,"event":"result","ok":true}
{"event":"error","type":"RomError","msg":"Not a valid NHL94 Genesis ROM: nhl94.bin"}
```

`fetch` without `--out` adds a `partial` event whose `data` is the whole rosters payload,
since there is no file to point at.

In `--json` mode stdout carries protocol and nothing else; logs go to stderr. Exit codes are
`0` success, `1` typed error, `2` usage error.

API keys come from `--api-key` or `$RETRO_ROSTER_API_KEY`. Only
`--provider api-football` needs one; every other provider is keyless.

League ids are the provider's own, so the same competition has a different id under
each. ESPN's soccer ids run from 2001 (Premier League) through 2016, in
`ESPN_LEAGUES`; API-Football's are its published ids, of which 39 is the Premier
League. An id the chosen provider does not know is reported as
`ApiError: League N not found`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

That run reports `5 deselected`: `tests/test_packaging.py` asserts the import came from an
installed distribution, so it fails against the source tree by design. Running that file on
its own selects nothing and exits `5`. CI's `wheel` job selects it with `-m packaging` after
installing a built wheel.
