# -*- coding: utf-8 -*-
"""把「拆件板」PSD 自动组装成「原位对齐」PSD（Live2D 可直接导入）。

原理：
  1) 每个图层按 alpha 拆成连通域（部件）——因为一张层里可能有多块（如两只眼）
  2) 每块在**参考图**（三视图正视图）上做**多尺度带掩码模板匹配** → 得到位置 + 比例
  3) 大块先定全局比例（中位数），小块再在该比例邻域内精定位（小块单独多尺度不可靠）
  4) 按"原生分辨率"把每块贴回原位，导出分层 PSD

用法：python tools/assemble_psd.py <输入.psd>
"""
import os
import sys
from collections import Counter, deque

import cv2
import numpy as np
from PIL import Image
from psd_tools import PSDImage

REF = r"D:\workbuddy\古见同学桌宠\live2d\reference\front.png"
OUT_PSD = r"D:\workbuddy\古见同学桌宠\live2d\拆层_原位对齐.psd"
OUT_PREVIEW = r"D:\workbuddy\古见同学桌宠\live2d\_work\assemble_check.png"

# 自下而上（PSD 内部顺序）= 绘制顺序 最后 → 最前
ORDER = ["后发", "腿", "裙子", "身体", "手", "头",
         "嘴巴", "鼻子", "腮红", "眼睛", "高光", "上眼敛",
         "左刘海", "右刘海", "头发"]
RENAME = {"图层22": "身体"}
SRC_ALIAS = {"身体": ["身体", "图层22"]}      # 目标层名 → 源图层名候选
BIG_AREA = 3000
FORCE_STRUCT = {"头发"}      # 自相似大件：放弃匹配，强制用结构锚点

# 位置先验：以参考图里"角色包围盒"的比例给出每层允许的搜索范围 (x0,y0,x1,y1)。
# 作用：掐掉伪匹配——没有先验时"裙摆"会匹配到头顶的头发并给出 0.89 的虚高相似度。
PRIORS = {
    "后发":    (0.00, 0.00, 1.00, 0.95),
    "腿":      (0.15, 0.68, 0.90, 1.00),
    "裙子":    (0.10, 0.52, 0.95, 0.88),
    "身体":    (0.10, 0.28, 0.95, 0.72),
    "手":      (0.00, 0.28, 1.00, 0.80),
    "头":      (0.00, 0.02, 1.00, 0.66),
    "头发":    (0.00, 0.00, 1.00, 0.52),     # 前发/刘海在头部上半
    "左刘海":  (0.00, 0.00, 1.00, 0.62),
    "右刘海":  (0.00, 0.00, 1.00, 0.62),
    "嘴巴":    (0.20, 0.18, 0.80, 0.58),
    "鼻子":    (0.20, 0.18, 0.80, 0.58),
    "腮红":    (0.05, 0.14, 0.95, 0.58),
    "眼睛":    (0.05, 0.10, 0.95, 0.55),
    "高光":    (0.05, 0.10, 0.95, 0.55),
    "上眼敛":  (0.05, 0.08, 0.95, 0.55),
}


