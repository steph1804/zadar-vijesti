import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://www.057info.hr/", "https://www.057info.hr/rss", "https://www.057info.hr/feed/", "https://057info.hr/rss"]:
    r = {}
    try:
        resp = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40)
        b = resp.read(800000).decode("utf-8", "replace")
        r = {"status": resp.status, "final": resp.geturl(), "type": resp.headers.get("content-type"),
             "feeds": re.findall(r'<link[^>]+application/(?:rss|atom)\+xml[^>]*>', b)[:3],
             "items": [((re.findall(r"<title>(.*?)</title>", i, re.S) or [""])[0][:80], (re.findall(r"<link>(.*?)</link>", i) or [""])[0][:80], re.findall(r"<category>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</category>", i)[:3], bool(re.search(r"<img|<enclosure|<media:", i))) for i in re.findall(r"<item>(.*?)</item>", b, re.S)][:10]}
    except Exception as e:
        r = {"error": repr(e)[:200], "server": getattr(getattr(e, "headers", None), "get", lambda k: None)("server")}
    out[u] = r
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
