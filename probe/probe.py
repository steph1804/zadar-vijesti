import re, urllib.request, json, concurrent.futures as cf
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
URLS = """https://www.index.hr/rss
https://www.24sata.hr/feeds/news.xml
https://www.24sata.hr/feeds/aktualno.xml
https://www.jutarnji.hr/feed
https://www.vecernji.hr/feeds/latest
https://www.tportal.hr/rss
https://n1info.hr/feed/
https://net.hr/feed
https://www.telegram.hr/feed/
https://www.novilist.hr/feed/
https://dnevnik.hr/assets/feed/articles/
https://www.rtl.hr/feeds/vijesti.xml
https://www.morski.hr/feed/
https://www.direktno.hr/rss/
https://www.hrt.hr/rss/vijesti
https://vijesti.hrt.hr/rss
https://www.glasistre.hr/rss
https://www.dalmacijadanas.hr/feed/
https://www.dalmatinskiportal.hr/rss
https://www.hina.hr/rss
https://www.poslovni.hr/feed
https://www.bug.hr/rss
https://www.sportske.jutarnji.hr/feed
https://sportklub.n1info.hr/feed/
https://www.gol.hr/feed""".split()
def go(u):
    try:
        resp = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30)
        b = resp.read(2000000).decode("utf-8", "replace")
        items = re.findall(r"<item[ >](.*?)</item>", b, re.S) or re.findall(r"<entry[ >](.*?)</entry>", b, re.S)
        z = [ (re.findall(r"<title[^>]*>(.*?)</title>", i, re.S) or [""])[0][:90] for i in items if re.search(r"zad[ar]|zadr[ua]|zadrom", i, re.I)]
        return u, {"status": resp.status, "final": resp.geturl(), "items": len(items), "img": sum(bool(re.search(r"<img|<enclosure|<media:", i)) for i in items), "zadar": z[:5]}
    except Exception as e:
        return u, {"error": repr(e)[:150]}
with cf.ThreadPoolExecutor(10) as p:
    out = dict(p.map(go, URLS))
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
