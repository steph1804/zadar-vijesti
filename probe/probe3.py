import json, os, urllib.request, concurrent.futures as cf
os.makedirs("models", exist_ok=True)
m = "models/face_detection_yunet_2023mar.onnx"
urllib.request.urlretrieve("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", m)
import aggregator as A
arts = [a for a in json.load(open("data/articles.json")) if a.get("image")][:120]
with cf.ThreadPoolExecutor(8) as p:
    yu = list(p.map(lambda a: A.face_box(a["image"]), arts))
os.rename(m, m + ".off")
A._FACES.clear()
with cf.ThreadPoolExecutor(8) as p:
    haar = list(p.map(lambda a: A.face_box(a["image"]), arts))
os.rename(m + ".off", m)
out = {"size": os.path.getsize(m), "n": len(arts), "yunet": sum(x is not None for x in yu), "haar": sum(x is not None for x in haar),
       "rows": [[a["title"][:50], a["image"], y, h] for a, y, h in zip(arts, yu, haar)]}
json.dump(out, open("probe/result3.json", "w"), ensure_ascii=False, indent=1)
