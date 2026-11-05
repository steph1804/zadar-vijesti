import json, re, urllib.request
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
urls = ['https://idassa.click/reset/', 'https://idassa.click/sw.js']
out = {}
for u in urls:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        raw = r.read(); open("probe/img%d.jpg" % urls.index(u), "wb").write(raw); b = raw.decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "len": len(b), "ctype": r.headers.get("Content-Type"),
                  "alt": re.findall(r'<link[^>]+(?:rss|atom)[^>]*>', b)[:5],
                  "links": sorted(set(re.findall(r'(?:src|content|data-src)="([^"]+\.(?:jpe?g|png|webp)[^"]*)"', b, re.I)))[:80], "api": sorted(set(re.findall(r'https?://[a-z0-9.-]*hrt\\.hr/[^"\\s<>]*(?:api|json|graphql|feed)[^"\\s<>]*', b)))[:20], "next": "__NEXT_DATA__" in b,
                  "head": b[:300], "items": len(re.findall(r"<item[ >]", b)), "chan": (re.findall(r'"externalId":"(UC[\w-]{22})"', b) or re.findall(r'"channelId":"(UC[\w-]{22})"', b) or [""])[0], "title": (re.findall(r"<title>(.*?)</title>", b, re.S) or [""])[0][:80], "osm": sorted(set(re.findall(r'href="([^"]*(?:osmrt|ukop|pogreb|sprovod)[^"]*)"', b, re.I)))[:15]}
        open("probe/page%d.html" % urls.index(u), "w").write(b)
        if "robots" in u: out[u]["body"] = b[:3000]
    except Exception as e:
        out[u] = {"err": repr(e)[:150], "hdr": dict(getattr(e, "headers", {}) or {}), "body": (e.read()[:400].decode("utf-8","replace") if hasattr(e, "read") else "")}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
# Tue Nov  3 08:24:04 UTC 2026
# 1 1793871161
# 2 1793871174
# 3 1793871187
# r1 1793871274
