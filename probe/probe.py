import re, urllib.request, json
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
def get(u):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40)
    return r.read(2000000).decode("utf-8", "replace")
out = {}
b = get("https://www.zadar-airport.hr/pregled-letova/?sekcija=putnici")
text = re.sub(r"\s+", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", b, flags=re.S))
i = text.find("flight")
out["page_has_flightno"] = re.findall(r"\b[A-Z0-9]{2}\s?\d{3,4}\b", re.sub(r"<[^>]+>", " ", text))[:30]
out["page_text"] = re.sub(r"<[^>]+>", " ", text)
out["page_text"] = re.sub(r"\s+", " ", out["page_text"])[:4000]
out["scripts_vars"] = re.findall(r"var \w+ = \{[^;]{0,600}", b)[:10]
for js in ["https://www.zadar-airport.hr/wp-content/themes/hello-elementor-child/assets/js/current-flights-view.js?ver=1786601153",
           "https://www.zadar-airport.hr/wp-content/themes/hello-elementor-child/assets/js/flights.js?ver=1773234846"]:
    s = get(js)
    out[js] = {"actions": sorted(set(re.findall(r"action['\"]?\s*[:=]\s*['\"]([\w-]+)", s))), "urls": sorted(set(re.findall(r"['\"`](https?://[^'\"`]+|/[\w\-/]+\.(?:json|php)[^'\"`]*)['\"`]", s)))[:20], "head": s[:1500]}
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
