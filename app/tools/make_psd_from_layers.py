# -*- coding: utf-8 -*-
"""把自动拆层的 13 张 PNG 组装成**分层 PSD**（Cubism Editor 可直接导入）。

不需要 Krita —— psd-tools 的 create_pixel_layer 支持从 PIL 图建层。
PSD 的层顺序是"底层在前"：所以按 后发 → … → 前发 的顺序 append。
产出：live2d/拆层_古见同学_v1.psd
"""
import json
import os
from PIL import Image
from psd_tools import PSDImage

ROOT = r"D:\workbuddy\古见同学桌宠\live2d"
LAY = os.path.join(ROOT, "_work", "layers")
PSD = os.path.join(ROOT, "拆层_古见同学_v1.psd")
CANVAS = 1024

# 自下而上（PSD 内部顺序）＝ 绘制顺序的最后 → 最前
ORDER = [
    ("16_后发",          "hair_back"),
    ("17_补块_被遮挡区",  "patch"),
    ("15_腿袜",          "legs"),
    ("14_裙摆",          "skirt"),
    ("12_身体制服",      "body"),
    ("10_脸_肤色底",     "face"),
    ("11_腮红",          "blush"),
    ("09_嘴",            "mouth"),
    ("06_眼_眼白",       "eye_white"),
    ("05_眼_瞳孔高光",   "eye_pupil"),
    ("04_眼_上睑",       "eye_lid_upper"),
    ("02_前发",          "hair_front"),
]


def main():
    psd = PSDImage.new(mode="rgb", size=(CANVAS, CANVAS), color=(255, 255, 255))
    mapping = {}
    missing = []
    for src, en in ORDER:
        p = os.path.join(LAY, src + ".png")
        if not os.path.isfile(p):
            missing.append(src)
            continue
        im = Image.open(p).convert("RGBA")
        # ⚠️ PSD 层名的主存储是 Pascal string（mac_roman 编码），**不支持中文**。
        # 所以用英文名（Cubism 不看名字），中英对照写在 layer_map.json。
        psd.create_pixel_layer(im, name=en)
        mapping[en] = src
    if missing:
        print("⚠️ 缺层:", missing)
    json.dump(mapping, open(os.path.join(ROOT, "_work", "psd_layer_map.json"), "w",
                            encoding="utf-8"), ensure_ascii=False, indent=1)
    psd.save(PSD)
    print("→", PSD, os.path.getsize(PSD) // 1024, "KB")

    # 回读校验：层数、名字、每层不透明像素
    back = PSDImage.open(PSD)
    print("\n回读校验（PSD 自上而下）:")
    ok = True
    for layer in back:
        n = layer.name
        w = h = 0
        try:
            im = layer.composite()
            if im is not None:
                a = im.getchannel("A") if im.mode in ("RGBA", "LA") else None
                if a is not None:
                    hist = a.histogram()
                    w = sum(hist[200:])
        except Exception as e:
            w = -1
        print(f"   {n:34} 不透明像素≈{w}")
        if w == 0:
            ok = False
    print("\n判定：", "✅ 每层都有内容" if ok else "⚠️ 有空层，需检查")


if __name__ == "__main__":
    main()
