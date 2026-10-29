import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://www.jadrolinija.hr/", "https://www.jadrolinija.hr/hr/red-plovidbe", "https://www.jadrolinija.hr/hr/obavijesti"]:
    r = {}
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40) as resp:
            body = resp.read(1500000).decode("utf-8", "replace")
            r["status"], r["final"] = resp.status, resp.geturl()
        hrefs = re.findall(r'href=["\']([^"\'#]+)["\']', body)
        r["hrefs"] = sorted(set(h for h in hrefs if re.search(r"red|plovid|linij|zadar|timetable|schedule|lines", h, re.I)))[:80]
        r["zadar_text"] = sorted(set(re.findall(r'href=["\']([^"\']+)["\'][^>]*>\s*([^<]*Zadar[^<]*)<', body)))[:40]
    except Exception as e:
        r["error"] = repr(e)[:300]
    out[u] = r
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
