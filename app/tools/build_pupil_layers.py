# -*- coding: utf-8 -*-
"""从 v3 立绘中提取「瞳孔独立层」+ 生成「无眼珠底图」。

原理（这套立绘的眼睛结构 = 眼白 + 大颗眼珠 + 上方粗睫毛线）：
  · 眼珠 = 眼区内最大的深色连通块（含高光，高光镂空处正好露出底图的白色，自动保留高光）
  · 底图把眼珠区域填回"眼区浅色均值"（眼白）——眼珠挪开时露出眼白，符合真眼
  · 睫毛线在眼框上方，自然留在底图（运行时先画底图再画眼珠，眼珠抬进睫毛线时
    会被眨眼贴片盖住 —— 贝尔现象就靠这个顺序成立）

产出（assets/pet_v3/）：
  pupils/<state>.png      双眼眼珠（裁到公共 bbox，透明底）
  eyeless/<state>.png     去掉眼珠的底图
  pupils/_pupil_config.json   各态瞳孔图的画布锚点（绘制时 + 偏移）
用法：python tools/build_pupil_layers.py
"""
import json
import os
from collections import deque

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
V3 = os.path.join(APP, "assets", "pet_v3")
CFG = os.path.join(V3, "_eye_config.json")
OUT_P = os.path.join(V3, "pupils")
OUT_E = os.path.join(V3, "eyeless")
os.makedirs(OUT_P, exist_ok=True)
os.makedirs(OUT_E, exist_ok=True)

STATES = ["idle", "happy", "pout", "shy", "blush", "stone", "surprise"]   # joy 眼睛本就闭着
MARGIN = 4


