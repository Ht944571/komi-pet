# -*- coding: utf-8 -*-
r"""生成「写字本子」素材（真透明底，供分层窗口 ULW_ALPHA 直接贴）。

参考视频里的本子：暖棕色软面封面 + 米白内页。视频本身是 AI 生成、**画面带水印**
（"AI生成"角标），所以它只当**动作参考**，素材一律自己画（项目既定红线）。

输出两张：
  assets/notebook/nb_closed.png   写字态：合着的本子（俯视 3/4），运行时再按角度旋转
  assets/notebook/nb_open.png     展示态：两页摊开（含中缝、纸沓、投影）

为什么不用 GDI+ 现画：项目踩过「椭圆/三角拼出来的纯色剪影像剪贴画」的坑
（见已删除的配件层）。这里的做法是**离屏渲染 + 渐变 + 高斯投影**，
运行时只做缩放/旋转/贴图，既好看又不吃每帧性能。

用法：
    python tools/make_notebook.py            # 重新生成（默认 3× 超采样后缩放）
    python tools/make_notebook.py --preview  # 额外拼一张预览图便于目视核对
"""

import argparse
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
OUT_DIR = os.path.join(ASSETS, "notebook")

S = 3                      # 超采样倍数（先画大再缩，边缘才干净）
W_CLOSED, H_CLOSED = 300, 380      # 合上：竖版小本子
W_OPEN, H_OPEN = 340, 250          # 翻开：横版跨页

# 配色（暖棕软面 + 米白内页，呼应参考视频）
COVER_L = (196, 150, 106)      # 封面亮部
COVER_D = (150, 106,  68)      # 封面暗部
SPINE_D = (120,  84,  52)      # 书脊
PAGE_L = (253, 250, 242)       # 纸亮部
PAGE_D = (231, 221, 202)       # 纸暗部
EDGE = (238, 230, 214)         # 纸沓切边
LINE = (150, 138, 122)         # 中缝阴影


def _vgrad(w, h, top, bot):
    """竖直线性渐变（返回 float 数组），top/bot 是 RGB。"""
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    top = np.array(top, dtype=float)[None, None, :]
    bot = np.array(bot, dtype=float)[None, None, :]
    g = top * (1 - t) + bot * t
    return np.repeat(g, w, axis=1)


def _round_mask(size, radius, box=None):
    """圆角矩形遮罩（0/255），box=(x0,y0,x1,y1)，默认整张。"""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle(box or (0, 0, size[0] - 1, size[1] - 1),
                        radius=radius, fill=255)
    return np.asarray(m, dtype=float) / 255.0


def _shadow(size, box, radius, blur, alpha, offset=(0, 6)):
    """柔和投影：单独一层模糊后按 alpha 合成。"""
    layer = Image.new("L", size, 0)
    d = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box
    d.rounded_rectangle((x0 + offset[0], y0 + offset[1], x1 + offset[0], y1 + offset[1]),
                        radius=radius, fill=255)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(layer, dtype=float) / 255.0 * alpha


def _comp(size, layers):
    """layers: [(rgb 数组, alpha 数组)]，按顺序 over 合成成 RGBA Image。"""
    w, h = size
    out = np.zeros((h, w, 3), dtype=float)
    a_out = np.zeros((h, w), dtype=float)
    for rgb, a in layers:
        a = np.clip(a, 0, 1)
        out = rgb * a[..., None] + out * (1 - a[..., None])
        a_out = a + a_out * (1 - a)
    img = np.dstack([np.clip(out, 0, 255), np.clip(a_out * 255, 0, 255)])
    return Image.fromarray(img.astype(np.uint8), "RGBA")


def make_closed():
    """合着的本子（俯视 3/4）：封面 + 右侧/下侧露出的纸沓 + 书脊 + 投影。"""
    w, h = W_CLOSED * S, H_CLOSED * S
    pad = 22 * S
    cw, ch = w - 2 * pad, h - 2 * pad
    x0, y0 = pad, pad
    x1, y1 = x0 + cw, y0 + ch
    rad = int(ch * 0.055)

    layers = []
    # 投影
    layers.append(((60, 46, 34),
                   _shadow((w, h), (x0, y0, x1, y1), rad, blur=9 * S, alpha=0.30,
                           offset=(2 * S, 8 * S))))
    # 纸沓（右下露出一圈米白，读作"里面有纸"）
    pw = int(cw * 0.035)
    ph = int(ch * 0.030)
    layers.append((EDGE, _round_mask(
        (w, h), rad,
        (x0 + pw, y0 + ph, x1 + pw, y1 + ph))))
    # 封面（竖向渐变）
    cover = _vgrad(w, h, COVER_L, COVER_D)
    layers.append((cover, _round_mask((w, h), rad, (x0, y0, x1, y1))))
    # 书脊（左侧一道更深的窄条，只到圆角内）
    spine = np.zeros((h, w, 3), dtype=float)
    spine[:, :] = SPINE_D
    sm = _round_mask((w, h), int(rad * 0.7),
                     (x0, y0, x0 + int(cw * 0.085), y1))
    layers.append((spine, sm * 0.55))
    # 封面顶部一道高光（软面上沿受光）
    hl = np.zeros((h, w, 3), dtype=float)
    hl[:, :] = (232, 200, 164)
    hm = _round_mask((w, h), rad, (x0 + int(cw * 0.06), y0 + int(ch * 0.012),
                                   x1 - int(cw * 0.06), y0 + int(ch * 0.045)))
    layers.append((hl, hm * 0.45))
    return _comp((w, h), layers).resize((W_CLOSED, H_CLOSED), Image.LANCZOS)


