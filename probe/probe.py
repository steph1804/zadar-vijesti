import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
b = urllib.request.urlopen(urllib.request.Request("https://slobodnadalmacija.hr/dalmacija/zadar/feed", headers={"User-Agent": UA}), timeout=40).read().decode("utf-8","replace")
items = re.findall(r"<item>(.*?)</item>", b, re.S)
out = [{"i": n, "link": (re.findall(r"<link>(.*?)</link>", it) or [""])[0][-60:], "date": (re.findall(r"<pubDate>(.*?)</pubDate>", it) or [""])[0]} for n, it in enumerate(items)]
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
