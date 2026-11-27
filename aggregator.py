#!/usr/bin/env python3
"""Skuplja članke s lokalnih portala (RSS) i generira statičnu stranicu.

Za svaki članak prikazuje se samo naslov, jedna slika, prvih nekoliko
rečenica i link na izvorni portal. Pokretanje: python3 aggregator.py
"""
import concurrent.futures
import email.utils
import html
import json
import zlib
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.abspath(__file__))
FEEDS_FILE = os.path.join(ROOT, "feeds.json")
CACHE_FILE = os.path.join(ROOT, "data", "articles.json")
OUT_DIR = os.path.join(ROOT, "site")
TEMPLATE = os.path.join(ROOT, "template.html")
PLACES_FILE = os.path.join(ROOT, "places.txt")

ZAGREB = ZoneInfo("Europe/Zagreb")
USER_AGENT = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"  # s kontaktom (Reddit ga traži)
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


def _parse_xml(data):
    """XML feeda; neki WordPress dodaci dvaput upišu isti atribut u <rss> (npr. xmlns:media na 023.hr),
    što je neispravan XML, pa ponovljene atribute u zaglavlju izbacimo i pokušamo opet."""
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        text = data.decode("utf-8", "replace")
        m = re.search(r"<(rss|feed|rdf:RDF)\b[^>]*>", text)
        if not m:
            raise
        seen, attrs = set(), []
        for a in re.findall(r'\s[\w:.-]+\s*=\s*"[^"]*"', m.group(0)):
            name = a.split("=")[0].strip()
            if name not in seen:
                seen.add(name)
                attrs.append(a)
        fixed = text[:m.start()] + "<" + m.group(1) + "".join(attrs) + ">" + text[m.end():]
        return ET.fromstring(fixed.encode("utf-8"))


def parse_feed(data, base_url):
    """Vraća listu dict-ova: title, link, date, summary, image."""
    root = _parse_xml(data)
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
        # Neki portali (eZadar) u naslovu imaju tvrde razmake (\xa0) pa se naslov ne lomi u retke.
        "title": html.unescape(title).replace("\xa0", " ").strip(),
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


def fix_image_url(url):
    if not url:
        return url
    p = urllib.parse.urlsplit(url)
    host = p.netloc.removeprefix("www.")
    for prefix in (f"/{host}/", f"/www.{host}/"):
        if p.path.startswith(prefix):
            return urllib.parse.urlunsplit(p._replace(path=p.path[len(prefix) - 1:]))
    return url


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
    if not image:
        # WordPress bez og:image (npr. Opća bolnica): istaknuta slika članka ima klasu wp-post-image
        m = re.search(r'<img\b[^>]*\bwp-post-image\b[^>]*>', page)
        c = m and re.search(r'\ssrc="([^"]+)"', m.group(0))
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


FLIGHTS_WEEK_URL = "https://www.zadar-airport.hr/pregled-letova/?sekcija=putnici"


def airport_flights():
    """Današnji i sutrašnji dolasci i odlasci Zračne luke Zadar. Tjedni raspored je ugrađen u stranicu
    "Pregled letova" (const weeklyFlights); current-flights.json ima samo letove s živim stanjem
    i ujutro još jučerašnje, pa se njime samo osvježava stanje (sletio, poletio, kasni)."""
    rows = []
    try:
        data, _ = fetch(FLIGHTS_WEEK_URL)
        m = re.search(r"const weeklyFlights = (\[.*?\]);", data.decode("utf-8", "replace"), re.S)
        rows = json.loads(m.group(1)) if m else []
    except Exception as e:  # noqa: BLE001
        print(f"[Zračna luka Zadar, tjedni] {e}", file=sys.stderr)
    try:
        data, _ = fetch(FLIGHTS_URL)
        live = {(r.get("brlet"), r.get("schtime")): r for r in json.loads(data)}
    except Exception as e:  # noqa: BLE001
        print(f"[Zračna luka Zadar] {e}", file=sys.stderr)
        live = {}
    if not rows and not live:
        return None
    rows = [live.pop((r.get("brlet"), r.get("schtime")), r) for r in rows] + list(live.values())
    today = datetime.now(ZAGREB).date()
    keep = {str(today), str(today + timedelta(days=1))}
    flights = []
    for r in rows:
        if (r.get("schtime") or "")[:10] not in keep:
            continue
        flights.append({
            "dir": "dolazak" if r.get("AD") == "ARRIVAL" else "odlazak",
            "flight": r.get("brlet", ""), "airline": (r.get("operlong") or "").title(),
            "city": r.get("fromto", ""), "via": r.get("via", ""),
            "time": r.get("schtime", ""), "est": r.get("esttime", ""), "act": r.get("acttime", ""),
            "status": r.get("comment", "") or "", "gate": r.get("gate", ""),
        })
    flights.sort(key=lambda f: f["time"])
    return flights


# videi na ekavici ne idu u vrpcu: jezik zvuka "sr", ćirilica, tipične ekavske riječi ili kanal s popisa
_EKAVICA = re.compile(
    r"[\u0400-\u04FF]|\b(?:lep[aeiou]?|lepo|reka|reke|mleko|vreme|vremena|deca|dece|dete|deteta|devojk\w*|čovek\w*|"
    r"mesto|mesta|pesm\w*|nedelj\w*|uvek|ovde|gde|posle|ceo|cela|celu|celog|reč|vest|vesti|sneg\w*|hteo|"
    r"fudbal\w*|hiljad\w*|obezbe\w*|takmič\w*|pobed\w*|istorij\w*|Srbij\w*|srpsk\w*|Beograd\w*)\b", re.I)
_YT_BLOCKED_FILE = os.path.join(ROOT, "yt_blocked.txt")
YT_BLOCKED = {l.strip().lower() for l in open(_YT_BLOCKED_FILE, encoding="utf-8")
              if l.strip() and not l.startswith("#")} if os.path.exists(_YT_BLOCKED_FILE) else set()


