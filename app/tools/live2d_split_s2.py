# -*- coding: utf-8 -*-
"""Live2D 自动拆层 Stage2：按「颜色分类 + 位置规则」组装各图层。

验收标准（客观）：把所有层按绘制顺序叠回去，应与原参考图基本一致（合成重建）。
产出：live2d/_work/layers/*.png（1024 画布坐标）+ 合成重建检查图。
"""
import os
import numpy as np
from PIL import Image
from collections import deque

OUT = r"D:\workbuddy\古见同学桌宠\live2d\_work"
LAY = os.path.join(OUT, "layers")
CANVAS = 1024
os.makedirs(LAY, exist_ok=True)

KEYS = ["hair", "skin", "blush", "cloth", "wine", "white", "dark", "line"]

# 调色板 v2（与 Stage1 相同）
PAL = {
    "hair":   ((0x56, 0x43, 0x59), (0x41, 0x2B, 0x42), (0x90, 0x78, 0x90), (0x30, 0x18, 0x30)),
    "skin":   ((0xFE, 0xF2, 0xE9), (0xFA, 0xF2, 0xEA), (0xFD, 0xF3, 0xEA)),
    "blush":  ((0xFB, 0xE0, 0xE0),),
    "cloth":  ((0x48, 0x48, 0x78), (0x3E, 0x33, 0x53)),
    "wine":   ((0x48, 0x30, 0x30), (0x4B, 0x30, 0x3E)),
    "white":  ((0xF4, 0xF4, 0xF2), (0xEF, 0xEF, 0xEA)),
    "dark":   ((0x14, 0x00, 0x02), (0x04, 0x04, 0x04)),
    "line":   ((0x10, 0x00, 0x06), (0x00, 0x00, 0x00)),
}
VECS = np.array([c for k in KEYS for c in PAL[k]], dtype=np.int32)
IDX = np.array([i for i, k in enumerate(KEYS) for c in PAL[k]])
CI = {k: i for i, k in enumerate(KEYS)}


def classify(a):
    h, w = a.shape[:2]
    rgb = a[..., :3].astype(np.int32).reshape(-1, 3)
    al = a[..., 3].reshape(-1)
    d = ((rgb[:, None, :] - VECS[None, :, :]) ** 2).sum(axis=2)
    cls = np.full(h * w, -1, np.int16)
    op = al > 128
    cls[op] = IDX[d.argmin(axis=1)][op]
    return cls.reshape(h, w)


def components(mask, min_px=1):
    """连通域（BFS）。返回 [(size, y0,y1,x0,x1, mask)]，按大小降序。"""
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32); cur = 0; out = []
    for sy in range(h):
        for sx in range(w):
            if mask[sy, sx] and lab[sy, sx] == 0:
                cur += 1; qq = deque([(sx, sy)]); lab[sy, sx] = cur
                cnt = 0; x0 = x1 = sx; y0 = y1 = sy
                while qq:
                    x, y = qq.popleft(); cnt += 1
                    x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
                    for nx, ny in ((x+1, y), (x-1, y), (x, y+1), (x, y-1)):
                        if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur; qq.append((nx, ny))
                out.append((cnt, y0, y1, x0, x1, lab == cur))
    out = [o for o in out if o[0] >= min_px]
    out.sort(key=lambda o: -o[0])
    return out


def solidify(mask, it=2):
    """填洞 + 闭运算（形态学近似）：让层变成实心，避免形变露底。"""
    m = mask.copy()
    for _ in range(it):
        p = np.pad(m, 1)
        m = (p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]) | m
    return m


def dilate(mask, it=1):
    m = mask.copy()
    for _ in range(it):
        p = np.pad(m, 1)
        m = (p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] |
             p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
    return m


def erode(mask, it=1):
    """腐蚀：与 dilate 相反（AND 邻域）。补块要往里收，避免从头发缝里露出肤色。"""
    m = mask.copy()
    for _ in range(it):
        p = np.pad(m, 1, constant_values=False)
        m = (p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:] &
             p[:-2, :-2] & p[:-2, 2:] & p[2:, :-2] & p[2:, 2:])
    return m


