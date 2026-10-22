# Kit catalogue

Builds `src/retro_roster_patcher/data/kit_catalog.json`, the home and away kits
`core.kits.default_kits` starts from for the clubs it knows.

1. `clubs.tsv`: rank, name, country and English Wikipedia title of each club
   (the footballdatabase.com world ranking's top 100, then every Brasileirao
   Serie A club; edit it for a new season).
2. `python extract.py`: reads each club's infobox kit fields through the
   MediaWiki API, reads the kit drawings' overlays to tell stripes, hoops,
   halves, a sash or plain apart, and writes `catalog.json` plus review sheets
   (`sheets/`, Wikipedia's drawing beside our render). Downloads are cached in
   `cache/`; nothing from them is kept but colours and a pattern name.
3. `python espn_match.py`: pairs each club with its ESPN team id
   (`espn_ids.json`); check the "check:" lines and the missing ones by hand.
4. Look through the sheets and put corrections in `overrides.json`.
5. `python build_catalog.py --sheet`: writes the catalogue and `final_sheet.png`.

Needs Pillow and network access; run with the project's virtualenv.
