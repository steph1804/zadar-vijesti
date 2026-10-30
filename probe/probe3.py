import json, urllib.request, xml.etree.ElementTree as ET
out = {}
for ua in ["Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)", "Python"]:
    r = urllib.request.urlopen(urllib.request.Request("https://idassa.click/sitemap.xml", headers={"User-Agent": ua}), timeout=20)
    b = r.read()
    try:
        ET.fromstring(b); ok = True
    except Exception as e:
        ok = repr(e)
    out[ua[:20]] = {"status": r.status, "headers": dict(r.headers), "len": len(b), "parse": ok, "body": b[:300].decode()}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
