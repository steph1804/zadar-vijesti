#!/usr/bin/env python3
"""Skuplja članke s lokalnih portala (RSS) i generira statičnu stranicu.

Za svaki članak prikazuje se samo naslov, jedna slika, prvih nekoliko
rečenica i link na izvorni portal. Pokretanje: python3 aggregator.py
"""
import concurrent.futures
import email.utils
import html
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(ROOT, "feeds.json")
CACHE_FILE = os.path.join(ROOT, "data", "articles.json")
OUT_DIR = os.path.join(ROOT, "site")
TEMPLATE = os.path.join(ROOT, "template.html")
PLACES_FILE = os.path.join(ROOT, "places.txt")

USER_AGENT = "Mozilla/5.0 (compatible; LokalniAgregator/1.0)"
TIMEOUT = 20
MAX_PER_SOURCE = 30      # koliko najnovijih članaka uzeti po portalu
MAX_TOTAL = 200          # koliko članaka čuvati i prikazati
SENTENCES = 3            # koliko početnih rečenica
MAX_SUMMARY = 320        # najviše znakova sažetka

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def fetch(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.geturl()


def fetch_retry(url, timeout, attempts):
    """Za spore portale: ponovi zahtjev ako istekne vrijeme ili veza pukne."""
    for n in range(attempts):
        try:
            return fetch(url, timeout)
        except (TimeoutError, OSError) as e:
            if n == attempts - 1 or getattr(e, "code", None):
                raise
            time.sleep(10)


class _TextAndImg(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.imgs, self._skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style", "figcaption"):
            self._skip += 1
        elif tag == "img":
            src = a.get("data-src") or a.get("src")
            if src and not src.startswith("data:"):
                self.imgs.append(src)
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "figcaption") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def parse_html(fragment):
    p = _TextAndImg()
    p.feed(fragment or "")
    text = re.sub(r"\s+", " ", "".join(p.parts)).strip()
    return text, p.imgs


# Rečenice koje WordPress i slični sustavi dodaju na kraj sažetka.
_BOILERPLATE = re.compile(
    r"(The post .*? appeared first on .*$|Članak .*? se pojavljuje prvi puta na .*$"
    r"|Pročitaj(te)? (više|dalje).*$|Opširnije.*$|\[…\]|\[\.\.\.\]|Continue reading.*$)",
    re.I,
)


def summarize(text):
    text = _BOILERPLATE.sub("", text).strip()
    sentences = re.split(r"(?<=[.!?…])\s+(?=[A-ZČĆŠŽĐ0-9„\"])", text)
    out = ""
    for s in sentences[:SENTENCES]:
        if out and len(out) + len(s) > MAX_SUMMARY:
            break
        out = (out + " " + s).strip()
    if len(out) > MAX_SUMMARY:
        out = out[:MAX_SUMMARY].rsplit(" ", 1)[0] + "…"
    return out


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def _text(el, path):
    found = el.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_feed(data, base_url):
    """Vraća listu dict-ova: title, link, date, summary, image."""
    root = ET.fromstring(data)
    items = []
    if root.tag.endswith("rss") or root.find("channel") is not None:
        for it in root.iter("item"):
            desc = _text(it, "description")
            content = _text(it, "content:encoded")
            image = None
            for enc in it.findall("enclosure"):
                if enc.get("type", "image").startswith("image") and enc.get("url"):
                    image = enc.get("url")
                    break
            if not image:
                for tag in ("media:content", "media:thumbnail"):
                    m = it.find(tag, NS)
                    if m is not None and m.get("url"):
                        image = m.get("url")
                        break
            items.append(_item(_text(it, "title"), _text(it, "link"),
                               _text(it, "pubDate") or _text(it, "{http://purl.org/dc/elements/1.1/}date"),
                               desc, content, image, base_url,
                               [c.text.strip() for c in it.findall("category") if c.text]))
    else:  # Atom
        for e in root.iter("{%s}entry" % NS["atom"]):
            link = ""
            for l in e.findall("atom:link", NS):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            items.append(_item(_text(e, "atom:title"), link,
                               _text(e, "atom:published") or _text(e, "atom:updated"),
                               _text(e, "atom:summary"), _text(e, "atom:content"), None, base_url,
                               [c.get("term", "") for c in e.findall("atom:category", NS)]))
    return items


def _item(title, link, date, desc, content, image, base_url, categories):
    desc_text, desc_imgs = parse_html(desc)
    content_text, content_imgs = parse_html(content)
    image = image or next(iter(desc_imgs + content_imgs), None)
    # Sažetak iz description; ako je prekratak, iz punog sadržaja.
    text = desc_text if len(desc_text) >= 80 or not content_text else content_text
    return {
        "title": html.unescape(title),
        "link": urllib.parse.urljoin(base_url, link.strip()),
        "date": parse_date(date),
        "summary": summarize(text),
        "image": urllib.parse.urljoin(base_url, image) if image else None,
        "categories": categories,
        "text": (desc_text + " " + content_text)[:3000],
    }


_OG_IMAGE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]*>', re.I)
_CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_FEED_LINK = re.compile(
    r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', re.I)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def og_image(url):
    """Slika iz <meta property="og:image"> s same stranice članka."""
    try:
        data, final = fetch(url, 60)
    except Exception:
        return None
    page = data[:300_000].decode("utf-8", "replace")
    m = _OG_IMAGE.search(page)
    if m:
        c = _CONTENT.search(m.group(0))
        if c:
            return urllib.parse.urljoin(final, html.unescape(c.group(1)))
    return None


_FACES = None
_FACES_LOCK = threading.Lock()


def face_focus(url):
    """Okomiti položaj lica na slici u postocima (0 = vrh), da ih rezanje na kartici ne odreže.

    Treba opencv-python-headless; bez njega (ili ako lica nema) vraća None i stranica
    koristi zadani položaj. Ako je lica više, uzima se sredina između najvišeg i najnižeg.
    """
    global _FACES
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        data, _ = fetch(url, 30)
    except Exception:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h = img.shape[0]
    side = min(img.shape[:2])
    with _FACES_LOCK:
        if _FACES is None:
            _FACES = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = _FACES.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6,
                                        minSize=(max(24, side // 20),) * 2)
    if len(faces) == 0:
        return None
    top = min(y for x, y, w, fh in faces)
    bottom = max(y + fh for x, y, w, fh in faces)
    return round(100 * (top + bottom) / 2 / h)


def discover_feed(site):
    """Traži RSS link u zaglavlju naslovnice portala."""
    try:
        data, final = fetch(site)
    except Exception:
        return None
    m = _FEED_LINK.search(data[:300_000].decode("utf-8", "replace"))
    if m:
        h = _HREF.search(m.group(0))
        if h:
            return urllib.parse.urljoin(final, html.unescape(h.group(1)))
    return None


def load_source(src):
    candidates = list(src.get("feeds", []))
    errors = []
    tried = set()
    while candidates:
        url = candidates.pop(0)
        if url in tried:
            continue
        tried.add(url)
        try:
            data, final = fetch_retry(url, src.get("timeout", TIMEOUT), src.get("retries", 1))
            items = parse_feed(data, final)
            if items:
                break
            errors.append(f"{url}: prazan feed")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
        if not candidates and src.get("site") and "discovered" not in src:
            src["discovered"] = discover_feed(src["site"])
            if src["discovered"]:
                candidates.append(src["discovered"])
    else:
        return [], errors

    if src.get("link_contains") or src.get("categories"):
        items = [i for i in items if wanted(src, i)]
    if src.get("scope") == "national":
        items = [i for i in items if is_about_zadar(i)]
    elif src.get("local_only", True) and src.get("kind") != "info":
        items = [i for i in items if is_local(i)]
    items = [i for i in items if i["title"] and i["link"]][:MAX_PER_SOURCE]
    for i in items:
        i["source"] = src["name"]
        if src.get("topic"):
            i["source_topic"] = src["topic"]
        i["topics"] = topics(i, src)
        i["source_url"] = src.get("site", "")
        i["link"] = strip_tracking(i["link"])
    return items, errors


def wanted(src, item):
    """Zadovoljava li članak filtar portala (dio linka ili rubrika u feedu)."""
    if any(part in item["link"] for part in src.get("link_contains", [])):
        return True
    allowed = {c.casefold() for c in src.get("categories", [])}
    return any(c.casefold() in allowed for c in item["categories"])


def _words(pats):
    return re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(pats), re.I)


def _load_places():
    sections, current = {}, None
    with open(PLACES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if re.fullmatch(r"\[\w+\]", line):
                current = sections.setdefault(line[1:-1], [])
            elif line and current is not None:
                current.append(line)
    clean = {name: [p.lstrip("?") for p in pats] for name, pats in sections.items()}
    certain = [p for pats in sections.values() for p in pats if not p.startswith("?")]
    unsure = [p[1:] for pats in sections.values() for p in pats if p.startswith("?")]
    return {name: _words(pats) for name, pats in clean.items()}, \
        _words([p for pats in clean.values() for p in pats]), _words(certain), _words(unsure)


AREAS, PLACES, CERTAIN_PLACES, UNSURE_PLACES = _load_places()


def is_about_zadar(item):
    """Za nacionalne portale: Zadar, mjesto iz županije ili osoba odande u naslovu ili uvodu.

    Dvosmisleno ime (Vir, Nin, Novigrad...) u naslovu ili uvodu vrijedi samo ako se
    negdje u tekstu iz feeda spominje i nešto sigurno zadarsko.
    """
    short = item["title"] + " " + item["summary"]
    if CERTAIN_PLACES.search(short):
        return True
    return bool(UNSURE_PLACES.search(short) and CERTAIN_PLACES.search(_haystack(item)))
SPORT = _words([r"sport\w*", r"nogomet\w*", r"košark\w*", r"vaterpol\w*", r"rukomet\w*",
                r"odbojk\w*", r"futsal\w*", r"tenis\w*", r"jedr\w*", r"veslač\w*", r"atlet\w*",
                r"plivač\w*", r"boks\w*", r"žnl", r"hnl", r"aba lig\w*", r"kk", r"hnk", r"nk"])
TOPICS = ["Zadar", "Županija", "Otoci", "Sport"]


def _haystack(item, full=True):
    slug = urllib.parse.unquote(urllib.parse.urlsplit(item["link"]).path).replace("-", " ").replace("/", " ")
    parts = [item["title"], slug, " ".join(item.get("categories", []))]
    if full:
        parts += [item["summary"], item.get("text", "")]
    return " ".join(parts)


def is_local(item):
    """Spominje li članak Zadar ili neko mjesto iz Zadarske županije."""
    return bool(PLACES.search(_haystack(item)))


def topics(item, src=None):
    """Teme za filtre na stranici: Zadar, Županija, Otoci, Sport."""
    hay = _haystack(item)
    found = []
    if AREAS["zadar"].search(hay):
        found.append("Zadar")
    if AREAS["zupanija"].search(hay):
        found.append("Županija")
    if AREAS["otoci"].search(hay):
        found.append("Otoci")
    if SPORT.search(_haystack(item, full=False)):
        found.append("Sport")
    topic = (src or {}).get("topic") or item.get("source_topic")
    if topic and topic not in found:
        found.append(topic)
    return sorted(found, key=TOPICS.index)


def strip_tracking(url):
    p = urllib.parse.urlsplit(url)
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not k.startswith("utm_")]
    return urllib.parse.urlunsplit(p._replace(query=urllib.parse.urlencode(q)))


def main():
    with open(FEEDS_FILE, encoding="utf-8") as f:
        sources = [s for s in json.load(f) if s.get("enabled", True)]
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = {a["link"]: a for a in json.load(f)}
    except (FileNotFoundError, json.JSONDecodeError):
        cache = {}

    now = datetime.now(timezone.utc).isoformat()
    status = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(load_source, sources))
        fresh, info = [], {}
        for src, (items, errors) in zip(sources, results):
            status.append({"name": src["name"], "count": len(items), "ok": bool(items),
                           "feed": src.get("discovered"), "errors": [] if items else errors})
            for e in errors if not items else []:
                print(f"[{src['name']}] {e}", file=sys.stderr)
            print(f"[{src['name']}] {len(items)} članaka", file=sys.stderr)
            if src.get("kind") == "info":
                info[src["name"]] = [{k: i[k] for k in ("title", "link", "date", "summary")}
                                     for i in items[:3]]
                continue
            for i in items:
                old = cache.get(i["link"])
                if old and not i["image"]:
                    i["image"] = old.get("image")
                i["date"] = i["date"] or (old or {}).get("date") or now
                fresh.append(i)

        # Slike koje nisu u feedu dohvaćamo sa stranice članka (og:image).
        missing = [i for i in fresh if not i["image"]]
        for i, img in zip(missing, pool.map(lambda i: og_image(i["link"]), missing)):
            i["image"] = img

        # Položaj lica računamo samo jednom po slici (staro se čuva u predmemoriji).
        todo = []
        for i in fresh:
            old = cache.get(i["link"])
            if old and old.get("image") == i["image"] and "focus" in old:
                i["focus"] = old["focus"]
            elif i["image"]:
                todo.append(i)
        for i, focus in zip(todo, pool.map(lambda i: face_focus(i["image"]), todo)):
            i["focus"] = focus

    for i in fresh:
        i.pop("categories", None)
        i.pop("text", None)
        cache[i["link"]] = i
    enabled = {s["name"] for s in sources if s.get("kind") != "info"}
    cache = {k: a for k, a in cache.items() if a["source"] in enabled and is_local(a)}
    articles = sorted(cache.values(), key=lambda a: a["date"], reverse=True)[:MAX_TOTAL]

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=1)
    with open(os.path.join(os.path.dirname(CACHE_FILE), "status.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "sources": status}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(os.path.dirname(CACHE_FILE), "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    render(articles, sources, status, now, info)


def _title_words(a):
    words = re.findall(r"\w+", (a["title"] + " " + " ".join(a["summary"].split()[:20])).casefold())
    return {w[:6] for w in words if len(w) > 3}


def current_topics(a):
    """Teme po trenutnom places.txt; Sport ostaje ako je dodijeljen pri preuzimanju (rubrika ili portal)."""
    found = topics(a)
    if "Sport" in a.get("topics", []) and "Sport" not in found:
        found.append("Sport")
    return sorted(found, key=TOPICS.index)


def group_duplicates(articles):
    """Isti događaj s više portala spaja u jednu karticu (najnoviji članak + linkovi na ostale)."""
    groups = []
    for a in articles:
        words = _title_words(a)
        when = datetime.fromisoformat(a["date"])
        for g in groups:
            lead = g["lead"]
            if a["source"] in g["sources"]:
                continue
            if abs((datetime.fromisoformat(lead["date"]) - when).total_seconds()) > 48 * 3600:
                continue
            shared = words & g["words"]
            if len(shared) >= 4 and len(shared) / min(len(words), len(g["words"])) >= 0.4:
                lead["also"].append({k: a.get(k) for k in ("source", "link", "title", "summary", "image", "date")})
                lead["topics"] = sorted(set(lead["topics"]) | set(current_topics(a)), key=TOPICS.index)
                g["sources"].add(a["source"])
                break
        else:
            lead = dict(a, also=[], topics=current_topics(a))
            groups.append({"lead": lead, "words": words, "sources": {a["source"]}})
    return [g["lead"] for g in groups]


def render(articles, sources, status, now, info=None):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    try:
        with open(os.path.join(ROOT, "site.json"), encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    payload = json.dumps({"articles": group_duplicates(articles), "topics": TOPICS,
                          "sources": [s["name"] for s in sources if s.get("kind") != "info"],
                          "info": info or {}, "settings": settings,
                          "status": status, "updated": now}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*DATA*/null", payload))
    print(f"Spremljeno {len(articles)} članaka u site/index.html", file=sys.stderr)


if __name__ == "__main__":
    main()
