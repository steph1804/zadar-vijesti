import re, urllib.request, json, collections
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://github.com/steph1804/zadar-vijesti)"
def get(u): return urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30).read().decode("utf-8","replace")
h = get("https://kkzadar.hr/")
css = re.findall(r'href=["\']([^"\']+\.css[^"\']*)', h)
txt = h
for c in css[:15]:
    try: txt += get(c)
    except Exception: pass
cols = collections.Counter(c.lower() for c in re.findall(r"#[0-9a-fA-F]{6}\b", txt))
out = {"theme": re.findall(r'theme-color[^>]*>', h), "vars": re.findall(r"--e-global-color-[a-z0-9]+:\s*#[0-9A-Fa-f]{3,8}", txt)[:20], "top": cols.most_common(25), "logo": re.findall(r'<img[^>]+logo[^>]*>', h)[:3]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
