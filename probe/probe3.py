import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
b = urllib.request.urlopen(urllib.request.Request("https://feed.hrt.hr/vijesti/page.xml", headers={"User-Agent": UA}), timeout=30).read().decode("utf-8","replace")
its = re.findall(r"<item>(.*?)</item>", b, re.S)
vid = [it for it in its if ".mp4" in it]
out = {"n": len(its), "videos": len(vid), "video_items": [v[:2500] for v in vid[:3]], "normal": its[0][:1500] if its else ""}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
