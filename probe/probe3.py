import json, socket, subprocess, urllib.request
out = {}
for n in ["idassa.click", "www.idassa.click"]:
    out[n] = subprocess.run(["dig", "+short", n], capture_output=True, text=True).stdout
    out[n + " cname"] = subprocess.run(["dig", "+short", "CNAME", n], capture_output=True, text=True).stdout
for u in ["https://steph1804.github.io/zadar-vijesti/", "http://idassa.click/", "https://idassa.click/", "https://www.idassa.click/"]:
    try:
        r = urllib.request.urlopen(u, timeout=20)
        b = r.read().decode("utf-8", "replace")
        out[u] = {"status": r.status, "final": r.geturl(), "title": b[b.find("<title>"):b.find("</title>")]}
    except Exception as e:
        out[u] = repr(e)[:200]
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
