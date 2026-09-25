# -*- coding: utf-8 -*-
r"""排查头身白缝：渲染分层位移帧，导出 PNG 供像素级分析。

用法：python tools/scan_seam.py
产出：$TEMP/wb_seam_*.png（常态帧 / 位移帧）
"""

import ctypes
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wb_whale_win as W          # noqa: E402
import wb_motion as MOTION        # noqa: E402

_gdi32 = ctypes.windll.gdi32


def surf_to_png(surf, path):
    """把 Surface(DIB, 底向上) 导出为 RGBA PNG（visual 顺序）。"""
    _gdi32.GdiFlush()
    w, h = surf.w, surf.h
    stride = w * 4
    buf = ctypes.create_string_buffer(stride * h)
    ctypes.memmove(buf, surf.bits, stride * h)
    from PIL import Image
    # 内存行 0 = visual 底部 → 先原样构图再垂直翻转
    im = Image.frombytes("RGBA", (w, h), buf.raw)
    im = im.transpose(Image.FLIP_TOP_BOTTOM)
    r, g, b, a = im.split()
    im = Image.merge("RGBA", (b, g, r, a))     # BGRA → RGBA
    im.save(path)
    return im


def render_frame(app, gaze, tail, tag):
    """强制指定位移渲染一帧，返回 (PIL图, 布局)。"""
    lay = app._layout()
    app._gaze_dx = gaze
    app._tail_dx = tail
    app._emotion = MOTION.EMOTION_NEUTRAL
    app._squash_t0 = 0.0
    app._hover_t = 0.0
    app._breath = 0.0
    app._float_dy = 0.0
    app._shadow_scale = 1.0
    app._wobble_until = 0.0
    app.draw()
    tmp = os.path.join(os.environ.get("TEMP", "."), f"wb_seam_{tag}.png")
    im = surf_to_png(app.surf, tmp)
    print(f"[{tag}] 已导出 {tmp}  {im.size}")
    return im, lay


def analyze_seam(im, lay, tag):
    """在头身分界高度附近扫描：找透明缝隙 / 亮白线。"""
    px = im.load()
    w, h = im.size
    # 立绘绘制：y_bottom = H-4sc，ph = pet_h-8sc；切分点 = y_bottom - ph*(1-split)
    sc = lay["sc"]
    H = lay["H"]
    ph = lay["pet_h"] - 8 * sc
    split = MOTION.DRAG_TILT_HEAD_RATIO           # 0.55
    seam_y = int(H - 4 * sc - ph * (1 - split))   # 分层接缝的视觉 y
    # 立绘水平范围（找不透明区）
    xs = [x for x in range(w) if px[x, min(h - 2, max(0, seam_y + 2))][3] > 30]
    print(f"[{tag}] lay H={H} pet_h={lay['pet_h']:.0f} ph={ph:.1f} 接缝y≈{seam_y} "
          f"立绘在y={seam_y+2}的x范围: {min(xs) if xs else '-'}..{max(xs) if xs else '-'}")
    # 扫描 seam_y-2 .. seam_y+2 五行：统计每行最小alpha、全透明缺口
    for yy in range(seam_y - 2, seam_y + 3):
        if not (0 <= yy < h):
            continue
        row_a = [px[x, yy][3] for x in range(w)]
        # 在立绘范围内的最小 alpha（排除大面积背景透明后的内部缺口检测）
        inx = [x for x in range(w) if px[x, min(h - 1, yy + 3)][3] > 30]
        if not inx:
            continue
        lo = min(px[x, yy][3] for x in range(inx[0], inx[-1]))
        # 连续全透明缺口（>4px 宽且在立绘宽度内部）
        gap = 0
        run = 0
        for x in range(inx[0], inx[-1] + 1):
            if px[x, yy][3] < 8:
                run += 1
                gap = max(gap, run)
            else:
                run = 0
        bright = sum(1 for x in range(inx[0], inx[-1] + 1)
                     if px[x, yy][3] > 60 and px[x, yy][0] > 235
                     and px[x, yy][1] > 235 and px[x, yy][2] > 235)
        print(f"[{tag}]  y={yy:3d} 立绘内minA={lo:3d} 最大透明缺口={gap:2d}px 亮白像素={bright}")
    return seam_y


def main():
    app = W.WhalePet(run_seconds=None)
    W._user32.ShowWindow(app.hwnd, 0)      # 隐藏实验窗口，不干扰
    app.active = []
    app._report_event = lambda *a, **k: None
    time.sleep(0.3)
    # 常态帧
    im0, lay = render_frame(app, 0.0, 0.0, "idle")
    analyze_seam(im0, lay, "idle")
    print()
    # 分层位移帧（头右移 / 尾左移，典型 gaze+tail 状态）
    im1, lay = render_frame(app, 1.2, -1.2, "tilt")
    analyze_seam(im1, lay, "tilt")
    app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
