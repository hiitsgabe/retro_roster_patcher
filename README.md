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
| International Superstar Soccer Deluxe (SNES) | `iss-deluxe-snes` | `snes` | `soccer` | `espn` |
| International Superstar Soccer (SNES) | `iss-snes` | `snes` | `soccer` | `espn` |
| International Superstar Soccer 2000 (N64) | `iss2000-n64` | `n64` | `soccer` | `espn` |
| Ken Griffey Jr. Presents MLB (SNES) | `kgj-mlb-snes` | `snes` | `baseball` | `espn` |
| MVP Baseball (PSP) | `mvp-psp` | `psp` | `baseball` | `espn` |
| NBA Live 95 (Genesis) | `nbalive95-genesis` | `genesis` | `basketball` | `espn` |
| NHL 2005 (PS2) | `nhl05-ps2` | `ps2` | `hockey` | `espn`, `nhl` |
| NHL 07 (PSP) | `nhl07-psp` | `psp` | `hockey` | `espn`, `nhl` |
| NHL 94 (Genesis) | `nhl94-genesis` | `genesis` | `hockey` | `espn`, `nhl` |
| NHL 94 (SNES) | `nhl94-snes` | `snes` | `hockey` | `espn`, `nhl` |
| PES 2013 (PS2) | `pes2013-ps2` | `ps2` | `soccer` | `espn` |
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

## Patch without code: Retro Toolbox