def make_open():
    """翻开的跨页：左页 + 右页 + 中缝 + 纸沓切边 + 投影。"""
    w, h = W_OPEN * S, H_OPEN * S
    pad = 20 * S
    bw, bh = w - 2 * pad, h - 2 * pad
    x0, y0 = pad, pad
    x1, y1 = x0 + bw, y0 + bh
    rad = int(bh * 0.05)
    mid = x0 + bw // 2

    layers = []
    layers.append(((60, 46, 34),
                   _shadow((w, h), (x0, y0, x1, y1), rad, blur=10 * S, alpha=0.32,
                           offset=(0, 8 * S))))
    # 纸沓：整块米白底（比内页略深，形成切边）
    layers.append((EDGE, _round_mask((w, h), rad, (x0, y0, x1, y1))))
    # 左右内页（各自一道渐变，靠近中缝更暗 → 有"翻开的弧度"）
    for lx0, lx1, dark_right in ((x0, mid + int(bw * 0.01), True),
                                 (mid - int(bw * 0.01), x1, False)):
        page = _vgrad(w, h, PAGE_L, PAGE_D)
        m = _round_mask((w, h), rad, (lx0, y0 + int(bh * 0.02),
                                      lx1, y1 - int(bh * 0.012)))
        # 靠近中缝的一侧压暗（纸面弯曲的自阴影）
        # 注意：要铺到**整张画布宽度**再相乘，否则广播对不上（页面只占中间一段）
        seg = np.linspace(0.0, 1.0, max(1, lx1 - lx0))
        if not dark_right:
            seg = seg[::-1]
        shade = np.zeros((1, w, 1))
        shade[:, lx0:lx1, 0] = seg
        page = page * (1 - 0.16 * shade)
        layers.append((page, m))
    # 中缝：一条柔和暗带
    gutter = np.zeros((h, w, 3), dtype=float)
    gutter[:, :] = LINE
    gw = int(bw * 0.022)
    gm = Image.new("L", (w, h), 0)
    ImageDraw.Draw(gm).rectangle((mid - gw // 2, y0, mid + gw // 2, y1), fill=255)
    gm = np.asarray(gm.filter(ImageFilter.GaussianBlur(2.2 * S)), dtype=float) / 255.0
    layers.append((gutter, gm * 0.30))
    # 纸面受光：**必须模糊成光斑**。
    # 第一版用了硬边圆角矩形，结果读起来像纸上贴了个白补丁（不是高光）——踩过。
    hlw = np.zeros((h, w, 3), dtype=float)
    hlw[:, :] = (255, 253, 248)
    hm_img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(hm_img).ellipse(
        (x0 + int(bw * 0.06), y0 - int(bh * 0.10),
         x1 - int(bw * 0.06), y0 + int(bh * 0.55)), fill=255)
    hm = np.asarray(hm_img.filter(ImageFilter.GaussianBlur(bh * 0.10)),
                    dtype=float) / 255.0
    layers.append((hlw, hm * 0.42))
    return _comp((w, h), layers).resize((W_OPEN, H_OPEN), Image.LANCZOS)


def preview(closed, opened):
    """拼预览：白/深两种底各来一行，便于看边缘与投影。"""
    gap = 26
    W = closed.width + opened.width + gap * 3
    H = max(closed.height, opened.height) + gap * 2
    sheet = Image.new("RGB", (W, H * 2 + gap), (250, 248, 244))
    for row, bg in enumerate(((250, 248, 244), (58, 56, 62))):
        band = Image.new("RGB", (W, H), bg)
        band.paste(closed, (gap, gap), closed)
        band.paste(opened, (gap * 2 + closed.width, gap), opened)
        sheet.paste(band, (0, row * (H + gap)))
    return sheet


def main():
    ap = argparse.ArgumentParser(description="生成本子素材")
    ap.add_argument("--preview", action="store_true", help="额外拼一张预览图")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    closed = make_closed()
    opened = make_open()
    p1 = os.path.join(OUT_DIR, "nb_closed.png")
    p2 = os.path.join(OUT_DIR, "nb_open.png")
    closed.save(p1)
    opened.save(p2)
    print(f"  ✅ {p1}  {closed.size}  {os.path.getsize(p1)/1024:.1f} KB")
    print(f"  ✅ {p2}  {opened.size}  {os.path.getsize(p2)/1024:.1f} KB")
    if args.preview:
        out = os.path.join(os.path.dirname(os.path.dirname(HERE)), "_preview_本子.png")
        preview(closed, opened).save(out)
        print(f"  ✅ 预览 {out}")


if __name__ == "__main__":
    main()