def components(rgba, min_area=12):
    """按 alpha 拆连通域 → [(sub_rgba, x, y, area)]"""
    a = rgba[..., 3]
    m = a > 20
    h, w = m.shape
    lab = np.zeros((h, w), np.int32)
    out = []
    cur = 0
    for sy in range(h):
        for sx in range(w):
            if m[sy, sx] and lab[sy, sx] == 0:
                cur += 1
                qq = deque([(sx, sy)]); lab[sy, sx] = cur
                x0 = x1 = sx; y0 = y1 = sy; cnt = 0
                while qq:
                    x, y = qq.popleft(); cnt += 1
                    x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1),
                                   (x + 1, y + 1), (x - 1, y - 1), (x + 1, y - 1), (x - 1, y + 1)):
                        if 0 <= nx < w and 0 <= ny < h and m[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur; qq.append((nx, ny))
                if cnt >= min_area:
                    out.append((rgba[y0:y1 + 1, x0:x1 + 1].copy(), x0, y0, cnt))
    return out


def masked_ncc(img_g, templ_g, mask):
    """掩码零均值归一化互相关（masked NCC）→ 相似度图，1.0 = 完美。

    为什么不用 OpenCV 的 TM_CCORR_NORMED：它对"亮块/小模板"有系统性偏袒
    （实测：裙摆会匹配到头顶并给出 0.89 的虚高分；尺度会被选成 0.56）。
    TM_CCOEFF_NORMED 不支持掩码，所以按标准配方手算：
      NCC = Σ(T'·I') / sqrt( ΣI'² · ΣT'² )，T' = (T - T̄)·M，I' = I - Ī（在掩码内求均值）
    """
    m = (mask > 20).astype(np.float32)
    npx = float(m.sum())
    if npx < 20:
        return None
    t = templ_g.astype(np.float32)
    tmean = float((t * m).sum() / npx)
    tc = (t - tmean) * m
    tc2 = float((tc ** 2).sum())
    if tc2 <= 1e-6:
        return None
    img = img_g.astype(np.float32)
    isum = cv2.matchTemplate(img, m, cv2.TM_CCORR)
    isum2 = cv2.matchTemplate(img * img, m, cv2.TM_CCORR)
    num = cv2.matchTemplate(img, tc, cv2.TM_CCORR)
    imean = isum / npx
    var = isum2 - 2.0 * imean * isum + (imean ** 2) * npx
    # ⚠️ 分母下限：平坦区域（掩码内近乎纯色）方差趋零 → NCC 会炸成几百的伪高分。
    # 要求掩码内至少有 ~10 灰阶的标准差，否则该位置视为不可信。
    var = np.maximum(var, npx * 100.0)
    ncc = num / np.sqrt(var * tc2)
    return np.clip(ncc, -1.0, 1.0)


def match_vote(ref_bgr, rgba, mask, scales, region=None, grid=3):
    """**抗遮挡**匹配：把部件切成重叠子块，各自匹配后按位置投票。

    为什么需要它：整块匹配对被遮挡部件天然无效——「头」层是完整头形，但参考图里
    额头被刘海盖住、脸颊被侧发挡住；「后发」更是大半藏在身后。实测整块匹配只给 0.20。
    可见子块会一致投出真位置（取中位数），被遮挡的子块成为离群值被自动忽略。
    """
    h, w = rgba.shape[:2]
    ph, pw = max(28, int(h * 0.55)), max(28, int(w * 0.55))
    if h <= ph or w <= pw:
        return match(ref_bgr, rgba[..., :3], mask, scales, region=region)
    ys_list = np.linspace(0, h - ph, grid).astype(int)
    xs_list = np.linspace(0, w - pw, grid).astype(int)
    votes = []
    for ys in ys_list:
        for xs in xs_list:
            sub = rgba[ys:ys + ph, xs:xs + pw, :3]
            sm = mask[ys:ys + ph, xs:xs + pw]
            if int((sm > 20).sum()) < 300:
                continue
            sc, s, mx, my = match(ref_bgr, sub, sm, scales, region=region)
            if s and sc >= 0.45:
                votes.append((mx - xs, my - ys, sc, s))     # 反推部件左上角
    if len(votes) < 3:
        return match(ref_bgr, rgba[..., :3], mask, scales, region=region)
    off = np.array([[v[0], v[1]] for v in votes], float)
    wts = np.array([v[2] for v in votes], float)
    # 中位数投票（对离群稳健）
    ox = int(round(np.median(off[:, 0])))
    oy = int(round(np.median(off[:, 1])))
    ss = float(np.median([v[3] for v in votes]))
    sc = float(wts.mean())
    print(f"      投票 {len(votes)} 块 → 左上角({ox},{oy}) s={ss:.2f} 平均分={sc:.3f}")
    return (sc, ss, ox, oy)


def match(ref_bgr, tmpl, mask, scales, blocked=None, region=None):
    """多尺度掩码 NCC 匹配 → (best_score, best_scale, x, y)

    blocked: [(cx,cy,w,h)] 已占位区域 → 压到 -1（对称部件必须抑制：两只眼长得一样）
    region:  (x0,y0,x1,y1) 位置先验限定搜索范围
    """
    best = (-1.0, None, 0, 0)
    rh, rw = ref_bgr.shape[:2]
    x0r, y0r, x1r, y1r = region if region else (0, 0, rw, rh)
    x0r, y0r = max(0, x0r), max(0, y0r)
    x1r, y1r = min(rw, x1r), min(rh, y1r)
    if x1r - x0r < 8 or y1r - y0r < 8:
        return best
    sub_g = cv2.GaussianBlur(cv2.cvtColor(ref_bgr[y0r:y1r, x0r:x1r], cv2.COLOR_BGR2GRAY), (0, 0), 0.8)
    for s in scales:
        th, tw = int(tmpl.shape[0] * s), int(tmpl.shape[1] * s)
        if th < 6 or tw < 6 or th > sub_g.shape[0] or tw > sub_g.shape[1]:
            continue
        t = cv2.resize(tmpl, (tw, th), interpolation=cv2.INTER_AREA)
        mk = cv2.resize(mask, (tw, th), interpolation=cv2.INTER_NEAREST)
        tg = cv2.cvtColor(t, cv2.COLOR_BGR2GRAY)
        score = masked_ncc(sub_g, cv2.GaussianBlur(tg, (0, 0), 1.2), mk)
        if score is None:
            continue
        score = np.nan_to_num(score, nan=-1, posinf=-1, neginf=-1)
        if blocked:
            for (bcx, bcy, bw, bh) in blocked:
                bx = bcx - x0r; by = bcy - y0r
                sx0 = max(0, int(bx - bw * 0.6)); sx1 = min(score.shape[1], int(bx + bw * 0.6))
                sy0 = max(0, int(by - bh * 0.6)); sy1 = min(score.shape[0], int(by + bh * 0.6))
                if sx1 > sx0 and sy1 > sy0:
                    score[sy0:sy1, sx0:sx1] = -1
        _, mx, _, ml = cv2.minMaxLoc(score)
        if mx > best[0]:
            best = (float(mx), s, int(ml[0]) + x0r, int(ml[1]) + y0r)
    return best


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\htft2\Pictures\Camera Roll\1790416633761.psd"
    ref = Image.open(REF).convert("RGB")
    ref_bgr = cv2.cvtColor(np.array(ref), cv2.COLOR_RGB2BGR)
    # 参考图里角色包围盒（非白区域）→ 位置先验的基准
    _g = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY)
    _nz = np.nonzero(_g < 245)
    CBX0, CBY0, CBX1, CBY1 = int(_nz[1].min()), int(_nz[0].min()), int(_nz[1].max()), int(_nz[0].max())
    CBW, CBH = CBX1 - CBX0, CBY1 - CBY0
    print(f"参考图角色包围盒: x[{CBX0},{CBX1}] y[{CBY0},{CBY1}]")

    def prior_region(name):
        p = PRIORS.get(name)
        if not p:
            return None
        return (CBX0 + int(p[0] * CBW), CBY0 + int(p[1] * CBH),
                CBX0 + int(p[2] * CBW), CBY0 + int(p[3] * CBH))

    psd = PSDImage.open(src)
    W, H = psd.width, psd.height

    # 收集图层（组内叶子也算，用 组/叶子 记录）
    layers = {}

    def collect(nodes, parent=""):
        for n in nodes:
            if n.is_group():
                collect(n, n.name)
            else:
                im = n.composite()
                if im is None:
                    continue
                if im.mode != "RGBA":
                    im = im.convert("RGBA")
                a = np.array(im)
                if a.shape[0] != H or a.shape[1] != W:      # 补齐到画布
                    big = np.zeros((H, W, 4), np.uint8)
                    big[:a.shape[0], :a.shape[1]] = a
                    a = big
                layers[n.name] = a
    collect(psd)
    print("图层:", list(layers))

    # ---- 拆连通域 ----
    comps = {}          # name -> [(rgba,x,y,area)]
    for name, arr in layers.items():
        cs = components(arr)
        comps[name] = cs
        print(f"  {name:8} → {len(cs)} 块 " + " ".join(f"{c[3]}px" for c in cs))

    # ---- 第 1 遍：大块多尺度，求全局比例 ----
    # ---- 第 1 遍：大块多尺度 → 求全局比例 ----
    # ⚠️ 尺度范围必须收紧（0.6~1.5）：放到 0.30 会让头发类部件在边界上骗到高分
    #（CCORR_NORMED 对"缩得很小的简单块"有偏），从而把全局比例投成 0.30。
    big_scales = np.arange(0.60, 1.52, 0.02)
    scale_votes = []
    for name, cs in comps.items():
        for i, (rgba, x, y, area) in enumerate(cs):
            if area < BIG_AREA:
                continue
            bgr = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2BGR)
            sc, s, mx, my = match(ref_bgr, bgr, rgba[..., 3], big_scales,
                                  region=prior_region(name))
            if s and sc >= 0.85:
                scale_votes += [round(s, 2)] * (min(area, 40000) // 4000)   # 面积加权
                print(f"  [大块] {name}#{i} {area}px score={sc:.3f} scale={s:.2f} at({mx},{my})")
    if not scale_votes:
        raise SystemExit("没有任何大块匹配成功")
    # 众数（比中位数更抗离群）
    gscale = Counter(scale_votes).most_common(1)[0][0]
    print(f"\n全局比例 = {gscale:.3f}（投票 {Counter(scale_votes).most_common(4)}）")

    # ---- 第 2 遍：逐层精定位（同层多块抑制已占位；小块限定搜索范围）----
    # ⚠️ 收紧到 ±0.01：同一张画切出来的部件，比例本就该一致。
    # 早期放到 ±0.04 时，各部件的匹配比例在 1.06~1.14 之间乱飘，
    # 而位置换算用全局比例 1.10 → 500px 处就有 15~20px 的系统性错位（头偏高、嘴鼻偏位）。
    fine = np.array([gscale - 0.01, gscale, gscale + 0.01])
    fine = fine[(fine > 0.4) & (fine < 1.6)]
    placed = {}
    head_box = None
    eye_boxes = []                      # 已对好的眼睛框（参考坐标）→ 供高光/上眼睑用
    for name, cs in comps.items():
        used = []                       # 本层已占位 (cx,cy,w,h)
        for i, (rgba, x, y, area) in enumerate(cs):
            # 高光/上眼睑**长在眼睛上**：搜索范围直接锁到眼睛框附近，避免漂到脸颊
            reg = prior_region(name)
            if name in ("高光", "上眼敛") and eye_boxes:
                ex0 = min(b[0] for b in eye_boxes); ey0 = min(b[1] for b in eye_boxes)
                ex1 = max(b[0] + b[2] for b in eye_boxes); ey1 = max(b[1] + b[3] for b in eye_boxes)
                pad = int(0.25 * (ey1 - ey0))
                reg = (int(ex0 - pad), int(ey0 - pad), int(ex1 + pad), int(ey1 + pad))
            bgr = cv2.cvtColor(rgba[..., :3], cv2.COLOR_RGB2BGR)
            sc, s, mx, my = match(ref_bgr, bgr, rgba[..., 3], fine,
                                  blocked=used, region=reg)
            if sc < 0.60 and area >= 900:      # 整块匹配不可靠 → 子块投票（抗遮挡）
                sc2, s2, mx2, my2 = match_vote(ref_bgr, rgba, rgba[..., 3], fine,
                                               region=reg)
                if sc2 > sc:
                    sc, s, mx, my = sc2, s2, mx2, my2
            placed[(name, i)] = (sc, s, mx, my, rgba, area)
            if name == "眼睛" and s and sc >= 0.5:
                eye_boxes.append((mx, my, rgba.shape[1] * s, rgba.shape[0] * s))
            if s:
                used.append((mx, my, int(rgba.shape[1] * s), int(rgba.shape[0] * s)))
            print(f"  {name:8}#{i} {area:6d}px score={sc:.3f} s={s if s else 0:.2f}"
                  f" at({mx:4d},{my:4d})")
        # 头匹配完后记录头框，供小块使用
        if name == "头" and cs:
            big_i = max(range(len(cs)), key=lambda k: cs[k][3])
            sc, s, mx, my, rgba, area = placed[("头", big_i)]
            if s:
                head_box = (int(mx * 0.98), int(my * 0.98),
                            int(mx + rgba.shape[1] * s * 1.05),
                            int(my + rgba.shape[0] * s * 1.35))   # 含下巴/嘴的范围

    # ---- 第 2.5 遍：全遮挡件用**结构锚点**定位 ----
    # 头/后发/裙/左刘海/嘴在参考图里几乎全被遮住（子块投票也救不回来），
    # 但它们的位置可由"已对好的部件"反推：眼睛→头/嘴/鼻；外套→裙；右刘海→左刘海（镜像）
    anchors = {}
    for (nm, i), (sc, s, mx, my, rgba, area) in placed.items():
        if nm == "眼睛" and s and sc > 0.6:
            anchors.setdefault("eye_c", []).append((mx + rgba.shape[1] * s / 2, my + rgba.shape[0] * s / 2))
        if nm in ("图层22", "身体") and s and sc > 0.6 and area > 5000:
            anchors["body"] = (mx, my, rgba.shape[1] * s, rgba.shape[0] * s)
        if nm == "右刘海" and s and sc > 0.45:
            anchors["bang_r"] = (mx, my, rgba.shape[1] * s)
    eye_x = eye_y = None
    if anchors.get("eye_c"):
        eye_x = sum(p[0] for p in anchors["eye_c"]) / len(anchors["eye_c"])
        eye_y = sum(p[1] for p in anchors["eye_c"]) / len(anchors["eye_c"])
    print(f"\n锚点: 眼中心=({eye_x and round(eye_x)},{eye_y and round(eye_y)}) "
          f"身体={anchors.get('body') and '有'} 右刘海={anchors.get('bang_r') and '有'}")

    def struct_place(name, rgba):
        """按结构锚点给出 (score, scale, x, y)；失败返回 None

        ⚠️ 偏移量必须用**头部高度**当尺子，不能用部件自身高度——
        早期用部件高度算，导致嘴贴到眼睛上、后发顶部跑到画布外（看着像顶帽子）。
        """
        h, w = rgba.shape[:2]
        hh = None
        if "头" in comps and comps["头"]:
            hh = max(comps["头"], key=lambda c: c[3])[0].shape[0] * gscale
        if eye_x is None or hh is None:
            return None
        s = gscale
        head_top = eye_y - 0.55 * hh                       # 眼线约在头部 55% 处（按实测校）
        if name == "头":
            return (0.99, s, int(eye_x - w * s / 2), int(head_top))
        if name == "鼻子":
            return (0.99, s, int(eye_x - w * s / 2), int(eye_y + 0.10 * hh))
        if name == "嘴巴":
            return (0.99, s, int(eye_x - w * s / 2), int(eye_y + 0.24 * hh))
        if name == "后发":
            return (0.99, s, int(eye_x - w * s / 2), int(head_top - 0.03 * hh))
        if name == "头发":
            # 前发（刘海）自相似性极强（137k px 的大片头发），子块投票会收敛到错误但自洽的偏移
            # （实测匹配到 (0,4) 这种位置，结果整块头发偏右偏下）→ 强制按头部锚点定位
            return (0.99, s, int(eye_x - w * s / 2), int(head_top - 0.05 * hh))
        if name == "裙子" and anchors.get("body"):
            bx, by, bw, bh = anchors["body"]
            return (0.99, s, int(bx + bw / 2 - w * s / 2), int(by + bh * 0.88))
        if name == "左刘海" and anchors.get("bang_r") and anchors.get("body"):
            rx, ry, rw = anchors["bang_r"]
            bx, by, bw, bh = anchors["body"]
            cx = bx + bw / 2
            return (0.99, s, int(2 * cx - (rx + rw)), int(ry))      # 关于角色中线镜像
        return None

    for name, cs in comps.items():
        for i, (rgba, x, y, area) in enumerate(cs):
            sc, s, mx, my, _, _ = placed[(name, i)]
            if sc >= 0.50 and name not in FORCE_STRUCT:
                continue
            sp = struct_place(name, rgba)
            if sp:
                placed[(name, i)] = (sp[0], sp[1], sp[2], sp[3], rgba, area)
                print(f"  结构锚点定位 {name}#{i} → ({sp[2]},{sp[3]}) s={sp[1]:.2f}")

    # ---- 组装：输出坐标 = 参考坐标 / gscale（保持部件原生分辨率）----
    OW = int(round(ref.width / gscale))
    OH = int(round(ref.height / gscale))
    print(f"\n输出画布: {OW}x{OH}")

    def out_layers():
        """按 ORDER 生成每层的输出图（多块合并到同层）"""
        canvas = {}
        for name in ORDER:
            src_names = [s for s in SRC_ALIAS.get(name, [name]) if s in comps]
            if not src_names:
                canvas[name] = None
                continue
            img = np.zeros((OH, OW, 4), np.uint8)
            for sn in src_names:
                for i, (rgba, x, y, area) in enumerate(comps.get(sn, [])):
                    sc, s, mx, my, _, _ = placed[(sn, i)]
                    if sc < 0.50:            # 低置信度：跳过（宁可缺，不错位）
                        print(f"  ⚠️ 跳过低置信 {sn}#{i} score={sc:.3f}")
                        continue
                    h, w = rgba.shape[:2]
                    # 部件原生尺寸贴到 输出坐标 = 参考坐标/s
                    # 用该部件自己的 s 换算（与它的匹配尺度一致，不能用全局比例硬套）
                    ss = s if s else gscale
                    ox = int(round(mx / ss))
                    oy = int(round(my / ss))
                    x0, y0 = max(0, ox), max(0, oy)
                    x1, y1 = min(OW, ox + w), min(OH, oy + h)
                    if x1 <= x0 or y1 <= y0:
                        continue
                    sub = rgba[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
                    dst = img[y0:y1, x0:x1]
                    al = sub[..., 3:4] / 255.0
                    dst[..., :3] = (sub[..., :3] * al + dst[..., :3] * (1 - al)).astype(np.uint8)
                    dst[..., 3] = np.maximum(dst[..., 3], sub[..., 3])
            canvas[name] = img
        return canvas

    out = out_layers()

    # 写 PSD（自下而上 append）
    new = PSDImage.new(mode="rgb", size=(OW, OH), color=(255, 255, 255))
    # ⚠️ psd-tools 的图层迭代是**自下而上**的：`create_pixel_layer` 先 append 的落在**底层**。
    # 所以按 ORDER（自下而上）顺序 append 才是对的；用 reversed(ORDER) 会把后发写到最上层，
    # 打开时整张脸被后发盖住（这正是"看着不对"的原因——预览对、PSD 错）。
    order_bottom_up = list(ORDER)
    for name in order_bottom_up:
        img = out.get(name)
        if img is None or img[..., 3].max() == 0:
            print(f"  （{name} 空，跳过）")
            continue
        pil = Image.fromarray(img, "RGBA")
        en = {"后发": "hair_back", "腿": "legs", "裙子": "skirt", "身体": "body", "手": "arms",
              "头": "head", "嘴巴": "mouth", "鼻子": "nose", "腮红": "blush", "眼睛": "eyes",
              "高光": "highlight", "上眼敛": "eyelid_upper", "左刘海": "hair_side_l",
              "右刘海": "hair_side_r", "头发": "hair_front"}.get(name, name)
        new.create_pixel_layer(pil, name=en)
    new.save(OUT_PSD)
    print("→", OUT_PSD, os.path.getsize(OUT_PSD) // 1024, "KB")

    # 预览：左参考 / 右组装
    comp = np.zeros((OH, OW, 4), np.uint8)
    for name in ORDER:              # ORDER 自下而上 → 正序叠
        img = out.get(name)
        if img is None:
            continue
        al = img[..., 3:4] / 255.0
        comp[..., :3] = (img[..., :3] * al + comp[..., :3] * (1 - al)).astype(np.uint8)
        comp[..., 3] = np.maximum(comp[..., 3], img[..., 3])
    a = Image.fromarray(comp, "RGBA")
    b = Image.new("RGB", a.size, (240, 238, 243)); b.paste(a, (0, 0), a)
    b = b.resize((a.width * 700 // max(a.width, 1), a.height * 700 // max(a.width, 1))) if a.width > 700 else b
    rr = ref.copy(); rr.thumbnail((b.width, b.height))
    cv = Image.new("RGB", (rr.width + b.width + 30, max(rr.height, b.height) + 24), (240, 238, 243))
    cv.paste(rr, (10, 20)); cv.paste(b, (rr.width + 20, 20))
    cv.save(OUT_PREVIEW)
    print("预览 →", OUT_PREVIEW)


if __name__ == "__main__":
    main()
