import urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
r = urllib.request.urlopen(urllib.request.Request("https://www.zadar-airport.hr/wp-content/themes/hello-elementor-child/current-flights.json", headers={"User-Agent": UA}), timeout=40)
b = r.read().decode("utf-8", "replace")
js = r.headers.get("content-type")
open("probe/result.json", "w").write(json.dumps({"type": js, "len": len(b), "head": b[:3000]}, ensure_ascii=False, indent=1))
