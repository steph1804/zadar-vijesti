import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
URLS = ['https://www.grad-zadar.hr/', 'https://www.grad-zadar.hr/feed/', 'https://www.zadarska-zupanija.hr/', 'https://www.zadarska-zupanija.hr/feed/', 'https://zadarska-policija.gov.hr/', 'https://zadarska-policija.gov.hr/rss', 'https://www.port-authority-zadar.hr/', 'https://www.jadrolinija.hr/', 'https://www.jadrolinija.hr/hr/obavijesti', 'https://zadar.travel/', 'https://www.hak.hr/', 'https://www.hak.hr/info/stanje-na-cestama/', 'https://zadar.hr/']
out = {}
for u in URLS:
    r = {}
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = resp.read(800000).decode("utf-8", "replace")
            r["status"], r["final"] = resp.status, resp.geturl()
        r["rss_links"] = sorted(set(re.findall(r'href=["\']([^"\']*(?:rss|feed|xml)[^"\']*)["\']', body)))[:15]
        r["feeds"] = re.findall(r'<link[^>]+application/(?:rss|atom)\+xml[^>]*>', body)[:5]
        items = re.findall(r"<item>(.*?)</item>", body, re.S)
        r["items"] = [{"title": (re.findall(r"<title>(.*?)</title>", i, re.S) or [""])[0][:90],
                       "link": (re.findall(r"<link>(.*?)</link>", i) or [""])[0],
                       "cats": re.findall(r"<category><!\[CDATA\[(.*?)\]\]></category>", i),
                       "img": bool(re.search(r"<img|<enclosure|<media:", i))} for i in items]
    except Exception as e:
        r["error"] = repr(e)[:300]
    out[u] = r
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