def youtube_search(prev=None):
    """Videi o Zadru i županiji s YouTubea (YouTube Data API, ključ u tajni YT_API_KEY).
    Pretraga košta 100 od 10.000 dnevnih bodova, pa se radi najviše jednom na sat,
    a između se koristi prošli rezultat. Ostaju samo videi s mjestom iz županije u naslovu ili opisu, iz zadnjih 30 dana i ne na ekavici."""
    key = os.environ.get("YT_API_KEY")
    if not key:
        return None
    now = datetime.now(timezone.utc)
    if prev and prev.get("v") == 2 and prev.get("fetched") and now - datetime.fromisoformat(prev["fetched"]) < timedelta(minutes=55):
        return prev
    after = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = ("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&order=date&maxResults=50"
           f"&q=Zadar%7CZadarska%20%C5%BEupanija&regionCode=HR&relevanceLanguage=hr&publishedAfter={after}&key={key}")
    try:
        data, _ = fetch(url)
        rows = json.loads(data).get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"[YouTube] {str(e).replace(key, '***')}", file=sys.stderr)
        return prev
    # jezik zvuka (defaultAudioLanguage) dolazi samo iz videos endpointa; 1 bod za do 50 videa
    langs = {}
    ids = ",".join(r.get("id", {}).get("videoId", "") for r in rows if r.get("id", {}).get("videoId"))
    if ids:
        try:
            meta, _ = fetch(f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={ids}&key={key}")
            for v in json.loads(meta).get("items", []):
                sn = v.get("snippet", {})
                langs[v["id"]] = (sn.get("defaultAudioLanguage") or sn.get("defaultLanguage") or "").lower()
        except Exception as e:  # noqa: BLE001
            print(f"[YouTube jezik] {str(e).replace(key, '***')}", file=sys.stderr)
    videos = []
    for r in rows:
        sn, vid = r.get("snippet", {}), r.get("id", {}).get("videoId")
        title, desc = html.unescape(sn.get("title", "")), html.unescape(sn.get("description", ""))
        if not vid or not is_local({"title": title, "summary": desc, "link": "", "categories": [], "text": ""}):
            continue
        channel = sn.get("channelTitle", "")
        if (langs.get(vid, "").startswith(("sr", "mk")) or channel.strip().lower() in YT_BLOCKED
                or _EKAVICA.search(f"{title} {desc} {channel}")):
            continue
        thumbs = sn.get("thumbnails", {})
        videos.append({"id": vid, "title": title, "channel": sn.get("channelTitle", ""),
                       "date": parse_date(sn.get("publishedAt")), "link": f"https://www.youtube.com/watch?v={vid}",
                       "image": (thumbs.get("medium") or thumbs.get("high") or {}).get("url", "")})
    return {"v": 2, "fetched": now.isoformat(), "items": videos[:15]}


REDDIT_URL = "https://www.reddit.com/r/zadar/new/.rss"


def reddit_posts(limit=8):
    """Najnovije teme s Reddita r/Zadar (javni Atom feed)."""
    try:
        data, _ = fetch(REDDIT_URL)
    except Exception as e:  # noqa: BLE001
        print(f"[Reddit r/Zadar] {e}", file=sys.stderr)
        return None
    out = []
    for entry in re.findall(r"<entry>(.*?)</entry>", data.decode("utf-8", "replace"), re.S)[:limit]:
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        link = re.search(r'<link href="([^"]+)"', entry)
        date = re.search(r"<published>(.*?)</published>", entry)
        if title and link:
            out.append({"title": html.unescape(html.unescape(title.group(1))).strip(), "link": link.group(1),
                        "date": date.group(1) if date else ""})
    return out


RETRO_URL = "https://www.retrozadar.com/blog"


def retro_posts():
    """Sve objave bloga Retro Zadar (povijest grada); popis čitamo iz podataka stranice /blog."""
    try:
        data, _ = fetch(RETRO_URL)
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', data.decode("utf-8", "replace"), re.S)
        posts = json.loads(m.group(1))["props"]["pageProps"]["data"]
    except Exception as e:  # noqa: BLE001
        print(f"[Retro Zadar] {e}", file=sys.stderr)
        return None
    out = []
    for p in posts:
        slug = (p.get("slug") or {}).get("current")
        img = (((p.get("mainImage") or {}).get("asset")) or {}).get("url")
        if not slug or not p.get("title"):
            continue
        out.append({"title": p["title"].strip(), "link": f"https://www.retrozadar.com/posts/{slug}",
                    "date": p.get("publishedAt") or "", "summary": (p.get("kratkiOpis") or "").strip(),
                    "image": img + "?w=480&h=300&fit=crop&auto=format" if img else None})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out


WATER_URL = "https://www.vodovod-zadar.hr/obavijesti"
_WATER_ROW = re.compile(r'<time datetime="([\d-]+)">.*?<div class="content clearfix">(.*?)</div>', re.S)


def water_notices(limit=6):
    """Obavijesti Vodovoda Zadar potrošačima (prekidi opskrbe vodom); stranica nema RSS."""
    try:
        data, _ = fetch(WATER_URL)
    except Exception as e:  # noqa: BLE001
        print(f"[Vodovod Zadar] {e}", file=sys.stderr)
        return None
    out = []
    for day, body in _WATER_ROW.findall(data.decode("utf-8", "replace"))[:limit]:
        text = html.unescape(re.sub(r"<[^>]+>|\s+", " ", body)).strip()
        if text:
            out.append({"day": day, "text": text})
    return out


POWER_URL = "https://www.hep.hr/ods/bez-struje/19?dp=zadar&el=ZD&datum={}"
_POWER_ROW = re.compile(
    r'<div class="mjesto[^"]*">(.*?)</div>\s*<div class="vrijeme[^"]*">(.*?)<div class="clear">', re.S)


def _power_field(block, label):
    m = re.search(rf"<strong>{label}:</strong>(.*?)</div>", block, re.S)
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", m.group(1))).strip(" .") if m else ""


def power_outages(days=2):
    """Planirana i neplanirana isključenja struje Elektre Zadar (HEP ODS, stranica "Bez struje")."""
    out = []
    for n in range(days):
        day = (datetime.now(ZAGREB) + timedelta(days=n)).strftime("%d.%m.%Y")
        try:
            data, _ = fetch(POWER_URL.format(day))
        except Exception as e:  # noqa: BLE001
            print(f"[HEP Bez struje] {e}", file=sys.stderr)
            return None
        for place, when in _POWER_ROW.findall(data.decode("utf-8", "replace")):
            kada = re.search(r'<div class="kada">(.*?)</div>', when, re.S)
            tip = re.search(r'<div class="tip">(.*?)</div>', when, re.S)
            out.append({"day": day, "place": _power_field(place, "Mjesto"), "street": _power_field(place, "Ulica"),
                        "note": _power_field(place, "Napomena"),
                        "time": kada.group(1).strip() if kada else "",
                        "unplanned": bool(tip and "neplan" in tip.group(1))})
    return out


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


HR_MONTHS = {m: i for i, m in enumerate(("siječnja", "veljače", "ožujka", "travnja", "svibnja", "lipnja", "srpnja",
                                           "kolovoza", "rujna", "listopada", "studenoga", "prosinca"), 1)}
HR_MONTHS["studenog"] = 11
_UNIZD_ITEM = re.compile(
    r'<article class="l-item c-item">.*?<img src="([^"]+)".*?c-item__date[^>]*>\s*(\d+)\.\s*(\w+)\s+(\d{4})'
    r'.*?c-item__title[^>]*><a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def parse_unizd(data, base_url):
    """Novosti Sveučilišta u Zadru (unizd.hr, DNN EasyDNNNews bez RSS-a)."""
    page = data.decode("utf-8", "replace")
    today = datetime.now(ZAGREB).date()
    items = []
    for img, d, month, y, link, title in _UNIZD_ITEM.findall(page):
        m = HR_MONTHS.get(month.lower())
        if not m:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        img = None if "placeholder" in img else img.split("?")[0]
        it = _item(title, link, None, "", "", img, base_url, [])
        day = datetime(int(y), m, int(d), 12, tzinfo=ZAGREB)
        it["date"] = None if day.date() == today else day.astimezone(timezone.utc).isoformat()
        it["need_summary"] = True
        items.append(it)
    return items


_CISTOCA_ITEM = re.compile(r'<a class="newsitem[^"]*" href="([^"]+)">(.*?)</a>', re.S)


def parse_cistoca(data, base_url):
    """Novosti Čistoće Zadar (cistoca-zadar.hr/novosti/novosti-1/, nema RSS-a)."""
    page = data.decode("utf-8", "replace")
    today = datetime.now(ZAGREB).date()
    items = []
    for link, body in _CISTOCA_ITEM.findall(page):
        title = re.search(r"<h3>(.*?)</h3>", body, re.S)
        day = re.search(r'datetime="(\d{4})-(\d{2})-(\d{2})"', body)
        if not title or not day:
            continue
        img = re.search(r'<img[^>]+src="([^"]+)"', body)
        summary = re.search(r"<p>(.*?)</p>", body, re.S)
        clean = lambda t: html.unescape(re.sub(r"<[^>]+>", "", t)).strip()
        it = _item(clean(title.group(1)), link, None, clean(summary.group(1)) if summary else "", "",
                   img.group(1) if img else None, base_url, [])
        d = datetime(*map(int, day.groups()), 12, tzinfo=ZAGREB)
        it["date"] = None if d.date() == today else d.astimezone(timezone.utc).isoformat()
        items.append(it)
    return items


PARSERS = {"mup": parse_mup_news, "hrt_radio": parse_hrt_radio, "unizd": parse_unizd, "cistoca": parse_cistoca}


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
        # "only_categories": bez te iznimke (npr. Bolnica: samo Vijesti, bez natječaja i javne nabave)
        items = [i for i in items if wanted(src, i) or (not src.get("only_categories") and is_about_zadar(i))]
    if src.get("scope") == "national":
        items = [i for i in items if is_about_zadar(i)]
    elif src.get("local_only", True) and src.get("kind") != "info":
        items = [i for i in items if is_local(i)]
    if src.get("exclude_title"):
        # npr. Sveučilište: bez natječaja, javnih poziva, rezultata izbora i sličnih službenih obavijesti
        skip = re.compile(src["exclude_title"], re.I)
        items = [i for i in items if not skip.search(i["title"])]
    items = [i for i in items if i["title"] and i["link"] and not is_media(i["link"])
             and not is_blocked(i["link"])][:MAX_PER_SOURCE]
    for i in items:
        # NASLOVI VELIKIM SLOVIMA (npr. Bolnica) prebacuju se u obična: prvo slovo veliko, ostalo malo
        letters = [c for c in i["title"] if c.isalpha()]
        if len(letters) > 8 and sum(c.isupper() for c in letters) > 0.8 * len(letters):
            t = i["title"].lower()
            t = re.sub(r"\w", lambda m: m.group(0).upper(), t, count=1)
            # imena mjesta (Diklo, Zadar, Ugljan...) ponovno velikim početnim slovom
            i["title"] = re.sub(r"\w+", lambda m: m.group(0).capitalize()
                                if (p := PLACES.search(m.group(0))) and p.span() == (0, len(m.group(0))) else m.group(0), t)
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
    short = _no_memorial(item["title"] + " " + item["summary"])
    if CERTAIN_PLACES.search(short):
        return True
    return bool(UNSURE_PLACES.search(short) and CERTAIN_PLACES.search(_haystack(item)))
SPORT = _words([r"sport\w*", r"nogomet\w*", r"košark\w*", r"vaterpol\w*", r"rukomet\w*",
                r"odbojk\w*", r"futsal\w*", r"tenis\w*", r"jedr\w*", r"veslač\w*", r"atlet\w*",
                r"plivač\w*", r"boks\w*", r"žnl", r"hnl", r"aba lig\w*", r"kk", r"nk"])
# HNK Zadar je kazalište (nogometni klub je NK Zadar), pa "HNK" ne znači sport.
THEATRE = re.compile(r"\bHNK Zadar|kazališ\w*|predstav\w*", re.I)
TOPICS = ["Zadar", "Županija", "Otoci", "Sport"]


# "Dan sjećanja na žrtvu Vukovara i Škabrnje" (18. 11.) spominje Škabrnju u gotovo svakom članku o Vukovaru;
# sam taj izraz ne čini vijest zadarskom (Stipe, 2026-11-04). Škabrnja se broji samo kad se spominje drukčije.
_MEMORIAL = re.compile(r"vukovar\w*\s*(?:,|\si)\s*[šs]kabrnj\w*|[šs]kabrnj\w*\s*(?:,|\si)\s*vukovar\w*", re.I)


# U zapisnicima utakmica ("Sudac: Ivan Vukančić (Benkovac)") mjesto suca ne čini utakmicu zadarskom.
_OFFICIALS = re.compile(r"\b(?:glavn\w* )?(?:sud(?:ac|ci|ije|ica)|pomoćn\w* sud\w*|delegat|opunomoćenik|kontrolor\w*)\s*:[^.\n]*", re.I)


# Ulice nazvane po mjestima ("Škabrnjska ulica" u Zagrebu, "Zadarska cesta" u Splitu) ne čine vijest zadarskom
# (Stipe, 2026-11-11: jutarnji.hr, Zagreb bez vode u Škabrnjskoj ulici).
_STREETS = re.compile(r"\b\w+(?:sk|čk|šk|ck)\w*\s+(?:ulic\w*|cest\w*|trg\w*)", re.I)


def _no_memorial(text):
    return _STREETS.sub(" ", _OFFICIALS.sub(" ", _MEMORIAL.sub(" ", text)))


def _haystack(item, full=True):
    slug = urllib.parse.unquote(urllib.parse.urlsplit(item["link"]).path).replace("-", " ").replace("/", " ")
    parts = [item["title"], slug, " ".join(item.get("categories", []))]
    if full:
        parts += [item["summary"], item.get("text", "")]
    return _no_memorial(" ".join(parts))


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
        # Neki portali (023.hr) u feed upišu adresu slike s udvostručenom domenom:
        # https://023.hr/023.hr/wp-content/... (404) -> https://023.hr/wp-content/...
        for i in fresh:
            i["image"] = fix_image_url(i["image"])
        # Izvor bez slike u članku (npr. Sveučilište) dobije jednu od fotografija s vlastitog weba ("fallback_images").
        backups = {s["name"]: s["fallback_images"] for s in sources if s.get("fallback_images")}
        for i in fresh:
            pics = backups.get(i["source"])
            if pics and (not i["image"] or "placeholder" in i["image"] or "Logo" in i["image"] or "mode=crop" in i["image"]):
                i["image"] = pics[zlib.crc32(i["link"].encode()) % len(pics)]

    # Obavijesti Liburnije za okvir "Autobusi": spremaju se posebno, jer na naslovnici ostaje samo
    # ~300 najnovijih članaka pa bi obavijesti starije od dan-dva nestale iz okvira.
    bus = sorted((i for i in fresh if i["source"] == "Liburnija Zadar"), key=lambda i: i["date"], reverse=True)
    info["buses"] = [{k: i[k] for k in ("title", "link", "date")} for i in bus[:5]] or None
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
    # Isti članak isti portal ponekad objavi pod dvije rubrike (npr. Zadarski list /style/ i /ostalo/scena/):
    # ostaje samo jedan po izvoru i naslovu.
    # Isto vrijedi kad portal promijeni naslov i adresu, a broj članka ostane (Index: .../2852125.aspx)
    # ili kad je isti članak s istom slikom u isto vrijeme.
    fresh_links = {i["link"] for i in fresh}
    seen, unique = set(), []
    for a in sorted(cache.values(), key=lambda a: (a["date"], a["link"] in fresh_links), reverse=True):
        keys = {(a["source"], "t", re.sub(r"\W+", " ", a["title"].lower()).strip())}
        num = re.findall(r"[/-](\d{6,})(?:\.\w+)?/?$", urllib.parse.urlsplit(a["link"]).path)
        if num:
            keys.add((a["source"], "n", num[-1]))
        if a.get("image"):
            keys.add((a["source"], "i", a["date"], a["image"]))
        if not keys & seen:
            unique.append(a)
        seen |= keys
    articles = unique[:MAX_TOTAL]
    # Izvori koji rijetko objavljuju (Sveučilište, Bolnica, Liburnija...) ne smiju ispasti zbog ograničenja:
    # svaki izvor zadržava do 3 svoja najnovija članka iz zadnjih 14 dana, niže na popisu.
    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    kept = {a["source"] for a in articles}
    extra, per = [], {}
    for a in unique[MAX_TOTAL:]:
        if a["source"] not in kept and a["date"] >= cutoff and per.get(a["source"], 0) < 3:
            per[a["source"]] = per.get(a["source"], 0) + 1
            extra.append(a)
    articles += extra

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
    info["power"] = power_outages()
    info["water"] = water_notices()
    info["reddit"] = reddit_posts()
    try:
        with open(os.path.join(os.path.dirname(CACHE_FILE), "info.json"), encoding="utf-8") as f:
            prev = json.load(f)
            prev_videos, prev_retro = prev.get("videos"), prev.get("retro")
    except (FileNotFoundError, ValueError):
        prev_videos = prev_retro = None
    info["retro"] = retro_posts() or prev_retro
    info["videos"] = youtube_search(prev_videos)
    with open(os.path.join(os.path.dirname(CACHE_FILE), "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    render(articles, sources, status, now, info)


def _title_words(a):
    words = re.findall(r"\w+", (a["title"] + " " + " ".join(a["summary"].split()[:20])).casefold())
    return {w[:6] for w in words if len(w) > 3}


def current_topics(a):
    """Teme po trenutnom places.txt; Sport ostaje ako je dodijeljen pri preuzimanju (rubrika ili portal)."""
    found = topics(a)
    if "Sport" in found and THEATRE.search(a["title"]) and a.get("source_topic") != "Sport":
        found.remove("Sport")
    if "Sport" in a.get("topics", []) and "Sport" not in found and not THEATRE.search(a["title"]):
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


# --- Vlastiti članci (Stipe, 2026-11-27) ---
# Svaki članak je datoteka clanci/GGGG-MM-DD-naslov.md: zaglavlje (title, date, summary, cover, gallery)
# između redaka "---", pa tekst u Markdownu. Fotografije su u clanci/slike/. Isti oblik koristi Pages CMS.
POSTS_DIR = os.path.join(ROOT, "clanci")
POST_SOURCE = "idassa.click"


def _front_matter(text):
    meta, body = {}, text
    m = re.match(r"﻿?---\s*\n(.*?)\n---\s*\n?(.*)", text, re.S)
    if not m:
        return meta, body
    body, key, block = m.group(2), None, None
    for line in m.group(1).splitlines():
        if block is not None:
            # YAML višeredni tekst (summary: >- / |): uvučeni retci pripadaju istoj vrijednosti
            if not line.strip() or line[:1] in " \t":
                block.append(line.strip())
                continue
            meta[key] = _join_block(block, style)
            block = None
        item = re.match(r"\s+-\s+(.*)", line)
        if item and key:
            if not isinstance(meta.get(key), list):
                meta[key] = []
            meta[key].append(_yaml_value(item.group(1)))
            continue
        kv = re.match(r"([A-Za-z_]\w*)\s*:\s*(.*)", line)
        if kv:
            key = kv.group(1)
            v = kv.group(2).strip()
            if re.fullmatch(r"[>|][+-]?", v):
                block, style = [], v[0]
                continue
            meta[key] = _yaml_value(v) if v else []
    if block is not None:
        meta[key] = _join_block(block, style)
    return meta, body


def _join_block(lines, style):
    """Spaja retke YAML višerednog teksta: '>' spaja retke u odlomke, '|' čuva retke."""
    while lines and not lines[-1]:
        lines.pop()
    if style == "|":
        return "\n".join(lines)
    paras, cur = [], []
    for l in lines:
        if l:
            cur.append(l)
        elif cur:
            paras.append(" ".join(cur))
            cur = []
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras)


def _yaml_value(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
        if v and v[0] == '"':
            pass
    return v.replace('\\"', '"')


def _post_image(path):
    """Ime datoteke fotografije, bez obzira je li zapisana kao clanci/slike/x.jpg ili /clanak/slike/x.jpg."""
    return os.path.basename(path.strip()) if path and isinstance(path, str) else ""


def _inline_md(t, img_url=None):
    t = html.escape(t, quote=False)
    if img_url:
        t = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)",
                   lambda m: f'<img src="{img_url(html.unescape(m.group(2)))}" alt="{m.group(1)}" loading="lazy">', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"\[([^\]]+)\]\(((?:https?://|mailto:)[^)\s]+)\)",
               lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>', t)
    return t


def markdown_html(md, img_url):
    """Mali pretvarač Markdowna: odlomci, podnaslovi (##), popisi (-), citati (>), fotografije ![opis](put)."""
    out = []
    for block in re.split(r"\n\s*\n", md.replace("\r\n", "\n").strip()):
        b = block.strip()
        if not b:
            continue
        img = re.fullmatch(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", b)
        if img:
            alt = html.escape(img.group(1))
            cap = f"<figcaption>{alt}</figcaption>" if alt else ""
            out.append(f'<figure><img src="{img_url(img.group(2))}" alt="{alt}" loading="lazy">{cap}</figure>')
        elif re.match(r"#{1,6}\s", b):
            out.append(f"<h2>{_inline_md(re.sub(r'^#+\s*', '', b), img_url)}</h2>")
        elif all(re.match(r"[-*]\s", l) for l in b.splitlines()):
            out.append("<ul>" + "".join(f"<li>{_inline_md(l[2:].strip(), img_url)}</li>" for l in b.splitlines()) + "</ul>")
        elif b.startswith(">"):
            out.append("<blockquote>" + _inline_md(" ".join(l.lstrip("> ").strip() for l in b.splitlines()), img_url) + "</blockquote>")
        else:
            out.append("<p>" + "<br>".join(_inline_md(l.strip(), img_url) for l in b.splitlines()) + "</p>")
    return "\n".join(out)


def _slug(text):
    t = text.lower()
    for a, b in (("č", "c"), ("ć", "c"), ("š", "s"), ("ž", "z"), ("đ", "d")):
        t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:80] or "clanak"


def _post_date(v, fallback):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(v[:19], fmt).replace(tzinfo=ZAGREB)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=ZAGREB)
    except ValueError:
        return fallback


def load_posts():
    posts = []
    if not os.path.isdir(POSTS_DIR):
        return posts
    for name in sorted(os.listdir(POSTS_DIR)):
        if not name.endswith(".md"):
            continue
        meta, body = _front_matter(open(os.path.join(POSTS_DIR, name), encoding="utf-8").read())
        title = (meta.get("title") or "").strip() if isinstance(meta.get("title"), str) else ""
        if not title or str(meta.get("draft", "")).lower() in ("true", "da", "yes"):
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})", name)
        fallback = _post_date(m.group(1), datetime.now(ZAGREB)) if m else datetime.now(ZAGREB)
        when = _post_date(meta.get("date") if isinstance(meta.get("date"), str) else "", fallback)
        gallery = meta.get("gallery") if isinstance(meta.get("gallery"), list) else []
        cover = _post_image(meta.get("cover")) or (_post_image(gallery[0]) if gallery else "")
        if not cover:
            first = re.search(r"!\[[^\]]*\]\(([^)\s]+)", body)
            cover = _post_image(first.group(1)) if first else ""
        summary = meta.get("summary") if isinstance(meta.get("summary"), str) else ""
        if not summary:
            plain = re.sub(r"!\[[^\]]*\]\([^)]*\)|[#>*_]|\[([^\]]+)\]\([^)]*\)", r"\1", body).strip()
            summary = re.sub(r"\s+", " ", plain)[:220]
        slug = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name[:-3]) or _slug(title)
        posts.append({"title": title, "date": when.isoformat(), "summary": summary.strip(), "slug": _slug(slug),
                      "cover": cover, "gallery": [_post_image(g) for g in gallery if _post_image(g)],
                      "author": meta.get("author") if isinstance(meta.get("author"), str) else "", "body": body,
                      "promo": str(meta.get("promo", "")).lower() in ("true", "da", "yes"),
                      "pinned": str(meta.get("pinned", "")).lower() in ("true", "da", "yes")})
    return posts


