# -*- coding: utf-8 -*-
"""把 ImageGen 的无标签输出图归属到各立绘态，并改名为 <state>.png。

为什么需要它：ImageGen 用提示词命名输出文件、**且不含态名**，并行调用还会**同秒撞名互相覆盖**。
归属判据：把输出按映射反算回画布后，与各态基底比 —— **「眼区之外」的差异最小**的那个才是它的源态
（因为生成图只改眼睛，其余应逐像素接近；而不同姿态之间差异极大）。

用法：
    python tools/assign_blink_outputs.py            # 只看归属矩阵与结果
    python tools/assign_blink_outputs.py --apply    # 实际改名
"""
import glob
import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
V3 = os.path.join(APP, "assets", "pet_v3")
GEN = os.path.join(APP, "assets", "_gen3")
IN_DIR = os.path.join(GEN, "blink_in")
OUT_DIR = os.path.join(GEN, "blink_out")
MAP = os.path.join(IN_DIR, "_map.json")
CFG = os.path.join(V3, "_eye_config.json")
SIZE = 1024
ZONE_DX, ZONE_DY = 1.2, 2.0      # 眼区外扩（比贴片用的更宽，确保把整只眼排除掉）


def eye_zone(cfg_state, W, H):
    z = np.zeros((H, W), bool)
    for e in (cfg_state.get("eyes") or {}).values():
        x0 = int((e["cx"] - e["w"] / 2) * W); x1 = int((e["cx"] + e["w"] / 2) * W)
        y0 = int((e["cy"] - e["h"] / 2) * H); y1 = int((e["cy"] + e["h"] / 2) * H)
        dx = int((x1 - x0) * ZONE_DX); dy = int((y1 - y0) * ZONE_DY)
        z[max(0, y0 - dy):min(H, y1 + dy), max(0, x0 - dx):min(W, x1 + dx)] = True
    return z


def main():
    apply = "--apply" in sys.argv
    mapping = json.load(open(MAP, encoding="utf-8"))
    cfg = json.load(open(CFG, encoding="utf-8"))["states"]

    # 候选 = 所有还不是 <state>.png 的输出
    named = {f"{s}.png" for s in mapping}
    cands = [p for p in sorted(glob.glob(os.path.join(OUT_DIR, "*.png")))
             if os.path.basename(p) not in named]
    if not cands:
        print("没有待归属的输出图。")
        return

    # 预读各态基底 + 眼区遮罩
    bases = {}
    for st in mapping:
        p = os.path.join(V3, f"pet_{st}.png")
        if not os.path.isfile(p):
            continue
        im = Image.open(p).convert("RGBA")
        W, H = im.size
        bgw = Image.new("RGBA", (W, H), (255, 255, 255, 255))
        rgb = np.array(Image.alpha_composite(bgw, im).convert("RGB"), dtype=np.int16)
        bases[st] = (rgb, eye_zone(cfg.get(st) or {}, W, H), W, H)

    # 成本矩阵：眼区外的平均绝对差
    cost = {}
    for p in cands:
        gen = Image.open(p).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
        for st, (rgb, zone, W, H) in bases.items():
            m = mapping[st]
            s = m["s_fit"]; ox, oy = m["offset"]
            nw = max(1, round(W * s)); nh = max(1, round(H * s))
            back = gen.crop((ox, oy, ox + nw, oy + nh)).resize((W, H), Image.LANCZOS)
            d = np.abs(np.array(back, dtype=np.int16) - rgb).max(axis=2)
            cost[(p, st)] = float(d[~zone].mean())

    print("=== 眼区外平均差异（越小越可能是它的源态）===")
    names = sorted(bases)
    print(f"{'输出文件':>34}  " + "".join(f"{n[:7]:>9}" for n in names))
    for p in cands:
        row = "".join(f"{cost[(p, n)]:9.1f}" for n in names)
        print(f"{os.path.basename(p)[-16:]:>34}  {row}")

    # 贪心指派：每次取全局最小
    todo = {(p, n) for p in cands for n in names}
    assign, used_f, used_s = {}, set(), set()
    while todo:
        p, st = min(todo, key=lambda k: cost[k])
        todo = {k for k in todo if k[0] != p and k[1] != st}
        assign[st] = p
        used_f.add(p); used_s.add(st)
    missing = [n for n in names if n not in used_s]

    print("\n=== 归属结果 ===")
    for st, p in sorted(assign.items()):
        print(f"  {st:10} ← {os.path.basename(p)}   (眼区外差异 {cost[(p, st)]:.1f})")
    if missing:
        print(f"\n⚠️ 无输出图的态（需重新生成）：{missing}")

    if apply:
        for st, p in assign.items():
            dst = os.path.join(OUT_DIR, f"{st}.png")
            os.replace(p, dst)
            print(f"  rename → {os.path.basename(dst)}")
        print("\n完成。")


if __name__ == "__main__":
    main()
