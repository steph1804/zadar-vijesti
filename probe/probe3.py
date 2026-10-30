import json, re, urllib.request
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
urls = ["https://www.zadarska-zupanija.hr/", "https://www.zadarska-zupanija.hr/feed/", "https://www.zadarska-zupanija.hr/rss",
        "https://www.zadarska-zupanija.hr/novosti", "https://www.zadarska-zupanija.hr/vijesti", "https://www.zadarska-zupanija.hr/index.php?format=feed&type=rss",
        "https://www.zadarska-zupanija.hr/robots.txt"]
out = {}
for u in urls:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "len": len(b), "ctype": r.headers.get("Content-Type"),
                  "alt": re.findall(r'<link[^>]+(?:rss|atom)[^>]*>', b)[:5],
                  "links": sorted(set(re.findall(r'href="([^"]*(?:novost|vijest|aktualn|news|clan)[^"]*)"', b, re.I)))[:60],
                  "head": b[:1500] if len(b) < 200000 else ""}
        if "robots" in u: out[u]["body"] = b[:1500]
    except Exception as e:
        out[u] = {"err": repr(e)[:150]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