POST_CSS = """
:root{--bg:#f4f6f9;--card:#fff;--ink:#17202a;--muted:#5e6c78;--blue:#3466ad;--dot:#ee6636;--line:#e3e8ee}
@media (prefers-color-scheme:dark){:root{--bg:#0f1620;--card:#17212d;--ink:#e8edf2;--muted:#9aa8b5;--blue:#7aa7e6;--line:#26323f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.65 Inter,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{background:var(--card);border-bottom:1px solid var(--line)}
.bar{max-width:760px;margin:0 auto;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;gap:12px}
.logo{font-weight:800;font-size:22px;color:var(--blue);text-decoration:none;letter-spacing:-.02em}.logo span{color:var(--muted);font-weight:600}
.back{color:var(--blue);text-decoration:none;font-weight:600;font-size:15px}
main{max-width:760px;margin:0 auto;padding:20px 16px 48px}
article{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 20px;overflow:hidden}
.promo{background:#f5b301;color:#1d1d1d;padding:2px 8px;border-radius:999px;margin-right:6px}
.promo-note{margin:0 0 8px;font-size:13px;color:var(--muted)}
.kick{font-size:13px;font-weight:700;color:var(--dot);text-transform:uppercase;letter-spacing:.06em}
h1{font-size:clamp(26px,5vw,36px);line-height:1.2;margin:6px 0 8px;letter-spacing:-.01em}
.meta{color:var(--muted);font-size:14px;margin-bottom:16px}
.lead{font-size:19px;font-weight:500}
.cover{margin:0 -20px 18px;width:calc(100% + 40px);max-width:none;display:block;max-height:520px;object-fit:cover;border-radius:0}
article h2{font-size:22px;margin:26px 0 6px}
article img{max-width:100%;height:auto;border-radius:10px}
figure{margin:18px 0}figcaption{color:var(--muted);font-size:14px;margin-top:4px}
blockquote{margin:16px 0;padding:4px 0 4px 16px;border-left:4px solid var(--dot);color:var(--muted)}
a{color:var(--blue)}
.gal{margin:30px -20px 0;background:#0e1726;color:#fff;padding:18px 0 14px;border-radius:0}
.gal-head{display:flex;align-items:baseline;justify-content:space-between;padding:0 20px 12px}
.gal-head b{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:#f5b301}
.gal-head span{font-size:14px;color:#aab4c3;font-variant-numeric:tabular-nums}
.stage{position:relative}
.track{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;scrollbar-width:none;scroll-behavior:smooth}
.track::-webkit-scrollbar{display:none}
.slide{flex:0 0 100%;scroll-snap-align:center;height:min(62vh,540px);display:flex;align-items:center;justify-content:center;padding:0 12px;cursor:zoom-in}
.slide img{max-width:100%;max-height:100%;width:auto;height:auto;object-fit:contain;border-radius:8px;box-shadow:0 10px 40px rgba(0,0,0,.45)}
.nav{position:absolute;top:50%;transform:translateY(-50%);width:44px;height:44px;border-radius:50%;border:0;background:rgba(255,255,255,.92);color:#0e1726;font-size:22px;line-height:44px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.35);transition:opacity .2s}
.nav.prev{left:10px}.nav.next{right:10px}.nav[disabled]{opacity:0;pointer-events:none}
.thumbs{display:flex;gap:8px;overflow-x:auto;padding:14px 20px 4px;scrollbar-width:none}
.thumbs::-webkit-scrollbar{display:none}
.thumbs button{flex:0 0 76px;height:56px;padding:0;border:2px solid transparent;border-radius:8px;overflow:hidden;background:#1d2a3d;cursor:pointer;opacity:.55;transition:opacity .2s,border-color .2s}
.thumbs button.on{opacity:1;border-color:#f5b301}
.thumbs img{width:100%;height:100%;object-fit:cover;border-radius:0;display:block}
@media(min-width:760px){.gal{margin:30px 0 0;border-radius:14px}}
.share{margin-top:22px;display:flex;gap:10px;flex-wrap:wrap}
.share a,.share button{font:inherit;font-size:15px;border:1px solid var(--line);background:var(--bg);color:var(--ink);padding:8px 14px;border-radius:999px;text-decoration:none;cursor:pointer}
footer{text-align:center;color:var(--muted);font-size:14px;padding:0 16px 32px}
#lb{position:fixed;inset:0;background:#060a12;display:none;z-index:9}
#lb.on{display:block}
#lb .track{height:100%}
#lb .slide{height:100%;padding:56px 8px;cursor:default}
#lb .slide img{border-radius:4px;box-shadow:none}
#lb .x{position:absolute;top:8px;right:12px;font-size:36px;background:none;border:0;color:#fff;cursor:pointer;z-index:2}
#lb .cnt{position:absolute;top:18px;left:18px;color:#cfd6e0;font-size:15px;font-variant-numeric:tabular-nums}
#lb .nav{z-index:2}
@media(max-width:600px){.nav{width:38px;height:38px;line-height:38px;font-size:19px}}
"""


