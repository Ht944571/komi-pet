# -*- coding: utf-8 -*-
"""为「分层差分眨眼」造 ImageGen 输入图：把 v3 立绘摆到 1024² 白底上。

为什么要摆到 1024² 再生成：
  ① ImageGen 支持的尺寸是 1024²/1024x1536/1536x1024；用**正方形**能与我们自己的
     画布建立 1:1 的已知映射（缩放 s_fit + 偏移 ox,oy），生成结果可以精确摆回原坐标；
  ② 白底而不是透明：模型对白底的表现更稳，回来后用同一套洪泛去底。

映射关系（build_blink_patches.py 按它反算）：
    输出图坐标 --(-ox,-oy)/s_fit--> v3 画布坐标

用法：
    python tools/gen_blink_variants.py            # 造全部需要的态
    python tools/gen_blink_variants.py idle       # 只造一个
"""
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
V3 = os.path.join(APP, "assets", "pet_v3")
OUT = os.path.join(APP, "assets", "_gen3", "blink_in")
MAP = os.path.join(APP, "assets", "_gen3", "blink_in", "_map.json")
SIZE = 1024

# joy 跳过：它的立绘眼睛本来就是闭的，眨眼无意义
STATES = ["idle", "happy", "pout", "shy", "blush", "stone", "surprise"]


def main():
    only = sys.argv[1:] or STATES
    os.makedirs(OUT, exist_ok=True)
    mapping = json.load(open(MAP, encoding="utf-8")) if os.path.isfile(MAP) else {}

    for st in only:
        src = os.path.join(V3, f"pet_{st}.png")
        if not os.path.isfile(src):
            print(f"  [跳过] 缺图 {src}")
            continue
        im = Image.open(src).convert("RGBA")
        W, H = im.size
        s = min(SIZE * 0.94 / W, SIZE * 0.94 / H)      # 留 3% 边距
        nw, nh = max(1, round(W * s)), max(1, round(H * s))
        small = im.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
        ox, oy = (SIZE - nw) // 2, (SIZE - nh) // 2
        canvas.paste(small, (ox, oy), small)
        p = os.path.join(OUT, f"pet_{st}.png")
        canvas.save(p)
        mapping[st] = {"from": [W, H], "s_fit": s, "offset": [ox, oy],
                       "in_size": [SIZE, SIZE]}
        print(f"  {st:10} 画布 {W}x{H} × {s:.4f} → 贴 ({ox},{oy})   {p}")

    json.dump(mapping, open(MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n映射表: {MAP}")
    print(f"共 {len(only)} 张输入图 → {OUT}")


if __name__ == "__main__":
    main()
