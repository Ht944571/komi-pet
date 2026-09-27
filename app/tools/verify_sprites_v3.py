# -*- coding: utf-8 -*-
"""v3 立绘归一化核验（**每次改归一化/bias 后都要重跑**）。

为什么不量立绘文件、而量真机渲染：
  第一轮只量了文件脸宽（620/619/…，看起来完美），落到屏幕上却是 115~127px（极差 9.4%）
  —— 各源图「脸框」纳入的下巴/脖子范围不同，**文件里的数不能代表屏幕上的观感**。
  本工具走真实绘制链路（WhalePet.draw → 离屏 Surface → 反预乘读回），量的是玩家看到的东西。

⚠️ 两个坑：
  1. **必须裁到立绘区再量**。气泡底色 `C_BUBBLE = #FDFBF6` 正好落在项目肤色判据里，
     直接对整窗跑 `_face_region` 会测到气泡（表现为 8 个态脸宽完全相同、脸心全在 y≈67）。
  2. **同一个进程里不要连开两个 WhalePet**：第一个 close() 后 GDI+ 字体/Graphics 已释放，
     第二个实例绘制文本时会 `OSError: access violation`。一次遍历同时完成测量与出图。

用法：
    python tools/verify_sprites_v3.py
"""
import os
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, APP)

STATES = ["idle", "happy", "pout", "shy", "blush", "stone", "joy", "surprise"]
OUT = os.path.join(os.path.dirname(APP), "docs", "face_calib_v3", "v3_render_verify.png")
TOL_PCT = 5.0        # 渲染脸宽极差容忍度


def skin_np(rgba):
    """与 calib_face.skin_mask 同判据（numpy 版，快）。"""
    a = np.array(rgba)
    rgb = a[..., :3].astype(int)
    al = a[..., 3]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (al >= 128) & (r > 228) & (g > 195) & (b > 185) \
        & ((r - b) >= 6) & ((r - b) <= 60) & ((r - g) <= 40)


def main():
    import wb_whale_win as W
    import calib_face as CF
    from _shotutil import surf_to_image

    app = W.WhalePet(run_seconds=None)
    lay = app._layout()
    top = lay["bubble_h"]                         # 立绘区上沿（避开气泡！）
    print(f"窗口 {lay['W']}x{lay['H']}   立绘区上沿 y={top}   scale={lay['sc']}")
    print(f"{'state':10}{'渲染脸宽':>9}{'脸心x':>7}{'脸心y':>7}{'内容底边':>9}{'内容高':>8}")

    shots, widths = [], []
    try:
        for st in STATES:
            app._morph_state = st
            app._morph_until = time.time() + 9999
            app._blinker.blink_t0 = 0.0                # 固定睁眼基准，排除眨眼相位
            app._blinker.blink_total = 0.0
            app.draw()
            app.draw()
            im = surf_to_image(app.surf).convert("RGBA")
            sub = im.crop((0, top, lay["W"], lay["H"]))
            fb = CF._face_region(sub, skin_np(sub).tolist())
            a = np.array(sub.getchannel("A"))
            ys, _ = np.nonzero(a > 16)
            fw = fb[2] - fb[0] + 1
            widths.append(fw)
            print(f"{st:10}{fw:9}{int((fb[0]+fb[2])/2):7}"
                  f"{int((fb[1]+fb[3])/2)+top:7}{int(ys.max())+top:9}"
                  f"{int(ys.max()-ys.min()+1):8}")
            t = sub.copy()
            dd = ImageDraw.Draw(t)
            dd.rectangle(fb, outline=(255, 0, 0), width=2)
            shots.append((st, t, fw))
    finally:
        try:
            app.close()
        except Exception:
            pass

    lo, hi = min(widths), max(widths)
    spread, pct = hi - lo, 100 * (hi - lo) / hi
    print(f"\n渲染脸宽 min={lo} max={hi} 极差={spread}px ({pct:.1f}%)")
    print("判定：", "✅ 通过" if pct <= TOL_PCT else
          f"❌ 超过 {TOL_PCT}% → 回填 build_pet_v3.BIAS_FIX 后重建 + 重跑 remap_eye_config_v3")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cw, ch = shots[0][1].size
    cols = 4
    rr = (len(shots) + cols - 1) // cols
    cv = Image.new("RGB", (cols * (cw + 12) + 12, rr * (ch + 34) + 26), (236, 232, 240))
    d = ImageDraw.Draw(cv)
    d.text((8, 6), f"渲染脸宽 min={lo} max={hi} 极差={spread}px ({pct:.1f}%)   红框=测到的脸框",
           fill=(30, 20, 40))
    for j, (st, im, fw) in enumerate(shots):
        x = 12 + (j % cols) * (cw + 12)
        y = 26 + (j // cols) * (ch + 34)
        cv.paste(im, (x, y), im)
        d.text((x + 3, y + ch + 4), f"pet_{st}  脸宽 {fw}", fill=(40, 20, 50))
    cv.save(OUT)
    print("核验图:", OUT)
    return 0 if pct <= TOL_PCT else 1


if __name__ == "__main__":
    sys.exit(main())