def write_posts(posts, site_url, settings):
    """Stranica za svaki vlastiti članak (site/clanak/<slug>/) i smanjene fotografije (site/clanak/slike/)."""
    if not posts:
        return
    esc = html.escape
    img_dir = os.path.join(OUT_DIR, "clanak", "slike")
    os.makedirs(img_dir, exist_ok=True)
    src_dir = os.path.join(POSTS_DIR, "slike")
    done = set()

    def copy_image(name):
        if not name or name in done:
            return
        done.add(name)
        src = os.path.join(src_dir, name)
        if not os.path.isfile(src):
            print(f"Vlastiti članak: nema fotografije {name}", file=sys.stderr)
            return
        dst = os.path.join(img_dir, name)
        try:
            import cv2
            img = cv2.imread(src)
            if img is not None and re.search(r"\.(jpe?g|png|webp)$", name, re.I):
                h, w = img.shape[:2]
                scale = 1600 / max(h, w)
                if scale < 1:
                    img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
                ext = name.rsplit(".", 1)[1].lower()
                params = [cv2.IMWRITE_JPEG_QUALITY, 82] if ext in ("jpg", "jpeg") else []
                if cv2.imwrite(dst, img, params):
                    return
        except Exception:
            pass
        shutil.copyfile(src, dst)

    def img_url(path):
        name = _post_image(path)
        if re.match(r"https?://", path):
            return esc(path)
        copy_image(name)
        return "../slike/" + urllib.parse.quote(name)

    for p in posts:
        for n in [p["cover"], *p["gallery"]]:
            copy_image(n)
        url = f"{site_url}clanak/{p['slug']}/"
        cover_url = f"{site_url}clanak/slike/{urllib.parse.quote(p['cover'])}" if p["cover"] else f"{site_url}share.png"
        when = datetime.fromisoformat(p["date"]).astimezone(ZAGREB)
        body = markdown_html(p["body"], img_url)
        gsrc = ["../slike/" + urllib.parse.quote(g) for g in p["gallery"]]
        gallery = ""
        if gsrc:
            slides = "".join(f'<div class="slide"><img src="{u}" alt="Fotografija {i + 1}" loading="{"eager" if i == 0 else "lazy"}"></div>'
                             for i, u in enumerate(gsrc))
            thumbs = "".join(f'<button type="button" aria-label="Fotografija {i + 1}"><img src="{u}" alt="" loading="lazy"></button>'
                             for i, u in enumerate(gsrc))
            gallery = (f'<section class="gal" aria-label="Fotogalerija"><div class="gal-head"><b>Fotogalerija</b>'
                       f'<span class="pos">1 / {len(gsrc)}</span></div><div class="stage"><div class="track">{slides}</div>'
                       f'<button type="button" class="nav prev" aria-label="Prethodna">‹</button>'
                       f'<button type="button" class="nav next" aria-label="Sljedeća">›</button></div>'
                       f'<div class="thumbs">{thumbs}</div></section>')
        ld = json.dumps({"@context": "https://schema.org", "@type": "NewsArticle", "headline": p["title"],
                         "datePublished": p["date"], "image": [cover_url], "url": url,
                         "author": {"@type": "Person", "name": p["author"]} if p["author"] else {"@type": "Organization", "name": "idassa.click"},
                         "publisher": {"@type": "Organization", "name": "idassa.click", "url": site_url}},
                        ensure_ascii=False).replace("</", "<\\/")
        ga = settings.get("google_analytics", "")
        page = f"""<!doctype html>
<html lang="hr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(p['title'])} – idassa.click</title>
<meta name="description" content="{esc(re.sub(r'\s+', ' ', p['summary']))}">
<link rel="canonical" href="{esc(url)}"><link rel="icon" href="../../favicon.png">
<meta property="og:type" content="article"><meta property="og:title" content="{esc(p['title'])}">
<meta property="og:description" content="{esc(re.sub(r'\s+', ' ', p['summary']))}"><meta property="og:url" content="{esc(url)}">
<meta property="og:image" content="{esc(cover_url)}"><meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">{ld}</script>
<style>{POST_CSS}</style></head><body>
<header><div class="bar"><a class="logo" href="../../">idassa<span>.click</span></a><a class="back" href="../../">← Sve vijesti</a></div></header>
<main><article>
<div class="kick">{'<span class="promo">Promo</span> ' if p.get('promo') else ''}idassa.click</div>
{'<p class="promo-note">Promotivni sadržaj</p>' if p.get('promo') else ''}
<h1>{esc(p['title'])}</h1>
<div class="meta">{esc(p['author'] + ' · ') if p['author'] else ''}<time datetime="{esc(p['date'])}">{when.day}. {when.month}. {when.year}. u {when:%H:%M}</time></div>
{f'<img class="cover" src="../slike/{urllib.parse.quote(p["cover"])}" alt="">' if p['cover'] else ''}
{"".join(f'<p class="lead">{esc(x)}</p>' for x in p["summary"].split("\n\n") if x.strip()) if p['summary'] and not p['body'].strip().startswith(p['summary'][:40]) else ''}
{body}
{gallery}
<div class="share"><button type="button" id="sh">Podijeli</button><a href="https://www.facebook.com/sharer/sharer.php?u={urllib.parse.quote(url)}" target="_blank" rel="noopener">Facebook</a><a href="https://wa.me/?text={urllib.parse.quote(p['title'] + ' ' + url)}" target="_blank" rel="noopener">WhatsApp</a></div>
</article></main>
<footer>© idassa.click · vijesti iz Zadra i županije · <a href="../../">naslovnica</a></footer>
<div id="lb" role="dialog" aria-label="Fotografije"><span class="cnt"></span><button type="button" class="x" aria-label="Zatvori">×</button><div class="track"></div><button type="button" class="nav prev" aria-label="Prethodna">‹</button><button type="button" class="nav next" aria-label="Sljedeća">›</button></div>
<script>
function slider(root,onMove){{const t=root.querySelector(".track"),n=t.children.length,pv=root.querySelector(".prev"),nx=root.querySelector(".next");let i=0;
const go=k=>{{k=Math.max(0,Math.min(n-1,k));t.scrollTo({{left:k*t.clientWidth,behavior:"smooth"}})}};
const upd=()=>{{const k=Math.round(t.scrollLeft/Math.max(1,t.clientWidth));if(k!==i||!upd.d){{i=k;upd.d=1;pv.disabled=i<=0;nx.disabled=i>=n-1;onMove&&onMove(i,n)}}}};
pv.onclick=e=>{{e.stopPropagation();go(i-1)}};nx.onclick=e=>{{e.stopPropagation();go(i+1)}};t.addEventListener("scroll",()=>requestAnimationFrame(upd));upd();
return {{go,jump:k=>{{t.style.scrollBehavior="auto";t.scrollLeft=k*t.clientWidth;t.style.scrollBehavior="";upd()}},get i(){{return i}}}}}}
const lb=document.getElementById("lb"),lt=lb.querySelector(".track"),cnt=lb.querySelector(".cnt");
const pics=[...document.querySelectorAll(".cover, article p img, article figure img, .gal .slide img")];
pics.forEach(im=>{{const d=document.createElement("div");d.className="slide";d.innerHTML='<img alt="">';d.firstChild.dataset.src=im.currentSrc||im.src;lt.appendChild(d)}});
const load=k=>[k-1,k,k+1].forEach(j=>{{const im=lt.children[j]&&lt.children[j].firstChild;if(im&&!im.src)im.src=im.dataset.src}});
const big=slider(lb,(k,n)=>{{cnt.textContent=(k+1)+" / "+n;load(k)}});
const open=k=>{{lb.classList.add("on");document.body.style.overflow="hidden";load(k);big.jump(k)}};
const close=()=>{{lb.classList.remove("on");document.body.style.overflow=""}};
pics.forEach((im,k)=>{{im.style.cursor="zoom-in";(im.closest(".slide")||im).addEventListener("click",()=>open(k))}});
lb.querySelector(".x").onclick=close;lt.addEventListener("click",e=>{{if(e.target.tagName!=="IMG")close()}});
document.addEventListener("keydown",e=>{{if(!lb.classList.contains("on"))return;if(e.key==="Escape")close();if(e.key==="ArrowRight"){{e.preventDefault();big.go(big.i+1)}}if(e.key==="ArrowLeft"){{e.preventDefault();big.go(big.i-1)}}}});
const g=document.querySelector(".gal");
if(g){{const th=[...g.querySelectorAll(".thumbs button")],pos=g.querySelector(".pos");
const sm=slider(g.querySelector(".stage"),(k,n)=>{{pos.textContent=(k+1)+" / "+n;th.forEach((b,j)=>b.classList.toggle("on",j===k));const b=th[k],c=b&&b.parentNode;c&&c.scrollTo({{left:b.offsetLeft-c.clientWidth/2+b.offsetWidth/2,behavior:"smooth"}})}});
th.forEach((b,k)=>b.onclick=()=>sm.go(k))}}
document.getElementById("sh").addEventListener("click",()=>{{const d={{title:document.title,url:location.href}};navigator.share?navigator.share(d).catch(()=>{{}}):navigator.clipboard.writeText(location.href).then(()=>alert("Link je kopiran"))}});
</script>
</body></html>
"""
        out = os.path.join(OUT_DIR, "clanak", p["slug"])
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
            f.write(page)


