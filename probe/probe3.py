import json, re, urllib.request
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
urls = ["https://www.unizd.hr/obavijesti/novosti/pgrid/18148/pageid/2", "https://www.unizd.hr/obavijesti/novosti", "https://www.unizd.hr/robots.txt", "https://www.unizd.hr/DesktopModules/EasyDNNNews/RSS.ashx"]
out = {}
for u in urls:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "len": len(b), "ctype": r.headers.get("Content-Type"),
                  "alt": re.findall(r'<link[^>]+(?:rss|atom)[^>]*>', b)[:5],
                  "links": sorted(set(re.findall(r'href="([^"]*(?:radio-zadar|novost|vijest|feed|rss)[^"]*)"', b, re.I)))[:80], "api": sorted(set(re.findall(r'https?://[a-z0-9.-]*hrt\\.hr/[^"\\s<>]*(?:api|json|graphql|feed)[^"\\s<>]*', b)))[:20], "next": "__NEXT_DATA__" in b,
                  "head": b[:300], "items": len(re.findall(r"<item[ >]", b)), "chan": (re.findall(r'"externalId":"(UC[\w-]{22})"', b) or re.findall(r'"channelId":"(UC[\w-]{22})"', b) or [""])[0], "title": (re.findall(r"<title>(.*?)</title>", b, re.S) or [""])[0][:80], "osm": sorted(set(re.findall(r'href="([^"]*(?:osmrt|ukop|pogreb|sprovod)[^"]*)"', b, re.I)))[:15]}
        open("probe/page%d.html" % urls.index(u), "w").write(b)
        if "robots" in u: out[u]["body"] = b[:3000]
    except Exception as e:
        out[u] = {"err": repr(e)[:150], "hdr": dict(getattr(e, "headers", {}) or {}), "body": (e.read()[:400].decode("utf-8","replace") if hasattr(e, "read") else "")}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
