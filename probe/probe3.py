import json, os, sys, traceback, urllib.request, concurrent.futures as cf
sys.path.insert(0, ".")
try:
    import aggregator as A, cv2, numpy as np
    arts = [a for a in json.load(open("data/articles.json")) if a.get("image")][:120]
    def go(a):
        try:
            data, _ = A.fetch(a["image"], 30)
        except Exception:
            return None, None
        return A.face_box(a["image"]), data
    with cf.ThreadPoolExecutor(8) as p:
        res = list(p.map(go, arts))
    def crop(img, face, bw, bh):
        ih, iw = img.shape[:2]; sc = max(bw / iw, bh / ih)
        im = cv2.resize(img, (max(bw, round(iw * sc)), max(bh, round(ih * sc))))
        H, W = im.shape[:2]
        def axis(a, z, shown, full):
            extra = full - shown
            if extra <= 0: return 0
            a, z = a / 100 * full, z / 100 * full
            off = a if z - a > shown else (a + z) / 2 - shown / 2
            return int(min(max(off, 0), extra))
        if face:
            y = axis(face[0], face[1], bh, H); x = axis(face[2], face[3], bw, W)
        else:
            y = int((H - bh) * .3); x = (W - bw) // 2
        return im[y:y + bh, x:x + bw]
    tiles = []
    for (face, data), a in zip(res, arts):
        if data is None: continue
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None: continue
        t = crop(img, face, 270, 120)
        cv2.putText(t, ("F" if face else "-"), (5, 20), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 255) if face else (0, 255, 255), 2)
        tiles.append(t)
    tiles = tiles[:60]
    while len(tiles) % 4: tiles.append(np.zeros((120, 270, 3), np.uint8))
    rows = [np.hstack([cv2.copyMakeBorder(t, 3, 3, 3, 3, cv2.BORDER_CONSTANT) for t in tiles[i:i + 4]]) for i in range(0, len(tiles), 4)]
    cv2.imwrite("probe/sheet.jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 70])
    json.dump({"n": len(arts), "faces": sum(1 for f, _ in res if f)}, open("probe/result3.json", "w"))
except Exception:
    json.dump({"err": traceback.format_exc()}, open("probe/result3.json", "w"))