def largest_dark_blob(region):
    """返回 region(RGBA np) 内的**眼珠**连通块 (mask, y0,y1,x0,x1)。

    判据（这套立绘眼睛的特点）：
      · 眼珠是**近椭圆的实心团**（fill = 面积/bbox面积 偏高，~0.6+）
      · 刘海/睫毛线是薄或枝状（fill 低），且**贴区域边缘**
    对每个团打分：fill × 面积 × (不贴边的权重)，取最优。
    """
    rgb = region[..., :3].astype(np.int32)
    dark = (rgb[..., 0] < 95) & (rgb[..., 1] < 95) & (rgb[..., 2] < 120)
    h, w = dark.shape
    lab = np.zeros((h, w), np.int32); cur = 0; blobs = []
    for sy in range(h):
        for sx in range(w):
            if dark[sy, sx] and lab[sy, sx] == 0:
                cur += 1
                qq = deque([(sx, sy)]); lab[sy, sx] = cur
                cnt = 0; x0 = x1 = sx; y0 = y1 = sy
                while qq:
                    x, y = qq.popleft(); cnt += 1
                    x0 = min(x0, x); x1 = max(x1, x); y0 = min(y0, y); y1 = max(y1, y)
                    for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                        if 0 <= nx < w and 0 <= ny < h and dark[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = cur; qq.append((nx, ny))
                bw, bh = x1 - x0 + 1, y1 - y0 + 1
                fill = cnt / (bw * bh)
                touch = (x0 <= 2) + (x1 >= w - 3) + (y0 <= 2) + (y1 >= h - 3)
                blobs.append({"cnt": cnt, "y0": y0, "y1": y1 + 1, "x0": x0, "x1": x1 + 1,
                              "fill": fill, "touch": touch,
                              "score": cnt * fill * (1.0 / (1 + touch))})
    if not blobs:
        return None
    b = max(blobs, key=lambda b: b["score"])
    if b["cnt"] < 60 or b["fill"] < 0.35:
        return None
    ex = 2
    y0, y1 = max(0, b["y0"] - ex), min(h, b["y1"] + ex)
    x0, x1 = max(0, b["x0"] - ex), min(w, b["x1"] + ex)
    return dark[y0:y1, x0:x1], y0, y1, x0, x1


def extract_eye(img, cfg_eye, W, H):
    """对一只眼：直接用**配置的眼框**当取区（它是手标的"眼区"），
    排除上缘 16%（粗睫毛线，留底图）→ 其余深色像素就是眼珠。
    不用连通域检测（框外自然不含刘海/眉，稳）。返回 (patch, patch_xy, 修改后的img np)。"""
    a = np.array(img)
    x0 = int((cfg_eye["cx"] - cfg_eye["w"] / 2) * W)
    x1 = int((cfg_eye["cx"] + cfg_eye["w"] / 2) * W)
    y0 = int((cfg_eye["cy"] - cfg_eye["h"] / 2) * H)
    y1 = int((cfg_eye["cy"] + cfg_eye["h"] / 2) * H)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    region = a[y0:y1, x0:x1]

    top_cut = max(2, int((y1 - y0) * 0.16))      # 睫毛线（上缘）留底图
    sub = region[top_cut:, :]
    rgb = sub[..., :3].astype(np.int32)
    dark = (rgb[..., 0] < 95) & (rgb[..., 1] < 95) & (rgb[..., 2] < 120)
    if int(dark.sum()) < 40:
        return None, None, a

    # 眼珠贴片：深色像素保留，其余透明（高光镂空 → 露出底图白色）
    pm = np.zeros(sub.shape, np.uint8)
    pm[..., :3] = sub[..., :3]
    pm[..., 3] = np.where(dark, 255, 0)

    # 底图填回：子区域浅色均值（眼白色），眼珠区向外扩 1px
    light = (sub[..., 0] > 200) & (sub[..., 1] > 200) & (sub[..., 2] > 200)
    fill = sub[light][:, :3].mean(axis=0) if light.any() else np.array([240, 238, 235])
    ys, xs = np.nonzero(dark)
    fy0, fy1 = max(0, ys.min() - 1), min(sub.shape[0], ys.max() + 2)
    fx0, fx1 = max(0, xs.min() - 1), min(sub.shape[1], xs.max() + 2)
    sub[fy0:fy1, fx0:fx1, :3] = fill.astype(np.uint8)
    sub[fy0:fy1, fx0:fx1, 3] = 255

    region[top_cut:, :] = sub
    a[y0:y1, x0:x1] = region
    return pm, (x0, y0 + top_cut), a


def main():
    cfg = json.load(open(CFG, encoding="utf-8"))["states"]
    out_cfg = {"version": 1, "states": {}}
    for st in STATES:
        im = Image.open(os.path.join(V3, f"pet_{st}.png")).convert("RGBA")
        W, H = im.size
        a = np.array(im)
        patches = []
        for key in ("left", "right"):
            e = cfg.get(st, {}).get("eyes", {}).get(key)
            if not e:
                continue
            pm, xy, a = extract_eye(Image.fromarray(a, "RGBA"), e, W, H)
            if pm is not None:
                patches.append((pm, xy))
        # 无眼珠底图
        Image.fromarray(a, "RGBA").save(os.path.join(OUT_E, f"pet_{st}.png"))
        # 双眼眼珠合成到公共 bbox
        if not patches:
            continue
        x0 = min(xy[0] for _, xy in patches) - MARGIN
        y0 = min(xy[1] for _, xy in patches) - MARGIN
        x1 = max(xy[0] + pm.shape[1] for pm, xy in patches) + MARGIN
        y1 = max(xy[1] + pm.shape[0] for pm, xy in patches) + MARGIN
        merged = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
        for pm, xy in patches:
            h, w = pm.shape[:2]
            dx, dy = xy[0] - x0, xy[1] - y0
            dst = merged[dy:dy + h, dx:dx + w]
            ov = pm[..., 3:4] / 255.0
            dst[..., :3] = (pm[..., :3] * ov + dst[..., :3] * (1 - ov)).astype(np.uint8)
            dst[..., 3] = np.maximum(dst[..., 3], pm[..., 3])
        pup_img = Image.fromarray(merged, "RGBA")
        pup_img.save(os.path.join(OUT_P, f"pet_{st}.png"))
        out_cfg["states"][st] = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
        # 镜像版（朝向 _f 是独立翻转文件）：底图与瞳孔都要出翻转版，锚点 x 也要翻
        eyeless = Image.fromarray(a, "RGBA")
        eyeless.transpose(Image.FLIP_LEFT_RIGHT).save(os.path.join(OUT_E, f"pet_{st}_f.png"))
        pup_img.transpose(Image.FLIP_LEFT_RIGHT).save(os.path.join(OUT_P, f"pet_{st}_f.png"))
        out_cfg["states"][st + "_f"] = {"x": W - (x0 + (x1 - x0)), "y": y0,
                                        "w": x1 - x0, "h": y1 - y0}
        print(f"  {st:9} 瞳孔 {merged.shape[1]}x{merged.shape[0]} @({x0},{y0})  底图已去眼珠（含镜像）")
    json.dump(out_cfg, open(os.path.join(OUT_P, "_pupil_config.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n→ pupils/ + eyeless/ 已生成，config →", os.path.join(OUT_P, "_pupil_config.json"))


if __name__ == "__main__":
    main()
