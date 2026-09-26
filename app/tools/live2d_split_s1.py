# -*- coding: utf-8 -*-
"""自动拆层 Stage1：把前视图按颜色分类，出分区检查图。

分类调色板取自参考图实测（见 拆层手册.md §2）。
描边（近黑）单独一类，之后按"邻近区域归属"分给各部件。
"""
import os
import numpy as np
from PIL import Image
from collections import deque

REF = r"D:\workbuddy\古见同学桌宠\live2d\reference\front.png"
OUT = r"D:\workbuddy\古见同学桌宠\live2d\_work"
os.makedirs(OUT, exist_ok=True)
CANVAS = 1024

# 调色板 v2 —— 按 canvas 实测坐标采样（量化采样偏差太大，脸被分去 white）
PAL = {
    "hair":   ((0x56, 0x43, 0x59), (0x41, 0x2B, 0x42), (0x90, 0x78, 0x90), (0x30, 0x18, 0x30)),
    "skin":   ((0xFE, 0xF2, 0xE9), (0xFA, 0xF2, 0xEA), (0xFD, 0xF3, 0xEA)),   # 脸 #FEF2E9
    "blush":  ((0xFB, 0xE0, 0xE0),),                                          # 腮红 #FBE0E0
    "cloth":  ((0x48, 0x48, 0x78), (0x3E, 0x33, 0x53)),                       # 外套 + 眼珠 #3E3353
    "wine":   ((0x48, 0x30, 0x30), (0x4B, 0x30, 0x3E)),                       # 裙纹 + 领结
    "white":  ((0xF4, 0xF4, 0xF2), (0xEF, 0xEF, 0xEA)),                       # 衬衫 + 眼白 #EFEFEA
    "dark":   ((0x14, 0x00, 0x02), (0x04, 0x04, 0x04)),                       # 裙深条 / 袜 / 鞋
    "line":   ((0x10, 0x00, 0x06), (0x00, 0x00, 0x00)),                       # 描边 #100006
}
KEYS = list(PAL)
VECS = np.array([c for k in KEYS for c in PAL[k]], dtype=np.int16)
IDX = np.array([i for i, k in enumerate(KEYS) for c in PAL[k]])


def classify(a):
    """每个不透明像素 → 最近的调色板类。返回 class 图（-1=透明）。

    ⚠️ 必须 int32：三通道平方和最大 ~195075，int16（上限 32767）会溢出，
    距离表变成垃圾 → 分类整体错位（实测：脸被分到 wine、眼珠被分到 white）。
    """
    h, w = a.shape[:2]
    rgb = a[..., :3].astype(np.int32).reshape(-1, 3)
    al = a[..., 3].reshape(-1)
    d = ((rgb[:, None, :] - VECS[None, :, :].astype(np.int32)) ** 2).sum(axis=2)
    nearest = IDX[d.argmin(axis=1)]
    cls = np.full(h * w, -1, np.int16)
    op = al > 128
    cls[op] = nearest[op]
    return cls.reshape(h, w)


def main():
    im = Image.open(r"D:\workbuddy\古见同学桌宠\live2d\reference\front.png").convert("RGBA")
    s = (CANVAS - 24) / im.height
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    cv.paste(im, ((CANVAS - im.width) // 2, CANVAS - 12 - im.height), im)
    a = np.array(cv)
    cls = classify(a)

    # 检查图：每类一个颜色
    vis = {
        -1: (255, 255, 255), "hair": (120, 60, 120), "skin": (250, 240, 200),
        "cloth": (60, 70, 160), "wine": (170, 40, 60), "white": (230, 230, 230),
        "dark": (30, 25, 20), "line": (0, 0, 0), "blush": (255, 170, 170),
    }
    out = np.zeros(a.shape[:2] + (3,), np.uint8)
    for i, k in enumerate(KEYS):
        out[cls == i] = vis[k]
    out[cls == -1] = (255, 255, 255)
    Image.fromarray(out).save(os.path.join(OUT, "s1_classify.png"))

    np.save(os.path.join(OUT, "s1_cls.npy"), cls)
    Image.fromarray(a).save(os.path.join(OUT, "s1_canvas.png"))
    print("画布上角色 bbox:")
    ys, xs = np.nonzero(a[..., 3] > 8)
    print("  x", xs.min(), "~", xs.max(), "  y", ys.min(), "~", ys.max())
    for i, k in enumerate(KEYS):
        print(f"  {k:6} {int((cls==i).sum()):7d} px")


if __name__ == "__main__":
    main()
