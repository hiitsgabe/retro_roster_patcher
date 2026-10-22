"""catalog.json + espn_ids.json + overrides.json -> src/retro_roster_patcher/data/kit_catalog.json.

Run extract.py, then espn_match.py, then this; `--sheet` also draws every kit
into final_sheet.png for a last look. overrides.json holds the corrections a
review by eye made to the extracted specs ({name, side, field, value, why}).
"""

import html
import io
import json
import os
import sys

from PIL import Image, ImageDraw

from retro_roster_patcher.core.kits import KitSpec, render_kit

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "..", "src", "retro_roster_patcher", "data", "kit_catalog.json")

catalog = json.load(open(os.path.join(HERE, "catalog.json")))
espn = json.load(open(os.path.join(HERE, "espn_ids.json")))
overrides = json.load(open(os.path.join(HERE, "overrides.json")))

by_name = {c["name"]: c for c in catalog}
applied = 0
for o in overrides:
    club = by_name.get(html.unescape(o["name"]))
    if club is None or not club.get(o["side"]):
        print("override skipped:", o["name"], o["side"], o["field"])
        continue
    value = None if (o["field"] == "sleeves" and o["value"] in ("none", "", None)) else o["value"]
    club[o["side"]][o["field"]] = value
    applied += 1
print("overrides applied", applied, "of", len(overrides))

clubs = []
for c in catalog:
    if not c.get("home") or not c.get("away"):
        print("no kit, left out:", c["name"])
        continue
    for side in ("home", "away"):
        KitSpec.from_dict(c[side])  # validates colours and pattern
    e = espn.get(c["name"], {})
    names = []
    for n in (c["name"], c["title"], e.get("espn_name")):
        if n and n not in names:
            names.append(n)
    clubs.append(
        {
            "name": c["name"],
            "country": c["country"],
            "espn_id": int(e["espn"]) if e.get("espn") else None,
            "names": names[1:],
            "wikipedia": c["title"],
            "ranking": int(c["rank"]) if c["rank"].isdigit() else None,
            "home": {
                k: c["home"][k]
                for k in ("shirt", "secondary", "pattern", "shorts", "socks", "sleeves")
            },
            "away": {
                k: c["away"][k]
                for k in ("shirt", "secondary", "pattern", "shorts", "socks", "sleeves")
            },
        }
    )
ids = [c["espn_id"] for c in clubs if c["espn_id"] is not None]
assert len(ids) == len(set(ids)), "an ESPN id appears twice"
doc = {
    "format": 1,
    "updated": "2026-10-22",
    "description": (
        "Home and away kits of real football clubs as retro_roster_patcher KitSpec dicts "
        "(shirt, secondary, pattern, shorts, socks, sleeves; colours #rrggbb). Look a club up by "
        "espn_id (ESPN's soccer team id) or, for one without, by name or one of its names. "
        "Clubs: the footballdatabase.com world ranking's top 100 (ranking) plus every Brasileirao "
        "Serie A 2026 club (ranking null)."
    ),
    "patterns": ["plain", "stripes", "hoops", "halves", "sash", "pinstripes", "chevron", "sleeves"],
    "source": (
        "Kit colours and patterns read from each club's English Wikipedia infobox kit fields "
        "(2026-27 season), then reviewed by eye against Wikipedia's kit drawings."
    ),
    "license": (
        "Derived from Wikipedia, CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/); "
        "attribute Wikipedia contributors."
    ),
    "clubs": clubs,
}
json.dump(doc, open(OUT, "w"), indent=1, ensure_ascii=False)
open(OUT, "a").write("\n")
print("wrote", OUT, len(clubs), "clubs,", len(ids), "with ESPN ids")

if "--sheet" in sys.argv:
    cell = (100, 150)
    cols = 8
    rows = (len(clubs) + cols - 1) // cols
    sheet = Image.new("RGBA", (cols * cell[0] * 2, rows * cell[1]), (50, 50, 60, 255))
    d = ImageDraw.Draw(sheet)
    for i, c in enumerate(clubs):
        x, y = (i % cols) * cell[0] * 2, (i // cols) * cell[1]
        for j, side in enumerate(("home", "away")):
            img = Image.open(io.BytesIO(render_kit(KitSpec.from_dict(c[side]), 96, 128))).convert(
                "RGBA"
            )
            sheet.alpha_composite(img, (x + j * cell[0] + 2, y + 2))
        d.text((x + 4, y + 134), c["name"][:30], fill=(255, 255, 0, 255))
    sheet.save(os.path.join(HERE, "final_sheet.png"))
    print("sheet", sheet.size)
