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
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(ROOT, "feeds.json")
CACHE_FILE = os.path.join(ROOT, "data", "articles.json")
OUT_DIR = os.path.join(ROOT, "site")
TEMPLATE = os.path.join(ROOT, "template.html")
PLACES_FILE = os.path.join(ROOT, "places.txt")

ZAGREB = ZoneInfo("Europe/Zagreb")
USER_AGENT = "Mozilla/5.0 (compatible; LokalniAgregator/1.0)"
TIMEOUT = 20
MAX_PER_SOURCE = 30      # koliko najnovijih članaka uzeti po portalu
MAX_TOTAL = 300          # koliko članaka čuvati i prikazati
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
_OG_DESC = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*>', re.I)
_CONTENT = re.compile(r'content=["\']([^"\']+)["\']', re.I)
_FEED_LINK = re.compile(
    r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>', re.I)
_HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)


def og_meta(url):
    """Slika (og:image) i opis (og:description) sa same stranice članka."""
    try:
        data, final = fetch(url, 60)
    except Exception:
        return None, None
    page = data[:300_000].decode("utf-8", "replace")
    image = desc = None
    m = _OG_IMAGE.search(page)
    if m:
        c = _CONTENT.search(m.group(0))
        if c:
            image = urllib.parse.urljoin(final, html.unescape(c.group(1)))
    m = _OG_DESC.search(page)
    if m:
        c = _CONTENT.search(m.group(0))
        if c:
            desc = summarize(html.unescape(c.group(1)))
    return image, desc


FACE_MODEL = os.path.join(ROOT, "models", "face_detection_yunet_2023mar.onnx")
_FACES = {}
_FACES_LOCK = threading.Lock()


