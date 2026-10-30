import json, re, urllib.request
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8", "replace")
out = {}
try:
    h = get("https://zadarska-policija.gov.hr/")
    i = h.find("default.aspx?id=89")
    out["home_news"] = re.sub(r"\s+", " ", h[max(0, h.find("<body")):])[0:200]
    out["home_links"] = [l for l in dict.fromkeys(re.findall(r'href="([^"#]+)"', h)) if re.search(r"/\d{3,}$|vijest|priopc", l)][:40]
    p = get("https://zadarska-policija.gov.hr/vijesti/8")
    b = p[p.find("<body"):]
    b = re.sub(r"<script.*?</script>", "", b, flags=re.S)
    out["v8"] = re.sub(r"\s+", " ", b)[2000:9000]
except Exception as e:
    out["err1"] = repr(e)
try:
    h = get("https://liburnija-zadar.hr/")
    out["lib_links"] = [(l, t.strip()[:60]) for l, t in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h, re.S) if re.search(r"vozn|red|linij|karta|cijen|kolodvor|gradsk|prigradsk|mapa", l + t, re.I)][:60]
except Exception as e:
    out["err2"] = repr(e)
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
