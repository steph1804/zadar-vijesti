import urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://www.hak.hr/info/stanje-na-cestama-novo/events", "https://www.hak.hr/info/stanje-na-cestama-novo/hash"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"}), timeout=40)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "type": r.headers.get("content-type"), "len": len(b), "head": b[:4000]}
    except Exception as e:
        out[u] = {"error": repr(e)[:300]}
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
