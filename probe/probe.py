import re, urllib.request, json
import urllib.parse
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
out = {}
u = "https://www.hak.hr/info/stanje-na-cestama/"
body = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=40).read().decode("utf-8", "replace")
out["len"] = len(body)
out["scripts"] = re.findall(r'<script[^>]*src=["\']([^"\']+)', body)
out["ids"] = re.findall(r'id=["\']([^"\']+)', body)[:80]
out["tabs"] = re.findall(r'(?:href|data-[\w-]+)=["\']([^"\']*(?:tekst|text|karta|map|tab)[^"\']*)["\']', body, re.I)[:40]
i = body.find("<main"); out["main"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", body[i:i+6000]))[:3000]
for s in out["scripts"]:
    if "traffic" in s:
        js = urllib.request.urlopen(urllib.request.Request(urllib.parse.urljoin(u, s), headers={"User-Agent": UA}), timeout=40).read().decode("utf-8","replace")
        out.setdefault("api", []).extend(sorted(set(re.findall(r'["\'](/[\w/\-\.]*(?:api|json|stanje|traffic|info)[\w/\-\.?=&]*)["\']', js)))[:40])
json.dump(out, open("probe/result.json", "w"), ensure_ascii=False, indent=1)
