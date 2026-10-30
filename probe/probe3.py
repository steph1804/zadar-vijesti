import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://liburnija-zadar.hr/feed/", "https://liburnija-zadar.hr/novosti/feed/", "https://liburnija-zadar.hr/category/novosti/feed/", "https://liburnija-zadar.hr/novosti/", "https://liburnija-zadar.hr/robots.txt", "https://liburnija-zadar.hr/wp-json/wp/v2/posts?per_page=3&_fields=link,title,date,categories"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = r.read().decode("utf-8", "replace")
        its = re.findall(r"<item>(.*?)</item>", b, re.S)
        out[u] = {"status": r.status, "final": r.geturl(), "len": len(b), "items": len(its),
                  "titles": [(re.findall(r"<title>(.*?)</title>", it, re.S) or [""])[0][:90] for it in its[:10]],
                  "links": [(re.findall(r"<link>(.*?)</link>", it, re.S) or [""])[0] for it in its[:3]],
                  "dates": [(re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0] for it in its[:2]],
                  "cats": [re.findall(r"<category>(.*?)</category>", it) for it in its[:4]],
                  "img": [bool(re.search(r"<img|media:|enclosure", it)) for it in its[:3]],
                  "feedlinks": re.findall(r'<link[^>]+(?:rss|atom)\+xml[^>]*>', b)[:3],
                  "head": b[:500] if not its else ""}
    except Exception as e:
        out[u] = {"error": repr(e)[:200]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