def post_articles(posts, site_url):
    """Vlastiti članci kao kartice na naslovnici (izvor "idassa.click")."""
    out = []
    for p in posts:
        a = {"title": p["title"], "link": f"{site_url}clanak/{p['slug']}/", "date": p["date"],
             "summary": re.sub(r"\s+", " ", p["summary"]).strip(), "source": POST_SOURCE, "own": True,
             **({"promo": True} if p.get("promo") else {}),
             **({"pinned": True} if p.get("pinned") else {}),
             "image": f"{site_url}clanak/slike/{urllib.parse.quote(p['cover'])}" if p["cover"] else ""}
        a["topics"] = current_topics(dict(a, text=p["body"]))
        out.append(a)
    return out


def render(articles, sources, status, now, info=None):
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    try:
        with open(os.path.join(ROOT, "site.json"), encoding="utf-8") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    site_url = settings.get("url", "")
    posts = load_posts()
    own = post_articles(posts, site_url)
    articles = sorted(own + [a for a in articles if a.get("source") != POST_SOURCE], key=lambda a: (bool(a.get("pinned")), a["date"]), reverse=True)  # "Na vrh" uvijek prvi
    grouped = group_duplicates(articles)
    payload = json.dumps({"articles": grouped, "topics": TOPICS,
                          "pages": PAGE_SLUGS,
                          "sources": ([POST_SOURCE] if own else []) + [s["name"] for s in sources if s.get("kind") != "info"],
                          "sites": dict({s["name"]: s.get("site", "") for s in sources if s.get("kind") != "info"},
                                        **({POST_SOURCE: site_url} if own else {})),
                          "info": info or {}, "settings": settings,
                          "status": status, "updated": now}, ensure_ascii=False)
    payload = payload.replace("</", "<\\/")
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
    write_posts(posts, site_url, settings)
    write_whatsapp(grouped, site_url)
    write_instagram(grouped)
    if settings.get("private"):
        # Zatvorena stranica (pod lozinkom): bez RSS-a i sitemapa, tražilicama zabranjeno.
        with open(os.path.join(OUT_DIR, "robots.txt"), "w", encoding="utf-8") as f:
            f.write("User-agent: *\nDisallow: /\n")
    else:
        write_feed(grouped, site_url, now)
        write_sitemap(site_url, now)
    print(f"Spremljeno {len(articles)} članaka u site/", file=sys.stderr)


