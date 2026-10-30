import json, re, urllib.request
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
for u in ["https://zadarska-policija.gov.hr/vijesti/", "https://zadarska-policija.gov.hr/rss", "https://zadarska-policija.gov.hr/rss.aspx", "https://zadarska-policija.gov.hr/vijesti/rss", "https://zadarska-policija.gov.hr/robots.txt", "https://zadarska-policija.gov.hr/sitemap.xml"]:
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "ctype": r.headers.get("Content-Type"), "len": len(b),
                  "items": len(re.findall(r"<item>", b)),
                  "feedlinks": re.findall(r'<link[^>]+(?:rss|atom)\+xml[^>]*>', b)[:3],
                  "rsshrefs": list(set(re.findall(r'href="([^"]*rss[^"]*)"', b, re.I)))[:8],
                  "newslinks": list(dict.fromkeys(re.findall(r'href="([^"]*/vijesti/[^"]+)"', b)))[:12],
                  "head": b[:1500] if "robots" in u or "sitemap" in u else ""}
    except Exception as e:
        out[u] = {"error": repr(e)[:200]}
b = urllib.request.urlopen(urllib.request.Request("https://zadarska-policija.gov.hr/vijesti/", headers={"User-Agent": UA}), timeout=30).read().decode("utf-8","replace")
i = b.find("/vijesti/"); 
m = re.search(r'(<(?:div|ul|article)[^>]*class="[^"]*(?:news|vijest|list)[^"]*".{0,3000})', b, re.S)
out["snippet"] = m.group(1)[:3000] if m else b[len(b)//3:len(b)//3+3000]
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
