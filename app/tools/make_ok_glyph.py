# -*- coding: utf-8 -*-
r"""从参考截图里提取「OK」字形，输出带透明通道的 assets/ok_glyph.png。

参考图 = 用户提供的示例（橙色素描：圆环头 + 交叉 X 身，白底）。
桌宠窗口是分层窗口（ULW_ALPHA），所以字形必须是真透明底，不能带白底。

算法：
  1. 只保留最大的两个连通域（圆环 + 身体），丢掉左上角的杂点；
  2. alpha 用「与白色的混合比例」反解 —— 纯色字形抗锯齿边缘是
     橙↔白的线性混合，取通道跨度最大的绿色通道反解最稳；
  3. 前景统一涂成参考图的代表橙（原图边缘有轻微压缩噪点，统一色更干净）；
  4. 裁到字形外接框 + 少量留白，长边归一化到 GLYPH_H。

用法：
  python tools/make_ok_glyph.py [参考图路径] [--out 输出png]
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
# 参考截图（生成时的本机路径；开源用户无需运行本工具——assets/ 已含成品字形。
# 如需重建：把参考截图放到 ~/Pictures/Screenshots/ 下同名文件，或改此常量）
DEFAULT_REF = os.path.expanduser(r"~\Pictures\Screenshots\IMG_1110_20260910.PNG")
GLYPH_H = 256                      # 输出高度（主文件再按需缩放缓存）
PAD_RATIO = 0.02                   # 四周留白占长边比例


def _orange_mask(rgb):
    """橙色前景掩码：R 明显高于 B 且足够亮（避开截图里的深色杂点）。"""
    r = rgb[:, :, 0].astype(int)
    g = rgb[:, :, 1].astype(int)
    b = rgb[:, :, 2].astype(int)
    return (r > 150) & ((r - b) > 60)


def _keep_main_components(mask, keep=2):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    order = sorted(range(1, n + 1), key=lambda i: -sizes[i - 1])[:keep]
    return np.isin(lab, order)


def _dominant_orange(rgb, mask):
    """取前景像素中位数作为代表橙（抗锯齿边缘会被中位数排除掉）。"""
    px = rgb[mask]
    return tuple(int(v) for v in np.median(px, axis=0))


def build(ref_path, out_path):
    im = Image.open(ref_path).convert("RGB")
    rgb = np.asarray(im)
    core = _keep_main_components(_orange_mask(rgb), keep=2)
    if core.sum() < 500:
        raise SystemExit(f"[ok-glyph] 参考图里没找到足够大的橙色字形：{ref_path}")

    orange = _dominant_orange(rgb, core)
    ys, xs = np.nonzero(core)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()

    # 在略大的窗口里反解 alpha，保住边缘 1-2px 的抗锯齿
    m = 3
    y0, x0 = max(0, y0 - m), max(0, x0 - m)
    y1, x1 = min(rgb.shape[0] - 1, y1 + m), min(rgb.shape[1] - 1, x1 + m)
    sub = rgb[y0:y1 + 1, x0:x1 + 1].astype(float)

    # pixel = a*orange + (1-a)*white  →  a = (255 - ch) / (255 - orange_ch)
    best = None
    for ch in range(3):
        span = 255.0 - orange[ch]
        if span < 40:                     # 该通道跨度太小，反解会放大噪声
            continue
        a = (255.0 - sub[:, :, ch]) / span
        best = a if best is None else np.maximum(best, a)
    if best is None:
        best = np.zeros(sub.shape[:2])
    alpha = np.clip(best, 0.0, 1.0)

    # 掩码外（截图噪点区）强制透明，避免把 JPEG/PNG 压缩杂点带进来
    keep = ndimage.binary_dilation(core[y0:y1 + 1, x0:x1 + 1], iterations=2)
    alpha[~keep] = 0.0

    rgba = np.zeros((alpha.shape[0], alpha.shape[1], 4), dtype=np.uint8)
    rgba[:, :, 0] = orange[0]
    rgba[:, :, 1] = orange[1]
    rgba[:, :, 2] = orange[2]
    rgba[:, :, 3] = (alpha * 255.0).round().astype(np.uint8)

    img = Image.fromarray(rgba, "RGBA")
    if img.height > img.width:
        new_h = GLYPH_H
        new_w = max(1, int(round(img.width * GLYPH_H / img.height)))
    else:
        new_w = GLYPH_H
        new_h = max(1, int(round(img.height * GLYPH_H / img.width)))
    img = img.resize((new_w, new_h), Image.LANCZOS)

    # 四周补留白，得到统一的方形画布之外仍按比例，便于主文件等比摆放
    pad = int(round(max(new_w, new_h) * PAD_RATIO))
    canvas = Image.new("RGBA", (new_w + pad * 2, new_h + pad * 2), (0, 0, 0, 0))
    canvas.paste(img, (pad, pad))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    canvas.save(out_path)
    print(f"[ok-glyph] 代表橙 #{orange[0]:02X}{orange[1]:02X}{orange[2]:02X}"
          f"  字形源框 {x1 - x0 + 1}x{y1 - y0 + 1}"
          f"  输出 {canvas.width}x{canvas.height}  ->  {out_path}")
    return orange


def main():
    ap = argparse.ArgumentParser(description="提取 OK 字形为透明 PNG")
    ap.add_argument("ref", nargs="?", default=DEFAULT_REF, help="参考截图路径")
    ap.add_argument("--out", default=os.path.join(ASSETS, "ok_glyph.png"))
    a = ap.parse_args()
    if not os.path.isfile(a.ref):
        sys.stderr.write(f"[ok-glyph] 找不到参考图：{a.ref}\n")
        return 1
    build(a.ref, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
