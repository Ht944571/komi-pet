# -*- coding: utf-8 -*-
r"""OK 态外观方案对比出图

把 `wb_whale_win.OK_STYLES` 里的每个候选方案各渲染一帧 —— 走真实 `draw()` 后读分层窗口
DIB 像素，因此所见即线上效果 —— 再纵向拼成一张对比图，用来敲定
「整个泡泡整体变成 OK 的样式」的最终形态。

方案差异全部集中在 `ok_spec()` 的几何比例上（图形大小/位置、是否保留文案与进度点、
椭圆是否收窄），不涉及任何额外资源。

用法：
  python tools/preview_ok_styles.py [--png 输出路径] [--scale 1.0]
"""

import argparse
import os
import sys
import time

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))                 # scripts/
sys.path.insert(0, HERE)                                  # tools/ 自身
from _shotutil import surf_to_image, label_font           # noqa: E402
import wb_whale_win as W                                  # noqa: E402

# 方案说明（给对比图当副标题，讲清每个方案的取舍）
BLURB = {
    "A": "图形主导：一颗大 OK 占满整颗气泡，无文字无进度点（最贴参考图）",
    "B": "图形主导 + 三颗进度点：保留「点三次」可发现性，无文字（当前默认）",
    "C": "图形 + 一行文案 + 进度点：信息最完整",
    "D": "紧凑徽章：椭圆按图形收窄，整颗泡泡就是一枚 OK 徽章",
}


def render(out_path, scale=1.0, full=False):
    app = W.WhalePet(run_seconds=0)
    app.scale = scale
    orig = W.ok_spec
    tiles = []
    try:
        if not app._ok_img:
            print(f"!! 未加载到 {os.path.join(W.ASSETS_DIR, 'ok_glyph.png')}")
            return 1
        for style in sorted(W.OK_STYLES):
            # 让绘制与字形预缩放都按这一方案走（ok_spec 是模块级函数，补丁即生效）
            W.ok_spec = (lambda s: (lambda lay, _s=s: orig(lay, _s)))(style)
            app._build_sprite_cache()
            app._bub_mode = W.BUBBLE_OK
            app._ok_clicks = 0
            app._ok_release_at = 0.0
            app._ok_pulse_t0 = 0.0
            app._ok_anim_t0, app._ok_anim_dur = 0.0, 0.0
            app._ok_anim_from = W.MOTION.OK_GLYPH_MIN_SCALE
            app.draw()
            img = surf_to_image(app.surf)
            lay = app._layout()
            box = (0, 0, lay["W"], lay["H"] if full else int(lay["bubble_h"] + 8))
            tiles.append((style, img.crop(box)))
    finally:
        W.ok_spec = orig
        try:
            app.close()
        except Exception:
            pass

    if not tiles:
        return 1

    f_t = label_font(26)
    f_s = label_font(17)
    pad, gap, bar = 16, 12, 62
    tw = max(t[1].width for t in tiles)
    th = max(t[1].height for t in tiles)
    Wt = tw + pad * 2
    Ht = (th + bar) * len(tiles) + gap * (len(tiles) - 1) + pad * 2

    sheet = Image.new("RGB", (Wt, Ht), (24, 26, 38))
    d = ImageDraw.Draw(sheet)
    y = pad
    for style, img in tiles:
        band = Image.new("RGB", (tw, th), (250, 250, 252))   # 浅底便于看白气泡
        band.paste(img, (0, 0), img)
        sheet.paste(band, (pad, y))
        ty = y + th + 8
        d.text((pad, ty), f"方案 {style}", font=f_t, fill=(255, 255, 255))
        d.text((pad + 105, ty + 8), BLURB.get(style, ""), font=f_s, fill=(170, 176, 196))
        y += th + bar + gap

    if full:
        d.text((pad, pad), "整只桌宠构图（含立绘）", font=f_s, fill=(140, 146, 168))

    sheet.save(out_path)
    print(f"对比图已保存：{out_path}  ({sheet.width}x{sheet.height})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--png", default=os.path.join(os.path.dirname(HERE),
                                                  "_ok_styles.png"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--full", action="store_true",
                    help="连立绘一起出图（看整只桌宠的构图）")
    args = ap.parse_args()
    return render(args.png, args.scale, args.full)


if __name__ == "__main__":
    sys.exit(main())
