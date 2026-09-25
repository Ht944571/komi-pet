# -*- coding: utf-8 -*-
r"""whale_retint.py — 鲸鱼玩偶生成素材的规范色板后处理（不重新出图）。

为什么：现役 cut_whale.png（生成式）三个问题造成 1× 下的"灰团感"——
  ① 主体饱和度只有规范色板的一半（#90A0B0/#607090 灰蓝，通道差 32-48；
     规范 body #566CC2 通道差 108）——与立绘制服蓝 #4E5684 同明度同灰度，糊在一起；
  ② 描边是暖棕 #603010，不是立绘线语言的 #2A2028 深暖紫黑（规范 §2 ①）；
  ③ 剪影一圈白色 halo（抠图边缘残留）。
ImageGen 通路不可用时，用像素级重映射直接把现有素材拉回规范：色相归位、
饱和度倍增、明度压深、描边换墨、肚皮降温、1px 腐蚀去 halo。全部可逆
（原始备份在 _gen/cut_whale_v1_grey.png）。

用法：
  python tools/whale_retint.py            # 处理 _gen/cut_whale.png（就地，自动备份）
  python tools/whale_retint.py --out 其他路径.png
之后跑 make_accessories.py 安装（刷新 acc_whale_plush(+_f)）。
"""
import argparse
import colorsys
import os
import shutil

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(os.path.dirname(HERE), "assets")
SRC = os.path.join(ASSETS, "_gen", "cut_whale.png")
BACKUP = os.path.join(ASSETS, "_gen", "cut_whale_v1_grey.png")

# 规范色板（配件美术规范.md §2/§8）
INK = (0x2A, 0x20, 0x28)          # 深暖紫黑（描边/睫毛/眼）
BODY = (0x56, 0x6C, 0xC2)         # 鲸鱼蓝主体
BELLY_COOL = 0.35                  # 肚皮降温强度（0=不动，1=完全到冷白）


def hls_of(rgb):
    return colorsys.rgb_to_hls(*(c / 255.0 for c in rgb))


def retint(im):
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    n_body = n_ink = n_belly = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            hue, l, s = hls_of((r, g, b))
            hue_deg = hue * 360
            if b >= r + 8 and hue_deg >= 190:
                # --- ① 主体蓝：灰蓝 → 规范蓝紫 ---
                # 色相归位 225°（#566CC2 的色相），饱和度加倍并加底，明度微压
                nh = 225.0 / 360.0 if 195 <= hue_deg <= 245 else hue
                ns = min(0.56, s * 2.1 + 0.10)
                nl = l * (0.90 if l > 0.38 else 0.96)
                r2, g2, b2 = (round(c * 255) for c in colorsys.hls_to_rgb(nh, nl, ns))
                px[x, y] = (r2, g2, b2, a)
                n_body += 1
            elif r >= b + 12 and l < 0.42:
                # --- ② 暖棕描边/眼 → 深暖紫黑（保留明度层次）---
                k = 0.82 + 0.55 * l          # 越亮越接近 #5A4A58，越暗越贴墨
                px[x, y] = (min(255, round(INK[0] * k + 30 * l)),
                            min(255, round(INK[1] * k + 24 * l)),
                            min(255, round(INK[2] * k + 32 * l)), a)
                n_ink += 1
            elif r >= b and l > 0.62 and s < 0.45:
                # --- ③ 肚皮暖奶油 → 规范冷白（#E4EAF8 方向）---
                r2 = round(r * (1 - BELLY_COOL * 0.10))
                g2 = round(g * (1 - BELLY_COOL * 0.03))
                b2 = min(255, round(b + (245 - b) * BELLY_COOL))
                px[x, y] = (r2, g2, b2, a)
                n_belly += 1
    # --- ④ 去白色 halo：邻透明区的浅/粉浅像素 = 棋盘 AA 残留 → 透明化 ---
    # （1px MinFilter 腐蚀不够：halo 有 1-3px；也不能用更大腐蚀——会把深色
    #   描边一起啃掉。深色像素一律保留：那是描边自身的 AA，压在深底上没问题）
    a_ch = im.getchannel("A")
    near_edge = a_ch.point(lambda v: 255 if v == 0 else 0) \
        .filter(ImageFilter.MaxFilter(5))          # 透明区外扩 2px
    ne = near_edge.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 or not ne[x, y]:
                continue
            hue, l, s = hls_of((r, g, b))
            if l > 0.60 and (s < 0.42 or r >= b):
                px[x, y] = (r, g, b, 0)            # 白/浅暖/粉浅 = 背景 AA 残留
    print(f"  body={n_body} ink={n_ink} belly={n_belly}")
    return im


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if not os.path.isfile(SRC):
        raise SystemExit(f"找不到 {SRC}")
    if not os.path.isfile(BACKUP):
        shutil.copy2(SRC, BACKUP)
        print(f"  已备份原始素材 → {os.path.basename(BACKUP)}")
    im = retint(Image.open(SRC))
    dst = args.out or SRC
    im.save(dst)
    print(f"  saved → {dst}")


if __name__ == "__main__":
    main()
