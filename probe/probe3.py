import json, urllib.request
out = {}
for u in ["http://idassa.click/", "http://www.idassa.click/", "https://steph1804.github.io/zadar-vijesti/", "https://idassa.click/sitemap.xml", "https://idassa.click/robots.txt", "https://idassa.click/share.png"]:
    try:
        r = urllib.request.urlopen(u, timeout=20)
        out[u] = {"status": r.status, "final": r.geturl()}
    except Exception as e:
        out[u] = repr(e)[:200]
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
