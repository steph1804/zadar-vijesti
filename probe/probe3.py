import json, sys, traceback, concurrent.futures as cf
from datetime import datetime
sys.path.insert(0, ".")
import aggregator as A
out = {}
def go(src):
    r = {"feeds": []}
    try:
        for url in src.get("feeds", []):
            data, final = A.fetch_retry(url, src.get("timeout", A.TIMEOUT), src.get("retries", 1))
            items = A.PARSERS[src["parser"]](data, final) if src.get("parser") else A.parse_feed(data, final)
            ds = sorted(i["date"] for i in items if i["date"])
            span = None
            if len(ds) > 1:
                span = round((datetime.fromisoformat(ds[-1]) - datetime.fromisoformat(ds[0])).total_seconds() / 3600, 1)
            sec = [i for i in items if (src.get("link_contains") or src.get("categories")) and not A.wanted(src, i)]
            r["feeds"].append({"url": url, "n": len(items), "span_h": span,
                "other_section_but_local": [[i["title"][:70], i["categories"][:3]] for i in sec if A.is_local(i)][:8],
                "other_section_not_local": len(sec) - len([i for i in sec if A.is_local(i)]),
                "dropped_place": [i["title"][:70] for i in items if i not in sec and src.get("scope") != "national" and src.get("local_only", True) and not A.is_local(i)][:8]})
            if not src.get("combine"):
                break
    except Exception:
        r["err"] = traceback.format_exc()[-300:]
    return src["name"], r
srcs = [s for s in json.load(open("feeds.json")) if s.get("enabled", True) and s.get("kind") != "info"]
with cf.ThreadPoolExecutor(8) as p:
    out = dict(p.map(go, srcs))
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
