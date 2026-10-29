import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40).read().decode("utf-8","replace")
b = get("https://slobodnadalmacija.hr/static/rss-1000725")
pairs = re.findall(r'<a[^>]+href=["\']([^"\']*feed[^"\']*)["\'][^>]*>(.*?)</a>', b, re.S)
out = {"pairs": [(h, re.sub(r"<[^>]+>|\s+", " ", t).strip()) for h, t in pairs]}
zad = [h for h, t in out["pairs"] if "zadar" in t.lower() or "zadar" in h.lower()]
out["zadar"] = {}
for h in zad[:4]:
    f = get(h if h.startswith("http") else "https://slobodnadalmacija.hr" + h)
    out["zadar"][h] = [(re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0] + " " + (re.findall(r"<link>(.*?)</link>", it) or [""])[0][-50:] for it in re.findall(r"<item>(.*?)</item>", f, re.S)[:3]]
g = get("https://slobodnadalmacija.hr/feed")
out["general_zadar"] = [(re.findall(r"<link>(.*?)</link>", it) or [""])[0][-70:] for it in re.findall(r"<item>(.*?)</item>", g, re.S) if "/zadar" in it]
out["general_first"] = [(re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0] for it in re.findall(r"<item>(.*?)</item>", g, re.S)][:2]
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
