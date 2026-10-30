import json, re, urllib.request, urllib.error, concurrent.futures as cf
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
cands = {
 "feed": "https://noviradio.hr/feed/", "wwwfeed": "https://www.noviradio.hr/feed/",
 "search": "https://noviradio.hr/?s=zadar&feed=rss2", "home": "https://noviradio.hr/",
 "rss": "https://noviradio.hr/rss",
}
search = {}
def go(kv):
    n, u = kv
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=25)
        b = r.read().decode("utf-8", "replace")
        its = re.findall(r"<(?:item|entry)[ >](.*?)</(?:item|entry)>", b, re.S)
        tit = [re.sub(r"<!\[CDATA\[|\]\]>", "", (re.findall(r"<title[^>]*>(.*?)</title>", it, re.S) or [""])[0])[:70] for it in its]
        dates = [(re.findall(r"<(?:pubDate|published|updated)>(.*?)</", it) or [""])[0] for it in its[:1]]
        z = [t for t in tit if re.search(r"zadar|zadr|biograd|benkovac|pag\b|nin\b|obrovac|ugljan|pašman|dugi otok|zemunik|sukošan|starigrad", t, re.I)]
        return n, {"u": u, "n": len(its), "first": dates, "zadar_titles": z[:5], "sample": tit[:6], "head": b[:600] if not its else "", "cats": re.findall(r"<category>(?:<!\\[CDATA\\[)?(.*?)(?:\\]\\]>)?</category>", b)[:30], "img": bool(re.search(r"<media:|<enclosure|<img", b))}
    except urllib.error.HTTPError as e:
        return n, {"u": u, "err": e.code, "srv": e.headers.get("Server")}
    except Exception as e:
        return n, {"u": u, "err": repr(e)[:80]}
with cf.ThreadPoolExecutor(10) as p:
    out = dict(p.map(go, list(cands.items()) + list(search.items())))
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
