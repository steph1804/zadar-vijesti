import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://www.hak.hr/info/stanje-na-cestama/", "https://www.hak.hr/"]:
    r = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40) as resp:
            body = resp.read(1500000).decode("utf-8", "replace")
            r["status"], r["final"] = resp.status, resp.geturl()
        hrefs = re.findall(r'href=["\']([^"\'#]+)["\']', body)
        r["hrefs"] = [] or sorted(set(h for h in hrefs if re.search(r"stanj|ceste|cesta|prohodn|vijest|info|tekst|promet|bilten", h, re.I)))[:80]
        r["title"] = (re.findall(r"<title[^>]*>([^<]*)", body) or [""])[0][:100]
        r["feeds"] = re.findall(r'<link[^>]+application/(?:rss|atom)\+xml[^>]*>', body)[:3]
        items = re.findall(r"<item>(.*?)</item>", body, re.S)
        r["items"] = [((re.findall(r"<title>(.*?)</title>", i, re.S) or [""])[0][:80], re.findall(r"<category>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</category>", i)[:3], bool(re.search(r"<img|<enclosure|<media:", i))) for i in items]
        r["zadar_text"] = sorted(set(re.findall(r'href=["\']([^"\']+)["\'][^>]*>\s*([^<]*Zadar[^<]*)<', body)))[:40]
    except Exception as e:
        r["error"] = repr(e)[:300]
    out[u] = r
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
