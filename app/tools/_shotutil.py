# -*- coding: utf-8 -*-
r"""渲染快照共用工具：把分层窗口的 Surface DIB 读成 PIL 图。

`wb_whale_win.Surface` 底层是 CreateDIBSection（BGRA、**预乘 alpha**、行序**自下而上**）。
测试与预览工具都要把绘制结果读回来看，逻辑放在这里避免两处各写一份、改一处漏一处。

优先用 numpy（快）；没有 numpy 时走纯 Python 兜底（慢但无需安装）。
不依赖桌面截屏，因此不受窗口遮挡 / DPI / 多屏影响。
"""

import ctypes
import os

from PIL import Image


def surf_to_image(surf):
    """把 Surface 的 DIB（BGRA，预乘 alpha，行序自下而上）读成 PIL RGBA。

    注意：CreateDIBSection 的 biHeight 为正 → 内存里是**自下而上**存放，
    直接按行读会得到上下颠倒的图（本项目早期"画面上下颠倒"就是同一类坑）。
    """
    buf = ctypes.string_at(surf.bits, surf.w * surf.h * 4)
    try:
        import numpy as np
        HAVE_NP = True
    except Exception:
        HAVE_NP = False

    if HAVE_NP:
        a = np.frombuffer(buf, dtype=np.uint8).reshape(surf.h, surf.w, 4).astype(np.float32)
        a = a[::-1]                                   # 自下而上 → 自上而下
        b, g, r, al = a[:, :, 0], a[:, :, 1], a[:, :, 2], a[:, :, 3]
        # 反预乘（GDI+ 写进 DIB 的是预乘 alpha）
        nz = al > 0
        for ch in (b, g, r):
            ch[nz] = np.clip(ch[nz] * 255.0 / al[nz], 0, 255)
        out = np.zeros((surf.h, surf.w, 4), dtype=np.uint8)
        out[:, :, 0] = r.round()
        out[:, :, 1] = g.round()
        out[:, :, 2] = b.round()
        out[:, :, 3] = al.round()
        return Image.fromarray(out, "RGBA")

    # 纯 Python 兜底（无 numpy 也能出快照）：逐像素反预乘 + 行翻转
    W, H = surf.w, surf.h
    out = bytearray(W * H * 4)
    stride = W * 4
    for y in range(H):
        src_row = (H - 1 - y) * stride               # 自下而上 → 自上而下
        dst_row = y * stride
        for x in range(W):
            o = src_row + x * 4
            bb, gg, rr, al = buf[o], buf[o + 1], buf[o + 2], buf[o + 3]
            if al:                                   # 反预乘：B' = B*a/255 → B = B'*255/a
                k = 255.0 / al
                bb = min(255, int(bb * k))
                gg = min(255, int(gg * k))
                rr = min(255, int(rr * k))
            else:
                bb, gg, rr = 0, 0, 0
            d = dst_row + x * 4
            out[d], out[d + 1], out[d + 2], out[d + 3] = rr, gg, bb, al
    return Image.frombytes("RGBA", (W, H), bytes(out))


def label_font(size=15):
    """取一个能渲染中文的字体（PIL 默认位图字体出不了汉字）。"""
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc"):
        p = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.isfile(p):
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return None
