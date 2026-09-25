# -*- coding: utf-8 -*-
r"""gen_to_asset.py — 把生成式出图转成桌宠可用的透明素材
=========================================================================

**为什么需要这一步**：ImageGen 请求了 `background="transparent"`，但实际返回的是
**RGB 无 alpha** 的图——而且把"透明"画成了**棋盘格图案**（浅灰/白交替的真实像素）。
所以必须自己抠。

抠图判据（比"按亮度阈值"稳，也比通用抠图快）
---------------------------------------------
  ① **中性且亮** = `min(R,G,B) > 190` 且 `max-min <= 12`
     棋盘格是纯中性灰（255 / 204），而立绘风格配色的物体**都有色相**
     （藏青 #4E5684 通道差 54、鲸鱼蓝 #566CC2 差 108）→ 一判就分开。
  ② **从图像边界做连通传播**（扫描线洪水），只有与边界连通的浅色才算背景。
     这一条是不可省的：物体内部可能有纯白高光（贝雷帽的骨白纽扣就是），
     只按颜色阈值会把它们一起抠掉，变成"透明窟窿"。
  ③ 边缘不做羽化：本体是**深色描边**，抗锯齿的过渡像素偏暗 → 判为非背景，
     自然保留描边。羽化反而会把描边虚掉。

用法：
  python tools/gen_to_asset.py <生成图.png> <输出.png> --w 60 --keep-aspect
"""

import argparse
import os
import sys

from PIL import Image


# ---------------------------------------------------------------------------
# 背景掩版（扫描线洪水，纯 PIL/标准库，无 numpy 依赖）
# ---------------------------------------------------------------------------

def background_mask(im, light_min=190, neutral_max=12):
    """返回 bytearray，1 = 背景（与边界连通的"中性且亮"区域）。"""
    w, h = im.size
    data = list(im.getdata())

    def is_cand(p):
        r, g, b = p[0], p[1], p[2]
        return min(r, g, b) > light_min and (max(r, g, b) - min(r, g, b)) <= neutral_max

    cand = bytearray(1 if is_cand(p) else 0 for p in data)
    visited = bytearray(w * h)
    stack = []

    # 种子：四条边上所有候选像素
    for x in range(w):
        for y in (0, h - 1):
            i = y * w + x
            if cand[i] and not visited[i]:
                stack.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            i = y * w + x
            if cand[i] and not visited[i]:
                stack.append((x, y))

    # 扫描线洪水：一次处理一整行游程，比逐像素 BFS 快一两个数量级
    while stack:
        sx, sy = stack.pop()
        row = sy * w
        if visited[row + sx] or not cand[row + sx]:
            continue
        x0 = sx
        while x0 > 0 and cand[row + x0 - 1] and not visited[row + x0 - 1]:
            x0 -= 1
        x1 = sx
        while x1 < w - 1 and cand[row + x1 + 1] and not visited[row + x1 + 1]:
            x1 += 1
        for x in range(x0, x1 + 1):
            visited[row + x] = 1
        for ny in (sy - 1, sy + 1):
            if not (0 <= ny < h):
                continue
            nrow = ny * w
            x = x0
            while x <= x1:
                if cand[nrow + x] and not visited[nrow + x]:
                    stack.append((x, ny))
                    while x <= x1 and cand[nrow + x]:
                        x += 1
                x += 1
    return visited


def cutout(src, dst, target_w=None, pad_ratio=0.02):
    im = Image.open(src).convert("RGB")
    w, h = im.size
    bg = background_mask(im)

    rgba = Image.new("RGBA", (w, h))
    rgba.putdata([
        (p[0], p[1], p[2], 0 if bg[i] else 255)
        for i, p in enumerate(im.getdata())
    ])
    bbox = rgba.getchannel("A").getbbox()
    if not bbox:
        print("  !! 抠完是空的 —— 判据可能不适用")
        return None
    x0, y0, x1, y1 = bbox
    pad = int(max(x1 - x0, y1 - y0) * pad_ratio)
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(w, x1 + pad); y1 = min(h, y1 + pad)
    out = rgba.crop((x0, y0, x1, y1))

    if target_w:
        out = out.resize((target_w, max(1, round(target_w * out.height / out.width))),
                         Image.LANCZOS)
    out.save(dst)
    ar = out.width / out.height
    print(f"  {os.path.basename(dst):24s} {out.width}x{out.height}  "
          f"宽高比 {ar:.3f}  实体占比 {100 * (x1 - x0) * (y1 - y0) / (w * h):.0f}%")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--w", type=int, default=None, help="输出宽度（省略=保持抠图原始分辨率）")
    a = ap.parse_args()
    cutout(a.src, a.dst, a.w)
