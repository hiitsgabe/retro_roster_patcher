"""Build a kit catalogue from Wikipedia's football-club infobox kit fields.

usage: extract.py [only-rank ...]
Reads clubs.tsv (rank, name, country, Wikipedia title), writes:
  raw/<slug>.json      the infobox kit fields and the overlay classification
  catalog.json         [{rank, name, country, title, home: KitSpec dict, away: ...}]
  sheets/sheet_NN.png  per 12 clubs: Wikipedia's kit (left) vs our render (right)
Only colours and a pattern name are kept; the overlay images are read to
classify the pattern and are not stored.
"""

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

from retro_roster_patcher.core.kits import KitSpec, render_kit

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
RAW = os.path.join(HERE, "raw")
SHEETS = os.path.join(HERE, "sheets")
for d in (CACHE, RAW, SHEETS):
    os.makedirs(d, exist_ok=True)
UA = {
    "User-Agent": "retro-roster-patcher kit catalogue (https://github.com/hiitsgabe/retro_roster_patcher)"
}
API = "https://en.wikipedia.org/w/api.php?"
PARTS = {  # infobox key -> Commons file prefix
    "la": "Kit_left_arm",
    "b": "Kit_body",
    "ra": "Kit_right_arm",
    "sh": "Kit_shorts",
    "so": "Kit_socks",
}
COLOUR_KEYS = {"la": "leftarm", "b": "body", "ra": "rightarm", "sh": "shorts", "so": "socks"}


def fetch(url: str, binary: bool = False):
    key = os.path.join(CACHE, re.sub(r"[^A-Za-z0-9]+", "_", url)[-180:])
    if os.path.exists(key):
        data = open(key, "rb").read()
    else:
        for attempt in range(4):
            try:
                data = urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=30
                ).read()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 + 3 * attempt)
        open(key, "wb").write(data)
        time.sleep(0.15)
    return data if binary else json.loads(data)


def wikitext(title: str) -> tuple[str, str]:
    q = fetch(
        API
        + urllib.parse.urlencode(
            {
                "action": "parse",
                "page": title,
                "prop": "wikitext",
                "section": 0,
                "format": "json",
                "redirects": 1,
            }
        )
    )
    if "error" in q:
        raise KeyError(f"{title}: {q['error'].get('info')}")
    return q["parse"]["title"], q["parse"]["wikitext"]["*"]


def kit_fields(text: str) -> dict[str, str]:
    out = {}
    for k, v in re.findall(
        r"\|\s*((?:pattern_(?:la|b|ra|sh|so)|leftarm|body|rightarm|shorts|socks)[123])\s*=\s*([^\n|}]*)",
        text,
    ):
        out[k] = re.sub(r"<!--.*?-->", "", v).strip()
    return out


def hexrgb(v: str) -> tuple[int, int, int] | None:
    m = re.search(r"([0-9A-Fa-f]{6})", v or "")
    if not m:
        named = {
            "white": "FFFFFF",
            "black": "000000",
            "red": "FF0000",
            "blue": "0000FF",
            "navy": "000080",
            "green": "008000",
            "yellow": "FFFF00",
        }
        h = named.get((v or "").strip().lower())
        if not h:
            return None
        m = re.match("(.*)", h)
    h = m.group(1)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def overlay_urls(names: list[str]) -> dict[str, str]:
    urls = {}
    for i in range(0, len(names), 40):
        chunk = names[i : i + 40]
        q = fetch(
            API
            + urllib.parse.urlencode(
                {
                    "action": "query",
                    "titles": "|".join("File:" + n for n in chunk),
                    "prop": "imageinfo",
                    "iiprop": "url",
                    "format": "json",
                }
            )
        )
        norm = {n["to"]: n["from"] for n in q.get("query", {}).get("normalized", [])}
        for page in q.get("query", {}).get("pages", {}).values():
            info = page.get("imageinfo")
            if info:
                title = norm.get(page["title"], page["title"])
                urls[title.removeprefix("File:").replace(" ", "_")] = info[0]["url"]
    return urls


