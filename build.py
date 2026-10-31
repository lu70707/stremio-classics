import html
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).parent / "docs"
PREFIX = "iaf-"
CATALOG = "ia-classics"
PAGE = 100
CANDIDATES = 1500
MIN_SECONDS = 40 * 60
DECADES = [f"{y}s" for y in range(1900, 2000, 10)]
VIDEO_FORMATS = ("h.264", "mpeg4", "512kb mpeg4", "h.264 ia")


def get_json(url):
    out = subprocess.run(["curl", "-sfL", "-m", "60", "--retry", "5", "--retry-all-errors", url], capture_output=True, check=True)
    return json.loads(out.stdout)


def search():
    query = "collection:feature_films AND mediatype:movies AND year:[1900 TO 1999] AND -subject:trailer"
    params = urlencode(
        [("q", query), ("fl[]", "identifier"), ("fl[]", "year"), ("sort[]", "downloads desc"),
         ("rows", CANDIDATES), ("output", "json")]
    )
    return get_json(f"https://archive.org/advancedsearch.php?{params}")["response"]["docs"]


def seconds(value):
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


def clean(text):
    if isinstance(text, list):
        text = " ".join(text)
    text = re.sub(r"<br\s*/?>|</p>", "\n", str(text or ""), flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def pick_videos(files):
    groups = {}
    for f in files:
        if not f["name"].lower().endswith(".mp4") or f.get("format", "").lower() not in VIDEO_FORMATS:
            continue
        key = f.get("original", f["name"]) if f.get("source") == "derivative" else f["name"]
        rank = ("512kb" not in f.get("format", "").lower(), int(f.get("height") or 0), int(f.get("size") or 0))
        if key not in groups or rank > groups[key][0]:
            groups[key] = (rank, f)
    return sorted((f for _, f in groups.values()), key=lambda f: f["name"])


def film(doc):
    ident = doc["identifier"]
    data = get_json(f"https://archive.org/metadata/{ident}")
    videos = pick_videos(data.get("files", []))
    if not videos or sum(seconds(v.get("length")) for v in videos) < MIN_SECONDS:
        return None
    m = data["metadata"]
    year = int(str(doc.get("year"))[:4])
    poster = f"https://archive.org/services/img/{ident}"
    streams = []
    for i, v in enumerate(videos, 1):
        part = f"Part {i}/{len(videos)} · " if len(videos) > 1 else ""
        height = f"{v['height']}p · " if v.get("height") else ""
        length = seconds(v.get("length"))
        streams.append({
            "url": f"https://archive.org/download/{ident}/{quote(v['name'])}",
            "name": "Internet Archive",
            "title": f"{part}{height}{int(length // 60)} min",
        })
    meta = {
        "id": PREFIX + ident,
        "type": "movie",
        "name": clean(m.get("title")) or ident,
        "poster": poster,
        "background": poster,
        "description": clean(m.get("description")),
        "releaseInfo": str(year),
        "runtime": m.get("runtime") or f"{int(sum(seconds(v.get('length')) for v in videos) // 60)} min",
        "genres": [f"{year // 10 * 10}s"],
        "website": f"https://archive.org/details/{ident}",
    }
    return meta, streams


def safe_film(doc):
    try:
        return film(doc)
    except (subprocess.CalledProcessError, ValueError, KeyError) as e:
        print(f"skip {doc['identifier']}: {e!r}", file=sys.stderr)
        return None


def write(path, payload):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def write_catalog(metas, genre=None):
    previews = [{k: m[k] for k in ("id", "type", "name", "poster", "releaseInfo")} for m in metas]
    for skip in range(0, max(len(previews), 1), PAGE):
        extras = "&".join(e for e in (f"genre={genre}" if genre else "", f"skip={skip}" if skip else "") if e)
        name = f"{CATALOG}/{extras}.json" if extras else f"{CATALOG}.json"
        write(f"catalog/movie/{name}", {"metas": previews[skip:skip + PAGE]})


def main():
    docs = search()
    seen = set()
    docs = [d for d in docs if d.get("year") and not (d["identifier"].lower() in seen or seen.add(d["identifier"].lower()))]
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(safe_film, docs))
    films = [r for r in results if r]
    shutil.rmtree(ROOT, ignore_errors=True)
    for meta, streams in films:
        write(f"meta/movie/{meta['id']}.json", {"meta": meta})
        write(f"stream/movie/{meta['id']}.json", {"streams": streams})
    metas = [meta for meta, _ in films]
    counts = {d: sum(m["genres"][0] == d for m in metas) for d in DECADES}
    decades = [d for d in DECADES if counts[d]]
    write_catalog(metas)
    for decade in decades:
        write_catalog([m for m in metas if m["genres"][0] == decade], decade)
    write("manifest.json", {
        "id": "com.lu70707.ia-classics",
        "version": "1.0.0",
        "name": "Classic Films 1900-1999",
        "description": f"{len(metas)} feature films from 1900 to 1999 from the Internet Archive. Free and legal, direct HTTP streams.",
        "logo": "https://archive.org/images/glogo.png",
        "resources": ["catalog", "meta", "stream"],
        "types": ["movie"],
        "idPrefixes": [PREFIX],
        "catalogs": [{
            "type": "movie",
            "id": CATALOG,
            "name": "Classic Films 1900-1999",
            "extra": [{"name": "genre", "options": decades}, {"name": "skip"}],
        }],
    })
    (ROOT / ".nojekyll").touch()
    print(f"{len(metas)} films from {len(docs)} candidates", counts, file=sys.stderr)


if __name__ == "__main__":
    main()
