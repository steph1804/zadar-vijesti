import json, sys, traceback, urllib.request
sys.path.insert(0, ".")
out = {}
try:
    import aggregator as A
    src = next(s for s in json.load(open("feeds.json")) if s["name"] == "Policija Zadar")
    items, errs = A.load_source(src)
    out["police"] = [[i["date"], i["title"], i["link"], i["summary"][:80]] for i in items[:12]]
    out["errs"] = errs
    out["img"] = A.og_image(items[1]["link"]) if len(items) > 1 else None
except Exception:
    out["tb"] = traceback.format_exc()
for slug in ["gradski-prijevoz", "prigradski-prijevoz", "medugradski-prijevoz"]:
    u = f"https://liburnija-zadar.hr/{slug}/"
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (compatible; LokalniAgregator/1.0)"}), timeout=20)
        out[u] = [r.status, r.geturl()]
    except Exception as e:
        out[u] = repr(e)[:100]
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