def _detect_faces(cv2, img):
    """Pravokutnici lica (x, y, w, h). YuNet (neuronska mreža) prepoznaje i lica iz profila,
    sa strane i u polusjeni; Haar je rezerva ako model nije dostupan."""
    h, w = img.shape[:2]
    if os.path.exists(FACE_MODEL):
        if "yunet" not in _FACES:
            _FACES["yunet"] = cv2.FaceDetectorYN.create(FACE_MODEL, "", (w, h), 0.75, 0.3, 50)
        det = _FACES["yunet"]
        det.setInputSize((w, h))
        _, found = det.detect(img)
        return [] if found is None else [tuple(f[:4]) for f in found]
    if "haar" not in _FACES:
        _FACES["haar"] = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return list(_FACES["haar"].detectMultiScale(gray, scaleFactor=1.1, minNeighbors=6,
                                                minSize=(max(24, min(h, w) // 20),) * 2))


def face_box(url):
    """Područje s licima na slici, [vrh, dno, lijevo, desno] u postocima, da ga kartica ne odreže.

    Treba opencv-python-headless; bez njega (ili ako lica nema) vraća None i stranica
    koristi zadani položaj. Sitna lica u pozadini (manja od pola najvećeg) se zanemaruju.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        data, _ = fetch(url, 30)
    except Exception:
        return None
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    if img.shape[1] > 1200:
        k = 1200 / img.shape[1]
        img = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]
    if min(h, w) < 40:
        return None
    try:
        with _FACES_LOCK:
            faces = _detect_faces(cv2, img)
    except cv2.error as e:
        print(f"[lica] {url}: {e}", file=sys.stderr)
        return None
    faces = [f for f in faces if f[3] >= h * 0.04]
    if not faces:
        return None
    biggest = max(f[3] for f in faces)
    faces = [f for f in faces if f[3] >= biggest / 2]
    # Malo prostora iznad (kosa) i ispod (brada) samog okvira lica.
    top = max(0, min(f[1] - 0.15 * f[3] for f in faces))
    bottom = min(h, max(f[1] + 1.1 * f[3] for f in faces))
    left = max(0, min(f[0] for f in faces))
    right = min(w, max(f[0] + f[2] for f in faces))
    return [round(100 * float(v) / size, 1)
            for v, size in ((top, h), (bottom, h), (left, w), (right, w))]


FLIGHTS_URL = "https://www.zadar-airport.hr/wp-content/themes/hello-elementor-child/current-flights.json"


def airport_flights():
    """Današnji dolasci i odlasci s javnog popisa Zračne luke Zadar (isti koji koristi njihova stranica)."""
    try:
        data, _ = fetch(FLIGHTS_URL)
        rows = json.loads(data)
    except Exception as e:  # noqa: BLE001
        print(f"[Zračna luka Zadar] {e}", file=sys.stderr)
        return None
    flights = []
    for r in rows:
        flights.append({
            "dir": "dolazak" if r.get("AD") == "ARRIVAL" else "odlazak",
            "flight": r.get("brlet", ""), "airline": (r.get("operlong") or "").title(),
            "city": r.get("fromto", ""), "via": r.get("via", ""),
            "time": r.get("schtime", ""), "est": r.get("esttime", ""), "act": r.get("acttime", ""),
            "status": r.get("comment", ""), "gate": r.get("gate", ""),
        })
    flights.sort(key=lambda f: f["time"])
    return flights


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
    if src.get("combine"):
        return load_combined(src)
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
            items = PARSERS[src["parser"]](data, final) if src.get("parser") else parse_feed(data, final)
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

    return finish_items(src, items, errors)


_MUP_ITEM = re.compile(r"<div class='news_item'><a href='([^']+)'><span class='h3'>(.*?)</span></a>(.*?)"
                       r"<span class='date'>(\d{1,2})\.(\d{1,2})\.(\d{4})", re.S)


def parse_mup_news(data, base_url):
    """Popis vijesti s MUP-ovih stranica policijskih uprava (nemaju RSS)."""
    page = data.decode("utf-8", "replace")
    today = datetime.now(ZAGREB).date()
    items = []
    for link, title, teaser, d, m, y in _MUP_ITEM.findall(page):
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        day = datetime(int(y), int(m), int(d), 12, tzinfo=ZAGREB)
        if title == "Sažetak dnevnih događanja":
            title = f"Sažetak dnevnih događanja PU zadarske, {int(d)}. {int(m)}."
        it = _item(title, link, None, teaser, "", None, base_url, [])
        # Na popisu je samo datum: današnje vijesti dobivaju vrijeme kad ih prvi put vidimo.
        it["date"] = None if day.date() == today else day.astimezone(timezone.utc).isoformat()
        items.append(it)
    return items


def parse_hrt_radio(data, base_url):
    """Radio Zadar (radio.hrt.hr/radio-zadar) nema RSS; popis članaka je u __NEXT_DATA__ naslovnice."""
    page = data.decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    found = {}

    def walk(o):
        if isinstance(o, dict):
            url = o.get("url") or ""
            if o.get("displayText") and "/radio-zadar/" in url and o.get("originalPublishedUtc"):
                found.setdefault(url, o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    items = []
    for url, o in found.items():
        it = _item(o["displayText"], url, None, "", "", (o.get("mediaImage") or {}).get("path"), base_url, [])
        it["date"] = datetime.fromisoformat(o["originalPublishedUtc"][:19] + "+00:00").isoformat()
        it["need_summary"] = True
        items.append(it)
    return sorted(items, key=lambda i: i["date"], reverse=True)


PARSERS = {"mup": parse_mup_news, "hrt_radio": parse_hrt_radio}


def load_combined(src):
    """Spaja sve feedove portala (npr. kad rubrika kasni, a opći feed je svjež)."""
    items, errors, seen = [], [], set()
    for url in src.get("feeds", []):
        try:
            data, final = fetch_retry(url, src.get("timeout", TIMEOUT), src.get("retries", 1))
            for i in parse_feed(data, final):
                key = strip_tracking(i["link"])
                if key not in seen:
                    seen.add(key)
                    items.append(i)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    items.sort(key=lambda i: i["date"] or "", reverse=True)
    return finish_items(src, items, errors) if items else ([], errors)


# HRT u feed stavlja i samostalne video priloge kojima je link gola .mp4 datoteka
# (bez stranice članka); njihov poslužitelj takve linkove odbija ("Access Denied").
_MEDIA = re.compile(r"\.(mp4|m3u8|mp3|mov|webm)(\?|$)", re.I)


def is_media(link):
    return bool(_MEDIA.search(link))


# Ručno izbačeni članci (spominju mjesto iz županije, ali nisu zadarska vijest):
# jedan link po retku u blocked.txt, # za komentar.
def _load_blocked():
    try:
        lines = open(os.path.join(ROOT, "blocked.txt"), encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return set()
    return {l.strip().rstrip("/") for l in lines if l.strip() and not l.lstrip().startswith("#")}


BLOCKED = _load_blocked()


def is_blocked(link):
    return link.rstrip("/") in BLOCKED


def finish_items(src, items, errors):
    if src.get("link_contains") or src.get("categories"):
        # Članak iz druge rubrike (sport, kultura...) ulazi ako Zadar ili mjesto iz
        # županije spominje u naslovu ili uvodu, kao kod nacionalnih portala.
        items = [i for i in items if wanted(src, i) or is_about_zadar(i)]
    if src.get("scope") == "national":
        items = [i for i in items if is_about_zadar(i)]
    elif src.get("local_only", True) and src.get("kind") != "info":
        items = [i for i in items if is_local(i)]
    items = [i for i in items if i["title"] and i["link"] and not is_media(i["link"])
             and not is_blocked(i["link"])][:MAX_PER_SOURCE]
    for i in items:
        i["source"] = src["name"]
        if src.get("topic"):
            i["source_topic"] = src["topic"]
        i["topics"] = topics(i, src)
        i["source_url"] = src.get("site", "")
        i["link"] = strip_tracking(i["link"])
        # Npr. Županija u feedu daje sličicu (_S.jpg); "image_replace" je zamijeni većom.
        for old, new in src.get("image_replace", {}).items():
            if i.get("image"):
                i["image"] = i["image"].replace(old, new)
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

        # Slike koje nisu u feedu dohvaćamo sa stranice članka (og:image), a za izvore
        # bez uvoda u popisu (Radio Zadar) i uvod iz og:description.
        for i in fresh:
            old = cache.get(i["link"])
            if old and not i["summary"]:
                i["summary"] = old.get("summary", "")
        missing = [i for i in fresh if (i.pop("need_summary", False) and not i["summary"]) or not i["image"]]
        for i, (img, desc) in zip(missing, pool.map(lambda i: og_meta(i["link"]), missing)):
            i["image"] = i["image"] or img
            i["summary"] = i["summary"] or desc or ""

    for i in fresh:
        i.pop("categories", None)
        i.pop("text", None)
        old = cache.get(i["link"])
        if old and i["image"] and old.get("face_img") == i["image"]:
            i["face"], i["face_img"] = old.get("face"), old["face_img"]
        cache[i["link"]] = i
    enabled = {s["name"] for s in sources if s.get("kind") != "info"}
    # Izvori s "local_only": false (npr. KK Zadar, Liburnija) su sami po sebi zadarski.
    trusted = {s["name"] for s in sources if s.get("local_only") is False}
    national = {s["name"] for s in sources if s.get("scope") == "national"}
    cache = {k: a for k, a in cache.items() if a["source"] in enabled and not is_media(a["link"])
             and not is_blocked(a["link"])
             and (a["source"] in trusted
                  or (is_about_zadar(a) if a["source"] in national else is_local(a)))}
    articles = sorted(cache.values(), key=lambda a: a["date"], reverse=True)[:MAX_TOTAL]

    # Položaj lica računamo jednom po slici; čuva se u predmemoriji dok se slika ne promijeni.
    todo = []
    for a in articles:
        a.pop("focus", None)
        if a.get("image") and a.get("face_img") != a["image"]:
            todo.append(a)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for a, face in zip(todo, pool.map(lambda a: face_box(a["image"]), todo)):
            a["face"], a["face_img"] = face, a["image"]

    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=1)
    with open(os.path.join(os.path.dirname(CACHE_FILE), "status.json"), "w", encoding="utf-8") as f:
        json.dump({"updated": now, "sources": status}, f, ensure_ascii=False, indent=1)
    info["flights"] = airport_flights()
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


# Naslovnica i po jedna stranica za svaku rubriku (vlastiti link za Google i dijeljenje).
PAGE_SLUGS = {"Zadar": "zadar", "Županija": "zupanija", "Otoci": "otoci", "Sport": "sport"}
PAGE_DESC = {
    None: "Najnovije vijesti iz Zadra i Zadarske županije s lokalnih portala na jednom mjestu.",
    "Zadar": "Najnovije vijesti iz grada Zadra s lokalnih portala na jednom mjestu.",
    "Županija": "Vijesti iz gradova i općina Zadarske županije na jednom mjestu.",
    "Otoci": "Vijesti sa zadarskih otoka: Ugljan, Pašman, Dugi otok, Pag, Silba, Olib, Iž i ostali.",
    "Sport": "Zadarski sport na jednom mjestu: KK Zadar, HNK Zadar i ostali klubovi.",
}


PAGE_H1 = {None: "Vijesti iz Zadra i županije", "Zadar": "Vijesti iz Zadra", "Županija": "Vijesti iz županije",
           "Otoci": "Vijesti sa zadarskih otoka", "Sport": "Zadarski sport"}


def prerender(articles, topic, limit=30):
    """Naslovi i uvodi kao obični HTML, da ih tražilice i pregled bez JavaScripta vide;
    skripta ih na stranici zamijeni interaktivnim popisom."""
    esc = html.escape
    out = []
    for a in [a for a in articles if not topic or topic in (a.get("topics") or [])][:limit]:
        out.append(f'<article><div class="body"><div class="meta"><b>{esc(a["source"])}</b>'
                   f'<time datetime="{esc(a["date"])}">{esc(a["date"][:10])}</time></div>'
                   f'<h2><a href="{esc(a["link"])}">{esc(a["title"])}</a></h2>'
                   + (f'<p>{esc(a["summary"])}</p>' if a.get("summary") else "") + "</div></article>")
    return "".join(out)


def jsonld(site_url, topic, url):
    data = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebSite", "@id": site_url + "#site", "url": site_url, "name": "idassa.click",
         "alternateName": "Idassa – vijesti iz Zadra", "inLanguage": "hr", "description": PAGE_DESC[None]},
        {"@type": "CollectionPage", "url": url, "name": PAGE_H1[topic], "description": PAGE_DESC[topic],
         "isPartOf": {"@id": site_url + "#site"}, "inLanguage": "hr",
         "about": {"@type": "Place", "name": "Zadar", "containedInPlace": "Zadarska županija, Hrvatska"}},
    ]}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render(articles, sources, status, now, info=None):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    try:
        with open(os.path.join(ROOT, "site.json"), encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    grouped = group_duplicates(articles)
    payload = json.dumps({"articles": grouped, "topics": TOPICS,
                          "pages": PAGE_SLUGS,
                          "sources": [s["name"] for s in sources if s.get("kind") != "info"],
                          "sites": {s["name"]: s.get("site", "") for s in sources if s.get("kind") != "info"},
                          "info": info or {}, "settings": settings,
                          "status": status, "updated": now}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
    site_url = settings.get("url", "")
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    shutil.copytree(os.path.join(ROOT, "static"), OUT_DIR)
    for topic in [None, *TOPICS]:
        slug = PAGE_SLUGS.get(topic, "")
        page = (tpl.replace("/*DATA*/null", payload)
                .replace("/*PAGE*/null", json.dumps(topic, ensure_ascii=False))
                .replace("{{BASE}}", "../" if slug else "./")
                .replace("{{TITLE}}", f"{topic} – idassa.click" if topic else "idassa.click – vijesti iz Zadra")
                .replace("{{DESC}}", PAGE_DESC[topic])
                .replace("{{URL}}", site_url + (slug + "/" if slug else ""))
                .replace("{{H1}}", PAGE_H1[topic])
                .replace("{{JSONLD}}", jsonld(site_url, topic, site_url + (slug + "/" if slug else "")))
                .replace("{{PRERENDER}}", prerender(grouped, topic))
                .replace("{{SITE}}", site_url))
        out = os.path.join(OUT_DIR, slug) if slug else OUT_DIR
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)
    if settings.get("private"):
        # Zatvorena stranica (pod lozinkom): bez RSS-a i sitemapa, tražilicama zabranjeno.
        with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
            f.write("User-agent: *\nDisallow: /\n")
    else:
        write_feed(grouped, site_url, now)
        write_sitemap(site_url, now)
    print(f"Spremljeno {len(articles)} članaka u site/", file=sys.stderr)


def write_sitemap(site_url, now):
    """sitemap.xml i robots.txt za tražilice (naslovnica i stranice rubrika)."""
    urls = [site_url] + [site_url + slug + "/" for slug in PAGE_SLUGS.values()]
    body = "".join(f"<url><loc>{u}</loc><lastmod>{now[:10]}</lastmod><changefreq>hourly</changefreq>"
                   f"<priority>{'1.0' if u == site_url else '0.8'}</priority></url>" for u in urls)
    with open(os.path.join(OUT_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>\n')
    with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(f"User-agent: *\nAllow: /\n\nSitemap: {site_url}sitemap.xml\n")


def write_feed(articles, site_url, now, limit=40):
    """RSS stranice (site/feed.xml): za čitače vijesti i za newsletter koji iz njega
    svako jutro sam složi i pošalje pregled vijesti."""
    from xml.sax.saxutils import escape
    rfc = lambda iso: email.utils.format_datetime(datetime.fromisoformat(iso))
    items = []
    for a in articles[:limit]:
        desc = escape((a.get("summary") or "") + f" (Izvor: {a['source']})")
        img = (f'<enclosure url="{escape(a["image"], {chr(34): "&quot;"})}" type="image/jpeg" length="0"/>'
               if a.get("image") else "")
        items.append(f"<item><title>{escape(a['title'])}</title><link>{escape(a['link'])}</link>"
                     f"<guid isPermaLink=\"true\">{escape(a['link'])}</guid><pubDate>{rfc(a['date'])}</pubDate>"
                     f"<description>{desc}</description>{img}</item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>'
           f"<title>Vijesti iz Zadra i županije</title><link>{escape(site_url)}</link>"
           "<description>Najnovije vijesti iz Zadra i Zadarske županije s lokalnih portala.</description>"
           f"<language>hr</language><lastBuildDate>{rfc(now)}</lastBuildDate>"
           + "".join(items) + "</channel></rss>\n")
    with open(os.path.join(OUT_DIR, "feed.xml"), "w", encoding="utf-8") as f:
        f.write(xml)


if __name__ == "__main__":
    main()
