import json, re, time, urllib.request, urllib.error
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
out = {}
for u in ["https://www.ezadar.hr/feed/", "https://ezadar.net.hr/feed/", "https://www.ezadar.hr/", "https://www.ezadar.hr/robots.txt",
          "https://www.057info.hr/feed/", "https://www.057info.hr/rss", "https://www.057info.hr/", "https://www.057info.hr/robots.txt"]:
    t = time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=45)
        b = r.read().decode("utf-8", "replace")
        its = re.findall(r"<item>(.*?)</item>", b, re.S)
        out[u] = {"status": r.status, "final": r.geturl(), "sec": round(time.time() - t, 1), "items": len(its),
                  "titles": [(re.findall(r"<title>(.*?)</title>", it, re.S) or [""])[0][:80] for it in its[:4]],
                  "dates": [(re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0] for it in its[:1]],
                  "head": b[:300] if not its else ""}
    except urllib.error.HTTPError as e:
        out[u] = {"error": e.code, "sec": round(time.time() - t, 1), "server": e.headers.get("Server"), "cf": e.headers.get("cf-mitigated"), "body": e.read()[:200].decode("utf-8", "replace")}
    except Exception as e:
        out[u] = {"error": repr(e)[:150], "sec": round(time.time() - t, 1)}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
