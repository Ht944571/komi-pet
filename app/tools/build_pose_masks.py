# -*- coding: utf-8 -*-
"""预计算各状态的"姿态掩码"（12×18 的 alpha 缩略），供运行时判断两张立绘差多少。

为什么需要：状态之间做**交叉溶解**只在两帧姿态相近时才好看（参考视频如此）。
这套 8 态 Q 版立绘姿态差异极大（比耶 / 瞪眼 / 嘟嘴，手臂位置完全不同），
硬做溶解 = 双重曝光 —— 用户反馈的"点击时出现多个重叠"就是这个。
运行时据此判断：差异小 → 溶解（好看）；差异大 → 硬切（不糊）。

产出：assets/pet_v3/_pose_masks.json（几 KB，纯数据；桌宠侧只用 stdlib json 读）
用法：python tools/build_pose_masks.py
"""
import json
import os

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
V3 = os.path.join(HERE, "assets", "pet_v3")   # tools/ 的上一级就是 app/
W, H = 32, 48


def main():
    out = {}
    for f in sorted(os.listdir(V3)):
        if not (f.startswith("pet_") and f.endswith(".png")):
            continue
        key = "q." + f[len("pet_"):-len(".png")]
        im = Image.open(os.path.join(V3, f)).convert("RGBA")
        # ⚠️ 必须带**明度**，不能只用 alpha 轮廓：
        # 实测 idle↔surprise 的轮廓差异是 0（同一姿态只是脸变了），
        # 但"双重曝光"恰恰主要来自脸的差异（平静半眯 vs 瞪眼张嘴）。
        # 做法：把 RGBA 合成到白底再取灰度。
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        merged = Image.alpha_composite(bg, im).convert("L").resize((W, H))
        out[key] = list(merged.getdata())
    dst = os.path.join(V3, "_pose_masks.json")
    with open(dst, "w", encoding="utf-8") as fp:
        json.dump({"w": W, "h": H, "states": out}, fp)
    print(f"→ {dst}  {len(out)} 个状态 {os.path.getsize(dst)} bytes")


if __name__ == "__main__":
    main()
