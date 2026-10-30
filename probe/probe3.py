import json, re, urllib.request, urllib.error
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://zadarska-policija.gov.hr/", "https://zadarska-policija.gov.hr/vijesti", "https://zadarska-policija.gov.hr/vijesti/8", "https://policija.gov.hr/", "https://policija.gov.hr/rss", "https://mup.gov.hr/rss", "https://policija.gov.hr/vijesti/"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "ctype": r.headers.get("Content-Type"), "len": len(b),
                  "items": len(re.findall(r"<item>", b)),
                  "feedlinks": re.findall(r'<link[^>]+(?:rss|atom)\+xml[^>]*>', b)[:3],
                  "rsshrefs": list(set(re.findall(r'href="([^"]*rss[^"]*)"', b, re.I)))[:8],
                  "links": list(dict.fromkeys(re.findall(r'href="([^"#]+)"', b)))[:60]}
    except urllib.error.HTTPError as e:
        out[u] = {"error": e.code, "server": e.headers.get("Server"), "body": e.read()[:400].decode("utf-8","replace")}
    except Exception as e:
        out[u] = {"error": repr(e)[:200]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