def overlay(name: str, urls: dict[str, str]) -> Image.Image | None:
    url = urls.get(name)
    if not url:
        return None
    try:
        return Image.open(io.BytesIO(fetch(url, binary=True))).convert("RGBA")
    except Exception:
        return None


def part_image(base, img, size):
    """The part as Wikipedia draws it: the base colour, the overlay on top."""
    canvas = Image.new("RGBA", size, base + (255,))
    if img is not None:
        canvas.alpha_composite(img.resize(size, Image.Resampling.NEAREST))
    return canvas


def dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=False)) ** 0.5


def contrast(rgb):
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return (0, 0, 0) if lum > 150 else (255, 255, 255)


# Where the cloth is in each part's drawing. Wikipedia's kit PNGs are opaque,
# with white around the garment, so the cloth is found statistically: the
# pixels that are not white in enough of all the drawings of that part.
MASKS: dict[tuple[str, tuple[int, int]], list[list[bool]]] = {}


def build_masks(images: dict[tuple[str, tuple[int, int]], list[Image.Image]]) -> None:
    from PIL import ImageFilter

    for key, imgs in images.items():
        w, h = key[1]
        hits = [[0] * w for _ in range(h)]
        for img in imgs:
            px = img.load()
            for y in range(h):
                for x in range(w):
                    r, g, b, _a = px[x, y]
                    if dist((r, g, b), (255, 255, 255)) > 40:
                        hits[y][x] += 1
        need = max(2, len(imgs) * 0.15)
        raw = Image.new("L", (w, h))
        raw.putdata([255 if hits[y][x] >= need else 0 for y in range(h) for x in range(w)])
        eroded = raw.filter(ImageFilter.MinFilter(3))  # drop the 1 px outline
        data = list(eroded.getdata())
        MASKS[key] = [[data[y * w + x] > 0 for x in range(w)] for y in range(h)]


