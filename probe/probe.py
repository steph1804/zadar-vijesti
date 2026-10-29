import re, urllib.request, urllib.parse, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40)
    return r.status, r.geturl(), r.headers.get("content-type"), r.read(1500000).decode("utf-8", "replace")
for u in ["https://www.zadar-airport.hr/", "https://www.zadar-airport.hr/hr/letovi/dolasci", "https://www.zadar-airport.hr/robots.txt"]:
    try:
        s, f, ct, b = get(u)
        out[u] = {"status": s, "final": f, "type": ct, "title": (re.findall(r"<title[^>]*>([^<]*)", b) or [""])[0][:100],
                  "links": sorted(set(h for h in re.findall(r'href=["\']([^"\'#]+)', b) if re.search(r"let|flight|dolas|odlas|arriv|depart|timetable|raspored", h, re.I)))[:40],
                  "api": sorted(set(re.findall(r'["\']([^"\']*(?:api|json|ajax|flights?)[^"\']*)["\']', b)))[:30],
                  "body": b[:1500] if "robots" in u else ""}
    except Exception as e:
        out[u] = {"error": repr(e)[:200]}
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
