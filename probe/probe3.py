import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://kkzadar.hr/feed/", "https://kkzadar.hr/novosti/feed/", "https://kkzadar.hr/category/novosti/feed/", "https://kkzadar.hr/novosti/", "https://kkzadar.hr/robots.txt", "https://kkzadar.hr/wp-json/wp/v2/posts?per_page=3"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = r.read().decode("utf-8", "replace")
        its = re.findall(r"<item>(.*?)</item>", b, re.S)
        out[u] = {"status": r.status, "final": r.geturl(), "len": len(b), "items": len(its),
                  "titles": [(re.findall(r"<title>(.*?)</title>", it, re.S) or [""])[0][:80] for it in its[:5]],
                  "dates": [(re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0] for it in its[:3]],
                  "cats": [re.findall(r"<category>(.*?)</category>", it) for it in its[:3]],
                  "feedlinks": re.findall(r'<link[^>]+(?:rss|atom)\+xml[^>]*>', b)[:5],
                  "head": b[:600] if not its else ""}
    except Exception as e:
        out[u] = {"error": repr(e)[:200]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