def to_img(mask, rgb):
    """mask → RGBA 图层（纯色 + alpha）。"""
    im = np.zeros((CANVAS, CANVAS, 4), np.uint8)
    im[..., 0], im[..., 1], im[..., 2] = rgb
    im[..., 3] = np.where(mask, 255, 0)
    return Image.fromarray(im, "RGBA")


def outline_of(mask, cls, it=2):
    """把 mask 周边的描边像素（line 类）并入该层 —— 描边必须跟着部件走。"""
    ln = cls == CI["line"]
    near = dilate(mask, it) & ln & ~mask
    return mask | near


def main():
    cv = np.array(Image.open(os.path.join(OUT, "s1_canvas.png")))
    cls = np.load(os.path.join(OUT, "s1_cls.npy"))
    H, W = cls.shape
    C = {k: (cls == CI[k]) for k in KEYS}
    sil = np.zeros((H, W), bool)
    for k in KEYS:
        sil |= C[k]

    # ---- 行剖面 → 找 neck / waist / legs 三条分界（实测：555 / 800 / 890）----
    rows = {k: C[k].sum(axis=1) for k in ("skin", "cloth", "wine", "dark")}
    silw = sil.sum(axis=1)
    face_rows = np.nonzero(rows["skin"] > 100)[0]
    neck = int(face_rows.max()) + 12                # 最后一次"脸很多"的下一行
    waist = next(y for y in range(neck + 40, H) if rows["wine"][y] > rows["cloth"][y] and y > neck + 40)
    wmax = silw[waist:waist + 60].max()
    legs = next(y for y in range(waist + 60, H) if silw[y] < wmax * 0.45)
    print(f"分界: neck={neck} waist={waist} legs={legs}")

    layers = {}

    # ---- 02_前发：全部可见头发（正视图里头发都在身体前面）----
    hair = components(C["hair"], 400)[0][5]
    hair = solidify(dilate(hair, 1), 1)
    layers["02_前发"] = outline_of(hair, cls, 2)

    # ---- 10_脸·肤色底：最大肤色连通域（脸），填洞 ----
    face = components(C["skin"], 500)[0][5]
    face = solidify(face, 3)
    face = dilate(face, 1)
    layers["10_脸_肤色底"] = outline_of(face, cls, 2)
    fb = np.nonzero(face.any(axis=1))[0]
    face_top, face_bot = fb.min(), fb.max()
    fxs = np.nonzero(face.any(axis=0))[0]
    face_x0, face_x1 = fxs.min(), fxs.max()

    # ---- 眼睛：脸区内的两个 cloth 团 ----
    head_y1 = face_bot + 20
    orbs = [c for c in components(C["cloth"] & (np.arange(H)[:, None] < head_y1), 300)]
    orbs = [o for o in orbs if o[0] > 800][:2]
    orbs.sort(key=lambda o: o[4])                     # 左右
    print("眼珠团:", [(o[0], o[3], o[4]) for o in orbs])
    eye_layers = {"05_眼_瞳孔高光": [], "06_眼_眼白": [], "04_眼_上睑": []}
    orb_all = np.zeros((H, W), bool)
    for _, oy0, oy1, ox0, ox1, om in orbs:
        orb_all |= om
        # 05：瞳孔 = 实心化的眼珠（往下延伸，供贝尔现象上移）
        eye_layers["05_眼_瞳孔高光"].append(solidify(dilate(om, 1), 2))
        # 06：眼白 = 眼框内的 white
        box = np.zeros((H, W), bool)
        box[oy0 - 4:oy1 + 6, ox0 - 6:ox1 + 6] = True
        eye_layers["06_眼_眼白"].append(C["white"] & box & ~om)
        # 04：上睑 = 眼框上方的肤色盖板 + 其上的睑粗线
        eh = oy1 - oy0
        flap = np.zeros((H, W), bool)
        flap[max(0, oy0 - eh - 4):oy0 + 3, ox0 - 8:ox1 + 8] = True
        flap &= (C["skin"] | C["line"] | C["white"]) & ~om
        flap = solidify(flap, 2) | (C["line"] & box & ~om)
        eye_layers["04_眼_上睑"].append(flap)
    for k, ms in eye_layers.items():
        m = np.zeros((H, W), bool)
        for x in ms:
            m |= x
        layers[k] = m

    # ---- 03_眉：本设计眉与睑同笔 → 已并入 04（留空并在手册注明）----
    layers["03_眉"] = np.zeros((H, W), bool)

    # ---- 09_嘴：脸下缘中央的 line 小团 + 合成张嘴形 ----
    mouth_zone = np.zeros((H, W), bool)
    mouth_zone[face_bot - 60:face_bot + 6, face_x0 + 60:face_x1 - 60] = True
    mc = [c for c in components(C["line"] & mouth_zone & ~orb_all, 20)]
    mouth = np.zeros((H, W), bool)
    for c in mc[:1]:
        mouth |= c[5]
    layers["09_嘴"] = mouth
    # 张嘴形（合成）：闭嘴线下方一个椭圆
    if mouth.any():
        ys, xs = np.nonzero(mouth)
        cy, cx = int(ys.mean()) + 6, int(xs.mean())
        yy, xx = np.ogrid[:H, :W]
        open_m = ((yy - cy) / 9.0) ** 2 + ((xx - cx) / 7.0) ** 2 <= 1
        layers["09_嘴_张形"] = open_m

    # ---- 11_腮红：只留**脸区内**的团（领结的粉条纹也会命中 blush 色，得按位置排除）----
    bl = np.zeros((H, W), bool)
    for c in components(C["blush"], 120):
        if c[2] < face_bot + 12:                 # y1 在脸下缘以上
            bl |= c[5]
    layers["11_腮红"] = bl

    # ---- 12_身体制服：cloth ∪ white ∪ wine ∩ [neck, waist) —— **全部团块并集**再实心化
    #      （外套被白衬衫/领结分隔成多块，只取最大块会只剩半边）
    band = np.zeros((H, W), bool); band[neck:waist] = True
    body = (C["cloth"] | (C["white"] & band) | (C["wine"] & band)) & band
    body = np.zeros((H, W), bool)
    for c in components((C["cloth"] | (C["white"] & band) | (C["wine"] & band)) & band, 300):
        body |= c[5]
    layers["12_身体制服"] = outline_of(solidify(body, 2), cls, 2)

    # ---- 14_裙摆：裙子的酒红/深色与头发同色系，被分进 hair 类 →
    #      用「hair∪wine∪dark + 位置(居中)」取，范围 [waist, waist+95]；底边多画 3px
    band2 = np.zeros((H, W), bool); band2[waist:waist + 95] = True
    cand = (C["wine"] | C["dark"] | C["hair"]) & band2
    cx_body = (face_x0 + face_x1) / 2
    body_w = (face_x1 - face_x0)
    skirt = np.zeros((H, W), bool)
    for c in components(cand, 250):
        x0, x1 = c[3], c[4]
        if x1 > cx_body - body_w * 0.42 and x0 < cx_body + body_w * 0.42:
            skirt |= c[5]
    skirt = solidify(skirt, 1)
    ext = np.zeros((H, W), bool); ext[waist + 88:waist + 94] = True
    skirt = skirt | (dilate(skirt, 1) & ext)
    layers["14_裙摆"] = outline_of(skirt, cls, 2)

    # ---- 15_腿袜：同样含 hair 类；y ≥ waist+85，只留居中的团 ----
    band3 = np.zeros((H, W), bool); band3[waist + 85:] = True
    legs_all = (C["dark"] | C["wine"] | C["hair"]) & band3
    legs_m = np.zeros((H, W), bool)
    for c in components(legs_all, 300):
        x0, x1 = c[3], c[4]
        if x1 > cx_body - body_w * 0.30 and x0 < cx_body + body_w * 0.30:
            legs_m |= c[5]
    layers["15_腿袜"] = outline_of(solidify(legs_m, 1), cls, 2)

    # ---- 17_补块：额头肤色片 = 前发上半部**往里收**（否则会从头发缝里露出肤色）----
    fy = np.nonzero(layers["02_前发"].any(axis=1))[0]
    head_top = int(fy.min())
    zone = np.zeros((H, W), bool); zone[head_top:face_top + 8] = True
    patch = erode(zone & layers["02_前发"], 6)
    patch &= ~layers["10_脸_肤色底"]
    layers["17_补块_被遮挡区"] = patch

    # ---- 16_后发：背视图发幔对齐后垫底（按**全身高度**对齐，不是头发高度）----
    bk = Image.open(r"D:\workbuddy\古见同学桌宠\live2d\reference\back.png").convert("RGBA")
    ba = np.array(bk)
    hm = (ba[..., 3] > 8) & (classify(ba) == CI["hair"])
    hm = components(hm, 2000)[0][5]
    bys, bxs = np.nonzero(hm)
    bh = bys.max() - bys.min()
    sys_ = np.nonzero(sil.any(axis=1))[0]
    fh = sys_.max() - sys_.min()                # 前视图全身高度
    s = fh / bh
    bim = Image.fromarray((hm * 255).astype(np.uint8)).resize(
        (max(1, int(hm.shape[1] * s)), max(1, int(hm.shape[0] * s))), Image.LANCZOS)
    back = np.zeros((H, W), bool)
    bb = np.array(bim) > 128
    fx = np.nonzero(sil.any(axis=0))[0]
    ox = int((fx.min() + fx.max()) / 2 - bim.width / 2)
    oy = int(sys_.min())
    # 安全贴合：背视发幔比正面"长"（背视几乎全是头发），超出画布的部分裁掉
    y0d, y1d = max(0, oy), min(H, oy + bim.height)
    x0d, x1d = max(0, ox), min(W, ox + bim.width)
    if y1d > y0d and x1d > x0d:
        back[y0d:y1d, x0d:x1d] = bb[y0d - oy:y1d - oy, x0d - ox:x1d - ox]
    back &= ~layers["02_前发"]                  # 避免与前发双重绘制
    layers["16_后发"] = back

    # ---- 保存 + 合成重建 ----
    RGB = {"02_前发": (0x56, 0x43, 0x59), "03_眉": (0x10, 0x00, 0x06),
           "04_眼_上睑": (0xFE, 0xF2, 0xE9), "05_眼_瞳孔高光": (0x3E, 0x33, 0x53),
           "06_眼_眼白": (0xEF, 0xEF, 0xEA), "09_嘴": (0x10, 0x00, 0x06),
           "09_嘴_张形": (0x3A, 0x22, 0x30), "10_脸_肤色底": (0xFE, 0xF2, 0xE9),
           "11_腮红": (0xFB, 0xE0, 0xE0), "12_身体制服": (0x48, 0x48, 0x78),
           "14_裙摆": (0x48, 0x30, 0x30), "15_腿袜": (0x30, 0x20, 0x1C),
           "16_后发": (0x41, 0x2B, 0x42), "17_补块_被遮挡区": (0xFE, 0xF2, 0xE9)}
    order = ["16_后发", "17_补块_被遮挡区", "15_腿袜", "14_裙摆", "12_身体制服",
             "10_脸_肤色底", "11_腮红", "09_嘴", "09_嘴_张形", "06_眼_眼白",
             "05_眼_瞳孔高光", "04_眼_上睑", "03_眉", "02_前发"]
    comp = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    for k in order:
        if k in layers and layers[k].any():
            lay = to_img(layers[k], RGB[k])
            comp.paste(lay, (0, 0), lay)
            lay.save(os.path.join(LAY, k + ".png"))
    comp.save(os.path.join(OUT, "s2_composite.png"))
    print("已产出", len([k for k in layers if layers[k].any()]), "个非空层 →", LAY)
    print("合成重建 →", os.path.join(OUT, "s2_composite.png"))


if __name__ == "__main__":
    main()