[Retro Toolbox](https://github.com/hiitsgabe/retro_toolbox) is the app for this library, a
graphical way to patch a game with no code and no terminal. Pick the game, the league and
the season, check or edit the teams, choose the screen art, and it writes the patched ROM.
It finds the game file already on your device, works with a gamepad, and runs on Android,
macOS, Windows and Linux handhelds. Get it from its
[releases](https://github.com/hiitsgabe/retro_toolbox/releases) or
[website](https://hiitsgabe.github.io/retro_toolbox/).

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
retro-roster images --game we2002 --rom we2002.bin --out art/
```

`fetch` and `patch` are separate verbs so you can inspect — or hand-edit — `rosters.json`
before anything touches a ROM. `patch --season N` does both in one run instead.

## Command line

Five verbs: `list`, `analyze`, `fetch`, `patch`, `images`. `req` below means the verb refuses
to run without the flag, `yes` means it accepts it, `-` means it does not.

| Flag | `list` | `analyze` | `fetch` | `patch` | `images` | Meaning |
| --- | --- | --- | --- | --- | --- | --- |
| `--help` | yes | yes | yes | yes | yes | usage for that verb |
| `--json` | yes | yes | yes | yes | yes | newline-delimited JSON on stdout instead of human text |
| `--game` | - | yes | req | req | req | patcher id from the table above |
| `--rom` | - | req | - | req | req | input ROM or ISO |
| `--out` | - | - | yes | req | req | where to write the rosters file / the patched ROM |
| `--season` | - | - | req | yes | - | season year, e.g. `2025` |
| `--rosters` | - | - | - | yes | - | patch from a `fetch` file instead of `--season` |
| `--fill-from-other-sources` | - | - | yes | yes | - | soccer: give players the main provider has no head shot for the one FotMob has, matched by birth date and name (a few extra requests per team); FotMob is unofficial, so treat it as best effort. Nothing to add for other sports |
| `--slot-map` | - | - | - | yes | - | JSON list of slot mappings |
| `--language` | - | - | - | yes | - | menu language, for a game that ships translations |
| `--no-logos` | - | - | - | yes | - | draw no team logos: by default `we2002`, `iss-snes` and `iss-deluxe-snes` draw each team's logo as its flag and `pes2013-ps2` as its crest, and `nhl94-snes` draws each team's logo on its menus, at center ice and on the title pucks; with this flag a team keeps the striped (`we2002`) or two-band (`iss-snes`) flag, and `pes2013-ps2`, `nhl94-snes` and `kgj-mlb-snes` (which draws each club's logo on team select, Today's Game and the season screens) the cartridge's or disc's logos |
| `--blank-unused` | - | - | - | yes | - | `we2002`, `iss-snes`, `iss-deluxe-snes` and `pes2013-ps2`: empty the name and flag (crest) of every slot the slot map leaves out; `iss-snes` and `iss-deluxe-snes` blank their player names too. On `pes2013-ps2` with a fill, only the kind of slot the fill leaves alone (clubs with `--fill-nations`, national teams with `--fill-clubs`; not with both) |
| `--fill-nations` | - | - | - | yes | - | soccer: fill the slots the slot map leaves out with today's top national teams by FIFA ranking (see [National teams in the free slots](#national-teams-in-the-free-slots)); not with `--blank-unused`, except on `pes2013-ps2` |
| `--fill-clubs` | - | - | - | yes | - | `pes2013-ps2`: fill the club slots the leagues leave with today's best European clubs by UEFA coefficient (see [European clubs in the free slots](#european-clubs-in-the-free-slots)); `--fill-clubs=champions` takes this season's Champions League clubs first. Goes with `--fill-nations` |
| `--image` | - | - | - | yes | - | `SLOT=PNG`, repeatable: replace a screen's art (`we2002`: `title`, `menu`, `boot`; `pes2013-ps2`, `nhl07-psp`, `nhl94-snes`, `nhl94-genesis`, `kgj-mlb-snes` and `iss-deluxe-snes`: see the per-game table) |
| `--provider` | - | - | yes | yes | - | data provider, when the game offers more than one |
| `--league-id` | - | - | yes | yes | - | provider league id |
| `--cache-dir` | - | yes | yes | yes | yes | where caches and generated assets live |
| `--assets-dir` | - | - | yes | yes | - | directory of user-supplied assets, read only |

`--json` and `--help` are accepted on either side of the verb. `analyze` without `--game`
probes every registered patcher and reports the ones that recognise the file. `patch` needs
exactly one of `--season` and `--rosters`; both, or neither, is a usage error.

### What each game requires

| Game | Requires |
| --- | --- |
| `iss-deluxe-snes` | `--league-id` for `fetch` and for `patch --season`, and `--slot-map` for every `patch`: the slots are the cartridge's 36 national teams (0-35 in the game's team order, Italy first; `analyze` lists them). The USA cartridge only (2 MiB, title SUPERSTAR SOCCER 2), `.sfc`/`.smc`, with or without a copier header. Each mapped slot gets the club's 20: the club's starting XI (see [Soccer lineups](#soccer-lineups)) in the formation nearest the club's own (full backs on the flanks, attacking midfielders forward), then the bench; names in the game's 8-letter menu font (the initial and surname when it fits, `I.Gomes`), the position code and nine skills placed by league percentile onto the cartridge's spread for that position. The cartridge has 16 formations, which team select prints and the match plays; the patch writes one of eight, the shapes real clubs play (`formations`: 4-4-2, 4-3-3, 4-5-1, 3-5-2, 3-4-3, 5-3-2, 5-4-1, 4-2-4). The slot's team record becomes that formation's default, and a club with no formation keeps the slot's own. A place is the record's index and its line comes from the formation, not from the player's shirt number (byte 5 of his skill record): the patch used to follow the numbers and stood a player on the wrong line in 15 of the 36 nations. Each line seats its players on its places by role. Each position code comes from the player's role, kept to one that can play his place's line (the code of a wing back or holding midfielder plays defence or midfield, that of an attacking midfielder or winger midfield or attack), so the game marks nobody out of position. Kits are colours (shirt, shorts and socks for both kits) plus the team's chest design, the nearest of the cartridge's own to the kit's pattern, drawn in its flag colours; `{"template": n}` copies nation n's kit (`kit_templates`, previews by `kit_preview_png`). The shirt numbers take the fixed colour that reads best and the clash class follows the shirt. Team select's team photo wears the home kit: its shirt, shorts and socks colours, the plain or striped shirt by the kit's pattern and its own stripe and collar colour (the secondary). Each club's crest becomes its flag (24x16, three colours; the flags move to bank $AF's free space), its name is redrawn on the scoreboard (32 pixels), in the big team-select letters and on the cup screens, the clubs fill team select's first pages together, and those pages are labelled with the league's name. Main-menu games start with the first two clubs. National squads (league 2021) keep the nation's name, flag and kits. `--blank-unused` empties the nations no club or squad took. Screen art (`--image`, `images`): `konami` (256x224, the Konami logo screen, revealed by its sweeping line; 44 colours), `title` (256x224, the whole title under the sprite lettering, PRESS START and copyright: the brush, ball and photos give way to it; 90 colours) and `menu` (256x256, the wallpaper the menus scroll, 15 colours in 128 tiles, shown darker, and with red and blue swapped on the CPU's team select). New streams take the cartridge's places of the ones they replace and the free runs outside bank $AF; the ROM stays 2 MiB, and a photo that would not fit is drawn in fewer tiles (a status line says how many). Player looks: a team draws its players in two looks, its own (a hair colour and five skin shades in each kit palette) and an alternate one that players with skill-record byte 6 bit 4 wear, plus the keeper's; `patch` measures each player's head shot, picks the pair that fits the team best (the own look the majority's, ISS's rule) and puts each outfield player in the nearer. Any of the cartridge's six skin ramps and six hair colours can be either look (the patch writes their records into bank $89's free space); the close-ups after an own goal and the heads in the team photo follow. A patched player never keeps a cartridge star's hair style, and one without a usable photo counts as the plain default (pale skin, black hair). `patch(looks=False)` leaves the looks as the cartridge had them (library API only). Checked in Mesen 2 through the intro, every main-menu mode and a match, and the looks and team photo on Atletico-MG and Cruzeiro. |
| `iss2000-n64` | `--league-id` for `fetch` and for `patch --season`, and `--slot-map` for every `patch`: the slots are the cartridge's 100 national teams (0-99 in the game's team order, Germany first; `analyze` lists them). The USA cartridge only (game code NISE), `.z64`, byte-swapped `.v64` or word-swapped `.n64`. The patched image stays 16 MiB: rewritten assets are re-compressed (an optimal parse of Konami's LZ, smaller than the cartridge's own) and go back in place or in room the patch freed, because some emulators read only 16 MiB and then draw headless players and broken flags. Team select opens on the clubs' page. Each mapped slot gets the club's 22: the club's starting XI (see [Soccer lineups](#soccer-lineups)) in the cartridge's formation nearest the roster's own (4-4-2, 4-5-1, 4-3-3, 3-5-2, 3-6-1, 3-4-3, 5-3-2; a fetch sets it to the lineup's shape, and an edited one is written too), each line seated on the formation's places by role, side and depth (a full back takes the widest place on his side, a deeper role a deeper place), then the bench by starts; names in the game's 8-character name code (accents dropped), real shirt numbers (ESPN's when it is in 1-63 and not worn already, else the lowest free one), position, height and build (the weight the game shows), and every rating placed by league percentile onto the spread of the cartridge's own players in that position. The team-select AT and DF ratings come from the XI. With `looks` (the default) each player's skin tone, hair colour and a matching face (bearded or bald when the photo shows it) come from his head shot. Each club's name (ESPN's short name when the full one runs past 13 letters) replaces the country's on every menu, team select's region label over mapped clubs becomes their league (as many whole words as fit 12 letters: BRASILEIRAO, PREMIER), and its crest (ESPN logo or `logos/<team_id>.png` under `--assets-dir`) becomes its flag on team select, the lineup screen, the VS banner, the flags in the stands and the match scoreboard; `--no-logos` keeps the flags. Screen art slots `menu`, the wallpaper behind every menu (312x236, 256 colours; World League, Training and Option tint it purple, yellow and green), and `title`, the whole PRESS START screen (320x240 in 15-bit colour, the ISS 2000 logo and copyright lines included; the game draws only PRESS START over it, and shows texels 4-316 x 1-237); a title too busy to fit the room the cartridge has left drops to 256, 128, 64, 32 or 16 colours rather than grow the image. The cartridge's other screens are the menu wallpaper, 3D scenes or small sprites. `national_slot_map` pairs 97 of the 100 nations with ESPN's national teams (league 2021; Latvia, North Korea and Nepal have none), so a caller can fill the slots no club takes with today's squads; a national side keeps the cartridge's name, flag and region label. Kits: each mapped club gets its catalogue kits (or its ESPN colours), or the ones `kits` gives, as colours over the slot's own design: the game draws each team's first and second kit from a 512-byte record of 16-colour palettes (assets 627-635, record k and k+106, k per team measured in the emulator), shirt, shorts, socks and waistband; the patch recolours each part's shading ramp, the shirt's trim in the secondary colour, and a striped design's stripes in the secondary colour or, for a plain kit, the shirt's. The design itself (Argentina's stripes) stays the slot's. Checked on team select and in a match. Checked in Mupen64Plus-Next on team select, the lineup, the Create Player screens, the title and a match. |
| `iss-snes` | `--league-id` for `fetch` and for `patch --season` — there is no default league. `--slot-map` for every `patch`: the ROM's 27 slots are national teams and the data is a club league, so there is nothing to match them by. An 8 Mbit (1 048 576-byte) SNES dump, headerless or with the 512-byte copier header. Two different checks, on purpose: `patch` refuses only a file too short to hold the 297 600 bytes this patcher writes, because that one provably cannot be patched, while `analyze` additionally reports `is_valid: false` unless the file clears the 1 MB floor and all 27 entries of each of three pointer tables — selection-screen names, in-game name tiles and team descriptions — dereference to something the writer could use. That second check is a guess about content, so it never blocks a `patch` the user asked for by name. Run on the retail cartridge in Mesen 2: the patched ROM boots, and the team screen shows the new names and eleven. The eleven is the club's starting XI (see [Soccer lineups](#soccer-lineups)); places the lineup leaves open go to the season's most frequent starters, so a player with no starts never takes a place from one with starts. The cartridge has no formation id: a team's formation is a record of eleven places, and its player k stands in place k. `patch` writes each club the retail record of the formation nearest its own, one of seven (4-4-2, 4-3-3, 3-4-3, 5-3-2, 4-5-1, 3-5-2, 4-2-4; a club with no formation or lineup gets the shape its slot had, or, with `order_as_given`, the 4-4-2 the suggested order is cut for), and prints that shape in team select's label (`Offense 4-4-2`). Each starter takes the place of his role and side: a full back the widest place on his side, a deeper role a deeper place. Each name is the first name's initial and the surname when that fits the eight characters (`I.Gomes`), else the surname; names that would still merge within a team are told apart by first name. Each player's nine abilities (three speeds, kick power, shooting, technique, stamina, defense, offense) are written where the game reads them, scaled into the ranges the original players use; speed comes from position and age, because ESPN measures no pace. Shirt numbers are unique 1-15 per team. The ability layout was checked in Mesen 2 against the four bars of the pre-match Substituting screen. Each team's crest (its ESPN logo, or `logos/<team_id>.png` under `--assets-dir`) becomes its flag, 24×16 pixels in four colours on white, wherever the game shows a flag: team select, the cup's group table, the league's fixtures, Substituting, the VS banner and the match scoreboard (seen in Mesen 2). At that size fine lines and lettering are lost. All 27 flags share 2 688 bytes; when the crests do not fit, the largest crest that saves bytes by losing a colour loses one, down to two, and only when none can does the largest fall back to the two-band flag in the team's colours. Each of those is reported as a status line, and then the number of crests drawn. A team without a logo gets the two-band flag, and so does every team with `--no-logos`. The flag's first palette entry is also the colour of the team's shirt numbers on both kits (seen in Mesen 2), so it holds a colour that reads on every shade of both shirts: the crest's own when it stands out enough (by brightness first, then red-green hue), else the best of the crest's colours and red, yellow, blue, white, black and grey, with a near tie going to the brighter contrast. A crest without that colour gives up the one it misses least, and the flag's other colours take whichever order of the next three entries compresses smallest. Under budget pressure a crest loses colours one at a time, and only a crest that can lose no more falls back; with real crests nothing falls back, but two-colour crests far from the number colour can, on some kits. A team that keeps the cartridge's flag but gets new kits has that flag redrawn the same way. Eight teams (Holland, England, Denmark, Norway, Ireland, Belgium, Austria, Russia) borrowed a partner's flag on the team-select and table screens; each now has one of its own there, in OBJ tiles those screens leave unused. Every mapped team gets home and away kits in colours and a chest design from its pattern (see Kits). `--blank-unused` empties every slot the slot map leaves out: no name on the team-select screen, in its description or on the in-game name tiles, a transparent flag and blank player names; its kit and abilities stay, and it can still be picked and played (seen in Mesen 2 on team select, the International Cup group table, the World Series fixtures and through a match). Player looks: a team draws its fifteen in two looks, its own palette (a hair colour and five skin shades before each kit) and an alternate one that players with b5 bit 6 wear (a skin ramp and a hair colour from shared records in bank $85, through the tables at $81:8D3C and $81:8D74, traced in the code at $80:ADDA), and the keeper a third of his own. `patch` measures each player's head shot (complexion and hair colour, as the other games do), picks the pair of looks that fits the team best, the team's own look always the majority's because the coin toss before kick-off draws the captain in it, and puts each outfield player in the nearer; the keeper gets his own. The looks are the cartridge's own skin ramps and hair colours; an alternate look takes only what a shared record holds (four skin ramps, five hair colours), and the three records nothing on the cartridge points at are given the missing ones. A player without a usable photo counts as the plain default (the lightest skin, black hair). `patch(looks=False)` downloads nothing and leaves the palettes and each slot's bit 6 as the cartridge had them (library API only). Checked in Mesen 2 on the palettes in CGRAM and in a match (Atletico-MG against Flamengo, 2026). |
| `kgj-mlb-snes` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. A 2 097 152-byte (16 Mbit) SNES dump, or the same grown to 4 MiB by an earlier patch, headerless or with the 512-byte copier header, and no other size. The team tables are located by searching the image for a 14-byte marker rather than at a fixed offset, which is what makes the header need no arithmetic. `analyze` reports `is_valid: false` when that marker is missing, and also when it matches within 25 280 bytes of the end of the file, because the 28 team blocks would then run off the end and every write would be silently dropped. Each club's 25 records are its lineup, the bench, five starters and five relievers. The lineup is the regulars, by season starts with each start in the club's last ten games counting as ten more (a catcher, a first baseman, three infielders, three outfielders, a batter ESPN lists at DH competing for the outfield, and the next-most-used non-catcher as DH; an NL club bats eight, sitting the weakest bat by OPS of the first baseman, outfielders and DH, and its pitcher ninth), in the order ESPN's last ten games batted them, which is also the order `suggest_squad_order` gives the editor. Ratings come from each player's full ESPN season line (one request a player, cached) on the cartridge's own scales: BAT from AVG, POW from isolated power, SPD from steals and triples, SPD, CON and FAT from K/9, BB/9 and innings per game (starters FAT 5-10, relievers 2-5, so nobody tires after seven pitches); a rating whose stat is missing, or rests on under 100 plate appearances or 20 innings, takes its position default alone. The cards show the real AVG, HR, RBI, W-L, ERA and saves, and both the batting and the throwing hand are written. A bench short of position players takes spare pitchers as IF with the game's own pitcher batting (2/1/3/3). Names: accents become plain letters, apostrophes go, hyphens stay (on the cards and the derby list; the at-bat label's font draws the hyphen as a period, P.CROW.ARM). Each player has his own look on the Line Up and Bullpen cards and in play, where the cartridge's one look used to go on everyone: his complexion from his ESPN head shot (the face's ITA, cut into the game's six tones; about one face in eight comes out a tone off, mostly between the middle tones), his build from his height and weight (small, average, tall or big at the plate; average, heavy or tall on the mound), and his head, hair colour, stance, arms, bat colour, at-bat animation and delivery a stable pick from his ESPN id among only the values the cartridge's own 700 players use (one past them glitches or hangs the game), hair and facial hair following the skin tone as the cartridge pairs them; a teammate whose card would look like another's is picked again. A player without a head shot, or every player without the `images` extra (said in a status line), gets a complexion weighted like the league's faces. Head shots are about 185 KB each, so a whole league's first patch downloads about 130 MB into the cache. With Seattle or Tampa Bay patched, a #24 on the Mariners or in the AL All-Stars' place no longer gets Griffey's own head and blonde hair (one byte, the jersey compared at $85:D1C7). `patch(looks=False)` keeps each slot's cartridge look (library API only; the CLI has no flag for it). The Home Run Derby's six batters, their cards and the names on its Select Player screen, become each league's three home-run leaders among the patched players (its two pitchers stay). A save made after Team Edit keeps its names for the edited teams, and an all-zero save blanks every name on any KGJ cartridge. Each club plays as today's: its city and nickname (team select, the standings, the season news), its full name on Today's Game (the nickname alone when both run past the 18 characters the screen shows, so DODGERS, GIANTS, PHILLIES, NATIONALS, GUARDIANS, ATHLETICS), its ESPN abbreviation on the scoreboard, and the Athletics' city is SACRAMENTO, where they play from 2025 to 2027 (ESPN gives none). Houston takes Milwaukee's AL slot and Milwaukee Houston's NL slot, so both bat by their league's rule and sit on its team-select page and in its standings; the slot keeps its 1994 ballpark. Each team-select page is in alphabetical order, ST LOUIS read as SAINT LOUIS as the cartridge sorts it (the logo moves with the team), and the three-division standings show today's divisions (the 1993 two-division format is left as it was). Uniforms take the club's ESPN colours over the cartridge's design for the slot: the cap, and every part of the batter the cartridge draws in the cap's colour, in the club's colour; the parts it draws in a second colour (stripes, often the bill, piping) in the alternate colour, or in the club's colour when the alternate is too light to show on a white or grey jersey; the jersey's and pants' whites and greys stay, and a black cap keeps the cartridge's ramp of dark greys. The fielders' cap and sleeves take the club's colour, their trim the alternate, and a jersey the cartridge draws in the cap's colour (the All-Stars', Chicago's black home) the club's colour; the fielders' colours also dress the pitcher on the mound and on the Bullpen cards. Each club's logo, its ESPN one or `logos/<team_id>.png` under `--assets-dir`, is fitted into the game's 40x40, drawn with the other 29 (the All-Stars' included) through one 15-colour palette the patch computes, which costs busy logos some detail (needs the `images` extra), and the season schedule draws them in it too; a patched ROM patched again with the same logos comes out the same, the All-Stars' logos being drawn from the cartridge's own each time; `--no-logos` keeps the cartridge's. Logos and uniforms are stored in the image grown to 4 MiB and the game's archive pointed at them. Arizona and Tampa Bay, who did not exist in 1994, take the two All-Star teams (`ARI` or `AZ` the National League's, `TB` the American League's, so `analyze` lists 30 slots): the All-Star menu's ALL-STAR GAME option reads ARIZONA-TAMPA and plays Tampa Bay against Arizona with their own 25 players (written to blocks of their own in the image grown to 4 MiB, which the game's player reader is pointed at), lineups of nine with a DH (the game always holds it in Baltimore's park, with the DH), the bench, rotation and bullpen, and their names, abbreviations, logos and home uniforms, which they also wear on the road (the cartridge has no road uniform for these two teams); a season's All-Star break plays them too. They have no place in Single Game, Season or World Series: the team-select grid's 28 logos fill the 1 024 tiles a background can address and its menus have no free cell, and the schedules, standings and division tables hold 28 teams. The Home Run Derby, under the same menu, still pits each league's three home-run leaders against each other (patched clubs, Arizona and Tampa Bay included), but its Select Player cards and batters show the two teams' logos and uniforms, which the derby takes from the same team numbers. Without either club in the rosters its All-Star team stays as the cartridge has it, and `patch(all_stars=True)` keeps both (library API only; the CLI has no flag for it); a ROM patched with them keeps them on a later patch without them. Checked in Mesen 2 on both team-select pages, Today's Game, a game (scoreboard, batter and fielders), the season standings and schedule, and the All-Star screens; Arizona and Tampa Bay on the All-Star menu, team select, Today's Game, every Line Up and Bullpen card of both, a game played computer against computer into the fifth inning, and the Home Run Derby (a season's All-Star break was not reached). Screen art slots `title` (the stadium behind the intro swing, the title, every front-end menu and, dimmed, the season and World Series result screen: 30 colours, 256 tiles; the game's logo, PUSH START, Ken Griffey Jr., the menu boxes, the result text and the copyright stay over it) and `boot` (the Nintendo screen at power-on: 90 colours, 848 tiles; the 176 tiles the credits after it and the hidden credits draw from stay). `images` exports PNGs; `--image` stores the pictures in the image grown to 4 MiB and points the game's archive at them, the cartridge's own left where they are, so a patched ROM can be patched again; an image whose art entries are neither the cartridge's nor a patch's is refused. Checked in Mesen 2 with photos at those budgets on the boot and title screens, the intro, every menu, the hidden credits, team select, Today's Game and a game, and, reached only by jumping to them, on the result screen and on a screen ($85:C4E2) that shows the game's logo over the stadium, probably before a game. |
| `mvp-psp` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. An **uncompressed** ISO 9660 disc image, and at least 686 122 913 bytes — `database.big` sits at a fixed sector 334 832 and runs for 386 977 bytes, so a shorter file does not contain the data at all. Two different checks, on purpose: `patch` refuses only a file that fails that arithmetic, because it provably cannot be patched, while `analyze` additionally reports `is_valid: false` unless the compressed section headers are where this game puts them *and* the disc's own `team` table carries at least one of MVP Baseball's 30 team ids. That second check is a guess about content, so it never blocks a `patch` the user asked for by name. A patch can still fail late, and loudly: each of the 19 database sections has a fixed allocation with no length word, so if a rebuilt table compresses larger than the one it replaces the run raises rather than silently keeping the original. Neither check has been run against a real disc. |
| `nbalive95-genesis` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation, Toronto and Memphis taking the East and West All-Stars' slots (New Orleans has none: the Slammers are a custom team). Also writes team names, team-select ranks, crests as logos (`--no-logos`), skin and head from each head shot, the five starters' intro portraits from their head shots, and each player's real season totals. A 2 MB (2 097 152-byte) Genesis dump. `analyze` reports `is_valid: false` unless all 360 player pointers resolve, which rules out every file shorter than 2 064 604 bytes — the ported reader's own 1 572 864-byte floor accepts those and then silently patches nothing for the last twelve teams. |
| `nhl05-ps2` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. An **uncompressed** ISO 9660 disc image. Two different checks, on purpose: `patch` refuses a file whose ISO 9660 directory places `DB.VIV` past the end of it, because a truncated archive silently decompresses short and would be written back to the disc corrupted, while `analyze` additionally reports `is_valid: false` unless `DB.VIV` is a BIGF archive holding an `nhl2005.tdb` that decompresses to a TDB. That second check is a guess about content, so it never blocks a `patch` the user asked for by name — `patch` reaches the same fact with a better message, naming the archive's actual file list. Patches the 30 NHL club slots, and Seattle and Vegas take the East and West All-Star sides, which Play Now lists right after Washington (exhibition games, at the All-Star game's arena). Each club gets its whole squad, in the given order, as many as the 30 to 40 roster rows the disc gives it hold (a goalie only in a goalie's row); Seattle and Vegas, which play exhibitions only, take the rows the clubs leave and are cut first, down to a dressed lineup of 2 goalies and 18 skaters, before any club loses a player. Free Agency's 120 places show the players a team had no row for, then today's NHL free agents, from the NHL API's player search on either provider (the players it shows without a team whose last season is this one or the last, best first by NHL games, those without one last and rated from an empty line, retirees among them since the search does not tell them apart; on the NHL API only when the season asked is the current one); a place no current player reaches keeps the disc's 2004 free agent, since Season mode breaks on a shorter list (checked in PCSX2). The disc's All-Star rows point at NHL players' bios, so the two sides play on roster rows the clubs left, whose bios no other row, free agent or minor-leaguer shows; their names change in the team table and, by string id, in the front end's text in all five languages. Writes each player's ESPN head shot as his portrait (needs Pillow), his flag, birthplace and age, and his skin tone measured from the head shot (`SKIN`; a plain pale default without a photo, an East Asian citizenship Richard Park's own shade). The game draws a player's face from the texture his `ART_` names, not from `SKIN`, so each player also gets the disc's generic face toned to his shade over his `GAMEDATA/FACES.VIV` texture, thumbnail included, and the generic head it is unwrapped for (on the old player's head it lost its eyes) (`patch(looks=False)` keeps the disc's faces and tones); players past the new squad leave the team. Each club's current crest goes over its four front-end logos, and Seattle's and Vegas's over the All-Star sides' three (`patch(logos=False)` keeps the disc's; on the NHL API, which sends no crests, each club takes ESPN's for its code); the Thrashers and Coyotes slots take the Jets' and Mammoth's names, arenas, menu text (the front end's own abbreviation too, `PHO`, and the `TEAM_ABBR_PHX` the scoreboard looks up), wordmark and crest at centre ice; the Jets wear the Winnipeg Jets classics the disc files under the Coyotes (white away, blue home, blue third: their jerseys' archive entries point at the classics' data and their menu pictures are redrawn), and Utah wears two jerseys drawn from the Coyotes' own, brick red turned black with Utah's crest on the chest and shoulder, numbers alike, and offers those two alone (`patch(jerseys=False)` keeps the disc's jerseys); Seattle and Vegas play on the 2005 All-Star game's centre ice instead of Atlanta's; `patch(images={slot: png})` replaces the `boot`, `title`, `legal` and `menu` screens (`export_images` writes the disc's own); the menu and title pictures are EA's packed 8-bit shapes and the faces its tighter 4-pixel packing, both decoded in `formats/ssh.py`. The game loads the roster saved on the memory card at boot, over the disc, so the patched executable saves in a folder of its own (`BASLUS-21001` plus three characters from the new `DB.VIV`): a save made with the retail disc or another roster is never loaded over the new one. Checked in PCSX2 against the retail US disc. |
| `nhl07-psp` | No `--league-id`, and it *refuses* `--slot-map`: it matches each team by its abbreviation. An **uncompressed** ISO 9660 disc image — a `.cso`, `.zso`, `.jso` or `.dax` is refused by name, with a message saying to decompress it, rather than reported as the wrong game. Two different checks, on purpose: `patch` refuses a file whose ISO 9660 directory places `db.viv` past the end of it, because a truncated archive silently decompresses short and would be written back to the disc corrupted, while `analyze` additionally reports `is_valid: false` unless `db.viv` is a BIGF archive holding an `nhlbioatt.tdb` that decompresses to a TDB. That second check is a guess about content, so it never blocks a `patch` the user asked for by name. Checked on a real disc (`ULUS10131`) and played in PPSSPP: ratings go in on the game's own 0-49 scale (shown as 50-99), weight as pounds above 150 and height as inches above 54. `db.viv` is written in place, or rebuilt compactly when a table outgrows its slot, and the game's cached copy of its directory (`db.bh` in `PRELOAD/FAPT.VIV`) is rewritten to match. Each written player's portrait in `FE/PORTAPT.VIV` gets the provider's head shot (NHL API or ESPN), or the game's own silhouette when there is none (needs the `images` extra; `patch(portraits=False)` keeps the disc's). Screen art slots `boot`, `title`, `menu`, `logo`, `player`, `loading`, `league_badge`, `players_badge` (`images` exports PNGs, `--image` writes every copy of a slot in place, with fewer colours when it would not fit). `patch(texts={id: text})` replaces front-end strings by id; none by default, so EA's copyright notice on the title stays as the disc has it (its field holds only those two lines). The Thrashers, Coyotes and East and West All-Star slots play as the Jets, Mammoth, Kraken and Golden Knights: their names in the team table, the Jets' and Mammoth's arenas, and their names in the menus' text in English, French and German (`FE/LOCAL.VIV` rebuilt, its cached directory `local.bh` rewritten); the abbreviation stays, since the disc names the club's files by it. Each club's current crest goes over its two front-end logos in `FE/TLOGOAPT.VIV` (64x64 and 32x32, in place; ESPN only, needs the `images` extra; `patch(logos=False)` keeps the disc's), Seattle's and Vegas's over the All-Star sides'. Checked in PPSSPP: Roster Management and Play Now show Winnipeg, Utah, Seattle and Vegas with their crests, and a game shows VGK on the scoreboard; the All-Star sides still wear the All-Star jerseys. The game loads its one roster save ("Roster and Settings 1.0") at the title screen, over the disc's rosters, and its EBOOT is encrypted, so the save folder cannot be moved as on NHL 2005: a save made with another roster must be deleted, or replaced in the game with Rosters > Set Default Rosters, then Save Rosters. Known gaps: the All-Star slots (Seattle, Vegas) share bio records with NHL players, and salaries and career stats stay the previous player's. Each written player's complexion is read from his head shot through the same measure the other games use (the lightest without one; `patch(looks=False)` gives everyone that, library API only) and written twice: as the bio's `SKIN` (1, 2, 3, 6, 5 from lightest, as the disc's own faces order them) and as his face, because the game draws the 3D head from the texture the bio's `ART_` names in `GAMEDATA/FACES.VIV` (`FACE<ART_>.msh`, 64x64), not from `SKIN`: the disc's generic face (`FACE9999.msh`) toned to the cheek colour the disc's own faces have for that shade, so no 2006 face stays on a new player (needs the `images` extra). A bio whose `ART_` other bios share, or that has no face of its own, keeps the face it has; on the retail disc 583 of 792 patched players got one. Checked in PPSSPP in the Player Editor's 3D head. ESPN's hockey head shots frame the head small, and a full beard can read a pale face a shade or two dark. |
| `nhl94-genesis` | No `--league-id`. It *refuses* `--slot-map`: it matches each team by its three-letter code. A 1 MiB dump of the retail cartridge; a patch that writes screen art or player cards grows it to 2 MiB (the battery RAM stays where the cartridge maps it, from 0x200001). Each slot takes up to 23 players (2 goalies, 14 forwards, 7 defencemen) as far as the space its old roster took allows, names cut to 14 characters. An odd-length name ends in a NUL and a roster in the word 2, as on the cartridge, because the game walks a roster with word reads (without them a patched team crashed Matchups and Player Stats), and the line tables count players from 1 (a patched team used to play each place one player off). Every rating comes from the player's own season, ranked against his group (forwards, defencemen, goalies) among every team fetched and set to the value at that rank of the cartridge's own spread of it: shot power from shots, shot accuracy from goals over shots, stick handling from goals, passing from assists, offensive awareness from points, defensive awareness from plus/minus and blocked shots, checking from hits (size with `--provider nhl`, which counts none), endurance from ice time, roughness from major penalties (the position's default with `nhl`), aggression from penalty minutes, and speed and agility from points with youth and lightness; a goalie, in the game's own goalie fields, gets agility and the four save zones from save %, defensive awareness from goals-against, puck control from wins and speed (3 or 4) from youth. Counting stats go per game, pulled toward the group's rate by 20 games, and a stat the line lacks keeps the position's default. Each written player's card (the black-and-white photo Game Setup cycles for the Best line, Matchups shows for the starters and the Player Cards screen lists) is drawn from the provider's head shot or, without one, is the game's own drawing for his role and hand (`patch(looks=False)` gives every player the drawing, with no download; head shots need the `images` extra); teams not patched and the All-Star teams keep the cartridge's cards, and a team an earlier patch drew that this one leaves out gets drawings. Screen art slots `title` (256x224, the picture behind the NHL and NHLPA shields, the NHL HOCKEY 94 letters and the credits, which stay: 3 palette rows less the colours those draw with, 526 tiles) and `menu` (320x224, Game Setup's rink behind the team names, logos and options box: 15 colours, 240 tiles). `images` exports PNGs; `--image` writes each picture into the grown image and points the game's loads at it, the cartridge's own left in place, so a patched ROM can be patched again. Checked in genesis_plus_gx on the title, Game Setup, Matchups, the line editor, Player Stats and a game. |
| `nhl94-snes` | No `--league-id`, and it *refuses* `--slot-map` for the same reason as its Genesis sibling. An 8 Mbit (1 048 576-byte) dump, headerless or with the 512-byte copier header. `analyze` reports `is_valid: false` for a file whose 28 team blocks it cannot find, which includes every file too short to hold the pointer table 927 207 bytes in. Each slot holds two goalies and as many forwards and defencemen as the cartridge's slot (up to its counts), and its header counts what was filled; a re-patch offers the cartridge's counts again, however short an earlier patch left a slot. A team with fewer than two goalies is skipped with a status line and its slot keeps the cartridge's team. The starting goalie is the one who played most (games on the NHL API, wins on ESPN), then the better save %; a goalie on none of ESPN's wins lists counts as one game, so the backups rank and rate by their own numbers. Goalies are written in the game's own goalie fields: agility and the four save zones from save %, awareness from goals-against, both pulled toward an average goalie by ten games' worth so a backup's one perfect night does not rate him a star, and puck control between the two, so a starter rates about 40 to 100 like the cartridge's. A skater missing from one of ESPN's top-25 leader lists takes that list's lowest value, and a stat no list has keeps his position's default. Names are folded to ASCII and kept whole up to the 19 characters the game shows: the 28 team blocks are rebuilt in bank $9C, with the padding that ends the bank (every one of the game's team readers is fixed to that bank, so the blocks cannot move into the grown image), and only if that ran out would the longest names lose their first names to an initial. Each patched team's header also gets the nibbles the game simulates the night's other scores from (attack from goals for, leakiness from goals against, ranked against every club fetched: ESPN's standings, the whole league, or the NHL API's club stats; when there are none a status line says the cartridge's are kept), its 1993 power-play and home-ice bonuses set to 0, and the team overall Matchups shows recomputed from the written Best line. `patch` also fixes the cartridge's hot/cold bug, two bytes at $9F:D23A and $9F:D23E: a player having a cold night had each lowered attribute maxed at 15 instead; `patch(hot_cold_fix=False)` keeps the retail behaviour (library API only; the CLI has no flag for it). Each mapped team plays as today's club, grown into a 2 MiB image: its city, nickname, arena (Ron Barr's welcome) and ESPN abbreviation (the in-game score) in its team block, its city on the coloured banners, banners and uniforms in its ESPN colours (home white with stripes in the team's colours, away in its colour), and its ESPN logo, or `logos/<team_id>.png` under `--assets-dir`, on Game Setup, Player Select, Matchups and the pause screens (48x32, 15 colours), at center ice and on the title-screen pucks (in the rink's colours, its white left to the ice; logos need the `images` extra); `--no-logos` keeps the cartridge's logos. Arenas come from a table in the library, since ESPN's are wrong for some clubs. Seattle and Vegas take the two All-Star slots, which play exhibition games only, and the 32 playoff brackets are rebuilt with today's conferences (Colorado in the West, Detroit and Toronto in the East). A slot no team is mapped to keeps its 1994 identity, and an image without the cartridge's tables keeps every team's. Screen art slots `boot` (the EA SPORTS screen, held still: 159 colours, 255 tiles, none flipped), `title` (the whole title screen under the credits and the two shields: 8 palette rows of 15 colours, 318 tiles; its zooming shields and pucks go) and `menu` (the wallpaper behind Game Setup, Player Select, Matchups, the playoff, shootout and password screens and every pause screen: 15 colours, 355 tiles; the game copies the top-left 56x56 logo box over the top-right one, the old blue wallpaper drawn into Ron Barr's outline on Matchups takes the picture's colours, and the playoff bracket keeps the cartridge's wallpaper). `images` exports PNGs; `--image` writes the pictures into the image grown to 2 MiB and points the game's loads at them, the cartridge's streams left where they are, so a patched ROM can be patched again. Each written player's portrait (Game Setup, Matchups, Edit Lines) gets the provider's head shot, in the 544 KiB of the image grown to 2 MiB that team identity and screen art leave: room for about 850, so every player on today's rosters gets his face (632 of 638 on ESPN's 2026-27 rosters; the other 6 have no photo), and were it ever full the faces go first to every team's Best line (the six Game Setup and Matchups show), then to each team's next player in turn; a player without a head shot (the NHL API's grey silhouette counts as none) gets the game's own drawing of a skater or a goalie, so no 1993 face stays on a new player (head shots need the `images` extra; `patch(portraits=False)` keeps the cartridge's, sending only an earlier patch's faces back to the drawing). Faces and drawings are drawn in the slot's portrait palette: the cartridge's own, and for a mapped team a jersey in its uniforms' colours (white, its colour, its alternate), Seattle and Vegas each on their own instead of the All-Stars' shared one. A re-patch redraws the faces of the teams it maps; the teams it leaves out get drawings. Checked in Mesen 2 on all those screens, the attract mode and a match. |
| `pes2013-ps2` | `--league-id` for `fetch` and for `patch --season` — there is no default league. `--slot-map` for every `patch`: the 139 club slots are named after the game's own clubs (licensed or not), and `analyze` lists them so a league can be laid over any of them. An **uncompressed** ISO 9660 disc image of the US release (`SLUS_219.55`); `analyze` reports `is_valid: false` unless the boot file, `0_TEXT.AFS` and `OVER.AFS` are all in the root directory. A patched club gets its name and three-letter code in every language overlay, its ESPN logo, or `assets_dir/logos/<team_id>.png` when the patcher was built with an `assets_dir`, drawn as its crest (64x64 and 32x32 atlases, each drawn through one palette shared with the flags, so the flags are redrawn through the new palette too; `--no-logos` keeps the originals), and the names and shirt numbers of the players its squad already points at: the club's starting XI (see [Soccer lineups](#soccer-lineups)) comes first, then the bench by position group, and the record's abilities are left as they were. A club or national team with a formation is written one of the 12 presets the PES editor lists over its record in `0_TEXT.AFS` entry 53: six three-line shapes (4-4-2, 4-3-3, 4-5-1, 3-5-2, 3-4-3, 5-4-1), the variant of the shape chosen by the players' roles. The first eleven squad places are the preset's places: the keeper stands in place 0, the ten outfield players are cut into the preset's lines by index, and each line is seated on its places by role, side and depth. Club slots 130 and up have no formation record, as with kits; their formation is kept and a status line says so. A team with no formation is placed as before: each starter takes the squad place whose record holds the position his lineup code gives him (a winger WF, a second striker SS). A written player's registered position comes from his role (a wing back is WB, a winger WF) and is his only playable one. The display name is the full name when it fits 15 characters, the shirt name the surname, as on the disc. The league itself is renamed and gets its ESPN logo on the team-select screen for every slot group (England 0-19, France 20-39, Italy 40-59, Netherlands 60-77, Spain 78-97) that received a team (the names are found relative to the untouched "Ligue 1" string, so patch an original disc, not a patched one). National teams are slots `1000` and up (`analyze` lists the 67 named ones; `national_slot_map` pairs them with league 2021, international friendlies, by code then name); a national squad takes over only records no club squad shares and leaves the places it cannot fill empty, and when the squads ask for more such records than the disc has, every squad is cut to the same size (17 for 60 nations over a Premier League patch). A record a patched club shares with a national row is copied for the row first, so the nation keeps its player. With `--fill-nations` the 60 nations' rows (not the seven Classic ones) take today's top nations: a nation the disc has a row for stays in it with the disc's name, flag and kits, and any other takes a free row with its own name, code and flag from the disc's list of 144 nationalities (in every language) and kits in its own colours. With `--fill-clubs` the club slots the leagues leave take today's best European clubs (see [European clubs in the free slots](#european-clubs-in-the-free-slots)): slots 98-125 first (the clubs of no league group, the Champions League's "Other leagues A" and "B"), then the slots of every league group no league takes, then 126-129 (shown in Exhibition only); never the hidden Master League sides 130-138. A club the disc has (Bayern in 104, Barcelona in 80) goes back to its own slot, and a club whose country's league group is free goes into that group; a fill never renames a league group. The Champions League mode offers every club of slots 0-125; its first group, "UEFA Champions League", is 18 more rows of the overlays' team table (326-343) that carry a club's slot (Man Utd 7 ... Shakhtar 124) and wear that club's UCL kits (`0_TEXT.AFS` entry 54 part 7). Every patch points those rows at their club's new name in every language and copies the club's new kits over its UCL kits, so the group shows whatever club the patch put in those slots. Several leagues go in at once by merging their `MappedRosters` (`a.merge(b)`). `--blank-unused` empties the name, code and crest of every club the mapping left out, the name and icon of every league group without a team, and the name of every national team left out (clubs only when clubs were mapped, nationals only when nationals were); `patch(blank_unused="clubs")` or `"nations"` blanks one kind only, which is what the CLI passes alongside a fill. Kits: every mapped club and national team gets home and away kits drawn from colours (`retro_roster_patcher.core.kits.KitSpec`: shirt, secondary, pattern, shorts, socks, sleeves), by default plain kits in the provider's two team colours; `patch(kits={slot: {"home": spec, "away": spec}})` overrides a slot, a spec being a `KitSpec`, its dict or `{"image": png}` (a picture of the kit, read for its colours and pattern by `kit_from_image`; a 512x256 picture for a team with a licensed kit is instead painted into that kit's texture package, re-encrypted, and the kit stays licensed; `{"template": n}` is refused, the disc has no jersey designs to pick from), and `kits=False` keeps the disc's. The disc's shirt template draws vertical stripes and contrasting sleeves, so `stripes`, `pinstripes` and `hoops` become stripes and `halves`, `sash` and `chevron` plain shirts with contrasting sleeves; a kit so written is parametric, drawn from the colours even for a club whose shirt was a licensed texture. Club slots 130 and up have no kit record. Screen art slots `title`, `menu`, `loading`, `match` are UI textures (`images` exports PNGs, `--image` writes them with their own 256-colour palette). A rebuilt entry may run into the sector padding after it but never changes its size field, because the game hangs at boot when an entry's size changes — checked on a real disc in PCSX2. Real names compress worse than the disc's, so when the player entries outgrow that room the records no squad points at are blanked (names and abilities) and the national squads take fewer of them. Each written player gets the generic built face (byte 102 bits 0-1: a face model, licensed or not, ignores skin and hair) with his complexion, hair colour and beard read from his head shot: the complexion (byte 115 bits 0-2, the editor's "Skin Color" 1-6) matched to the closest of the six values the disc draws (the disc's own regional face stays with the nationalities it uses it for), a plain short hairstyle (a stable pick of four, or bald; bytes 92-93, the licensed special-hairstyle flag in byte 99 bit 6 cleared, since a special hairstyle ignores the colour), the hair colour in RGB (byte 94 and bytes 102-104) and a short full beard or none (byte 95, its colour the hair's). The layout is PES 6's (the COFPES option-file editor's), each field checked in PCSX2's Edit Player. A light beard is often missed rather than a clean face given one. Without a usable photo one plain default (pale skin, black hair, no beard); `patch(looks=False)` gives everyone that default (library API only). A player who is on the disc already keeps the disc's own look for him wherever he plays now, licensed face included: the packaged `pes2013_disc_players.json` pairs 244 players with their disc records, 199 in the main table and 45 among the 2012 South American clubs of the Copa Libertadores (`0_TEXT.AFS` entries 48 and 49, 943 records of the same layout, 38 squads of 25), built offline in October 2026 from the disc and ESPN: the same name, nationality (byte 112) and age (byte 113), and a 2010-2014 club in ESPN's team history that is the disc club holding the record; only matches unique on both sides kept (the first generator is in the history of commit 10639e7). A patched player it names gets that record's look region (bytes 92-123) copied over instead of a look read from his photo (seen in PCSX2: Willian Arao at Santos with the face the disc built for him at Corinthians). Checked in PCSX2 on a patched Brasileirão club in Edit mode. |
| `we2002` | `--league-id` for `fetch` and for `patch --season` — there is no default league, and without one both fail with `CapabilityError` before any request goes out. Patch an original disc: the community English translation writes absolute offsets, so on another dump or a disc patched before (its screen files were rebuilt) it would corrupt the menu art; the patcher notices (the screen files no longer parse), restores the output from the input and reports the translation as skipped. `--slot-map` for every `patch`: the ROM's team slots are unnamed, so there is nothing to match teams against. Each team gets 23 players: a national slot holds them all, and a Master League club (slots 0-31) fields the same 23, its squad links pointing at the national slot of the same number; the managed club (the game's default team) keeps the game's default squad. The club's starting XI (see [Soccer lineups](#soccer-lineups)) takes the first 11 places: the keeper, then the ten outfield players cut into the formation's lines by index and each line seated on the formation's places by role, side and depth; a second keeper sits. The formation is one of the disc's 16 presets, written into the national slot's record and the Master League club's: eight shapes (4-5-1, 4-4-2, 4-3-3, 3-6-1, 3-5-2, 3-4-3, 5-4-1, 5-3-2), each with one holding midfielder (A) or two (B, when the eleven hold two or more `CDM`). A team with no formation keeps the slot's own preset, and its eleven fill a 4-4-2's places back to front by the positions the lineup gives them. Places the lineup leaves open, or every place when ESPN has no lineup, go to the season's most frequent starters. The bench takes a keeper and one player from each line in turn, up to 2 keepers, 3 defenders, 2 midfielders and 4 forwards, so the first subs cover every line however the eleven was coded; the best of the rest take the last place. A player's position (the byte the game shows) comes from his role: SB for a full back or wing back, DH for a holding or central midfielder, SH for a wide midfielder, OH for an attacking midfielder, CF for a striker and WG for a winger; a player with no role has his squad group's. Shirt numbers are the players' real ones, 1-32 and all different, in the national slot's table and the Master League club's row: a number already taken, above 32 or missing gets the lowest free one. Kits: every mapped team gets home and away kits drawn from colours (`retro_roster_patcher.core.kits.KitSpec`: shirt, secondary, pattern, shorts, socks, sleeves), by default plain kits in the provider's two team colours, painted into the team's 3D kit texture (`TEX_nn.BIN`) and its 2D team-select preview; `patch(kits={slot: {"home": kit, "away": kit}})` overrides a slot, a kit being a `KitSpec`, its dict, `{"image": png}` (the picture itself is mapped onto the texture: chest, sleeves, shorts, socks) or `{"template": n}` (one of the disc's 95 jersey designs, which `kit_templates` lists with a preview of each), and `kits=False` leaves the textures and previews as the disc has them. A spec or picture is painted over a plain white template (Classic England, or the design named) so the texture keeps its folds and seams: `stripes` are 4 px bands running on across the front, sides and sleeves, `pinstripes` a 1 px line every 4, `hoops` 4 px bands down each piece, `halves` the right half of the front with the right sleeve and side, `sash` a diagonal band across the front, `chevron` a V across the chest and `sleeves` contrasting sleeves; the collar always takes the contrast colour, the goalkeeper's half of the page and the number font keep their colours, and a file is held under the 36 864 bytes the game loads of it. Needs Pillow; without it a slot takes the whole texture of the team whose shirt colour is nearest, as before. Each player's look comes from his head shot (needs the `images` extra): his complexion and hair colour are read from the photo and matched to the closest of the values the disc draws (the disc's own regional face stays with the nationalities it uses it for), a shaved head gets the closest crop, height and build follow the provider's height, age the provider's, and hair style and beard are a stable pick spread like the disc's own players; a player without a usable photo gets one plain default look, never the disc's previous occupant. `patch(looks=False)` skips the photos and gives everyone that default (library API only). Checked in DuckStation: the appearance editor cycled through every value, and a Brasileirão line-up at the team photo. |

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

### Team logos

`we2002` draws every mapped team's logo where the game draws its flag: the
team-select grid, the small flag beside the selected team, and the large flag of
the match intro and scoreboard. The logo is the one the provider publishes for
the team (ESPN's `Team.logo_url`), downloaded once into `--cache-dir` and
rendered with Pillow, so this needs the `images` extra:

```bash
pip install "retro-roster-patcher[images]"
```

Without Pillow the roster patch still applies and the flags stay the striped
default, with one status line saying so. `--no-logos` turns the logos off on
purpose. `--blank-unused` deals with the slots a league does not fill: every
national and Master League slot outside the slot map gets an empty name, a
black kit and a fully transparent flag, so the menus show the mapped teams and
blank entries rather than the 2002 squads. To use your own image for a team, put a PNG at
`<assets-dir>/logos/<team_id>.png` (ESPN's team id, the one in the slot map)
and pass `--assets-dir`; it wins over the download.

How it works, and the limits that follow. A WE2002 flag is a *style* byte per
team (one of 61 shapes drawn as 4bpp bitmaps) plus a 16-colour palette per
team, so each logo needs a style of its own: the patcher picks styles no
untouched team uses first, then the least-used ones, and 53 styles exist, so a
league of more than 53 teams leaves the rest striped. Fifteen opaque colours
and a transparent background is what a style holds, which is what the logo is
reduced to: fourteen of its own colours plus white, because the copies of the
atlas in the stadium files (the sideline and crowd flags a match draws) get
the logo on a white ground, where the menu copies keep it on transparency.
Master League slot 22 draws through slot 10's palette and keeps the striped
flag. The bitmaps live LZSS-compressed in the team select, match HUD, editor
and stadium data files; those are re-encoded, and one that no longer fits
grows into the free sectors after it, with the executable's own file table
raised to match. The rewritten sectors carry fresh EDC checksums but
not recomputed ECC, as with the jersey textures.

### Screen art

`we2002` can also swap the art of three screens for your own PNGs. Each is a
*slot*: the texture page the game draws that screen from, with the pieces laid
out exactly as the game keeps them, so the first step is to export what is
there now and paint over it:

```bash
retro-roster images --game we2002 --rom we2002.bin --out art/
```

That writes `art/title.png` (the title logo, the ball and the credits),
`art/menu.png` (the main menu's ball, "Winning Eleven" banner, "2002" and
glow) and `art/boot.png` (the KONAMI logo and the JFA licence screen). Edit a
file in place, keeping its size, and hand it back on the next patch, either as
`--image menu=art/menu.png` (repeatable) or by leaving it at
`<assets-dir>/images/<slot>.png`. Each piece is reduced to its palette's colour
budget, 15 colours for the 4bpp title and menu, 255 for the 8bpp boot logos,
plus transparency, and the palette records are rewritten with it. The menu
ball's reflection is cleared whenever a menu image is given. The sky of the
title screen and the dark field behind the main menu are not textures and stay
as they are. Needs Pillow, like the logos.

### Kits

`we2002` draws every mapped team's home and away kits, the same `kits` contract
as `pes2013-ps2` (see the library section): the default kits by default (below),
a `KitSpec`, a picture or a template per slot on request,
`kits=False` to leave the disc's. Each team owns a 3D texture file (`TEX_nn.BIN`,
0-62 the national teams, 63-94 the Master League) holding the home and away
kit pages and their palettes, and the game composes the shirt, shorts and socks
from rectangles of that page. A spec is painted into those rectangles over the
art of a plain white template, each pixel's brightness kept, so the shirt keeps
its folds; a picture is mapped onto them (its upper middle on the chest, its
sides on the sleeves, its lower part on the shorts and socks); a template copies
one of the 95 designs as drawn, previews of which `kit_templates(rom_path)`
returns as 96x128 PNGs. To paint the texture itself, `kit_page_png(rom_path,
slot, which)` returns the slot's 128x256 page as a PNG (the regions outlined
and labelled in red unless `guides=False`; `template=n` reads a design
instead), and a 128x256 PNG handed back as `{"image": png}` is written as the
page: opaque pixels of the outfield half replace the art, transparent ones keep
it, so leave the guides out of what you upload. `kit_preview_png(rom_path, slot,
which, choice)` shows any such choice as a 96x128 kit photo before it is
written (`choice=None` for the kit as it is). The goalkeeper shares the page through a palette of its
own and is left alone, as are the shirt numbers. The 2D team-select preview is
a 16-colour sprite palette, written from the same colours (striped kits put the
contrast colour on the collar and shoulder zones). Needs Pillow; without
it each slot takes the whole texture of the shipped team whose shirt colour is
nearest, which is all earlier versions did.

`iss-snes` (and `iss-deluxe-snes`, the same way) takes the same `kits` contract within what the cartridge holds.
Every team draws the same player tiles, so a kit is colours: the shirt in three
shades (body and sleeves alike), the shorts in three and the socks in two, for
home and away; the goalkeeper keeps the fixed green kit. The one drawing a team
owns is an 8x8 chest design in eight poses, drawn in its flag colours (palette
entries 12-15, entry 12 being the shirt numbers), so it is the same on both
shirts and only the home kit's pattern counts. A `KitSpec`'s pattern borrows
the nearest of the cartridge's own designs, nothing new is drawn: `stripes` and
`pinstripes` take Argentina's stripes, `hoops`, `sash` and `chevron` Germany's
chest band, and `plain`, `halves` and `sleeves` a blank chest (contrasting
sleeves and whole-shirt patterns cannot be drawn). The design shows in the
crest's colours, or the two-band flag's without a crest: the flag's last three
palette entries are reordered so the design takes the crest colour nearest the
spec's `secondary` (Argentina's stripes on a template take the crest colour
nearest sky blue), which on a crest without such a colour can be far from it,
and a design in entry 12 (Brazil's badge) is drawn in the shirt-number colour.
A patched team without a pattern gets a blank chest, never the old nation's
design. `{"template": n}` copies one of the 26 national kits, numbered by slot
(`kit_templates` previews each), its chest design included, as the retail
cartridge has it even when the image was patched before; `{"image": png}` is
read with `kit_from_image`. The byte that decides which team changes kit when
two look alike is set from the home shirt (a template's is copied); black and
grey shirts get values of their own, so black meets red without a change.
`kit_preview_png(rom_path, slot, which, choice, home)` draws the pre-match Team
Colors player from the cartridge's own tiles as a 96x144 PNG; for a kit as
written it matches Mesen 2 pixel for pixel, for a `choice` not yet written the
chest shows the colours the patch asks of the crest, and an away preview takes
its chest design from `home`, the team's home kit. `kits=False` keeps the
cartridge's kits and chest designs. Pictures and previews need Pillow.

#### Default kits and the kit catalogue

Every soccer patcher starts a team from `core.kits.default_kits(team)`. For a
club in the packaged catalogue, `src/retro_roster_patcher/assets/kit_catalog.json`,
that is the club's real home and away kit (Flamengo in red and black hoops,
Juventus in black and white stripes, Feyenoord in red and white halves); for any
other team, plain kits in the provider's two colours. The catalogue holds the
footballdatabase.com world ranking's top 100 clubs and every Brasileirao Serie A
club, read from each club's English Wikipedia kit fields for the 2026-27 season
and checked by eye against Wikipedia's kit drawings. It is plain JSON so other
programs can use it: `clubs` is a list of `{name, country, espn_id, names,
wikipedia, ranking, home, away}`, the kits as `KitSpec` dicts (`shirt`,
`secondary`, `pattern`, `shorts`, `socks`, `sleeves`, colours `#rrggbb`). A club
with an `espn_id` is matched by that id only, so a namesake elsewhere never takes
its kit; one without (a league ESPN does not carry) is matched by name.
`assets/kit_catalog/` rebuilds it. Kit colours derived from Wikipedia,
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), Wikipedia
contributors.

### Soccer lineups

Every soccer game takes its starting eleven from `EspnClient.get_lineup`, which reads every completed league match of the season: each match summary lists both sides' formation and matchday squad, starters and bench, and is cached once per match. A league's first fetch reads one summary per completed league match (about 380 for a full 20-club season), later fetches only the new ones.

* **Who starts.** Each player on the current ESPN squad is ranked by how many of his own last 5 matchday squads he started. A match he missed (injured, suspended, not signed yet) never counts against him, so a regular back from injury keeps his place and a player who left never appears.
* **The formation.** Each match's formation is folded to three lines. With one striker, the line behind him joins the attack when it has one or two players, its two wide players when it has three, and nobody when it has four or more: 4-2-3-1 is 4-3-3, 3-4-2-1 is 3-4-3, 4-4-1-1 is 4-4-2, 4-1-4-1 is 4-5-1. The shape is the one used most in the last 5 matches, ties to the latest.
* **Where each plays.** The latest match in that shape gives the places. Players ask for places in the order of their FotMob positions (one FotMob squad request per team, matched to ESPN by birth date, then name, and one league table per season). A player FotMob lacks, or whose FotMob positions the formation does not have, asks for the roles of his own ESPN starts, latest first. A place keeps whoever started more of his last 5 squads, then the player for whom that position comes earlier, then the season's starts, then the more recent start. Without FotMob, places go by ESPN's position codes, and a status line says so; a forward who also plays wide can then be seated wide and a winger left out.
* **What a game reads.** `TeamRoster.extra["lineup"]` carries `shape` and, for each starter, `line` (`GK`, `DF`, `MF`, `FW`), `role` (FotMob's `RB`, `CDM`, `RW`…) and `side` (`L`, `C`, `R`).
* **Roles and formations.** `fetch` gives every soccer player a `role` (`GK RB CB LB RWB LWB CDM CM CAM RM LM RW LW ST`): a starter has the role of his place, anyone else his FotMob position, else the role of his latest start, else his squad group's (`GK`, `CB`, `CM` or `ST`). Every team with a lineup gets an `extra["formation"]`, the lineup's shape (three lines, such as `"4-3-3"`). Both can be edited before `patch`, and a game reads the roster's own, not the lineup's. A rosters file fetched before roles and formations still patches: a team with no formation but a saved lineup uses the lineup's shape, and a player with no role takes the one his lineup code gives.
* **An edited order.** With `order_as_given`, a team with a formation has its first eleven cut by index into the lines of the formation the game writes: the first goalkeeper listed is in goal, then come the defenders, the midfielders and the forwards in the order given, whatever their positions, so a player moved out of his line plays where he was put. Each line is then seated on its places by role: first by how wide the place is (a full back takes the touchline place on his side), then by depth (a deeper role sits deeper).
* **What a game writes.** A patcher's `writes_formation` says whether `patch` writes each team's formation, and `formations` lists the shapes it can write. Every soccer game writes one, the nearest it has to the team's: the one with the smallest total difference in players per line, then the nearest number of defenders (a 5-4-1 stays a back five where there is no 5-4-1), then the first listed; the soccer rows of [What each game requires](#what-each-game-requires) say how.

### National teams in the free slots

A cartridge holds more teams than a club league fills, and `patch --fill-nations` puts today's best national teams in the slots the slot map leaves out, instead of the 1990s originals (`--blank-unused` empties them instead; the two exclude each other, except on `pes2013-ps2`, where `--blank-unused` then blanks the clubs). The order is the FIFA men's world ranking as FotMob serves it, fetched once a day into the cache. When FotMob cannot be reached, a snapshot packaged with the library is used, and a status line gives its date.

Each ranked nation is found among ESPN's national teams (league 2021) by name, then by FIFA code against ESPN's abbreviation (only a code no other ESPN team shares). A nation goes into its own cartridge slot when that slot is free, and the others fill the free slots in the game's order, until they run out. A nation ESPN has no squad for is skipped with a status line, and the next one takes its place. Only games whose patcher has `supports_nation_fill` take the flag; `sports.nationals.fetch_top_nations` is the same step for library callers, its result merged into the league's rosters before `patch`.

### European clubs in the free slots

`patch --fill-clubs` puts today's best European clubs in the club slots the leagues leave, with their current squads, lineups, names, crests and kits (`pes2013-ps2` only). The order is UEFA's men's club coefficient ranking from comp.uefa.com, fetched once a day into the cache; when UEFA cannot be reached, a snapshot packaged with the library (the 2026-11-05 ranking, top 200) is used, with a status line. `--fill-clubs=champions` takes the clubs of this season's Champions League (ESPN's `uefa.champions` teams) first, by coefficient, then the rest.

Each club's ESPN team comes from the snapshot, which pairs UEFA's ids with ESPN's; a club the snapshot does not pair is matched by name among the ESPN teams of its own country only (so Dinamo Zagreb never becomes Dinamo Moscow). Squads and lineups come from the club's own ESPN league. The league's own clubs are never placed twice, and a club ESPN has no team or squad for is skipped with a status line, the next one taking its place. With `--fill-nations` too, the clubs are placed first and the nations fill the national rows. `sports.clubs.fetch_top_clubs` is the same step for library callers, gated by the patcher's `supports_club_fill`.

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

A game whose patcher has `supports_kits` takes a `kits` option on `patch`: absent or `True`
draws every mapped team's home and away kits from its colours (`default_kits`: plain kits in
the provider's two team colours), `False` keeps the ROM's, and a dict
`{slot: {"home": kit, "away": kit}}` overrides a slot, where a kit is a `KitSpec`, its dict,
`{"image": png}` (a picture of the kit, bytes or a path; a game that cannot take the picture
reads a `KitSpec` off it with `kit_from_image`), `{"template": n}` (one of the game's own
jersey designs, listed by the patcher's `kit_templates`) or `None` for the default.
`render_kit` draws a `KitSpec` as a PNG to preview it. `slot_groups` (name, first slot, count),
`supports_nationals` and `national_slot_base` describe a game's slot layout for a picker.
`max_leagues` says how many leagues one patch takes, the k-th filling the k-th group: 1 by
default and for `we2002`, whose two groups (Master League, national only) are one run of
slots; 5 for `pes2013-ps2`, one per named league (a league in "Other" would get no league
name or icon).

### Root exports

`from retro_roster_patcher import ...`

- Errors: `RetroRosterError`, `ApiError`, `CapabilityError`, `MappingError`,
  `MissingAssetError`, `RomError`, `StorageError`
- Registry: `Patcher`, `PatcherInfo`, `get_patcher`, `list_patchers`, `register`
- Game-side models: `MappedRosters`, `PatchResult`, `RomInfo`, `RomSlot`, `SlotMapping`
- Sports models: `League`, `LeagueData`, `Player`, `PlayerStats`, `Team`, `TeamRoster`
- Serialisation: `league_data_from_dict`, `league_data_to_dict`
- Finding a ROM on disk: `RomFinder`, `RomFinderConfig`, `RomFinderResult`
- Kits: `KitSpec`, `KitTemplate`, `PATTERNS`, `default_kits`, `kit_from_image`, `render_kit`
- Also: `Transport`, `__version__`

`from retro_roster_patcher.sports import ...`

- Clients: `EspnClient`, `FotmobClient`, `NhlApiClient`, `Transport`, `team_colors`
- Filling gaps: `fill_headshots`
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
| `images` | `images` | `paths[]`, the PNG files written |

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

A game that draws or measures players' faces shares a few helpers.
`core.headshots.load_headshot(cache_dir, url, transport)` returns a provider head shot
from `cache_dir/headshots`, downloading it once, or `None` (an error page is never
cached, and no failure raises). `core.skin.face_ita(png)` measures the face's skin as
its ITA angle (`None` when there is no face; needs Pillow), and `core.skin.tone(ita,
cuts)` cuts it into the game's own number of tones at the game's cut points, lightest
first; `kgj_mlb_snes.looks.SKIN_CUTS` are KGJ's six. `core.hair.hair_colour(png)` reads
the hair above the face as one of `black`, `dark_brown`, `brown`, `blond`, `red`,
`grey` or `bald`, and `core.beard.beard(png)` whether the lower face shows a beard.
`core.headshots.measure_faces(cache_dir, urls, transport, status)` runs all three over
a whole league's photos (by player id, each URL once, with status lines) and gives a
`Face(ita, hair, beard)` per player; `core.looks.pick(weights, key)` is the stable
weighted pick a game's `looks.py` dresses the rest with, and `core.looks.EAST_ASIAN`
the nationalities whose players two discs draw with a regional face of their own.

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
