import json, re, urllib.request, urllib.error, concurrent.futures as cf
UA = "Mozilla/5.0 (compatible; LokalniAgregator/1.0; +https://idassa.click)"
cands = {
 "Slobodna Dalmacija (sve)": "https://slobodnadalmacija.hr/feed",
 "Dalmatinski portal": "https://dalmatinskiportal.hr/feed",
 "Dalmacija News": "https://www.dalmacijanews.hr/feed/",
 "Nacional": "https://www.nacional.hr/feed/",
 "Lider": "https://lidermedia.hr/feed",
 "Narod.hr": "https://narod.hr/feed",
 "Hrvatska danas": "https://hrvatska-danas.com/feed/",
 "Express": "https://express.24sata.hr/feeds/rss",
 "24sata": "https://www.24sata.hr/feeds/aktualno.xml",
 "Gol.hr": "https://gol.dnevnik.hr/assets/feed/articles/",
 "Sportske novosti": "https://sportske.jutarnji.hr/feed",
 "Telesport": "https://telesport.telegram.hr/feed/",
 "HRTurizam": "https://hrturizam.hr/feed/",
 "Agroklub": "https://www.agroklub.com/rss/",
 "Glas Hrvatske/HRT Radio": "https://radio.hrt.hr/rss",
 "RTL": "https://www.rtl.hr/rss",
 "Direktno": "https://direktno.hr/rss/",
 "Bug (n/a)": "https://www.bug.hr/rss",
 "Šibenik.in": "https://www.sibenik.in/feed",
 "Ličke novine": "https://www.licke-novine.hr/feed/",
 "Croatia Week": "https://www.croatiaweek.com/feed/",
 "Total Croatia News": "https://total-croatia-news.com/feed/",
 "Dalmatinska akcija/dalmacija.hr": "https://www.dalmacija.hr/feed",
 "Mirovina.hr": "https://mirovina.hr/feed/",
 "Hina": "https://www.hina.hr/rss",
 "Vecernji Dalmacija": "https://www.vecernji.hr/feeds/section/dalmacija",
 "Index search": "https://www.index.hr/rss/vijesti",
}
search = {n: u for n, u in {
 "Jutarnji ?s": "https://www.jutarnji.hr/feed?s=zadar", "Telegram ?s": "https://www.telegram.hr/?s=zadar&feed=rss2",
 "N1 ?s": "https://n1info.hr/?s=zadar&feed=rss2", "Novi list ?s": "https://www.novilist.hr/?s=zadar&feed=rss2",
 "Dalmacija Danas ?s": "https://www.dalmacijadanas.hr/?s=zadar&feed=rss2", "Poslovni ?s": "https://www.poslovni.hr/?s=zadar&feed=rss2",
 "Dalmatinski portal ?s": "https://dalmatinskiportal.hr/?s=zadar&feed=rss2", "Morski ?s": "https://www.morski.hr/?s=zadar&feed=rss2",
 "Sportklub ?s": "https://sportklub.hr/?s=zadar&feed=rss2", "HRTurizam ?s": "https://hrturizam.hr/?s=zadar&feed=rss2",
 "Dalmacija News ?s": "https://www.dalmacijanews.hr/?s=zadar&feed=rss2", "Nacional ?s": "https://www.nacional.hr/?s=zadar&feed=rss2",
 "Lider ?s": "https://lidermedia.hr/?s=zadar&feed=rss2", "Narod ?s": "https://narod.hr/?s=zadar&feed=rss2",
 "Telesport ?s": "https://telesport.telegram.hr/?s=zadar&feed=rss2", "Sportske ?s": "https://sportske.jutarnji.hr/?s=zadar&feed=rss2",
 "Total Croatia ?s": "https://total-croatia-news.com/?s=zadar&feed=rss2", "Croatia Week ?s": "https://www.croatiaweek.com/?s=zadar&feed=rss2",
}.items()}
def go(kv):
    n, u = kv
    try:
        r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=25)
        b = r.read().decode("utf-8", "replace")
        its = re.findall(r"<(?:item|entry)[ >](.*?)</(?:item|entry)>", b, re.S)
        tit = [re.sub(r"<!\[CDATA\[|\]\]>", "", (re.findall(r"<title[^>]*>(.*?)</title>", it, re.S) or [""])[0])[:70] for it in its]
        dates = [(re.findall(r"<(?:pubDate|published|updated)>(.*?)</", it) or [""])[0] for it in its[:1]]
        z = [t for t in tit if re.search(r"zadar|zadr|biograd|benkovac|pag\b|nin\b|obrovac|ugljan|pašman|dugi otok|zemunik|sukošan|starigrad", t, re.I)]
        return n, {"u": u, "n": len(its), "first": dates, "zadar_titles": z[:5], "sample": tit[:2]}
    except urllib.error.HTTPError as e:
        return n, {"u": u, "err": e.code, "srv": e.headers.get("Server")}
    except Exception as e:
        return n, {"u": u, "err": repr(e)[:80]}
with cf.ThreadPoolExecutor(10) as p:
    out = dict(p.map(go, list(cands.items()) + list(search.items())))
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
