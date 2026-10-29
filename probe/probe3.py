import json, sys, traceback, urllib.request
sys.path.insert(0, ".")
import aggregator as A
out = {}
try:
    A.main()
    out["ok"] = True
except BaseException:
    out["err"] = traceback.format_exc()[-3000:]
json.dump(out, open("probe/result3.json", "w"))