def cloth_colours(img: Image.Image, mask) -> list[tuple[tuple[int, int, int], float]]:
    """[(colour, share)] of the cloth, texture smoothed, biggest first."""
    from PIL import ImageFilter

    smooth = img.convert("RGB").filter(ImageFilter.ModeFilter(3))
    px = smooth.load()
    w, h = smooth.size
    bins: dict[tuple[int, int, int], list[int]] = {}
    n = 0
    for y in range(h):
        for x in range(w):
            if not mask[y][x]:
                continue
            r, g, b = px[x, y]
            q = (r // 24, g // 24, b // 24)
            acc = bins.setdefault(q, [0, 0, 0, 0])
            acc[0] += r
            acc[1] += g
            acc[2] += b
            acc[3] += 1
            n += 1
    out = []
    for acc in sorted(bins.values(), key=lambda a: -a[3]):
        col = tuple(round(c / acc[3]) for c in acc[:3])
        # merge near-identical bins (shading of one cloth colour)
        for i, (c, share) in enumerate(out):
            if dist(c, col) < 45:
                out[i] = (c, share + acc[3] / n)
                break
        else:
            out.append((col, acc[3] / n))
    return sorted(out, key=lambda t: -t[1])


def classify_body(img: Image.Image, mask, shirt, secondary):
    """Pattern of the shirt front: stripes/pinstripes/hoops/halves/sash/plain."""
    from PIL import ImageFilter

    smooth = img.convert("RGB").filter(ImageFilter.ModeFilter(3))
    px = smooth.load()
    w, h = smooth.size

    def lab(x, y):
        if not mask[y][x]:
            return None
        c = px[x, y]
        return 1 if dist(c, secondary) < dist(c, shirt) else 0

    def transitions(line):
        vals = [v for v in line if v is not None]
        return sum(1 for a, b in zip(vals, vals[1:], strict=False) if a != b), vals

    rows = [transitions([lab(x, int(h * f)) for x in range(w)]) for f in (0.4, 0.55, 0.7, 0.85)]
    cols = [
        transitions([lab(int(w * f), y) for y in range(int(h * 0.25), h)])
        for f in (0.35, 0.5, 0.65)
    ]
    across = sorted(t for t, _ in rows)[len(rows) // 2]
    down = sorted(t for t, _ in cols)[len(cols) // 2]
    sec = [lab(x, y) for y in range(h) for x in range(w)]
    share = sum(1 for v in sec if v == 1) / max(1, sum(1 for v in sec if v is not None))
    if across >= 4 and across >= down and share > 0.15:
        runs = []
        for _, vals in rows:
            n = 0
            for v in vals:
                if v == 1:
                    n += 1
                elif n:
                    runs.append(n)
                    n = 0
        thin = runs and sum(runs) / len(runs) <= 2
        return "pinstripes" if thin else "stripes"
    if down >= 4 and share > 0.2:
        return "hoops"
    left = [lab(x, y) for y in range(h) for x in range(w // 2)]
    right = [lab(x, y) for y in range(h) for x in range(w // 2, w)]

    def frac(vals):
        known = [v for v in vals if v is not None]
        return sum(known) / max(1, len(known))

    if abs(frac(left) - frac(right)) > 0.5:
        return "halves"
    diag = [lab(round(i * (w - 1) / 30), round(h * 0.2 + i * (h * 0.75) / 30)) for i in range(31)]
    anti = [
        lab(round((30 - i) * (w - 1) / 30), round(h * 0.2 + i * (h * 0.75) / 30)) for i in range(31)
    ]
    if 0.1 < share < 0.45 and max(frac(diag), frac(anti)) > 0.7:
        return "sash"
    return "plain"


def side(fields, n, urls):
    """KitSpec dict + the parts drawn the Wikipedia way, for kit n (1 home, 2 away)."""
    base = {p: hexrgb(fields.get(f"{c}{n}", "")) for p, c in COLOUR_KEYS.items()}
    pats = {p: fields.get(f"pattern_{p}{n}", "") for p in PARTS}
    imgs = {p: overlay(f"{PARTS[p]}{pats[p]}.png", urls) if pats[p] else None for p in PARTS}
    if base["b"] is None and imgs["b"] is None:
        return None, None, {}
    for p in base:
        base[p] = base[p] or base["b"] or (255, 255, 255)

    def colours(p):
        img = imgs[p]
        if img is None:
            return [(base[p], 1.0)]
        mask = MASKS.get((p, img.size))
        if mask is None or not any(map(any, mask)):
            return [(base[p], 1.0)]
        # Generic overlays (_whitetopl, _redborder) are see-through: read them
        # over the part's base colour, as Wikipedia draws them.
        return cloth_colours(part_image(base[p], img, img.size), mask)

    body = colours("b")
    shirt = body[0][0]
    secondary = next((c for c, s in body[1:] if s >= 0.1 and dist(c, shirt) > 70), None)
    pattern = "plain"
    if secondary is not None and imgs["b"] is not None:
        pattern = classify_body(
            part_image(base["b"], imgs["b"], imgs["b"].size),
            MASKS[("b", imgs["b"].size)],
            shirt,
            secondary,
        )
    sleeve = colours("la")[0][0]
    sleeves = sleeve if dist(sleeve, shirt) > 70 and pattern not in ("halves",) else None
    if pattern == "plain" and sleeves is not None:
        pattern = "sleeves"
    shorts = colours("sh")[0][0]
    socks = colours("so")[0][0]
    if secondary is None:
        secondary = sleeves or (shorts if dist(shorts, shirt) > 70 else contrast(shirt))
    spec = KitSpec(
        shirt=shirt,
        secondary=secondary,
        pattern=pattern,
        shorts=shorts,
        socks=socks,
        sleeves=sleeves,
    )
    parts = {
        p: part_image(base[p], imgs[p], size)
        for p, size in (
            ("la", (31, 59)),
            ("b", (38, 59)),
            ("ra", (31, 59)),
            ("sh", (100, 36)),
            ("so", (100, 26)),
        )
    }
    return (
        spec.to_dict(),
        parts,
        {
            "body_colours": [("#{:02x}{:02x}{:02x}".format(*c), round(s, 2)) for c, s in body[:4]],
            "patterns": pats,
        },
    )


def wiki_kit(parts):
    """Wikipedia's kit drawing: arms beside the body, shorts and socks below."""
    img = Image.new("RGBA", (100, 121), (0, 0, 0, 0))
    img.alpha_composite(parts["la"], (0, 0))
    img.alpha_composite(parts["b"], (31, 0))
    img.alpha_composite(parts["ra"], (69, 0))
    img.alpha_composite(parts["sh"], (0, 59))
    img.alpha_composite(parts["so"], (0, 95))
    return img


def main(only):
    clubs = [
        line.rstrip("\n").split("\t")
        for line in open(os.path.join(HERE, "clubs.tsv"))
        if line.strip()
    ]
    if only:
        clubs = [c for c in clubs if c[0] in only]
    # Pass 1: every club's kit fields and overlay drawings, for the cloth masks.
    found = []
    by_part: dict = {}
    for rank, name, country, title in clubs:
        try:
            real, text = wikitext(title)
        except Exception as exc:
            print(f"!! {rank} {name}: {exc}")
            continue
        fields = kit_fields(text)
        names = set()
        for k, v in fields.items():
            m = re.match(r"pattern_(la|b|ra|sh|so)[12]$", k)
            if m and v:
                names.add(f"{PARTS[m.group(1)]}{v}.png")
        urls = overlay_urls(sorted(names)) if names else {}
        for k, v in fields.items():
            m = re.match(r"pattern_(la|b|ra|sh|so)[12]$", k)
            if m and v:
                img = overlay(f"{PARTS[m.group(1)]}{v}.png", urls)
                if img is not None:
                    by_part.setdefault((m.group(1), img.size), []).append(img)
        found.append((rank, name, country, title, real, text, fields, urls))
    build_masks(by_part)
    print("masks:", {f"{k[0]}{k[1]}": len(v) for k, v in by_part.items()})
    catalog = []
    for rank, name, country, _title, real, _text, fields, urls in found:
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        entry = {"rank": rank, "name": name, "country": country, "title": real}
        drawings = []
        for n, key in ((1, "home"), (2, "away")):
            spec, parts, info = side(fields, n, urls)
            entry[key] = spec
            entry[f"{key}_info"] = info
            drawings.append(parts)
        json.dump(
            {"entry": entry, "fields": fields},
            open(os.path.join(RAW, f"{slug}.json"), "w"),
            indent=1,
        )
        entry["_drawings"] = drawings
        catalog.append(entry)
        h, a = entry["home"], entry["away"]

        def brief(k):
            return (
                f"{k['pattern']:<10} {k['shirt']} {k['secondary']}" if k else "-- no kit fields --"
            )

        print(f"{rank:>3} {name:<26} home {brief(h)} | away {brief(a)}")
    # contact sheets: per club, wiki home, ours home, wiki away, ours away
    cell = (110, 140)
    for s in range(0, len(catalog), 12):
        batch = catalog[s : s + 12]
        sheet = Image.new("RGBA", (cell[0] * 4 + 160, cell[1] * len(batch)), (60, 60, 60, 255))
        d = ImageDraw.Draw(sheet)
        for i, e in enumerate(batch):
            y = i * cell[1]
            d.text((4, y + 4), f"{e['rank']} {e['name']}"[:24], fill=(255, 255, 0, 255))
            for j, (key, parts) in enumerate(zip(("home", "away"), e["_drawings"], strict=False)):
                x = 160 + j * 2 * cell[0]
                if parts:
                    sheet.alpha_composite(wiki_kit(parts), (x + 5, y + 10))
                if e[key]:
                    ours = Image.open(
                        io.BytesIO(render_kit(KitSpec.from_dict(e[key]), 96, 128))
                    ).convert("RGBA")
                    sheet.alpha_composite(ours, (x + cell[0] + 5, y + 6))
                    d.text(
                        (x + cell[0] + 5, y + cell[1] - 12),
                        e[key]["pattern"],
                        fill=(255, 255, 255, 255),
                    )
        sheet.save(os.path.join(SHEETS, f"sheet_{s // 12:02d}.png"))
    for e in catalog:
        e.pop("_drawings")
    out = os.path.join(HERE, "catalog.json" if not only else "catalog_partial.json")
    json.dump(catalog, open(out, "w"), indent=1, ensure_ascii=False)
    print("wrote", out, len(catalog))


if __name__ == "__main__":
    main(set(sys.argv[1:]))
