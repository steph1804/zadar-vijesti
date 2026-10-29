import urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://m.hak.hr/", "https://m.hak.hr/stanje.asp", "https://www.hak.hr/info/stanje-na-cestama/tekst", "https://www.hak.hr/info/prohodnost-cesta/"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}), timeout=40)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "type": r.headers.get("content-type"), "len": len(b), "head": __import__("re").sub(r"\s+"," ",__import__("re").sub(r"<[^>]+>"," ",b))[:2500], "title": (__import__("re").findall(r"<title[^>]*>([^<]*)", b) or [""])[0]}
    except Exception as e:
        out[u] = {"error": repr(e)[:300]}
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
