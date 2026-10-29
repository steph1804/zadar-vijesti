import re, urllib.request, json, concurrent.futures as cf
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
ids = [h.rsplit("/",1)[1] for h,_ in json.load(open("probe/result.json"))["pairs"] if "/category/" in h]
def go(i):
    try:
        b = urllib.request.urlopen(urllib.request.Request("https://www.slobodnadalmacija.hr/feed/category/"+i, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8","replace")
        its = re.findall(r"<item>(.*?)</item>", b, re.S)
        links = [(re.findall(r"<link>(.*?)</link>", it) or [""])[0] for it in its]
        zad = sum("/zadar" in l for l in links)
        return i, {"n": len(its), "zadar": zad, "title": (re.findall(r"<title>(.*?)</title>", b) or [""])[0][:60], "first": (re.findall(r"<pubDate>(.*?)</pubDate>", its[0]) or [""])[0] if its else "", "sample": links[0][-80:] if links else ""}
    except Exception as e:
        return i, {"error": repr(e)[:100]}
with cf.ThreadPoolExecutor(8) as p:
    out = dict(p.map(go, ids))
json.dump(out, open("probe/result2.json", "w"), ensure_ascii=False, indent=1)