WA_DAYS = ["ponedjeljak", "utorak", "srijeda", "četvrtak", "petak", "subota", "nedjelja"]


def top_stories(grouped, limit=8):
    """Najvažnije vijesti iz zadnja 24 sata: prvo one o kojima piše više portala, pa najnovije."""
    now = datetime.now(timezone.utc)
    recent = [a for a in grouped if now - datetime.fromisoformat(a["date"]) < timedelta(hours=24)]
    return sorted(recent, key=lambda a: (len(a.get("also") or []), a["date"]), reverse=True)[:limit]


def today_label():
    today = datetime.now(ZAGREB)
    return f"{WA_DAYS[today.weekday()].capitalize()}, {today.day}. {today.month}. {today.year}."


def whatsapp_digest(grouped, site_url, limit=8):
    """Tekst dnevnog pregleda za WhatsApp kanal, svaka vijest s naslovom i linkom na izvorni članak."""
    top = top_stories(grouped, limit)
    lines = ["📰 *Vijesti iz Zadra i županije*", today_label(), ""]
    for a in top:
        lines += [f"▪️ *{a['title']}*", a["link"], ""]
    lines.append(f"Sve vijesti na jednom mjestu: {site_url or 'https://idassa.click/'}")
    return "\n".join(lines)


def write_whatsapp(grouped, site_url):
    """Skrivena stranica /whatsapp/ s gotovim tekstom za kopiranje u WhatsApp kanal."""
    text = whatsapp_digest(grouped, site_url)
    esc = html.escape(text)
    page = f"""<!doctype html><html lang="hr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex, nofollow">
<title>WhatsApp pregled – idassa.click</title>
<style>
:root {{ --bg: #f3f5f7; --card: #fff; --text: #0f1d2b; --muted: #5b6b7a; --line: #e1e6eb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #0b141c; --card: #13212c; --text: #e8eef3; --muted: #93a4b3; --line: #213340; }} }}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 640px; margin: 0 auto; padding: 20px 16px 40px; }}
h1 {{ font-size: 1.4rem; margin: 0 0 4px; }} p {{ color: var(--muted); margin: 0 0 14px; }}
.btns {{ display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; position: sticky; top: 0; padding: 10px 0; background: var(--bg); }}
button, a.btn {{ font: inherit; font-weight: 700; border: 0; border-radius: 999px; padding: 11px 20px; cursor: pointer; text-decoration: none; }}
#copy {{ background: #25d366; color: #06301a; }} a.btn {{ background: var(--card); color: var(--text); border: 1px solid var(--line); }}
pre {{ white-space: pre-wrap; word-break: break-word; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
       padding: 16px; font: 15px/1.5 system-ui, sans-serif; margin: 0; }}
</style></head><body><main>
<h1>Pregled za WhatsApp kanal</h1>
<p>Najvažnije vijesti iz zadnja 24 sata (prvo one o kojima piše više portala). Osvježava se svakih 15 minuta.</p>
<div class="btns"><button id="copy" type="button">Kopiraj tekst</button><a class="btn" href="../">Natrag na stranicu</a></div>
<pre id="t">{esc}</pre>
</main><script>
const b = document.getElementById("copy");
b.onclick = async () => {{
  const t = document.getElementById("t").textContent;
  try {{ await navigator.clipboard.writeText(t); }}
  catch (e) {{ const r = document.createRange(); r.selectNodeContents(document.getElementById("t"));
               const s = getSelection(); s.removeAllRanges(); s.addRange(r); document.execCommand("copy"); }}
  b.textContent = "Kopirano ✓"; setTimeout(() => b.textContent = "Kopiraj tekst", 2000);
}};
</script></body></html>
"""
    os.makedirs(os.path.join(OUT_DIR, "whatsapp"), exist_ok=True)
    with open(os.path.join(OUT_DIR, "whatsapp", "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def write_instagram(grouped):
    """Skrivena stranica /instagram/: preglednik iz predloška instagram.html crta sliku
    "5 vijesti dana" i kartice pojedinih vijesti (naš dizajn, bez tuđih fotografija)."""
    with open(os.path.join(ROOT, "instagram.html"), encoding="utf-8") as f:
        tpl = f.read()
    with open(TEMPLATE, encoding="utf-8") as f:
        m = re.search(r'<svg viewBox="[^"]+" class="wordmark".*?</svg>', f.read(), re.S)
    stories = [{"title": a["title"], "source": a["source"], "topics": a.get("topics") or []} for a in top_stories(grouped)]
    page = (tpl.replace("/*STORIES*/null", json.dumps(stories, ensure_ascii=False).replace("</", "<\\/"))
               .replace("{{DATE}}", today_label())
               .replace("{{WORDMARK}}", m.group(0) if m else ""))
    os.makedirs(os.path.join(OUT_DIR, "instagram"), exist_ok=True)
    with open(os.path.join(OUT_DIR, "instagram", "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


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
