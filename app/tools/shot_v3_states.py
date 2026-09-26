# -*- coding: utf-8 -*-
"""出「桌宠真机渲染图」：每个 v3 状态各截一张实际窗口画面，拼成总览图。

用途：验证「接线后的真实观感」——立绘尺寸、眼睑盖板位置、气泡与角色的相对关系，
都只能在真实 Surface 上出图才看得出来（自画像式验证，见 3D 那条线的教训：
截图会被别的 TOPMOST 窗盖住，所以直接取离屏 Surface）。

用法：
    python tools/shot_v3_states.py [--scale 1.0] [--out docs/pet_v3_states.png]
"""
import argparse
import os
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, APP)

STATES = ["idle", "happy", "pout", "shy", "blush", "stone", "joy", "surprise"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(APP), "docs", "pet_v3_states.png"))
    args = ap.parse_args()

    import wb_whale_win as W
    from _shotutil import surf_to_image

    app = W.WhalePet(run_seconds=None)
    shots = []
    try:
        # scale 用设置文件里的值（构造时就定了窗口尺寸）；这里只做记录
        args.scale = getattr(app, "scale", args.scale)
        for st in STATES:
            app._morph_state = st
            app._morph_until = time.time() + 9999
            app._react_until = 0.0
            app._blinker.blink_t0 = 0.0          # 不带眨眼相位，看"睁眼"基准
            app._blinker.blink_total = 0.0
            app.draw()
            app.draw()
            img = surf_to_image(app.surf).convert("RGBA").copy()
            shots.append((st, img))
            print(f"  {st:10} 窗口 {img.size}")
    finally:
        try:
            app.close()
        except Exception:
            pass

    CW = max(im.width for _, im in shots)
    CH = max(im.height for _, im in shots)
    cols = 4
    rows = (len(shots) + cols - 1) // cols
    pad = 14
    cv = Image.new("RGB", (cols * (CW + pad) + pad, rows * (CH + pad * 3) + pad),
                   (236, 232, 240))
    d = ImageDraw.Draw(cv)
    for j, (st, im) in enumerate(shots):
        x = pad + (j % cols) * (CW + pad)
        y = pad + (j // cols) * (CH + pad * 3)
        # 棋盘底 → 透明区可辨
        for yy in range(0, im.height, 12):
            for xx in range(0, im.width, 12):
                if ((xx // 12) + (yy // 12)) % 2 == 0:
                    d.rectangle([x + xx, y + yy, x + xx + 11, y + yy + 11],
                                fill=(226, 222, 230))
        cv.paste(im, (x, y), im)
        d.text((x + 4, y + CH + 4), f"pet_{st}", fill=(40, 20, 50))
        d.text((x + 4, y + CH + 18), f"{im.width}x{im.height}  scale={args.scale}",
               fill=(150, 110, 130))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    cv.save(args.out)
    print("→", args.out, cv.size)


if __name__ == "__main__":
    main()
