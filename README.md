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

Zero runtime dependencies. Usable as a library or as a CLI.

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
retro-roster analyze --rom PATH [--game ID]
retro-roster fetch   --game ID --season N [--league-id N] [--provider P] [--out rosters.json]
retro-roster patch   --game ID --rom IN --out OUT
                     (--season N | --rosters rosters.json) [--slot-map map.json]
```

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

API keys come from `--api-key` or `$RETRO_ROSTER_API_KEY`.

## Development

```bash
pip install -e ".[dev]"
pytest
```

That run reports `5 deselected`: `tests/test_packaging.py` asserts the import came from an
installed distribution, so it fails against the source tree by design. Running that file on
its own selects nothing and exits `5`. CI's `wheel` job selects it with `-m packaging` after
installing a built wheel.
