# -*- coding: utf-8 -*-
"""出「眨眼帧序列」对照图：把 eye_opening_ratio 钉在若干值上各渲染一帧。

用途：肉眼核对分层差分贴片的接缝、位置与闭合观感（含镜像朝向的翻转是否正确）。
比看单张图有效——眨眼是**时间序列**，问题（接缝、错位、盖不全）只在序列里露出来。

用法：
    python tools/shot_blink_frames.py                 # idle + stone + surprise
    python tools/shot_blink_frames.py idle happy      # 指定态
"""
import os
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, APP)

RATIOS = [1.0, 0.75, 0.5, 0.25, 0.0]


def main():
    states = sys.argv[1:] or ["idle", "shy", "stone", "surprise"]
    import wb_whale_win as W
    from _shotutil import surf_to_image

    app = W.WhalePet(run_seconds=None)
    shots = []
    try:
        print(f"贴片已加载: {sorted(getattr(app, '_blink_patches', {}).keys())}")
        for st in states:
            for facing in ("", "_f"):
                for r in RATIOS:
                    app._morph_state = st
                    app._morph_until = time.time() + 9999
                    app._current_facing = lambda now, f=facing: f
                    app._blinker.eye_opening_ratio = lambda now, rr=r: rr
                    app._blinker.blink_phase = lambda now: (
                        "idle" if r >= 1.0 else ("hold" if r <= 0.01 else "close"))
                    app.draw()
                    app.draw()
                    im = surf_to_image(app.surf).convert("RGBA")
                    shots.append((f"{st}{facing}@{r:g}", im))
    finally:
        try:
            app.close()
        except Exception:
            pass

    # 排成 每行一个态（5 个 ratio），两张朝向各一行
    cw, ch = shots[0][1].size
    cols = len(RATIOS)
    rows = len(shots) // cols
    cv = Image.new("RGB", (cols * (cw + 8) + 8, rows * (ch + 30) + 24), (236, 232, 240))
    d = ImageDraw.Draw(cv)
    d.text((8, 4), "眨眼帧序列：ratio 1.0(睁) → 0.0(闭)   上下两行分别为 正/镜像 朝向",
           fill=(30, 20, 40))
    for j, (lab, im) in enumerate(shots):
        x = 8 + (j % cols) * (cw + 8)
        y = 22 + (j // cols) * (ch + 30)
        # 棋盘底，便于看透明区
        for yy in range(0, im.height, 14):
            for xx in range(0, im.width, 14):
                if ((xx // 14) + (yy // 14)) % 2 == 0:
                    d.rectangle([x + xx, y + yy, x + xx + 13, y + yy + 13],
                                fill=(226, 222, 230))
        cv.paste(im, (x, y), im)
        d.text((x + 3, y + ch + 4), lab, fill=(40, 20, 50))
    out = os.path.join(os.path.dirname(APP), "docs", "blink_frames.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cv.save(out)
    print("→", out, cv.size)


if __name__ == "__main__":
    main()
