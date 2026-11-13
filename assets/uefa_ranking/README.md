# UEFA club ranking snapshot

Builds `src/retro_roster_patcher/assets/uefa_club_ranking.json`, the snapshot
`sports.clubs.uefa_ranking` falls back to and takes each club's ESPN team id from.

1. `PYTHONPATH=../../src python build.py [season_year] [top]` (defaults: the
   season ending next July or this one, top 200). It fetches UEFA's men's club
   coefficients from comp.uefa.com and the ESPN team lists of
   `sports.clubs.COUNTRY_LEAGUES` and `UEFA_LEAGUES`, and pairs each club with
   the library's own `pair_club`.
2. Look through the "check:" lines: each is a pairing that is not one name for
   one name (Ajax / Ajax Amsterdam), or a club left without an id. A club
   without an id is fine when ESPN has no league for its country and it plays
   no UEFA competition this season (it has no squad to fetch).
3. Put corrections in `overrides.json` (UEFA id -> ESPN id, `""` for none) and
   run it again.

Needs network access; run with the project's virtualenv.
