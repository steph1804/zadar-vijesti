import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
URLS = """https://023.hr/
https://www.023.hr/
https://www.023portal.com/
https://www.ezadar.hr/
https://radio.hrt.hr/radio-zadar
https://slobodnadalmacija.hr/zadar
https://slobodnadalmacija.hr/feed
https://slobodnadalmacija.hr/feed/zadar
https://slobodnadalmacija.hr/zadar/feed
https://slobodnadalmacija.hr/rss""".split()
out = {}
for u in URLS:
    r = {}
    try:
        req = urllib.request.Request(u, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=40) as resp:
            body = resp.read(600000).decode("utf-8", "replace")
            r["status"], r["final"], r["type"] = resp.status, resp.geturl(), resp.headers.get("content-type")
        r["items"] = body.count("<item") + body.count("<entry")
        r["feeds"] = re.findall(r'<link[^>]+application/(?:rss|atom)\+xml[^>]*>', body)[:5]
        r["rss_links"] = sorted(set(re.findall(r'href=["\']([^"\']*(?:rss|feed)[^"\']*)["\']', body)))[:20]
        r["links"] = re.findall(r"<link>([^<]+)</link>", body)[:40] if r["items"] else []
        r["title"] = (re.findall(r"<title[^>]*>([^<]*)", body) or [""])[0][:100]
    except Exception as e:
        r["error"] = repr(e)[:300]
    out[u] = r
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
